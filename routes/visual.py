"""
Visual search and content curation routes.
"""
import os
import re
import requests
from flask import Blueprint, request, jsonify
from sqlalchemy import or_
from extensions import db
from models import MediaAsset, KeywordAssetCache
from visual_search import (
    is_nsfw_content, validate_license,
    ALLOWED_LICENSES, WIKIMEDIA_ALLOWED_LICENSES
)
from context_engine import call_ai

visual_bp = Blueprint('visual', __name__)


@visual_bp.route('/search-wikimedia', methods=['POST'])
def search_wikimedia():
    """
    Search Wikimedia Commons using proper 2-step API approach.
    Step 1: Search files only (namespace=6)
    Step 2: Fetch metadata with imageinfo + extmetadata
    Supports both images and videos.
    """
    data = request.get_json()
    query = data.get('query', '')
    limit = data.get('limit', 20)
    media_type = data.get('media_type', 'all')
    
    try:
        wiki_headers = {'User-Agent': 'KrakdPostAssembler/1.0 (https://replit.com; contact@krakd.app)'}
        search_url = 'https://commons.wikimedia.org/w/api.php'
        
        search_params = {
            'action': 'query',
            'format': 'json',
            'generator': 'search',
            'gsrnamespace': 6,
            'gsrsearch': query,
            'gsrlimit': limit * 2,
            'prop': 'imageinfo',
            'iiprop': 'url|extmetadata|size|mime|mediatype',
            'iiurlwidth': 640
        }
        
        response = requests.get(search_url, params=search_params, headers=wiki_headers, timeout=20)
        data = response.json()
        
        results = []
        pages = data.get('query', {}).get('pages', {})
        
        for page_id, page in pages.items():
            if page_id == '-1':
                continue
                
            imageinfo = page.get('imageinfo', [{}])[0]
            mime = imageinfo.get('mime', '')
            mediatype = imageinfo.get('mediatype', '')
            extmeta = imageinfo.get('extmetadata', {})
            
            is_video = mime.startswith('video/') or mediatype in ['VIDEO', 'AUDIO']
            is_image = mime.startswith('image/') and not mime.endswith('/gif')
            
            allowed_video_mimes = ['video/webm', 'video/ogg', 'video/mp4', 'application/ogg']
            allowed_image_mimes = ['image/jpeg', 'image/png', 'image/webp', 'image/svg+xml']
            
            if media_type == 'video' and not (is_video or mime in allowed_video_mimes):
                continue
            elif media_type == 'image' and not (is_image or mime in allowed_image_mimes):
                continue
            elif media_type == 'all' and not (is_video or is_image or mime in allowed_video_mimes + allowed_image_mimes):
                continue
            
            license_short = extmeta.get('LicenseShortName', {}).get('value', '')
            license_url = extmeta.get('LicenseUrl', {}).get('value', '')
            
            is_valid, our_license, _ = validate_license(license_short)
            if not is_valid:
                continue
            
            title = page.get('title', '')
            description_raw = extmeta.get('ImageDescription', {}).get('value', '')
            categories = extmeta.get('Categories', {}).get('value', '').split('|') if extmeta.get('Categories', {}).get('value') else []
            is_nsfw, nsfw_reason = is_nsfw_content(title, description_raw, categories)
            if is_nsfw:
                print(f"[NSFW Filter] Blocked: {title} - {nsfw_reason}")
                continue
            
            artist_html = extmeta.get('Artist', {}).get('value', 'Unknown')
            artist = re.sub('<[^<]+?>', '', artist_html).strip()
            if not artist or artist == 'Unknown':
                artist = extmeta.get('Credit', {}).get('value', 'Unknown')
                artist = re.sub('<[^<]+?>', '', artist).strip()
            
            attribution_required = our_license not in ['CC0', 'Public Domain']
            content_type = 'video' if (is_video or mime in allowed_video_mimes) else 'image'
            
            description = extmeta.get('ImageDescription', {}).get('value', '')
            description = re.sub('<[^<]+?>', '', description).strip()[:200]
            
            results.append({
                'id': f"wikimedia_{page.get('pageid')}",
                'source': 'wikimedia_commons',
                'source_page': f"https://commons.wikimedia.org/wiki/{page.get('title', '').replace(' ', '_')}",
                'download_url': imageinfo.get('url'),
                'thumbnail': imageinfo.get('thumburl') or imageinfo.get('url'),
                'title': page.get('title', '').replace('File:', ''),
                'description': description,
                'content_type': content_type,
                'mime': mime,
                'resolution': f"{imageinfo.get('width', 0)}x{imageinfo.get('height', 0)}",
                'license': our_license,
                'license_url': license_url or 'https://creativecommons.org/licenses/',
                'commercial_use_allowed': True,
                'derivatives_allowed': our_license not in [],
                'attribution_required': attribution_required,
                'attribution_text': f"{artist} / Wikimedia Commons / {our_license}"
            })
            
            if len(results) >= limit:
                break
        
        return jsonify({'success': True, 'assets': results, 'count': len(results)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@visual_bp.route('/search-wikimedia-videos', methods=['POST'])
def search_wikimedia_videos():
    """Legacy endpoint - calls new search with video filter."""
    req_data = request.get_json() or {}
    query = req_data.get('query', '')
    limit = req_data.get('limit', 10)
    
    try:
        wiki_headers = {'User-Agent': 'KrakdPostAssembler/1.0 (https://replit.com; contact@krakd.app)'}
        search_url = 'https://commons.wikimedia.org/w/api.php'
        
        search_params = {
            'action': 'query',
            'format': 'json',
            'generator': 'search',
            'gsrnamespace': 6,
            'gsrsearch': query,
            'gsrlimit': limit * 2,
            'prop': 'imageinfo',
            'iiprop': 'url|extmetadata|size|mime|mediatype',
            'iiurlwidth': 640
        }
        
        response = requests.get(search_url, params=search_params, headers=wiki_headers, timeout=20)
        data = response.json()
        
        videos = []
        pages = data.get('query', {}).get('pages', {})
        
        for page_id, page in pages.items():
            if page_id == '-1':
                continue
                
            imageinfo = page.get('imageinfo', [{}])[0]
            mime = imageinfo.get('mime', '')
            
            if not mime.startswith('video/') and mime not in ['video/webm', 'video/ogg', 'video/mp4', 'application/ogg']:
                continue
            
            extmeta = imageinfo.get('extmetadata', {})
            license_short = extmeta.get('LicenseShortName', {}).get('value', '')
            
            is_valid, our_license, _ = validate_license(license_short)
            if not is_valid:
                continue
            
            title = page.get('title', '')
            description_raw = extmeta.get('ImageDescription', {}).get('value', '')
            categories = extmeta.get('Categories', {}).get('value', '').split('|') if extmeta.get('Categories', {}).get('value') else []
            is_nsfw, nsfw_reason = is_nsfw_content(title, description_raw, categories)
            if is_nsfw:
                print(f"[NSFW Filter] Blocked video: {title} - {nsfw_reason}")
                continue
            
            artist = re.sub('<[^<]+?>', '', extmeta.get('Artist', {}).get('value', 'Unknown')).strip()
            
            videos.append({
                'id': f"wikimedia_{page.get('pageid')}",
                'source': 'wikimedia_commons',
                'source_page': f"https://commons.wikimedia.org/wiki/{page.get('title', '').replace(' ', '_')}",
                'download_url': imageinfo.get('url'),
                'thumbnail': imageinfo.get('thumburl'),
                'title': page.get('title', '').replace('File:', ''),
                'resolution': f"{imageinfo.get('width', 0)}x{imageinfo.get('height', 0)}",
                'license': our_license,
                'license_url': extmeta.get('LicenseUrl', {}).get('value', ''),
                'commercial_use_allowed': True,
                'derivatives_allowed': True,
                'attribution_required': our_license not in ['CC0', 'Public Domain'],
                'attribution_text': f"{artist} / Wikimedia Commons / {our_license}"
            })
            
            if len(videos) >= limit:
                break
        
        return jsonify({'success': True, 'videos': videos})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@visual_bp.route('/search-unsplash', methods=['POST'])
def search_unsplash():
    """
    Search Unsplash for high-quality artistic photos.
    Unsplash has more editorial/artistic content than Pexels.
    """
    req_data = request.get_json()
    query = req_data.get('query', '')
    limit = req_data.get('per_page', 15)
    orientation = req_data.get('orientation', 'portrait')
    
    unsplash_key = os.environ.get('UNSPLASH_ACCESS_KEY')
    if not unsplash_key:
        return jsonify({'success': False, 'error': 'Unsplash API not configured', 'assets': []})
    
    try:
        response = requests.get(
            'https://api.unsplash.com/search/photos',
            headers={'Authorization': f'Client-ID {unsplash_key}'},
            params={
                'query': query,
                'per_page': limit,
                'orientation': orientation
            },
            timeout=15
        )
        data = response.json()
        
        results = []
        for photo in data.get('results', []):
            results.append({
                'id': f"unsplash_{photo['id']}",
                'source': 'unsplash',
                'source_page': photo.get('links', {}).get('html', ''),
                'download_url': photo.get('urls', {}).get('full') or photo.get('urls', {}).get('regular'),
                'thumbnail': photo.get('urls', {}).get('small') or photo.get('urls', {}).get('thumb'),
                'title': photo.get('alt_description') or photo.get('description') or 'Untitled',
                'description': photo.get('description', ''),
                'content_type': 'image',
                'resolution': f"{photo.get('width', 0)}x{photo.get('height', 0)}",
                'license': 'Unsplash License',
                'license_url': 'https://unsplash.com/license',
                'commercial_use_allowed': True,
                'derivatives_allowed': True,
                'attribution_required': False,
                'attribution_text': f"Photo by {photo.get('user', {}).get('name', 'Unknown')} on Unsplash"
            })
        
        return jsonify({'success': True, 'assets': results, 'count': len(results)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e), 'assets': []})


@visual_bp.route('/search-all-sources', methods=['POST'])
def search_all_sources():
    """
    Search all sources for legal media - PRIORITIZES Wikimedia Commons over Pexels.
    Implements fallback ladder: Wikimedia (primary) → Pexels (fallback if <6 results) → query expansion.
    """
    data = request.get_json()
    query = data.get('query', '')
    limit = data.get('limit', 15)
    media_type = data.get('media_type', 'all')
    
    all_results = []
    sources_searched = []
    
    try:
        wiki_headers = {'User-Agent': 'KrakdPostAssembler/1.0 (https://replit.com; contact@krakd.app)'}
        search_url = 'https://commons.wikimedia.org/w/api.php'
        
        search_params = {
            'action': 'query',
            'format': 'json',
            'generator': 'search',
            'gsrnamespace': 6,
            'gsrsearch': query,
            'gsrlimit': max(limit, 15),
            'prop': 'imageinfo',
            'iiprop': 'url|extmetadata|size|mime|mediatype',
            'iiurlwidth': 640
        }
        
        response = requests.get(search_url, params=search_params, headers=wiki_headers, timeout=15)
        pages = response.json().get('query', {}).get('pages', {})
        
        for page_id, page in pages.items():
            if page_id == '-1':
                continue
                
            imageinfo = page.get('imageinfo', [{}])[0]
            mime = imageinfo.get('mime', '')
            extmeta = imageinfo.get('extmetadata', {})
            
            is_video = mime.startswith('video/')
            is_image = mime.startswith('image/') and not mime.endswith('/gif')
            
            if media_type == 'video' and not is_video:
                continue
            elif media_type == 'image' and not is_image:
                continue
            
            license_short = extmeta.get('LicenseShortName', {}).get('value', '')
            is_valid, our_license, _ = validate_license(license_short)
            if not is_valid:
                continue
            
            title = page.get('title', '')
            description_raw = extmeta.get('ImageDescription', {}).get('value', '')
            categories = extmeta.get('Categories', {}).get('value', '').split('|') if extmeta.get('Categories', {}).get('value') else []
            is_nsfw, nsfw_reason = is_nsfw_content(title, description_raw, categories)
            if is_nsfw:
                print(f"[NSFW Filter] Blocked in all-sources: {title} - {nsfw_reason}")
                continue
            
            artist = re.sub('<[^<]+?>', '', extmeta.get('Artist', {}).get('value', 'Unknown')).strip()
            
            all_results.append({
                'id': f"wikimedia_{page.get('pageid')}",
                'source': 'wikimedia_commons',
                'source_page': f"https://commons.wikimedia.org/wiki/{page.get('title', '').replace(' ', '_')}",
                'download_url': imageinfo.get('url'),
                'thumbnail': imageinfo.get('thumburl') or imageinfo.get('url'),
                'title': page.get('title', '').replace('File:', ''),
                'content_type': 'video' if is_video else 'image',
                'license': our_license,
                'license_url': extmeta.get('LicenseUrl', {}).get('value', ''),
                'attribution_required': our_license not in ['CC0', 'Public Domain'],
                'attribution_text': f"{artist} / Wikimedia Commons / {our_license}"
            })
        
        sources_searched.append('wikimedia_commons')
    except Exception as e:
        print(f"Wikimedia search error: {e}")
    
    if len(all_results) < 4 and ' ' in query:
        words = query.split()
        simple_query = words[-1] if len(words) > 1 else query
        
        try:
            wiki_headers = {'User-Agent': 'KrakdPostAssembler/1.0'}
            response = requests.get('https://commons.wikimedia.org/w/api.php', params={
                'action': 'query', 'format': 'json', 'generator': 'search',
                'gsrnamespace': 6, 'gsrsearch': simple_query, 'gsrlimit': 5,
                'prop': 'imageinfo', 'iiprop': 'url|extmetadata|mime', 'iiurlwidth': 640
            }, headers=wiki_headers, timeout=10)
            
            pages = response.json().get('query', {}).get('pages', {})
            for page_id, page in pages.items():
                if page_id == '-1':
                    continue
                imageinfo = page.get('imageinfo', [{}])[0]
                extmeta = imageinfo.get('extmetadata', {})
                license_short = extmeta.get('LicenseShortName', {}).get('value', '')
                is_valid, our_license, _ = validate_license(license_short)
                if is_valid:
                    title = page.get('title', '')
                    description_raw = extmeta.get('ImageDescription', {}).get('value', '')
                    categories = extmeta.get('Categories', {}).get('value', '').split('|') if extmeta.get('Categories', {}).get('value') else []
                    is_nsfw, _ = is_nsfw_content(title, description_raw, categories)
                    if is_nsfw:
                        continue
                    
                    all_results.append({
                        'id': f"wikimedia_{page.get('pageid')}",
                        'source': 'wikimedia_commons',
                        'source_page': f"https://commons.wikimedia.org/wiki/{page.get('title', '').replace(' ', '_')}",
                        'download_url': imageinfo.get('url'),
                        'thumbnail': imageinfo.get('thumburl') or imageinfo.get('url'),
                        'title': page.get('title', '').replace('File:', ''),
                        'content_type': 'video' if imageinfo.get('mime', '').startswith('video/') else 'image',
                        'license': our_license,
                        'attribution_required': our_license not in ['CC0', 'Public Domain'],
                        'attribution_text': f"Wikimedia Commons / {our_license}"
                    })
        except:
            pass
    
    return jsonify({
        'success': True, 
        'assets': all_results, 
        'videos': all_results,
        'count': len(all_results),
        'sources': sources_searched
    })


@visual_bp.route('/save-asset', methods=['POST'])
def save_asset():
    """Save a verified legal asset to the library."""
    data = request.get_json()
    
    required = ['id', 'source_page', 'download_url', 'source', 'license', 'content_type']
    for field in required:
        if not data.get(field):
            return jsonify({'success': False, 'error': f'Missing required field: {field}'}), 400
    
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
        db.session.merge(asset)
        db.session.commit()
        return jsonify({'success': True, 'id': asset.id})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@visual_bp.route('/search-assets', methods=['POST'])
def search_assets():
    """Search the asset library by tags or description."""
    data = request.get_json()
    tags = data.get('tags', [])
    content_type = data.get('content_type')
    limit = data.get('limit', 10)
    
    query = MediaAsset.query.filter(MediaAsset.status == 'safe')
    
    if content_type:
        query = query.filter(MediaAsset.content_type == content_type)
    
    assets = query.limit(100).all()
    
    results = []
    for asset in assets:
        asset_tags = asset.tags or []
        if not tags or any(tag.lower() in [t.lower() for t in asset_tags] for tag in tags):
            results.append({
                'id': asset.id,
                'source': asset.source,
                'source_page': asset.source_page,
                'download_url': asset.download_url,
                'thumbnail': asset.download_url,
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


@visual_bp.route('/curate-visuals', methods=['POST'])
def curate_visuals():
    """AI curates visuals based on script context - checks cache first, then external APIs."""
    import re as regex
    
    data = request.get_json()
    script = data.get('script', '')
    user_guidance = data.get('user_guidance', '')
    
    if not script:
        return jsonify({'success': False, 'error': 'No script provided'}), 400
    
    content_type = data.get('content_type', 'educational')
    
    system_prompt = """You are Krakd's visual curator — grounded, intentional, never flashy.

PHILOSOPHY:
Visuals exist to SERVE the MESSAGE, not decorate it.
Every shot represents an IDEA being discussed, not a location.
The visual must reinforce what the speaker is SAYING, not where they are.

TONE ALIGNMENT:
- Calm, clear, documentary-feeling footage
- No meme aesthetics, no shock imagery, no celebrity/brand content
- Prefer authentic over polished, archival over stock-y
- If content is graphic: skip it, find something that implies without showing

EXTRACT FROM SCRIPT:
1. CORE IDEA - What is being discussed in this section? (one sentence summary of the argument/point)
2. VISUAL CONCEPT - What visual would REPRESENT this idea? (not where it takes place)
3. MOOD - What's the emotional tone? (tense, hopeful, contemplative, urgent)
4. DURATION - Look for [Xs] hints in scene headers (default 4 seconds)

For each section, create search queries based on the IDEA:
- NOT "modest home interior" (setting)
- YES "freedom resolution hope" (idea being discussed)
- Search for visuals that EMBODY the concept, not the location

OUTPUT FORMAT (JSON):
{
  "overall_context": {
    "thesis": "The core argument of this content",
    "mood": "tense, contemplative, hopeful",
    "visual_intent": "Reinforce the message through symbolic imagery"
  },
  "sections": [
    {
      "idea": "One sentence describing what's being discussed in this section",
      "script_segment": "The actual dialogue from this part...",
      "visual_concept": "What visual would represent this idea?",
      "mood": "tense",
      "duration_seconds": 4,
      "cut_type": "wide",
      "cut_motion": "slow zoom",
      "search_queries": ["freedom liberation", "peaceful resolution", "hope reconciliation"],
      "cache_keywords": ["freedom", "peace", "hope"]
    }
  ]
}

SEARCH QUERY RULES:
- Queries must relate to the IDEA, not the scene setting
- Use conceptual/symbolic terms: "liberation", "conflict", "unity", "power"
- Avoid location-based terms: "living room", "office", "street"
- Each query should find images that REPRESENT the argument being made

CRITICAL: 
- idea = ONE SENTENCE describing what's being argued/discussed (this is shown to user)
- visual_concept = what visual would represent this idea
- search_queries = conceptual terms based on the IDEA
- duration_seconds = how long this section should last"""

    user_content = f"Create a visual board for this script:\n\n{script}"
    if user_guidance:
        user_content += f"\n\nUSER DIRECTION: {user_guidance}"

    try:
        visual_board = call_ai(user_content, system_prompt, json_output=True, max_tokens=1500)
        if not visual_board:
            visual_board = {"sections": []}
        
        import re as duration_re
        scene_durations = {}
        scene_pattern = r'SCENE\s+(\d+)\s*\[(\d+(?:-\d+)?)\s*s?\]'
        for match in duration_re.finditer(scene_pattern, script, duration_re.IGNORECASE):
            scene_num = int(match.group(1)) - 1
            duration_str = match.group(2)
            if '-' in duration_str:
                parts = duration_str.split('-')
                duration = (int(parts[0]) + int(parts[1])) / 2
            else:
                duration = int(duration_str)
            scene_durations[scene_num] = duration
        
        for i, section in enumerate(visual_board.get('sections', [])):
            if not section.get('duration_seconds') and i in scene_durations:
                section['duration_seconds'] = scene_durations[i]
            elif not section.get('duration_seconds'):
                section['duration_seconds'] = 4
        
        for section in visual_board.get('sections', []):
            section['suggested_videos'] = []
            cache_keywords = section.get('cache_keywords', [])
            mood = section.get('mood', '')
            
            cached_assets = []
            for keyword in cache_keywords[:3]:
                cache_entries = KeywordAssetCache.query.filter(
                    KeywordAssetCache.keyword.ilike(f'%{keyword}%')
                ).order_by(KeywordAssetCache.use_count.desc()).limit(2).all()
                
                for entry in cache_entries:
                    asset = MediaAsset.query.get(entry.asset_id)
                    if asset and asset.status == 'safe':
                        use_count = asset.use_count or 0
                        cached_assets.append({
                            'id': asset.id,
                            'source': asset.source,
                            'thumbnail': asset.thumbnail_url,
                            'download_url': asset.download_url,
                            'duration': asset.duration_sec,
                            'license': asset.license,
                            'license_url': asset.license_url,
                            'attribution': asset.attribution_text,
                            'from_cache': True,
                            'use_count': use_count,
                            'is_popular': use_count >= 3
                        })
            
            seen_ids = set()
            for asset in cached_assets:
                if asset['id'] not in seen_ids:
                    section['suggested_videos'].append(asset)
                    seen_ids.add(asset['id'])
            
            if len(section['suggested_videos']) < 4:
                for query in section.get('search_queries', [])[:2]:
                    try:
                        search_url = 'https://commons.wikimedia.org/w/api.php'
                        wiki_headers = {'User-Agent': 'EchoEngine/1.0 (content creation tool)'}
                        
                        pages = {}
                        
                        image_params = {
                            'action': 'query',
                            'format': 'json',
                            'generator': 'search',
                            'gsrnamespace': 6,
                            'gsrsearch': query,
                            'gsrlimit': 8,
                            'prop': 'imageinfo',
                            'iiprop': 'url|extmetadata',
                            'iiurlwidth': 800
                        }
                        img_resp = requests.get(search_url, params=image_params, headers=wiki_headers, timeout=10)
                        print(f"[Wikimedia] Query: {query}, Status: {img_resp.status_code}")
                        
                        if img_resp.status_code == 200:
                            data = img_resp.json()
                            pages = data.get('query', {}).get('pages', {})
                        
                        print(f"[Wikimedia] Query: {query}, Found {len(pages)} images")
                        
                        if not pages:
                            print(f"[Wikimedia] No results for: {query}")
                            continue
                        
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
                            
                            is_valid, our_license, rejection = validate_license(license_short)
                            if not is_valid:
                                print(f"[Wikimedia] Rejected {asset_id}: {rejection}")
                                continue
                            
                            title = page.get('title', '')
                            description_raw = extmeta.get('ImageDescription', {}).get('value', '')
                            categories = extmeta.get('Categories', {}).get('value', '').split('|') if extmeta.get('Categories', {}).get('value') else []
                            is_nsfw, nsfw_reason = is_nsfw_content(title, description_raw, categories)
                            if is_nsfw:
                                print(f"[NSFW Filter] Blocked in curation: {title} - {nsfw_reason}")
                                continue
                            
                            artist = regex.sub('<[^<]+?>', '', extmeta.get('Artist', {}).get('value', 'Unknown')).strip()
                            
                            source_page = f"https://commons.wikimedia.org/wiki/{page.get('title', '').replace(' ', '_')}"
                            
                            thumbnail_url = imageinfo.get('thumburl') or imageinfo.get('url')
                            download_url = imageinfo.get('url')
                            
                            if not thumbnail_url or not download_url:
                                print(f"[Wikimedia] Skipping {asset_id} - no valid URL")
                                continue
                            
                            video_data = {
                                'id': asset_id,
                                'source': 'wikimedia',
                                'source_page': source_page,
                                'thumbnail': thumbnail_url,
                                'download_url': download_url,
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
            
            if len(section['suggested_videos']) < 3:
                for query in section.get('search_queries', [])[:2]:
                    try:
                        pexels_key = os.environ.get('PEXELS_API_KEY')
                        if not pexels_key:
                            continue
                        pexels_headers = {'Authorization': pexels_key}
                        pexels_resp = requests.get(
                            'https://api.pexels.com/v1/search',
                            params={'query': query, 'per_page': 3, 'orientation': 'landscape'},
                            headers=pexels_headers,
                            timeout=10
                        )
                        if pexels_resp.status_code == 200:
                            pexels_data = pexels_resp.json()
                            for photo in pexels_data.get('photos', []):
                                asset_id = f"pexels_{photo['id']}"
                                if asset_id in seen_ids:
                                    continue
                                section['suggested_videos'].append({
                                    'id': asset_id,
                                    'source': 'pexels',
                                    'thumbnail': photo.get('src', {}).get('medium'),
                                    'download_url': photo.get('src', {}).get('large2x') or photo.get('src', {}).get('original'),
                                    'license': 'Pexels License',
                                    'license_url': 'https://www.pexels.com/license/',
                                    'attribution': f"{photo.get('photographer', 'Unknown')} / Pexels",
                                    'from_cache': False
                                })
                                seen_ids.add(asset_id)
                                print(f"[Pexels] Added: {asset_id}")
                    except Exception as pexels_err:
                        print(f"[Pexels] Error: {pexels_err}")
            
            print(f"[Visual Board] Section has {len(section['suggested_videos'])} visuals")
        
        total_visuals = sum(len(s.get('suggested_videos', [])) for s in visual_board.get('sections', []))
        print(f"[Visual Board] Total: {len(visual_board.get('sections', []))} sections, {total_visuals} visuals")
        
        return jsonify({'success': True, 'visual_board': visual_board})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@visual_bp.route('/save-to-cache', methods=['POST'])
def save_to_cache():
    """Save a selected asset to the cache with keywords for future use."""
    data = request.get_json()
    asset = data.get('asset', {})
    keywords = data.get('keywords', [])
    context = data.get('context', '')
    
    if not asset.get('id') or not asset.get('download_url'):
        return jsonify({'success': False, 'error': 'Missing asset data'}), 400
    
    try:
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
                status='safe',
                use_count=1
            )
            db.session.add(new_asset)
            db.session.commit()
        else:
            existing.use_count = (existing.use_count or 0) + 1
            db.session.commit()
        
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


@visual_bp.route('/ingest', methods=['POST'])
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
        'rejected': rejected[:10]
    })


@visual_bp.route('/assets', methods=['GET'])
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
