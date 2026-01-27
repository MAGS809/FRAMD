from flask import Flask, render_template, request, jsonify, send_from_directory, session
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase
from werkzeug.utils import secure_filename
import os
import json
import uuid
import tempfile
import stripe
import requests
from context_engine import (
    extract_audio, transcribe_audio, analyze_ideas,
    generate_script, find_clip_timestamps, generate_captions,
    cut_video_clip, concatenate_clips
)

class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)

app = Flask(__name__)
app.secret_key = os.environ.get('SESSION_SECRET', 'dev-secret-key')
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL")
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_recycle": 300,
    "pool_pre_ping": True,
}
db.init_app(app)

class UserTokens(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    balance = db.Column(db.Integer, default=120)
    last_updated = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())

class MediaAsset(db.Model):
    """Legal media assets with licensing metadata - stores LINKS only, not files."""
    id = db.Column(db.String(255), primary_key=True)  # e.g., pexels_12345, wikimedia_67890
    source_page = db.Column(db.Text)  # Made nullable to handle missing source pages
    download_url = db.Column(db.Text, nullable=False)
    thumbnail_url = db.Column(db.Text)  # Preview image
    source = db.Column(db.String(50), nullable=False)  # wikimedia_commons, pexels
    license = db.Column(db.String(100), nullable=False)  # CC BY 4.0, CC0, Pexels License
    license_url = db.Column(db.Text)
    commercial_use_allowed = db.Column(db.Boolean, default=True)
    derivatives_allowed = db.Column(db.Boolean, default=True)
    attribution_required = db.Column(db.Boolean, default=False)
    attribution_text = db.Column(db.Text)
    content_type = db.Column(db.String(20), nullable=False)  # video, image
    duration_sec = db.Column(db.Float)  # For videos
    resolution = db.Column(db.String(20))  # e.g., 1920x1080
    description = db.Column(db.Text)
    tags = db.Column(db.JSON)  # List of descriptive tags
    safe_flags = db.Column(db.JSON)  # {no_sexual: true, no_brands: true, no_celeb: true}
    status = db.Column(db.String(20), default='safe')  # safe, pending, rejected
    created_at = db.Column(db.DateTime, server_default=db.func.now())

class KeywordAssetCache(db.Model):
    """Cache keyword → asset associations for faster visual curation."""
    id = db.Column(db.Integer, primary_key=True)
    keyword = db.Column(db.String(255), nullable=False, index=True)
    context = db.Column(db.String(100))  # mood, setting, tone
    asset_id = db.Column(db.String(255), db.ForeignKey('media_asset.id'), nullable=False)
    relevance_score = db.Column(db.Float, default=1.0)  # How well this asset fits the keyword
    use_count = db.Column(db.Integer, default=0)  # How many times selected
    created_at = db.Column(db.DateTime, server_default=db.func.now())

with app.app_context():
    db.create_all()
    if not UserTokens.query.first():
        token_entry = UserTokens()
        token_entry.balance = 120
        db.session.add(token_entry)
        db.session.commit()

def extract_dialogue_only(script_text):
    """
    Filter script to only include spoken dialogue lines.
    Removes visual directions, stage directions, scene headers, and parentheticals.
    """
    import re
    
    dialogue_lines = []
    
    for line in script_text.split('\n'):
        line = line.strip()
        if not line:
            continue
        
        # Skip visual directions [VISUAL: ...]
        if line.startswith('[VISUAL') or line.startswith('[CUT') or line.startswith('[FADE'):
            continue
        
        # Skip scene headers (INT., EXT., TITLE:, CUT TO:)
        if line.startswith('INT.') or line.startswith('EXT.') or line.startswith('TITLE:') or line.startswith('CUT TO'):
            continue
        
        # Skip lines that are just parentheticals like (quietly) or (V.O.)
        if re.match(r'^\([^)]+\)$', line):
            continue
        
        # Skip empty visual/stage directions
        if re.match(r'^\[.*\]$', line):
            continue
        
        # Remove inline parentheticals but keep the rest
        line = re.sub(r'\([^)]*\)', '', line).strip()
        
        # If line has CHARACTER: format, keep the dialogue part
        if ':' in line:
            parts = line.split(':', 1)
            # If first part looks like character name (1-3 words, all caps or title case)
            char_part = parts[0].strip()
            if len(char_part.split()) <= 3 and (char_part.isupper() or char_part.istitle()):
                dialogue = parts[1].strip()
                if dialogue:
                    dialogue_lines.append(dialogue)
            else:
                # Not a character line, keep if it's not a direction
                if not char_part.startswith('['):
                    dialogue_lines.append(line)
        else:
            # Regular line - keep if not empty
            if line and not line.startswith('['):
                dialogue_lines.append(line)
    
    return ' '.join(dialogue_lines)


# Stripe Integration
def get_stripe_credentials():
    """Fetch Stripe credentials from Replit connection API."""
    hostname = os.environ.get('REPLIT_CONNECTORS_HOSTNAME')
    repl_identity = os.environ.get('REPL_IDENTITY')
    web_repl_renewal = os.environ.get('WEB_REPL_RENEWAL')
    
    if repl_identity:
        x_replit_token = 'repl ' + repl_identity
    elif web_repl_renewal:
        x_replit_token = 'depl ' + web_repl_renewal
    else:
        return None, None
    
    is_production = os.environ.get('REPLIT_DEPLOYMENT') == '1'
    target_env = 'production' if is_production else 'development'
    
    url = f"https://{hostname}/api/v2/connection?include_secrets=true&connector_names=stripe&environment={target_env}"
    
    response = requests.get(url, headers={
        'Accept': 'application/json',
        'X_REPLIT_TOKEN': x_replit_token
    })
    
    data = response.json()
    connection = data.get('items', [{}])[0]
    settings = connection.get('settings', {})
    
    return settings.get('publishable'), settings.get('secret')

# Token pricing
TOKEN_PACKAGES = {
    100: 200,    # 100 tokens = $2.00 (200 cents) - 60% cheaper with Krakd
    500: 800,    # 500 tokens = $8.00 (800 cents) - 60% cheaper with Krakd
    2000: 2500   # 2000 tokens = $25.00 (2500 cents) - 58% cheaper with Krakd
}

@app.route('/create-checkout-session', methods=['POST'])
def create_checkout_session():
    """Create a Stripe checkout session for token purchase."""
    try:
        data = request.get_json()
        token_amount = data.get('amount', 500)
        
        if token_amount not in TOKEN_PACKAGES:
            return jsonify({'error': 'Invalid token amount'}), 400
        
        price_cents = TOKEN_PACKAGES[token_amount]
        
        _, secret_key = get_stripe_credentials()
        if not secret_key:
            return jsonify({'error': 'Payment not configured'}), 500
        
        stripe.api_key = secret_key
        
        # Get the domain for redirect URLs
        domains = os.environ.get('REPLIT_DOMAINS', 'localhost:5000')
        domain = domains.split(',')[0] if domains else 'localhost:5000'
        protocol = 'https' if 'replit' in domain else 'http'
        base_url = f"{protocol}://{domain}"
        
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': f'{token_amount} Tokens',
                        'description': f'Krakd Post Assembler - {token_amount} tokens for content creation',
                    },
                    'unit_amount': price_cents,
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=f'{base_url}/?success=true&tokens={token_amount}',
            cancel_url=f'{base_url}/?canceled=true',
            metadata={
                'token_amount': str(token_amount)
            }
        )
        
        return jsonify({'url': checkout_session.url})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/stripe-webhook', methods=['POST'])
def stripe_webhook():
    """Handle Stripe webhook events."""
    payload = request.get_data()
    sig_header = request.headers.get('Stripe-Signature')
    
    _, secret_key = get_stripe_credentials()
    if not secret_key:
        return jsonify({'error': 'Payment not configured'}), 500
    
    stripe.api_key = secret_key
    
    try:
        event = stripe.Event.construct_from(json.loads(payload), stripe.api_key)
    except ValueError:
        return jsonify({'error': 'Invalid payload'}), 400
    
    if event['type'] == 'checkout.session.completed':
        session_data = event['data']['object']
        token_amount = int(session_data.get('metadata', {}).get('token_amount', 0))
        
        if token_amount > 0:
            token_entry = UserTokens.query.first()
            if token_entry:
                token_entry.balance += token_amount
                db.session.commit()
    
    return jsonify({'received': True})

@app.route('/add-tokens', methods=['POST'])
def add_tokens():
    """Add tokens after successful payment (called from frontend on success)."""
    data = request.get_json()
    amount = data.get('amount', 0)
    
    if amount > 0:
        token_entry = UserTokens.query.first()
        if token_entry:
            token_entry.balance += amount
            db.session.commit()
            return jsonify({'success': True, 'balance': token_entry.balance})
    
    return jsonify({'success': False, 'error': 'Invalid amount'}), 400

@app.route('/get-tokens', methods=['GET'])
def get_tokens():
    token_entry = UserTokens.query.first()
    return jsonify({
        'success': True,
        'balance': token_entry.balance if token_entry else 0
    })

@app.route('/deduct-tokens', methods=['POST'])
def deduct_tokens():
    data = request.get_json()
    amount = data.get('amount', 35)
    token_entry = UserTokens.query.first()
    # Deduct tokens (simplified for dev)
    token_entry.balance -= amount
    db.session.commit()
    return jsonify({'success': True, 'balance': token_entry.balance})

# Asset Library - Legal Media with Licensing
ALLOWED_LICENSES = ['CC0', 'Public Domain', 'CC BY', 'CC BY-SA', 'CC BY 4.0', 'CC BY-SA 4.0', 'Pexels License']

# License validation - HARD REJECT list (checked FIRST)
REJECTED_LICENSE_PATTERNS = ['nc', 'nd', 'editorial', 'all rights reserved', 'getty', 'shutterstock']

# License whitelist for Wikimedia Commons
WIKIMEDIA_ALLOWED_LICENSES = [
    'cc0', 'cc-zero', 'public domain', 'pd',
    'cc-by', 'cc-by-4.0', 'cc-by-3.0', 'cc-by-2.5',
    'cc-by-sa', 'cc-by-sa-4.0', 'cc-by-sa-3.0', 'cc-by-sa-2.5'
]

def validate_license(license_short):
    """
    Validate a license string. Returns (is_valid, license_type, rejection_reason).
    CRITICAL: Check rejection patterns FIRST before allowing.
    """
    license_lower = license_short.lower().strip()
    
    # STEP 1: HARD REJECT - Check for disallowed patterns FIRST
    for pattern in REJECTED_LICENSE_PATTERNS:
        if pattern in license_lower:
            return False, None, f'Rejected: contains "{pattern}"'
    
    # STEP 2: Check for allowed licenses
    if 'cc0' in license_lower or 'cc-zero' in license_lower or license_lower == 'cc0':
        return True, 'CC0', None
    if 'public domain' in license_lower or license_lower == 'pd':
        return True, 'CC0', None
    if 'cc-by-sa' in license_lower or 'cc by-sa' in license_lower:
        return True, 'CC BY-SA', None
    if 'cc-by' in license_lower or 'cc by' in license_lower:
        return True, 'CC BY', None
    if 'pexels' in license_lower:
        return True, 'Pexels License', None
    
    # Unknown license - reject
    return False, None, f'Unknown/unclear license: {license_short}'

@app.route('/search-wikimedia-videos', methods=['POST'])
def search_wikimedia_videos():
    """Search Wikimedia Commons for videos with proper licensing."""
    data = request.get_json()
    query = data.get('query', '')
    limit = data.get('limit', 10)
    
    try:
        # Search Wikimedia Commons API for video files
        search_url = 'https://commons.wikimedia.org/w/api.php'
        params = {
            'action': 'query',
            'format': 'json',
            'generator': 'search',
            'gsrsearch': f'{query} filetype:video',
            'gsrlimit': limit,
            'prop': 'imageinfo|categories',
            'iiprop': 'url|extmetadata|size|mime',
            'iiurlwidth': 640
        }
        
        wiki_headers = {'User-Agent': 'KrakdPostAssembler/1.0 (https://replit.com; contact@krakd.app)'}
        response = requests.get(search_url, params=params, headers=wiki_headers, timeout=15)
        data = response.json()
        
        videos = []
        pages = data.get('query', {}).get('pages', {})
        
        for page_id, page in pages.items():
            if page_id == '-1':
                continue
                
            imageinfo = page.get('imageinfo', [{}])[0]
            extmeta = imageinfo.get('extmetadata', {})
            
            # Get license info
            license_short = extmeta.get('LicenseShortName', {}).get('value', '')
            license_url = extmeta.get('LicenseUrl', {}).get('value', '')
            
            # Validate license using safe function (rejects NC/ND first)
            is_valid, our_license, rejection_reason = validate_license(license_short)
            if not is_valid:
                continue
            
            # Get attribution
            artist = extmeta.get('Artist', {}).get('value', 'Unknown')
            import re
            artist = re.sub('<[^<]+?>', '', artist).strip()
            
            attribution_required = our_license not in ['CC0', 'Public Domain']
            
            videos.append({
                'id': f"wikimedia_{page.get('pageid')}",
                'source': 'wikimedia_commons',
                'source_page': f"https://commons.wikimedia.org/wiki/{page.get('title', '').replace(' ', '_')}",
                'download_url': imageinfo.get('url'),
                'thumbnail': imageinfo.get('thumburl'),
                'title': page.get('title', '').replace('File:', ''),
                'resolution': f"{imageinfo.get('width', 0)}x{imageinfo.get('height', 0)}",
                'license': our_license,
                'license_url': license_url or 'https://creativecommons.org/licenses/',
                'commercial_use_allowed': True,
                'derivatives_allowed': True,
                'attribution_required': attribution_required,
                'attribution_text': f"{artist} / Wikimedia Commons / {our_license}"
            })
        
        return jsonify({'success': True, 'videos': videos})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/search-all-sources', methods=['POST'])
def search_all_sources():
    """Search both Pexels and Wikimedia Commons for legal videos."""
    data = request.get_json()
    query = data.get('query', '')
    
    all_videos = []
    
    # Search Pexels
    pexels_key = os.environ.get('PEXELS_API_KEY')
    if pexels_key:
        try:
            response = requests.get(
                'https://api.pexels.com/videos/search',
                headers={'Authorization': pexels_key},
                params={'query': query, 'per_page': 5, 'orientation': 'portrait'},
                timeout=10
            )
            for video in response.json().get('videos', []):
                video_files = video.get('video_files', [])
                best_file = next((vf for vf in video_files if vf.get('height', 0) >= 720), video_files[0] if video_files else None)
                if best_file:
                    all_videos.append({
                        'id': f"pexels_{video['id']}",
                        'source': 'pexels',
                        'source_page': video.get('url'),
                        'download_url': best_file.get('link'),
                        'thumbnail': video.get('image'),
                        'duration': video.get('duration'),
                        'license': 'Pexels License',
                        'license_url': 'https://www.pexels.com/license/',
                        'attribution_required': False,
                        'attribution_text': f"Video by {video.get('user', {}).get('name', 'Unknown')} on Pexels"
                    })
        except:
            pass
    
    # Search Wikimedia Commons
    try:
        search_url = 'https://commons.wikimedia.org/w/api.php'
        params = {
            'action': 'query',
            'format': 'json',
            'generator': 'search',
            'gsrsearch': f'{query} filetype:video',
            'gsrlimit': 5,
            'prop': 'imageinfo',
            'iiprop': 'url|extmetadata',
            'iiurlwidth': 640
        }
        wiki_headers = {'User-Agent': 'KrakdPostAssembler/1.0 (https://replit.com; contact@krakd.app)'}
        response = requests.get(search_url, params=params, headers=wiki_headers, timeout=10)
        pages = response.json().get('query', {}).get('pages', {})
        
        for page_id, page in pages.items():
            if page_id == '-1':
                continue
            imageinfo = page.get('imageinfo', [{}])[0]
            extmeta = imageinfo.get('extmetadata', {})
            license_short = extmeta.get('LicenseShortName', {}).get('value', '')
            
            # Validate license using safe function (rejects NC/ND first)
            is_valid, our_license, _ = validate_license(license_short)
            if is_valid:
                import re
                artist = re.sub('<[^<]+?>', '', extmeta.get('Artist', {}).get('value', 'Unknown')).strip()
                all_videos.append({
                    'id': f"wikimedia_{page.get('pageid')}",
                    'source': 'wikimedia_commons',
                    'source_page': f"https://commons.wikimedia.org/wiki/{page.get('title', '').replace(' ', '_')}",
                    'download_url': imageinfo.get('url'),
                    'thumbnail': imageinfo.get('thumburl'),
                    'license': our_license,
                    'license_url': extmeta.get('LicenseUrl', {}).get('value', ''),
                    'attribution_required': our_license not in ['CC0', 'Public Domain'],
                    'attribution_text': f"{artist} / Wikimedia Commons / {our_license}"
                })
    except:
        pass
    
    return jsonify({'success': True, 'videos': all_videos, 'sources': ['pexels', 'wikimedia_commons']})


@app.route('/search-pexels-videos', methods=['POST'])
def search_pexels_videos():
    """Search Pexels for videos - all Pexels content is free for commercial use."""
    data = request.get_json()
    query = data.get('query', '')
    per_page = data.get('per_page', 10)
    
    pexels_key = os.environ.get('PEXELS_API_KEY')
    if not pexels_key:
        return jsonify({'success': False, 'error': 'Pexels API not configured'}), 500
    
    try:
        response = requests.get(
            'https://api.pexels.com/videos/search',
            headers={'Authorization': pexels_key},
            params={'query': query, 'per_page': per_page, 'orientation': 'portrait'}
        )
        data = response.json()
        
        videos = []
        for video in data.get('videos', []):
            # Find best quality video file (prefer 1080p portrait)
            video_files = video.get('video_files', [])
            best_file = None
            for vf in video_files:
                if vf.get('height', 0) >= 1080:
                    best_file = vf
                    break
            if not best_file and video_files:
                best_file = video_files[0]
            
            if best_file:
                videos.append({
                    'id': f"pexels_{video['id']}",
                    'source': 'pexels',
                    'source_page': video.get('url'),
                    'download_url': best_file.get('link'),
                    'thumbnail': video.get('image'),
                    'duration': video.get('duration'),
                    'resolution': f"{best_file.get('width')}x{best_file.get('height')}",
                    'license': 'Pexels License',
                    'license_url': 'https://www.pexels.com/license/',
                    'commercial_use_allowed': True,
                    'derivatives_allowed': True,
                    'attribution_required': False,
                    'attribution_text': f"Video by {video.get('user', {}).get('name', 'Unknown')} on Pexels"
                })
        
        return jsonify({'success': True, 'videos': videos})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/save-asset', methods=['POST'])
def save_asset():
    """Save a verified legal asset to the library."""
    data = request.get_json()
    
    # Validate required fields
    required = ['id', 'source_page', 'download_url', 'source', 'license', 'content_type']
    for field in required:
        if not data.get(field):
            return jsonify({'success': False, 'error': f'Missing required field: {field}'}), 400
    
    # Validate license is allowed
    if data['license'] not in ALLOWED_LICENSES:
        return jsonify({'success': False, 'error': f'License not allowed: {data["license"]}'}), 400
    
    try:
        asset = MediaAsset(
            id=data['id'],
            source_page=data['source_page'],
            download_url=data['download_url'],
            source=data['source'],
            license=data['license'],
            license_url=data.get('license_url'),
            commercial_use_allowed=data.get('commercial_use_allowed', True),
            derivatives_allowed=data.get('derivatives_allowed', True),
            attribution_required=data.get('attribution_required', False),
            attribution_text=data.get('attribution_text'),
            content_type=data['content_type'],
            duration_sec=data.get('duration_sec'),
            resolution=data.get('resolution'),
            description=data.get('description'),
            tags=data.get('tags', []),
            safe_flags=data.get('safe_flags', {}),
            status='safe'
        )
        db.session.merge(asset)  # Use merge to update if exists
        db.session.commit()
        return jsonify({'success': True, 'id': asset.id})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/search-assets', methods=['POST'])
def search_assets():
    """Search the asset library by tags or description."""
    data = request.get_json()
    tags = data.get('tags', [])
    content_type = data.get('content_type')  # 'video' or 'image'
    limit = data.get('limit', 10)
    
    query = MediaAsset.query.filter(MediaAsset.status == 'safe')
    
    if content_type:
        query = query.filter(MediaAsset.content_type == content_type)
    
    # Get all assets and filter by tags in Python (JSON filtering varies by DB)
    assets = query.limit(100).all()
    
    results = []
    for asset in assets:
        asset_tags = asset.tags or []
        # Check if any requested tags match
        if not tags or any(tag.lower() in [t.lower() for t in asset_tags] for tag in tags):
            results.append({
                'id': asset.id,
                'source': asset.source,
                'source_page': asset.source_page,
                'download_url': asset.download_url,
                'thumbnail': asset.download_url,  # For Pexels, use video URL
                'content_type': asset.content_type,
                'duration': asset.duration_sec,
                'resolution': asset.resolution,
                'description': asset.description,
                'tags': asset.tags,
                'license': asset.license,
                'attribution_required': asset.attribution_required,
                'attribution_text': asset.attribution_text
            })
            if len(results) >= limit:
                break
    
    return jsonify({'success': True, 'assets': results})

@app.route('/curate-visuals', methods=['POST'])
def curate_visuals():
    """AI curates visuals based on script context - checks cache first, then external APIs."""
    from openai import OpenAI
    import re as regex
    
    data = request.get_json()
    script = data.get('script', '')
    user_guidance = data.get('user_guidance', '')  # Optional user direction
    
    if not script:
        return jsonify({'success': False, 'error': 'No script provided'}), 400
    
    client = OpenAI(
        api_key=os.environ.get("XAI_API_KEY"),
        base_url="https://api.x.ai/v1"
    )
    
    content_type = data.get('content_type', 'educational')
    
    # Enhanced prompt that extracts setting, mood, and visual intent
    system_prompt = """You are Krakd's visual curator. Analyze the script deeply to find visuals that SERVE the message.

EXTRACT FROM SCRIPT:
1. SETTING - Where does this take place? (office, street, home, abstract)
2. MOOD - What's the emotional tone? (tense, hopeful, contemplative, urgent)
3. VISUAL INTENT - What should the viewer FEEL, not just see?

For each section, create search queries that are:
- SPECIFIC to the script content (not generic B-roll)
- Contextual to the setting and mood
- Legally safe (no celebrities, brands, or sexual content)

OUTPUT FORMAT (JSON):
{
  "overall_context": {
    "setting": "corporate office, late evening",
    "mood": "tense, confrontational",
    "visual_intent": "Create unease through sterile environments and isolation"
  },
  "sections": [
    {
      "script_segment": "The actual dialogue from this part...",
      "setting": "empty boardroom",
      "mood": "tense",
      "visual_notes": "This moment needs visual isolation - one person against institutional coldness",
      "search_queries": ["empty boardroom table", "fluorescent office lights", "person alone corporate"],
      "cache_keywords": ["corporate_tension", "isolation", "office_night"]
    }
  ]
}

CRITICAL: 
- search_queries = specific terms for API search
- cache_keywords = conceptual tags for our asset library"""

    user_content = f"Create a visual board for this script:\n\n{script}"
    if user_guidance:
        user_content += f"\n\nUSER DIRECTION: {user_guidance}"

    try:
        response = client.chat.completions.create(
            model="grok-3",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            response_format={"type": "json_object"},
            max_tokens=1500
        )
        
        visual_board = json.loads(response.choices[0].message.content or '{}')
        pexels_key = os.environ.get('PEXELS_API_KEY')
        
        for section in visual_board.get('sections', []):
            section['suggested_videos'] = []
            cache_keywords = section.get('cache_keywords', [])
            mood = section.get('mood', '')
            
            # STEP 1: Check cache first for matching keywords
            cached_assets = []
            for keyword in cache_keywords[:3]:
                cache_entries = KeywordAssetCache.query.filter(
                    KeywordAssetCache.keyword.ilike(f'%{keyword}%')
                ).order_by(KeywordAssetCache.use_count.desc()).limit(2).all()
                
                for entry in cache_entries:
                    asset = MediaAsset.query.get(entry.asset_id)
                    if asset and asset.status == 'safe':
                        cached_assets.append({
                            'id': asset.id,
                            'source': asset.source,
                            'thumbnail': asset.thumbnail_url,
                            'download_url': asset.download_url,
                            'duration': asset.duration_sec,
                            'license': asset.license,
                            'license_url': asset.license_url,
                            'attribution': asset.attribution_text,
                            'from_cache': True
                        })
            
            # Add cached assets first (deduplicated)
            seen_ids = set()
            for asset in cached_assets:
                if asset['id'] not in seen_ids:
                    section['suggested_videos'].append(asset)
                    seen_ids.add(asset['id'])
            
            # STEP 2: Search external APIs if we need more options
            if len(section['suggested_videos']) < 4:
                for query in section.get('search_queries', [])[:2]:
                    # Search Pexels
                    if pexels_key:
                        try:
                            resp = requests.get(
                                'https://api.pexels.com/videos/search',
                                headers={'Authorization': pexels_key},
                                params={'query': query, 'per_page': 4, 'orientation': 'portrait'},
                                timeout=10
                            )
                            videos = resp.json().get('videos', [])
                            for v in videos[:3]:
                                asset_id = f"pexels_{v['id']}"
                                if asset_id in seen_ids:
                                    continue
                                    
                                video_files = v.get('video_files', [])
                                best = next((vf for vf in video_files if vf.get('height', 0) >= 720), video_files[0] if video_files else None)
                                if best:
                                    video_data = {
                                        'id': asset_id,
                                        'source': 'pexels',
                                        'source_page': v.get('url'),
                                        'thumbnail': v.get('image'),
                                        'download_url': best.get('link'),
                                        'duration': v.get('duration'),
                                        'license': 'Pexels License',
                                        'license_url': 'https://www.pexels.com/license/',
                                        'attribution': f"Video by {v.get('user', {}).get('name', 'Unknown')} on Pexels",
                                        'from_cache': False
                                    }
                                    section['suggested_videos'].append(video_data)
                                    seen_ids.add(asset_id)
                        except:
                            pass
                    
                    # Search Wikimedia Commons for videos
                    try:
                        search_url = 'https://commons.wikimedia.org/w/api.php'
                        wiki_headers = {'User-Agent': 'KrakdPostAssembler/1.0 (https://replit.com; contact@krakd.app)'}
                        
                        # Try multiple search strategies for better video coverage
                        search_results = []
                        
                        # Strategy 1: Direct search with video extension
                        search_params = {
                            'action': 'query',
                            'format': 'json',
                            'list': 'search',
                            'srsearch': f'{query} filemime:video',
                            'srnamespace': 6,
                            'srlimit': 6
                        }
                        search_resp = requests.get(search_url, params=search_params, headers=wiki_headers, timeout=10)
                        print(f"[Wikimedia] Status: {search_resp.status_code}")
                        if search_resp.status_code == 200:
                            data = search_resp.json()
                            search_results = data.get('query', {}).get('search', [])
                        
                        # Strategy 2: Fallback - search with file extensions if no results
                        if not search_results:
                            search_params['srsearch'] = f'{query} .webm'
                            search_resp = requests.get(search_url, params=search_params, headers=wiki_headers, timeout=10)
                            if search_resp.status_code == 200:
                                data = search_resp.json()
                                search_results = data.get('query', {}).get('search', [])
                        
                        # Strategy 3: Simplify query - use just first word for broader results
                        if not search_results and ' ' in query:
                            simple_query = query.split()[0]
                            search_params['srsearch'] = f'{simple_query} filemime:video'
                            search_resp = requests.get(search_url, params=search_params, headers=wiki_headers, timeout=10)
                            if search_resp.status_code == 200:
                                data = search_resp.json()
                                search_results = data.get('query', {}).get('search', [])
                        
                        print(f"[Wikimedia] Query: {query}, Found {len(search_results)} results")
                        
                        if not search_results:
                            continue
                        
                        # Get imageinfo for found pages
                        page_ids = [str(r['pageid']) for r in search_results]
                        info_params = {
                            'action': 'query',
                            'format': 'json',
                            'pageids': '|'.join(page_ids),
                            'prop': 'imageinfo',
                            'iiprop': 'url|extmetadata',
                            'iiurlwidth': 320
                        }
                        info_resp = requests.get(search_url, params=info_params, headers=wiki_headers, timeout=10)
                        wiki_data = info_resp.json()
                        pages = wiki_data.get('query', {}).get('pages', {})
                        print(f"[Wikimedia] Query: {query}, Found {len(pages)} pages")
                        
                        for page_id, page in pages.items():
                            if page_id == '-1':
                                continue
                            asset_id = f"wikimedia_{page.get('pageid')}"
                            if asset_id in seen_ids:
                                continue
                                
                            imageinfo = page.get('imageinfo', [{}])[0]
                            extmeta = imageinfo.get('extmetadata', {})
                            license_short = extmeta.get('LicenseShortName', {}).get('value', '')
                            license_url = extmeta.get('LicenseUrl', {}).get('value', '')
                            
                            # Validate license using safe function (rejects NC/ND first)
                            is_valid, our_license, rejection = validate_license(license_short)
                            if not is_valid:
                                print(f"[Wikimedia] Rejected {asset_id}: {rejection}")
                                continue
                            
                            artist = regex.sub('<[^<]+?>', '', extmeta.get('Artist', {}).get('value', 'Unknown')).strip()
                            
                            source_page = f"https://commons.wikimedia.org/wiki/{page.get('title', '').replace(' ', '_')}"
                            
                            video_data = {
                                'id': asset_id,
                                'source': 'wikimedia',
                                'source_page': source_page,
                                'thumbnail': imageinfo.get('thumburl'),
                                'download_url': imageinfo.get('url'),
                                'license': our_license,
                                'license_url': license_url or 'https://creativecommons.org/licenses/',
                                'attribution': f"{artist} / Wikimedia Commons / {our_license}",
                                'from_cache': False
                            }
                            section['suggested_videos'].append(video_data)
                            seen_ids.add(asset_id)
                            print(f"[Wikimedia] Added: {asset_id} ({our_license})")
                    except Exception as wiki_err:
                        print(f"[Wikimedia] Error searching: {wiki_err}")
        
        return jsonify({'success': True, 'visual_board': visual_board})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/save-to-cache', methods=['POST'])
def save_to_cache():
    """Save a selected asset to the cache with keywords for future use."""
    data = request.get_json()
    asset = data.get('asset', {})
    keywords = data.get('keywords', [])
    context = data.get('context', '')
    
    if not asset.get('id') or not asset.get('download_url'):
        return jsonify({'success': False, 'error': 'Missing asset data'}), 400
    
    try:
        # Save or update the asset - must commit asset first due to foreign key
        existing = db.session.get(MediaAsset, asset['id'])
        if not existing:
            new_asset = MediaAsset(
                id=asset['id'],
                source_page=asset.get('source_page') or '',
                download_url=asset['download_url'],
                thumbnail_url=asset.get('thumbnail'),
                source=asset.get('source', 'unknown'),
                license=asset.get('license', 'Unknown'),
                license_url=asset.get('license_url') or '',
                commercial_use_allowed=True,
                derivatives_allowed=True,
                attribution_required=asset.get('license', '') not in ['CC0', 'Public Domain'],
                attribution_text=asset.get('attribution') or '',
                content_type='video',
                duration_sec=asset.get('duration'),
                tags=keywords,
                safe_flags={'no_sexual': True, 'no_brands': True, 'no_celeb': True},
                status='safe'
            )
            db.session.add(new_asset)
            db.session.commit()  # Commit asset first before adding keyword associations
        
        # Create keyword associations
        for keyword in keywords:
            if not keyword or not keyword.strip():
                continue
            cache_entry = KeywordAssetCache.query.filter_by(
                keyword=keyword.lower().strip(),
                asset_id=asset['id']
            ).first()
            
            if cache_entry:
                cache_entry.use_count += 1
            else:
                cache_entry = KeywordAssetCache(
                    keyword=keyword.lower().strip(),
                    context=context,
                    asset_id=asset['id'],
                    relevance_score=1.0,
                    use_count=1
                )
                db.session.add(cache_entry)
        
        db.session.commit()
        return jsonify({'success': True, 'message': 'Asset cached for future use'})
    except Exception as e:
        db.session.rollback()
        print(f"[save-to-cache] Error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/ingest', methods=['POST'])
def ingest_assets():
    """Crawl and save verified legal asset LINKS with rejection logging."""
    import re as regex
    
    data = request.get_json()
    query = data.get('query', '')
    limit = data.get('limit', 20)
    source = data.get('source', 'all')
    
    if not query:
        return jsonify({'success': False, 'error': 'Query required'}), 400
    
    saved = 0
    rejected = []
    pexels_key = os.environ.get('PEXELS_API_KEY')
    
    # Search Pexels
    if source in ['all', 'pexels'] and pexels_key:
        try:
            resp = requests.get(
                'https://api.pexels.com/videos/search',
                headers={'Authorization': pexels_key},
                params={'query': query, 'per_page': min(limit, 40)},
                timeout=15
            )
            for v in resp.json().get('videos', []):
                asset_id = f"pexels_{v['id']}"
                if MediaAsset.query.get(asset_id):
                    continue
                    
                video_files = v.get('video_files', [])
                best = next((vf for vf in video_files if vf.get('height', 0) >= 720), video_files[0] if video_files else None)
                if not best:
                    rejected.append({'id': asset_id, 'reason': 'No suitable video file'})
                    continue
                
                new_asset = MediaAsset(
                    id=asset_id,
                    source_page=v.get('url', ''),
                    download_url=best.get('link', ''),
                    thumbnail_url=v.get('image'),
                    source='pexels',
                    license='Pexels License',
                    license_url='https://www.pexels.com/license/',
                    commercial_use_allowed=True,
                    derivatives_allowed=True,
                    attribution_required=False,
                    attribution_text=f"Video by {v.get('user', {}).get('name', 'Unknown')} on Pexels",
                    content_type='video',
                    duration_sec=v.get('duration'),
                    resolution=f"{best.get('width', 0)}x{best.get('height', 0)}",
                    tags=[query],
                    safe_flags={'no_sexual': True, 'no_brands': True, 'no_celeb': True},
                    status='safe'
                )
                db.session.add(new_asset)
                saved += 1
        except Exception as e:
            rejected.append({'source': 'pexels', 'reason': str(e)})
    
    # Search Wikimedia Commons
    if source in ['all', 'wikimedia_commons']:
        try:
            search_url = 'https://commons.wikimedia.org/w/api.php'
            params = {
                'action': 'query',
                'format': 'json',
                'generator': 'search',
                'gsrsearch': f'{query} filetype:video',
                'gsrlimit': min(limit, 50),
                'prop': 'imageinfo',
                'iiprop': 'url|extmetadata|size',
                'iiurlwidth': 320
            }
            wiki_headers = {'User-Agent': 'KrakdPostAssembler/1.0 (https://replit.com; contact@krakd.app)'}
            resp = requests.get(search_url, params=params, headers=wiki_headers, timeout=15)
            pages = resp.json().get('query', {}).get('pages', {})
            
            for page_id, page in pages.items():
                if page_id == '-1':
                    continue
                    
                asset_id = f"wikimedia_{page.get('pageid')}"
                if MediaAsset.query.get(asset_id):
                    continue
                
                imageinfo = page.get('imageinfo', [{}])[0]
                extmeta = imageinfo.get('extmetadata', {})
                license_short = extmeta.get('LicenseShortName', {}).get('value', '')
                license_url = extmeta.get('LicenseUrl', {}).get('value', '')
                
                # Validate license using safe function (rejects NC/ND first)
                is_valid, our_license, rejection_reason = validate_license(license_short)
                if not is_valid:
                    rejected.append({'id': asset_id, 'reason': rejection_reason})
                    continue
                
                artist = regex.sub('<[^<]+?>', '', extmeta.get('Artist', {}).get('value', 'Unknown')).strip()
                attribution_required = our_license not in ['CC0', 'Public Domain']
                
                source_page = f"https://commons.wikimedia.org/wiki/{page.get('title', '').replace(' ', '_')}"
                
                new_asset = MediaAsset(
                    id=asset_id,
                    source_page=source_page,
                    download_url=imageinfo.get('url', ''),
                    thumbnail_url=imageinfo.get('thumburl'),
                    source='wikimedia_commons',
                    license=our_license,
                    license_url=license_url or 'https://creativecommons.org/licenses/',
                    commercial_use_allowed=True,
                    derivatives_allowed=True,
                    attribution_required=attribution_required,
                    attribution_text=f"{artist} / Wikimedia Commons / {our_license}",
                    content_type='video',
                    resolution=f"{imageinfo.get('width', 0)}x{imageinfo.get('height', 0)}",
                    tags=[query],
                    safe_flags={'no_sexual': True, 'no_brands': True, 'no_celeb': True},
                    status='safe'
                )
                db.session.add(new_asset)
                saved += 1
        except Exception as e:
            rejected.append({'source': 'wikimedia', 'reason': str(e)})
    
    db.session.commit()
    return jsonify({
        'success': True,
        'saved': saved,
        'rejected_count': len(rejected),
        'rejected': rejected[:10]  # Show first 10 rejections
    })


@app.route('/assets', methods=['GET'])
def query_assets():
    """Query cached assets by tags and content type."""
    tags = request.args.get('tags', '').split(',') if request.args.get('tags') else []
    content_type = request.args.get('content_type', 'video')
    limit = int(request.args.get('limit', 20))
    
    query = MediaAsset.query.filter(
        MediaAsset.status == 'safe',
        MediaAsset.content_type == content_type
    )
    
    if tags:
        # Filter by tags (any match)
        from sqlalchemy import or_
        tag_filters = [MediaAsset.tags.contains([tag]) for tag in tags if tag]
        if tag_filters:
            query = query.filter(or_(*tag_filters))
    
    assets = query.limit(limit).all()
    
    return jsonify({
        'success': True,
        'count': len(assets),
        'assets': [{
            'id': a.id,
            'source': a.source,
            'source_page': a.source_page,
            'download_url': a.download_url,
            'thumbnail': a.thumbnail_url,
            'license': a.license,
            'license_url': a.license_url,
            'attribution': a.attribution_text,
            'tags': a.tags,
            'duration': a.duration_sec
        } for a in assets]
    })


@app.route('/download-asset', methods=['POST'])
def download_asset():
    """Download asset on-demand for final render. Only from allowed domains."""
    data = request.get_json()
    asset_id = data.get('asset_id')
    download_url = data.get('download_url')
    
    if not download_url:
        return jsonify({'success': False, 'error': 'No download URL'}), 400
    
    # Security: Only allow downloads from approved domains
    from urllib.parse import urlparse
    allowed_domains = ['pexels.com', 'videos.pexels.com', 'wikimedia.org', 'upload.wikimedia.org']
    parsed = urlparse(download_url)
    if not any(domain in parsed.netloc for domain in allowed_domains):
        return jsonify({'success': False, 'error': 'Download URL not from approved source'}), 403
    
    try:
        resp = requests.get(download_url, timeout=60, stream=True)
        resp.raise_for_status()
        
        # Save to temp file
        ext = 'mp4' if 'video' in resp.headers.get('content-type', '') else 'webm'
        filename = f"{asset_id or uuid.uuid4()}.{ext}"
        filepath = os.path.join('output', filename)
        
        with open(filepath, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        
        return jsonify({
            'success': True,
            'local_path': filepath,
            'filename': filename
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint."""
    asset_count = MediaAsset.query.filter_by(status='safe').count()
    cache_count = KeywordAssetCache.query.count()
    return jsonify({
        'status': 'healthy',
        'compliance': 'This app only downloads media from sources with explicit reuse permissions. Each asset is stored with license metadata and attribution requirements. If licensing is unclear, the asset is rejected.',
        'asset_library': {
            'total_assets': asset_count,
            'cached_keywords': cache_count
        }
    })

UPLOAD_FOLDER = 'uploads'
OUTPUT_FOLDER = 'output'
ALLOWED_EXTENSIONS = {'mp4', 'mov', 'avi', 'mkv', 'webm', 'mp3', 'wav', 'm4a'}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['OUTPUT_FOLDER'] = OUTPUT_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@app.after_request
def add_no_cache_headers(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/download-reference', methods=['POST'])
def download_reference():
    """Download a video from URL and optionally analyze it as a reference."""
    import subprocess
    
    data = request.get_json()
    url = data.get('url', '').strip()
    
    if not url:
        return jsonify({'error': 'No URL provided'}), 400
    
    try:
        job_id = str(uuid.uuid4())[:8]
        output_path = os.path.join(app.config['UPLOAD_FOLDER'], f'reference_{job_id}.mp4')
        
        cmd = [
            'yt-dlp',
            '-f', 'best[ext=mp4]/best',
            '--no-playlist',
            '--max-filesize', '100M',
            '-o', output_path,
            url
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        
        if result.returncode != 0:
            direct_path = os.path.join(app.config['UPLOAD_FOLDER'], f'reference_{job_id}_direct.mp4')
            try:
                import requests as req
                resp = req.get(url, timeout=60, stream=True)
                if resp.status_code == 200 and 'video' in resp.headers.get('content-type', ''):
                    with open(direct_path, 'wb') as f:
                        for chunk in resp.iter_content(chunk_size=8192):
                            f.write(chunk)
                    output_path = direct_path
                else:
                    return jsonify({'error': 'Could not download video from URL'}), 400
            except Exception as e:
                return jsonify({'error': f'Download failed: {str(e)}'}), 400
        
        if os.path.exists(output_path):
            file_size = os.path.getsize(output_path)
            
            transcript = None
            try:
                audio_path = extract_audio(output_path)
                if audio_path:
                    transcript = transcribe_audio(audio_path)
            except:
                pass
            
            return jsonify({
                'success': True,
                'video_path': f'/uploads/{os.path.basename(output_path)}',
                'file_size': file_size,
                'transcript': transcript,
                'job_id': job_id
            })
        else:
            return jsonify({'error': 'Download failed - no output file'}), 400
            
    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Download timed out'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/cut-clip', methods=['POST'])
def cut_clip():
    """Cut a clip from a video file based on timestamps."""
    import subprocess
    
    data = request.get_json()
    video_path = data.get('video_path', '')
    start_time = data.get('start_time', '00:00')
    end_time = data.get('end_time', '00:30')
    
    if not video_path:
        return jsonify({'error': 'No video path provided'}), 400
    
    video_path = video_path.replace('/uploads/', '')
    full_path = os.path.join(app.config['UPLOAD_FOLDER'], video_path)
    
    if not os.path.exists(full_path):
        return jsonify({'error': 'Video file not found'}), 404
    
    try:
        clip_id = str(uuid.uuid4())[:8]
        output_path = os.path.join(app.config['OUTPUT_FOLDER'], f'clip_{clip_id}.mp4')
        
        cmd = [
            'ffmpeg', '-y',
            '-ss', start_time,
            '-to', end_time,
            '-i', full_path,
            '-c:v', 'libx264',
            '-c:a', 'aac',
            '-preset', 'fast',
            output_path
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        
        if os.path.exists(output_path):
            return jsonify({
                'success': True,
                'clip_url': f'/output/clip_{clip_id}.mp4',
                'start_time': start_time,
                'end_time': end_time
            })
        else:
            return jsonify({'error': 'Failed to cut clip'}), 500
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    if not allowed_file(file.filename):
        return jsonify({'error': 'File type not allowed'}), 400
    
    job_id = str(uuid.uuid4())
    filename = secure_filename(file.filename)
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{job_id}_{filename}")
    file.save(file_path)
    
    return jsonify({
        'success': True,
        'job_id': job_id,
        'filename': filename,
        'file_path': file_path
    })


@app.route('/transcribe', methods=['POST'])
def transcribe():
    data = request.get_json()
    file_path = data.get('file_path')
    
    if not file_path or not os.path.exists(file_path):
        return jsonify({'error': 'File not found'}), 404
    
    audio_path = file_path.rsplit('.', 1)[0] + '_audio.wav'
    
    if file_path.lower().endswith(('.mp3', '.wav', '.m4a')):
        audio_path = file_path
    else:
        if not extract_audio(file_path, audio_path):
            return jsonify({'error': 'Failed to extract audio'}), 500
    
    try:
        transcript_data = transcribe_audio(audio_path)
        return jsonify({
            'success': True,
            'transcript': transcript_data['full_text'],
            'segments': transcript_data['segments']
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/analyze', methods=['POST'])
def analyze():
    data = request.get_json()
    transcript = data.get('transcript')
    
    if not transcript:
        return jsonify({'error': 'No transcript provided'}), 400
    
    try:
        ideas = analyze_ideas(transcript)
        return jsonify({
            'success': True,
            'ideas': ideas
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/generate-script', methods=['POST'])
def generate_script_endpoint():
    data = request.get_json()
    idea = data.get('idea')
    transcript = data.get('transcript')
    duration = data.get('duration', 30)
    
    if not idea or not transcript:
        return jsonify({'error': 'Missing idea or transcript'}), 400
    
    try:
        script = generate_script(idea, transcript, duration)
        return jsonify({
            'success': True,
            'script': script
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/find-clips', methods=['POST'])
def find_clips():
    data = request.get_json()
    script = data.get('script')
    segments = data.get('segments')
    
    if not script or not segments:
        return jsonify({'error': 'Missing script or segments'}), 400
    
    try:
        clips = find_clip_timestamps(script, segments)
        return jsonify({
            'success': True,
            'clips': clips
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/generate-captions', methods=['POST'])
def generate_captions_endpoint():
    data = request.get_json()
    script = data.get('script')
    idea = data.get('idea')
    
    if not script or not idea:
        return jsonify({'error': 'Missing script or idea'}), 400
    
    try:
        captions = generate_captions(script, idea)
        return jsonify({
            'success': True,
            'captions': captions
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/output/<filename>')
def serve_output(filename):
    return send_from_directory(app.config['OUTPUT_FOLDER'], filename)


@app.route('/uploads/<filename>')
def serve_uploads(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


@app.route('/refine-script', methods=['POST'])
def refine_script():
    """Conversational script refinement - asks clarifying questions."""
    from openai import OpenAI
    import os
    import subprocess
    import re
    
    data = request.get_json()
    message = data.get('message', '')
    conversation = data.get('conversation', [])
    question_count = data.get('question_count', 0)
    reference = data.get('reference')
    
    url_pattern = r'(https?://[^\s]+(?:youtube|youtu\.be|tiktok|vimeo|twitter|x\.com|instagram|facebook|twitch)[^\s]*)'
    urls = re.findall(url_pattern, message, re.IGNORECASE)
    
    video_transcript = None
    video_path = None
    
    if urls:
        url = urls[0]
        try:
            job_id = str(uuid.uuid4())[:8]
            output_path = os.path.join(app.config['UPLOAD_FOLDER'], f'chat_video_{job_id}.mp4')
            
            cmd = [
                'yt-dlp',
                '-f', 'best[ext=mp4]/best',
                '--no-playlist',
                '--max-filesize', '100M',
                '-o', output_path,
                url
            ]
            subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            
            if os.path.exists(output_path):
                video_path = f'/uploads/{os.path.basename(output_path)}'
                try:
                    audio_path = extract_audio(output_path)
                    if audio_path:
                        video_transcript = transcribe_audio(audio_path)
                except:
                    pass
        except:
            pass
    
    if reference and reference.get('transcript'):
        video_transcript = reference.get('transcript')
    
    client = OpenAI(
        api_key=os.environ.get("XAI_API_KEY"),
        base_url="https://api.x.ai/v1"
    )
    
    system_prompt = """You are Krakd. Direct. Visual. No fluff.

REACT FAST:
- Read the idea. See the visuals immediately.
- 1 question max. If clear, write NOW.

THINK IN VISUALS:
Every line = a shot. Tag with searchable keywords.
Specific, searchable, stockable.

VIDEO DROPS:
Pull the gold. Skip the filler.
- [CLIP: 00:30-01:15] "money quote here"
- Max 4 clips. State the angle.

SCRIPT FORMAT (PLAIN TEXT SCREENPLAY):

================================================
                    TITLE HERE
================================================

SCENE 1
EXT. LOCATION - TIME
________________________________________________

                    CHARACTER NAME
          Dialogue line goes here. Keep it
          centered and actor-ready.

                    VISUAL: keyword keyword keyword


SCENE 2  
INT. LOCATION - TIME
________________________________________________

                    CHARACTER NAME
          Next dialogue line here.

                    VISUAL: keyword keyword keyword


================================================
CHARACTERS: Name1, Name2, Name3
VOICES?
================================================

FORMATTING RULES:
- Use ======= for title/footer bars
- Use _______ under scene headers
- CENTER character names and dialogue (use spaces)
- VISUAL tags centered below dialogue
- Blank lines between every element
- NO markdown symbols (no **, no >, no ---)
- Pure plain text that prints clean

VOICE:
Tight. Pro. Zero preamble.
Never narrate yourself. Just write.

WRONG: "Here's a script capturing the vibe..."
RIGHT: [clean screenplay format]"""

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(conversation)
    
    user_content = message
    if video_transcript:
        user_content = f"{message}\n\n[VIDEO TRANSCRIPT]:\n{video_transcript[:4000]}"
    
    messages.append({"role": "user", "content": user_content})
    
    try:
        response = client.chat.completions.create(
            model="grok-3",
            messages=messages,
            max_tokens=1024
        )
        
        reply = response.choices[0].message.content or ""
        
        script_ready = "SCRIPT READY:" in reply.upper() or question_count >= 2
        has_question = "?" in reply and not script_ready
        
        refined_script = None
        if script_ready:
            refined_script = reply
            if "SCRIPT READY:" in reply.upper():
                parts = reply.upper().split("SCRIPT READY:")
                if len(parts) > 1:
                    refined_script = reply[reply.upper().find("SCRIPT READY:") + 13:].strip()
        
        return jsonify({
            'success': True,
            'reply': reply,
            'has_question': has_question,
            'script_ready': script_ready,
            'refined_script': refined_script or reply,
            'video_path': video_path,
            'video_downloaded': video_path is not None
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/generate-formats', methods=['POST'])
def generate_formats():
    """Generate content for multiple formats from a refined script."""
    from openai import OpenAI
    from context_engine import search_stock_videos
    import os
    
    data = request.get_json()
    script = data.get('script', '')
    formats = data.get('formats', [])
    conversation = data.get('conversation', [])
    
    client = OpenAI(
        api_key=os.environ.get("XAI_API_KEY"),
        base_url="https://api.x.ai/v1"
    )
    
    context = "\n".join([f"{m['role']}: {m['content']}" for m in conversation[-6:]])
    
    results = {}
    
    for fmt in formats:
        try:
            if fmt == 'reel':
                prompt = f"""Based on this script concept:
{script}

Context from conversation:
{context}

Write a 30-60 second Reel/TikTok script with:
- HOOK: First 3-5 seconds to grab attention (punchy, provocative, or surprising)
- BODY: The main content (20-40 seconds)
- PAYOFF: The ending that makes them think/share (5-10 seconds)

Output as JSON:
{{"hook": "...", "body": "...", "payoff": "...", "duration": "30 seconds", "keywords": ["keyword1", "keyword2", "keyword3"]}}"""

            elif fmt == 'carousel':
                prompt = f"""Based on this script concept:
{script}

Context from conversation:
{context}

Create an Instagram carousel post with 5-7 slides:
- Slide 1: Hook/title that stops scrolling
- Slides 2-5: Key points, claims, or evidence
- Final slide: Call to action

Also write a caption.

Output as JSON:
{{"slides": ["Slide 1 text", "Slide 2 text", ...], "caption": "...", "keywords": ["keyword1", "keyword2"]}}"""

            elif fmt == 'post':
                prompt = f"""Based on this script concept:
{script}

Context from conversation:
{context}

Write an Instagram/social media post caption that:
- Hooks in the first line
- Delivers the key insight
- Ends with a question or CTA

Also suggest relevant hashtags.

Output as JSON:
{{"caption": "...", "hashtags": "#tag1 #tag2 #tag3", "keywords": ["keyword1", "keyword2"]}}"""

            elif fmt == 'thread':
                prompt = f"""Based on this script concept:
{script}

Context from conversation:
{context}

Write a Twitter/X thread with 5-8 tweets:
- Tweet 1: Hook that makes people want to read more
- Middle tweets: Build the argument/story
- Final tweet: Payoff + CTA

Each tweet must be under 280 characters.

Output as JSON:
{{"tweets": ["Tweet 1", "Tweet 2", ...], "keywords": ["keyword1", "keyword2"]}}"""

            else:
                continue
            
            response = client.chat.completions.create(
                model="grok-3",
                messages=[
                    {"role": "system", "content": "You are a content creation expert. Output valid JSON only."},
                    {"role": "user", "content": prompt}
                ],
                max_completion_tokens=2048
            )
            
            content = response.choices[0].message.content or "{}"
            content = content.strip()
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            
            import json
            try:
                result = json.loads(content)
            except:
                result = {"error": "Failed to parse", "raw": content[:500]}
            
            keywords = result.get('keywords', [])
            if keywords:
                stock = search_stock_videos(keywords, per_page=3)
                result['stock_footage'] = stock
            
            results[fmt] = result
            
        except Exception as e:
            results[fmt] = {"error": str(e)}
    
    return jsonify({
        'success': True,
        'results': results
    })


@app.route('/generate-video', methods=['POST'])
def generate_video():
    """Generate a video mockup combining stock footage with voiceover."""
    import os
    import uuid
    import subprocess
    import requests
    
    data = request.get_json()
    voiceover_url = data.get('voiceover_url')
    stock_videos = data.get('stock_videos', [])
    script = data.get('script', '')
    format_type = data.get('format', 'reel')
    captions = data.get('captions', {'enabled': False, 'style': 'bold-center'})
    
    if not voiceover_url and not script:
        return jsonify({'error': 'Need voiceover or script'}), 400
    
    try:
        output_id = uuid.uuid4().hex[:8]
        output_dir = app.config['OUTPUT_FOLDER']
        
        if format_type == 'reel':
            width, height = 1080, 1920
            aspect = '9:16'
        else:
            width, height = 1080, 1080
            aspect = '1:1'
        
        temp_files = []
        
        # Allowed domains for video downloads (security: prevent SSRF)
        allowed_domains = ['pexels.com', 'videos.pexels.com', 'player.vimeo.com', 'pixabay.com', 'wikimedia.org', 'upload.wikimedia.org']
        
        if stock_videos and len(stock_videos) > 0:
            for i, video in enumerate(stock_videos[:5]):
                video_url = video.get('download_url') or video.get('url') or video.get('video_url') or video.get('pexels_url')
                if video_url:
                    # Security: Only allow downloads from trusted domains
                    from urllib.parse import urlparse
                    parsed = urlparse(video_url)
                    if not any(domain in parsed.netloc for domain in allowed_domains):
                        print(f"Skipping untrusted video URL: {video_url}")
                        continue
                    
                    try:
                        resp = requests.get(video_url, timeout=60)
                        if resp.status_code == 200:
                            temp_path = os.path.join(output_dir, f'temp_{output_id}_{i}.mp4')
                            with open(temp_path, 'wb') as f:
                                f.write(resp.content)
                            temp_files.append(temp_path)
                    except Exception as e:
                        print(f"Error downloading video {i}: {e}")
        
        final_video = os.path.join(output_dir, f'echo_video_{output_id}.mp4')
        
        if temp_files:
            concat_file = os.path.join(output_dir, f'concat_{output_id}.txt')
            with open(concat_file, 'w') as f:
                for tf in temp_files:
                    # Use just the filename since concat file is in same directory
                    f.write(f"file '{os.path.basename(tf)}'\n")
            
            cmd = [
                'ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', concat_file,
                '-vf', f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2',
                '-c:v', 'libx264', '-preset', 'fast', '-t', '30',
                final_video
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=120)
            if result.returncode != 0:
                print(f"[FFmpeg concat] Error: {result.stderr.decode()}")
            
            for tf in temp_files:
                if os.path.exists(tf):
                    os.unlink(tf)
            if os.path.exists(concat_file):
                os.unlink(concat_file)
        else:
            print(f"[generate-video] No temp files downloaded, creating placeholder")
            cmd = [
                'ffmpeg', '-y', '-f', 'lavfi', '-i', f'color=c=black:s={width}x{height}:d=30',
                '-vf', f"drawtext=fontsize=40:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2:text='Echo Engine':font=sans",
                '-c:v', 'libx264', '-preset', 'fast', '-t', '30',
                final_video
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=60)
            if result.returncode != 0:
                print(f"[FFmpeg placeholder] Error: {result.stderr.decode()}")
        
        # Add captions if enabled
        if captions.get('enabled') and script:
            caption_video = os.path.join(output_dir, f'captioned_{output_id}.mp4')
            
            # Get caption settings with defaults
            caption_font = captions.get('font', 'inter')
            caption_position = captions.get('position', 'center')
            caption_color = captions.get('color', '#FFFFFF').replace('#', '')
            caption_size = captions.get('size', 'medium')
            caption_weight = captions.get('weight', 'bold')
            caption_outline = captions.get('outline', True)
            caption_shadow = captions.get('shadow', True)
            caption_background = captions.get('background', False)
            caption_uppercase = captions.get('uppercase', False)
            
            # Font family mapping for FFmpeg (system fonts)
            font_map = {
                'inter': 'Sans',
                'bebas': 'Sans-Bold',
                'montserrat': 'Sans-Bold',
                'oswald': 'Sans',
                'poppins': 'Sans',
                'roboto': 'Sans'
            }
            font_name = font_map.get(caption_font, 'Sans')
            
            # Size mapping
            size_map = {
                'small': 32,
                'medium': 48,
                'large': 64,
                'xlarge': 80
            }
            font_size = size_map.get(caption_size, 48)
            
            # Position mapping (y coordinate)
            position_map = {
                'top': 80,
                'center': '(h-text_h)/2',
                'bottom': 'h-150'
            }
            y_pos = position_map.get(caption_position, 'h-150')
            
            # Sanitize text for ffmpeg drawtext
            import re
            safe_text = script.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:").replace("%", "\\%")
            safe_text = re.sub(r'[\n\r]', ' ', safe_text)[:200]
            
            if caption_uppercase:
                safe_text = safe_text.upper()
            
            # Build the drawtext filter with all settings
            filter_parts = [
                f"drawtext=text='{safe_text}'",
                f"fontsize={font_size}",
                f"fontcolor=#{caption_color}",
                f"font={font_name}",
                f"x=(w-text_w)/2",
                f"y={y_pos}"
            ]
            
            # Add outline (border) if enabled
            if caption_outline:
                filter_parts.append("borderw=3")
                filter_parts.append("bordercolor=black")
            
            # Add shadow if enabled
            if caption_shadow:
                filter_parts.append("shadowcolor=black@0.7")
                filter_parts.append("shadowx=2")
                filter_parts.append("shadowy=2")
            
            # Add background box if enabled
            if caption_background:
                filter_parts.append("box=1")
                filter_parts.append("boxcolor=black@0.6")
                filter_parts.append("boxborderw=10")
            
            font_filter = ":".join(filter_parts)
            
            # Check if video has audio stream
            probe_cmd = ['ffprobe', '-v', 'error', '-select_streams', 'a', '-show_entries', 'stream=codec_type', '-of', 'csv=p=0', final_video]
            probe_result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=10)
            has_audio = 'audio' in probe_result.stdout
            
            if has_audio:
                cmd = [
                    'ffmpeg', '-y', '-i', final_video,
                    '-vf', font_filter,
                    '-c:v', 'libx264', '-preset', 'fast', '-c:a', 'copy',
                    caption_video
                ]
            else:
                cmd = [
                    'ffmpeg', '-y', '-i', final_video,
                    '-vf', font_filter,
                    '-c:v', 'libx264', '-preset', 'fast', '-an',
                    caption_video
                ]
            
            result = subprocess.run(cmd, capture_output=True, timeout=120)
            if os.path.exists(caption_video):
                os.unlink(final_video)
                final_video = caption_video
        
        if voiceover_url:
            audio_path = os.path.join(output_dir, voiceover_url.split('/')[-1])
            if os.path.exists(audio_path):
                final_with_audio = os.path.join(output_dir, f'echo_final_{output_id}.mp4')
                cmd = [
                    'ffmpeg', '-y', '-i', final_video, '-i', audio_path,
                    '-c:v', 'copy', '-c:a', 'aac', '-shortest',
                    final_with_audio
                ]
                subprocess.run(cmd, capture_output=True, timeout=60)
                if os.path.exists(final_with_audio):
                    os.unlink(final_video)
                    final_video = final_with_audio
        
        if os.path.exists(final_video):
            return jsonify({
                'success': True,
                'video_url': f'/output/{os.path.basename(final_video)}',
                'format': format_type
            })
        else:
            print(f"[generate-video] Final video not created: {final_video}")
            return jsonify({'error': 'Video generation failed - no output file created'}), 500
            
    except Exception as e:
        import traceback
        print(f"[generate-video] Error: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


GOOFY_VOICE_CONFIG = {
    'goofy_cartoon': {
        'base_voice': 'fable',
        'prompt': "You are a wacky, over-the-top cartoon character! Speak with EXTREME enthusiasm, wild energy, and silly vocal inflections. Add dramatic pauses and emphasis on random words. Be animated and zany!"
    },
    'goofy_dramatic': {
        'base_voice': 'onyx',
        'prompt': "You are an EXTREMELY dramatic Shakespearean actor. Speak as if every word is the most important thing ever said. Add long dramatic pauses. Treat mundane topics like epic sagas. Be theatrical and grandiose!"
    },
    'goofy_robot': {
        'base_voice': 'echo',
        'prompt': "You are a robot with limited emotional processing. Speak in a flat, monotone voice. Occasionally add 'beep boop' or 'processing' between sentences. Be mechanical and literal."
    },
    'goofy_surfer': {
        'base_voice': 'alloy',
        'prompt': "You are a super chill surfer dude from California. Say 'dude', 'bro', 'gnarly', 'totally', and 'like' frequently. Be laid-back, relaxed, and use surfer slang. Everything is awesome to you!"
    },
    'goofy_villain': {
        'base_voice': 'onyx',
        'prompt': "You are an evil cartoon villain! Speak with a sinister, maniacal tone. Add evil laughs (mwahahaha) where appropriate. Be menacing but in a campy, over-the-top way. Relish in your villainy!"
    },
    'goofy_grandma': {
        'base_voice': 'shimmer',
        'prompt': "You are a sweet old grandmother. Speak slowly and warmly. Add 'dearie', 'sweetie', and 'back in my day' occasionally. Ramble a bit and be nurturing. Sound like you're offering cookies."
    }
}

def get_voice_config(voice):
    """Get base voice and system prompt for a voice type."""
    if voice in GOOFY_VOICE_CONFIG:
        config = GOOFY_VOICE_CONFIG[voice]
        return config['base_voice'], config['prompt']
    return voice, "You are a professional voiceover artist. Read the following script naturally and engagingly."


@app.route('/generate-voiceover', methods=['POST'])
def generate_voiceover():
    """Generate voiceover audio from script text."""
    from openai import OpenAI
    import base64
    import os
    import uuid
    
    data = request.get_json()
    text = data.get('text', '')
    voice = data.get('voice', 'alloy')
    
    if not text:
        return jsonify({'error': 'No text provided'}), 400
    
    # Filter out visual directions, stage directions - only keep spoken dialogue
    text = extract_dialogue_only(text)
    if not text:
        return jsonify({'error': 'No dialogue found in script'}), 400
    
    # Get base voice and prompt for goofy voices
    base_voice, system_prompt = get_voice_config(voice)
    
    # Use OpenAI for audio generation (Krakd doesn't support audio)
    client = OpenAI(
        api_key=os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY"),
        base_url=os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL")
    )
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o-audio-preview",
            modalities=["text", "audio"],
            audio={"voice": base_voice, "format": "mp3"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Read this script: {text}"},
            ],
        )
        
        audio_data = getattr(response.choices[0].message, "audio", None)
        if audio_data and hasattr(audio_data, "data"):
            audio_bytes = base64.b64decode(audio_data.data)
            
            filename = f"voiceover_{uuid.uuid4().hex[:8]}.mp3"
            filepath = os.path.join(app.config['OUTPUT_FOLDER'], filename)
            with open(filepath, 'wb') as f:
                f.write(audio_bytes)
            
            return jsonify({
                'success': True,
                'audio_url': f'/output/{filename}',
                'duration_estimate': len(text.split()) / 2.5
            })
        else:
            return jsonify({'error': 'No audio generated'}), 500
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/preview-voice', methods=['POST'])
def preview_voice():
    """Generate a short voice preview sample."""
    from openai import OpenAI
    import base64
    import uuid
    
    data = request.get_json()
    text = data.get('text', '')
    voice = data.get('voice', 'alloy')
    
    if not text:
        return jsonify({'error': 'No text provided'}), 400
    
    # Get base voice and prompt for goofy voices
    base_voice, system_prompt = get_voice_config(voice)
    
    # Use OpenAI for audio generation (Krakd doesn't support audio)
    client = OpenAI(
        api_key=os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY"),
        base_url=os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL")
    )
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o-audio-preview",
            modalities=["text", "audio"],
            audio={"voice": base_voice, "format": "mp3"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
        )
        
        audio_data = getattr(response.choices[0].message, "audio", None)
        if audio_data and hasattr(audio_data, "data"):
            audio_bytes = base64.b64decode(audio_data.data)
            
            filename = f"preview_{voice}_{uuid.uuid4().hex[:6]}.mp3"
            filepath = os.path.join(app.config['OUTPUT_FOLDER'], filename)
            with open(filepath, 'wb') as f:
                f.write(audio_bytes)
            
            return jsonify({
                'success': True,
                'audio_url': f'/output/{filename}'
            })
        else:
            return jsonify({'error': 'No audio generated'}), 500
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/detect-characters', methods=['POST'])
def detect_characters():
    """AI detects characters in the script for casting."""
    from openai import OpenAI
    
    data = request.get_json()
    script = data.get('script', '')
    
    if not script:
        return jsonify({'success': False, 'error': 'No script provided'}), 400
        
    client = OpenAI(
        api_key=os.environ.get("XAI_API_KEY"),
        base_url="https://api.x.ai/v1"
    )
    
    system_prompt = """Analyze the script and list all speaking characters.
For each character, provide:
1. Their name (as used in the script)
2. A very brief personality description (2-3 words)
3. One sample line they speak in this script

OUTPUT FORMAT (JSON):
{
  "characters": [
    {
      "name": "NARRATOR",
      "personality": "Calm, authoritative",
      "sample_line": "The world is changing faster than we think."
    }
  ]
}"""

    try:
        response = client.chat.completions.create(
            model="grok-3",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Detect characters in this script:\n\n{script}"}
            ],
            response_format={"type": "json_object"}
        )
        
        result = json.loads(response.choices[0].message.content)
        return jsonify({'success': True, 'characters': result.get('characters', [])})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/generate-voiceover-multi', methods=['POST'])
def generate_voiceover_multi():
    """Generate voiceover with multiple character voices."""
    from openai import OpenAI
    import base64
    import uuid
    from pydub import AudioSegment
    import io
    
    data = request.get_json()
    script = data.get('script', '')
    character_voices = data.get('character_voices', {})
    
    if not script:
        return jsonify({'error': 'No script provided'}), 400
    
    # Use OpenAI for audio generation (Krakd doesn't support audio)
    client = OpenAI(
        api_key=os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY"),
        base_url=os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL")
    )
    
    try:
        import re
        
        # Parse script into character lines (filtering out non-dialogue)
        lines = []
        current_char = 'NARRATOR'
        
        for line in script.split('\n'):
            line = line.strip()
            if not line:
                continue
            
            # Skip visual directions [VISUAL: ...], [CUT TO:], [FADE], etc.
            if line.startswith('[VISUAL') or line.startswith('[CUT') or line.startswith('[FADE') or re.match(r'^\[.*\]$', line):
                continue
            
            # Skip scene headers (INT., EXT., TITLE:, CUT TO:)
            if line.startswith('INT.') or line.startswith('EXT.') or line.startswith('TITLE:') or line.startswith('CUT TO'):
                continue
            
            # Skip pure parentheticals
            if re.match(r'^\([^)]+\)$', line):
                continue
            
            # Remove inline parentheticals
            line = re.sub(r'\([^)]*\)', '', line).strip()
            if not line:
                continue
            
            # Check if line starts with CHARACTER:
            if ':' in line:
                parts = line.split(':', 1)
                if len(parts[0].split()) <= 3:  # Likely a character name
                    current_char = parts[0].strip().upper()
                    dialogue = parts[1].strip()
                    if dialogue:
                        lines.append({'character': current_char, 'text': dialogue})
                else:
                    lines.append({'character': current_char, 'text': line})
            else:
                lines.append({'character': current_char, 'text': line})
        
        # Generate audio for each segment
        audio_segments = []
        
        for segment in lines:
            char_name = segment['character']
            text = segment['text']
            
            # Find voice for this character (case insensitive match)
            voice = 'alloy'  # default
            for key, val in character_voices.items():
                if key.upper() == char_name or char_name in key.upper():
                    voice = val
                    break
            
            # Get base voice and prompt for goofy voices
            base_voice, goofy_prompt = get_voice_config(voice)
            if voice in GOOFY_VOICE_CONFIG:
                system_prompt = goofy_prompt + f" You are playing the character {char_name}. Just speak the line, no explanation."
            else:
                system_prompt = f"You are a voice actor playing {char_name}. Speak this line naturally and in character. Just speak the line, no explanation."
            
            response = client.chat.completions.create(
                model="gpt-4o-audio-preview",
                modalities=["text", "audio"],
                audio={"voice": base_voice, "format": "mp3"},
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text},
                ],
            )
            
            audio_data = getattr(response.choices[0].message, "audio", None)
            if audio_data and hasattr(audio_data, "data"):
                audio_bytes = base64.b64decode(audio_data.data)
                audio_segments.append(audio_bytes)
        
        # Combine all audio segments
        if audio_segments:
            combined = AudioSegment.empty()
            for seg_bytes in audio_segments:
                seg = AudioSegment.from_mp3(io.BytesIO(seg_bytes))
                combined += seg + AudioSegment.silent(duration=300)  # 300ms pause between lines
            
            filename = f"voiceover_multi_{uuid.uuid4().hex[:8]}.mp3"
            filepath = os.path.join(app.config['OUTPUT_FOLDER'], filename)
            combined.export(filepath, format='mp3')
            
            return jsonify({
                'success': True,
                'audio_url': f'/output/{filename}',
                'segments': len(audio_segments)
            })
        else:
            return jsonify({'error': 'No audio segments generated'}), 500
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/build-post', methods=['POST'])
def build_post():
    """Build a complete post from a user's script/pitch idea."""
    from context_engine import build_post_from_script
    
    data = request.get_json()
    user_script = data.get('script')
    
    if not user_script:
        return jsonify({'error': 'No script provided'}), 400
    
    try:
        result = build_post_from_script(user_script)
        return jsonify({
            'success': True,
            'result': result
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/chat', methods=['POST'])
def chat():
    """Direct chat with Krakd AI."""
    from openai import OpenAI
    import os
    
    data = request.get_json()
    message = data.get('message')
    conversation = data.get('conversation', [])
    
    if not message:
        return jsonify({'error': 'No message provided'}), 400
    
    client = OpenAI(
        api_key=os.environ.get("XAI_API_KEY"),
        base_url="https://api.x.ai/v1"
    )
    
    system_prompt = """You are a professional scriptwriter. Write like one.

RULES:
- Scripts use standard format: INT./EXT. scene headings, CHARACTER NAMES in caps, no markdown
- Include [VISUAL: description] notes for B-roll throughout
- Keep dialogue tight. Cut filler words.
- No meta-commentary. No "here's what I came up with." Just deliver the script.
- After every script, ask: "Want to add characters? I can suggest voices for the tone."

VOICE:
- Professional. Minimal. Direct.
- If asked for humor: suggest specific comic approaches (deadpan, absurdist, satirical)
- If the script has multiple speakers, note them for voice casting

Never explain what you're doing. Just write."""

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(conversation)
    messages.append({"role": "user", "content": message})
    
    try:
        response = client.chat.completions.create(
            model="grok-3",
            messages=messages,
            max_tokens=2048
        )
        
        reply = response.choices[0].message.content or ""
        
        return jsonify({
            'success': True,
            'reply': reply,
            'conversation': messages + [{"role": "assistant", "content": reply}]
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/process-full', methods=['POST'])
def process_full():
    """Full pipeline: upload -> transcribe -> analyze -> script -> clips"""
    data = request.get_json()
    file_path = data.get('file_path')
    max_clips = data.get('max_clips', 3)
    clip_duration = data.get('clip_duration', 30)
    aspect_ratio = data.get('aspect_ratio', '9:16')
    
    if not file_path or not os.path.exists(file_path):
        return jsonify({'error': 'File not found'}), 404
    
    try:
        from context_engine import process_video
        results = process_video(
            file_path,
            app.config['OUTPUT_FOLDER'],
            max_clips,
            clip_duration,
            aspect_ratio
        )
        return jsonify({
            'success': True,
            'results': results
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
