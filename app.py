from flask import Flask, render_template, request, jsonify, send_from_directory, session, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix
import os
import json
import uuid
import tempfile
import stripe
import requests
import re
import logging
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont
from urllib.parse import urlparse
import io
from context_engine import (
    extract_audio, transcribe_audio, analyze_ideas,
    generate_script, find_clip_timestamps, generate_captions,
    cut_video_clip, concatenate_clips,
    extract_thesis, identify_anchors, detect_thought_changes,
    generate_thesis_driven_script, process_source_for_clipping,
    learn_from_source_content, unified_content_engine
)

logging.basicConfig(level=logging.DEBUG)

class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)

app = Flask(__name__)
app.secret_key = os.environ.get('SESSION_SECRET')
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
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
    use_count = db.Column(db.Integer, default=0)  # Track popularity
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

class SourceDocument(db.Model):
    """Source documents/citations for education reels."""
    id = db.Column(db.Integer, primary_key=True)
    url = db.Column(db.Text, nullable=False, unique=True)
    doc_type = db.Column(db.String(20))  # pdf, article, webpage
    title = db.Column(db.Text)
    author = db.Column(db.Text)
    publisher = db.Column(db.String(255))
    publish_date = db.Column(db.String(100))
    preview_method = db.Column(db.String(30))  # official_preview, rendered_snapshot, title_card
    preview_image_path = db.Column(db.Text)  # Path to generated preview image
    excerpts = db.Column(db.JSON)  # List of short excerpts (<=25 words each)
    og_image = db.Column(db.Text)  # OpenGraph image if available
    verified = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

with app.app_context():
    db.create_all()
    if not UserTokens.query.first():
        token_entry = UserTokens()
        token_entry.balance = 120
        db.session.add(token_entry)
        db.session.commit()
    logging.info("Database tables created")

def extract_dialogue_only(script_text):
    """
    Extract ONLY spoken dialogue from script - bare minimum for voice generation.
    Keeps lines formatted as [CHARACTER]: dialogue or CHARACTER: dialogue.
    Filters AI commentary that appears BEFORE script starts.
    """
    import re
    
    dialogue_lines = []
    in_script = False
    
    # AI commentary patterns - ONLY applied before script starts
    ai_meta_patterns = [
        r'^Understood', r'^I\'ll create', r'^Here\'s', r'^Let me create',
        r'^This script', r'^The script', r'^I\'ve', r'^I can create',
        r'^Let me know', r'^Would you like', r'^The message',
        r'^exaggerated personas', r'^With voices', r'^I hope this',
        r'^This uses a', r'^The humor comes',
    ]
    
    for line in script_text.split('\n'):
        line = line.strip()
        if not line:
            continue
        
        # Detect when actual script content starts (SCENE, [CHARACTER]:, or CHARACTER:)
        if re.match(r'^SCENE\s+\d+', line, re.IGNORECASE):
            in_script = True
            continue  # Skip the scene header itself
        if re.match(r'^\[.+\]:', line) or re.match(r'^[A-Z][A-Z\-]+:', line):
            in_script = True
        
        # Before script starts: skip AI commentary and long prose
        if not in_script:
            if any(re.match(p, line, re.IGNORECASE) for p in ai_meta_patterns):
                continue
            if len(line) > 80:  # Long prose = AI explanation
                continue
        
        # Skip direction headers (always, even during script)
        if line.startswith('[VISUAL') or line.startswith('[CUT') or line.startswith('[FADE'):
            continue
        if line.startswith('VISUAL:') or line.startswith('CUT:'):
            continue
        if re.match(r'^(INT\.|EXT\.|TITLE:|CUT TO)', line):
            continue
        
        # Skip all-caps location lines like "HOLY LAND ARENA"
        if re.match(r'^[A-Z\s\-]+$', line) and len(line) < 50 and ':' not in line:
            continue
        
        # Pattern 1: [CHARACTER]: dialogue (brackets)
        match1 = re.match(r'^\[([^\]]+)\]:\s*(.+)$', line)
        if match1:
            dialogue = match1.group(2).strip()
            dialogue = re.sub(r'\([^)]*\)', '', dialogue).strip()
            if dialogue:
                dialogue_lines.append(dialogue)
            continue
        
        # Pattern 2: CHARACTER: dialogue (no brackets)
        match2 = re.match(r'^([A-Za-z][A-Za-z0-9\-\.\'\s]{0,25}):\s*(.+)$', line)
        if match2:
            char_name = match2.group(1).strip().upper()
            dialogue = match2.group(2).strip()
            if char_name in ['SCENE', 'VISUAL', 'CUT', 'FADE', 'INT', 'EXT', 'TITLE', 'CHARACTERS', 'VOICES']:
                continue
            dialogue = re.sub(r'\([^)]*\)', '', dialogue).strip()
            if dialogue:
                dialogue_lines.append(dialogue)
            continue
    
    return ' '.join(dialogue_lines)


def generate_sound_effect(effect_type, output_path, duration=1.0):
    """
    Generate a sound effect using FFmpeg synthesis.
    Returns the path to the generated audio file.
    
    Supported effect types:
    - whoosh: Quick transition swoosh
    - impact: Deep bass hit
    - tension: Rising drone
    - reveal: Bright chime/sting
    - alarm: Alert/warning tone
    - heartbeat: Rhythmic pulse
    - static: Radio/TV static
    - beep: Simple notification
    - rumble: Low rumble/earthquake
    - wind: Ambient wind
    """
    import subprocess
    
    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else 'output', exist_ok=True)
    
    # FFmpeg anoisesrc uses 'color' and 'amplitude' (not 'c' and 'a')
    # Use -filter_complex for complex filter graphs
    effect_commands = {
        'whoosh': [
            'ffmpeg', '-y', '-f', 'lavfi',
            '-i', f'anoisesrc=d={duration}:color=pink:amplitude=0.3,afade=t=in:d=0.05,afade=t=out:d={duration*0.8}:st={duration*0.2},highpass=f=800,lowpass=f=4000',
            '-c:a', 'aac', '-b:a', '128k', output_path
        ],
        'impact': [
            'ffmpeg', '-y', '-f', 'lavfi',
            '-i', f'sine=f=60:d={duration},afade=t=out:d={duration*0.9}:st=0.1,volume=2',
            '-c:a', 'aac', '-b:a', '128k', output_path
        ],
        'tension': [
            'ffmpeg', '-y', '-f', 'lavfi',
            '-i', f'sine=f=80:d={duration},tremolo=f=5:d=0.5,afade=t=in:d={duration*0.3},afade=t=out:d={duration*0.3}:st={duration*0.7}',
            '-c:a', 'aac', '-b:a', '128k', output_path
        ],
        'reveal': [
            'ffmpeg', '-y', '-f', 'lavfi',
            '-i', f'sine=f=880:d={duration},afade=t=in:d=0.05,afade=t=out:d={duration*0.5}:st={duration*0.5},volume=0.5',
            '-c:a', 'aac', '-b:a', '128k', output_path
        ],
        'alarm': [
            'ffmpeg', '-y', '-f', 'lavfi',
            '-i', f'sine=f=800:d={duration},tremolo=f=8:d=0.9,afade=t=out:d=0.1:st={duration-0.1}',
            '-c:a', 'aac', '-b:a', '128k', output_path
        ],
        'heartbeat': [
            'ffmpeg', '-y', '-f', 'lavfi',
            '-i', f'sine=f=50:d={duration},tremolo=f=1.5:d=0.9,afade=t=in:d=0.1,afade=t=out:d=0.2:st={max(0.1, duration-0.2)}',
            '-c:a', 'aac', '-b:a', '128k', output_path
        ],
        'static': [
            'ffmpeg', '-y', '-f', 'lavfi',
            '-i', f'anoisesrc=d={duration}:color=white:amplitude=0.2,bandpass=f=2000:width_type=h:w=1000',
            '-c:a', 'aac', '-b:a', '128k', output_path
        ],
        'beep': [
            'ffmpeg', '-y', '-f', 'lavfi',
            '-i', f'sine=f=1000:d={min(duration, 0.3)},afade=t=in:d=0.01,afade=t=out:d=0.05:st={max(0.01, min(duration, 0.3)-0.05)}',
            '-c:a', 'aac', '-b:a', '128k', output_path
        ],
        'rumble': [
            'ffmpeg', '-y', '-f', 'lavfi',
            '-i', f'anoisesrc=d={duration}:color=brown:amplitude=0.4,lowpass=f=120,afade=t=in:d={duration*0.2},afade=t=out:d={duration*0.3}:st={duration*0.7}',
            '-c:a', 'aac', '-b:a', '128k', output_path
        ],
        'wind': [
            'ffmpeg', '-y', '-f', 'lavfi',
            '-i', f'anoisesrc=d={duration}:color=pink:amplitude=0.15,lowpass=f=600,afade=t=in:d={duration*0.3},afade=t=out:d={duration*0.3}:st={duration*0.7}',
            '-c:a', 'aac', '-b:a', '128k', output_path
        ],
    }
    
    # Default to whoosh if effect type not found
    cmd = effect_commands.get(effect_type.lower(), effect_commands['whoosh'])
    
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        if result.returncode == 0 and os.path.exists(output_path):
            return output_path
        else:
            print(f"SFX generation failed for {effect_type}: {result.stderr.decode()[:200]}")
            return None
    except Exception as e:
        print(f"SFX generation error: {e}")
        return None


def parse_sfx_from_directions(script_text, stage_directions=''):
    """
    Parse [SOUND: description] tags from script and stage directions.
    Returns a list of sound effect requests with estimated timing.
    """
    import re
    
    sfx_requests = []
    combined_text = f"{script_text}\n{stage_directions}"
    
    # Map common descriptions to effect types
    description_to_effect = {
        'whoosh': 'whoosh',
        'swoosh': 'whoosh',
        'transition': 'whoosh',
        'swipe': 'whoosh',
        'impact': 'impact',
        'hit': 'impact',
        'boom': 'impact',
        'thud': 'impact',
        'punch': 'impact',
        'tension': 'tension',
        'suspense': 'tension',
        'drone': 'tension',
        'rising': 'tension',
        'reveal': 'reveal',
        'sting': 'reveal',
        'chime': 'reveal',
        'discovery': 'reveal',
        'alarm': 'alarm',
        'alert': 'alarm',
        'warning': 'alarm',
        'siren': 'alarm',
        'heartbeat': 'heartbeat',
        'heart': 'heartbeat',
        'pulse': 'heartbeat',
        'static': 'static',
        'noise': 'static',
        'interference': 'static',
        'beep': 'beep',
        'notification': 'beep',
        'ping': 'beep',
        'rumble': 'rumble',
        'earthquake': 'rumble',
        'thunder': 'rumble',
        'bass': 'rumble',
        'wind': 'wind',
        'breeze': 'wind',
        'atmosphere': 'wind',
    }
    
    # Find all [SOUND: description] tags
    sound_pattern = re.compile(r'\[SOUND:\s*([^\]]+)\]', re.IGNORECASE)
    
    lines = combined_text.split('\n')
    line_position = 0
    
    for line in lines:
        matches = sound_pattern.findall(line)
        for description in matches:
            description_lower = description.lower().strip()
            
            # Find matching effect type
            effect_type = 'whoosh'  # default
            for keyword, effect in description_to_effect.items():
                if keyword in description_lower:
                    effect_type = effect
                    break
            
            # Parse duration if specified (e.g., "whoosh 2s")
            duration = 1.0
            duration_match = re.search(r'(\d+(?:\.\d+)?)\s*s', description_lower)
            if duration_match:
                duration = float(duration_match.group(1))
            
            sfx_requests.append({
                'description': description.strip(),
                'effect_type': effect_type,
                'duration': duration,
                'position': line_position  # Approximate position in script
            })
        
        line_position += 1
    
    return sfx_requests


def mix_sfx_into_audio(voiceover_path, sfx_requests, output_path, total_script_lines=None):
    """
    Generate sound effects and mix them into the voiceover audio.
    SFX are placed based on their relative position in the script.
    """
    import subprocess
    from pydub import AudioSegment
    
    if not sfx_requests or not os.path.exists(voiceover_path):
        # No SFX to add, copy original
        if os.path.exists(voiceover_path):
            import shutil
            shutil.copy(voiceover_path, output_path)
        return output_path
    
    try:
        # Load voiceover
        voiceover = AudioSegment.from_file(voiceover_path)
        total_duration_ms = len(voiceover)
        
        print(f"Mixing {len(sfx_requests)} sound effects into {total_duration_ms/1000:.1f}s audio")
        
        # Calculate max position from all SFX for proper proportioning
        max_position = max(sfx['position'] for sfx in sfx_requests) if sfx_requests else 1
        if total_script_lines and total_script_lines > max_position:
            max_position = total_script_lines
        max_position = max(1, max_position)  # Avoid division by zero
        
        # Generate and overlay each SFX
        for i, sfx in enumerate(sfx_requests):
            # Calculate position based on script line position
            # This maps the line position to a timestamp in the audio
            position_ratio = sfx['position'] / max_position
            # Place SFX at the proportional position, accounting for effect duration
            sfx_duration_ms = sfx['duration'] * 1000
            start_ms = int(position_ratio * max(0, total_duration_ms - sfx_duration_ms))
            
            # Generate the sound effect
            sfx_path = f"output/sfx_temp_{i}_{uuid.uuid4().hex[:6]}.m4a"
            generated_path = generate_sound_effect(sfx['effect_type'], sfx_path, sfx['duration'])
            
            if generated_path and os.path.exists(generated_path):
                try:
                    sfx_audio = AudioSegment.from_file(generated_path)
                    # Reduce SFX volume so it doesn't overpower voice
                    sfx_audio = sfx_audio - 6  # -6dB
                    
                    # Overlay at calculated position
                    voiceover = voiceover.overlay(sfx_audio, position=start_ms)
                    print(f"  Added {sfx['effect_type']} at {start_ms/1000:.1f}s")
                except Exception as e:
                    print(f"  Failed to overlay SFX {i}: {e}")
                finally:
                    # Cleanup temp file
                    try:
                        os.remove(generated_path)
                    except:
                        pass
        
        # Export mixed audio
        voiceover.export(output_path, format='mp3', bitrate='192k')
        print(f"SFX mixed audio saved to {output_path}")
        return output_path
        
    except Exception as e:
        print(f"SFX mixing failed: {e}")
        # Return original on failure
        import shutil
        if os.path.exists(voiceover_path):
            shutil.copy(voiceover_path, output_path)
        return output_path


def extract_voice_actor_script(script_text, character_filter=None):
    """
    Extract a clean voice actor script from the full screenplay.
    If character_filter is provided, only include lines for that character.
    
    Returns clean dialogue only - what the voice actor reads.
    """
    import re
    
    lines = script_text.split('\n')
    voice_lines = []
    
    for line in lines:
        stripped = line.strip()
        
        # Skip empty lines (we'll add spacing later)
        if not stripped:
            continue
        
        # Skip decorative separator lines (===, ---, ___)
        if re.match(r'^[=_\-]{3,}$', stripped):
            continue
        
        # Skip VISUAL: and CUT: lines
        if stripped.startswith('VISUAL:') or stripped.startswith('CUT:'):
            continue
        
        # Skip scene directions
        if stripped.startswith('SCENE ') or stripped.startswith('INT.') or stripped.startswith('EXT.'):
            continue
        
        # Skip CUT TO: transitions
        if stripped.startswith('CUT TO'):
            continue
        
        # Skip CHARACTERS: and VOICES? lines
        if stripped.startswith('CHARACTERS:') or stripped.startswith('VOICES?'):
            continue
        
        # Skip title lines
        if stripped.startswith('===') or stripped.endswith('==='):
            continue
        
        # Direction keywords to skip
        direction_keywords = {'VISUAL', 'CUT', 'FADE', 'SCENE', 'INT', 'EXT', 'TITLE'}
        
        # Match [CHARACTER NAME]: dialogue pattern (case-insensitive, allows punctuation)
        bracket_match = re.match(r'^\[([A-Za-z][A-Za-z0-9\s_\.\-\']+)\]:\s*(.+)$', stripped)
        if bracket_match:
            character = bracket_match.group(1).strip().upper()
            dialogue = bracket_match.group(2).strip()
            
            if character in direction_keywords:
                continue
            
            # If filtering by character, only include their lines
            if character_filter:
                if character == character_filter.upper():
                    voice_lines.append(dialogue)
            else:
                # Include all dialogue (no character labels for voice actor)
                voice_lines.append(dialogue)
            continue
        
        # Match CHARACTER NAME: dialogue pattern (no brackets, case-insensitive)
        colon_match = re.match(r'^([A-Za-z][A-Za-z0-9\s_\.\-\']+):\s*(.+)$', stripped)
        if colon_match:
            character = colon_match.group(1).strip().upper()
            dialogue = colon_match.group(2).strip()
            if character not in direction_keywords:
                if character_filter:
                    if character == character_filter.upper():
                        voice_lines.append(dialogue)
                else:
                    voice_lines.append(dialogue)
            continue
        
        # Skip [VISUAL...], [CUT...], [FADE...] directions
        if re.match(r'^\[', stripped):
            continue
    
    # Clean up multiple consecutive empty lines
    result = []
    prev_empty = False
    for line in voice_lines:
        if not line.strip():
            if not prev_empty:
                result.append('')
            prev_empty = True
        else:
            result.append(line)
            prev_empty = False
    
    return '\n'.join(result).strip()


def parse_character_lines(script_text):
    """
    Parse a multi-character script and extract lines per character.
    Expected format: [CHARACTER NAME]: dialogue text
    Also handles mixed case and punctuation in character names.
    
    Returns list of dicts with order preserved:
    [{'character': 'NEWS ANCHOR', 'line': 'The market crashed.', 'order': 0}, ...]
    """
    import re
    
    character_lines = []
    order = 0
    
    # Direction keywords to skip (case-insensitive)
    direction_keywords = {'VISUAL', 'CUT', 'FADE', 'SCENE', 'INT', 'EXT', 'TITLE', 'CUT TO', 'FADE TO'}
    
    for line in script_text.split('\n'):
        line = line.strip()
        if not line:
            continue
        
        # Skip decorative/direction lines
        if re.match(r'^[=_\-]{3,}$', line):
            continue
        if line.upper().startswith('[VISUAL') or line.upper().startswith('[CUT') or line.upper().startswith('[FADE'):
            continue
        if line.upper().startswith('VISUAL:') or line.upper().startswith('CUT:'):
            continue
        if line.upper().startswith('SCENE ') or line.upper().startswith('INT.') or line.upper().startswith('EXT.'):
            continue
        if line.upper().startswith('CHARACTERS:') or line.upper().startswith('VOICES?'):
            continue
        
        # Match [CHARACTER NAME]: dialogue pattern (case-insensitive, allows punctuation)
        bracket_match = re.match(r'^\[([A-Za-z][A-Za-z0-9\s_\.\-\']+)\]:\s*(.+)$', line)
        if bracket_match:
            character = bracket_match.group(1).strip().upper()  # Normalize to uppercase
            dialogue = bracket_match.group(2).strip()
            if dialogue and character not in direction_keywords:
                character_lines.append({
                    'character': character,
                    'line': dialogue,
                    'order': order
                })
                order += 1
            continue
        
        # Match CHARACTER NAME: dialogue pattern (no brackets, case-insensitive)
        colon_match = re.match(r'^([A-Za-z][A-Za-z0-9\s_\.\-\']+):\s*(.+)$', line)
        if colon_match:
            character = colon_match.group(1).strip().upper()  # Normalize to uppercase
            dialogue = colon_match.group(2).strip()
            # Exclude direction keywords
            if character not in direction_keywords:
                if dialogue:
                    character_lines.append({
                        'character': character,
                        'line': dialogue,
                        'order': order
                    })
                    order += 1
    
    return character_lines


def get_character_voice_map(voice_assignments):
    """
    Map character names to their assigned voice personas.
    voice_assignments is a dict like {'NEWS ANCHOR': 'news_anchor', 'WOLF': 'wolf_businessman'}
    """
    return voice_assignments if voice_assignments else {}


def assemble_audio_clips(clip_paths, output_path):
    """
    Assemble multiple audio clips into a single file in order.
    Uses FFmpeg filter_complex for reliable MP3 concatenation with re-encoding.
    """
    import subprocess
    
    if not clip_paths:
        return None
    
    if len(clip_paths) == 1:
        import shutil
        shutil.copy(clip_paths[0], output_path)
        return output_path
    
    try:
        # Build filter_complex for reliable concat with re-encoding
        inputs = []
        filter_parts = []
        
        for i, clip in enumerate(clip_paths):
            inputs.extend(['-i', clip])
            filter_parts.append(f'[{i}:a]')
        
        filter_str = ''.join(filter_parts) + f'concat=n={len(clip_paths)}:v=0:a=1[out]'
        
        cmd = [
            'ffmpeg', '-y',
            *inputs,
            '-filter_complex', filter_str,
            '-map', '[out]',
            '-c:a', 'libmp3lame', '-q:a', '2',
            output_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            print(f"FFmpeg concat error: {result.stderr}")
            return None
        
        return output_path
    except Exception as e:
        print(f"Audio assembly error: {e}")
        return None


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

@app.route('/create-subscription', methods=['POST'])
def create_subscription():
    """Create a Stripe subscription checkout session for Pro tier ($10/month)."""
    try:
        from models import Subscription
        from flask_login import current_user
        
        _, secret_key = get_stripe_credentials()
        if not secret_key:
            return jsonify({'error': 'Payment not configured'}), 500
        
        stripe.api_key = secret_key
        
        domains = os.environ.get('REPLIT_DOMAINS', 'localhost:5000')
        domain = domains.split(',')[0] if domains else 'localhost:5000'
        protocol = 'https' if 'replit' in domain else 'http'
        base_url = f"{protocol}://{domain}"
        
        user_id = None
        if current_user.is_authenticated:
            user_id = current_user.id
        else:
            user_id = session.get('dev_user_id', 'dev_user')
        
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': 'Framd Pro',
                        'description': 'Unlimited video generation & hosting - $10/month',
                    },
                    'unit_amount': 1000,
                    'recurring': {
                        'interval': 'month',
                    },
                },
                'quantity': 1,
            }],
            mode='subscription',
            success_url=f'{base_url}/?subscription=success',
            cancel_url=f'{base_url}/?subscription=canceled',
            metadata={
                'user_id': user_id,
                'plan': 'pro'
            }
        )
        
        return jsonify({'url': checkout_session.url})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/subscription-status', methods=['GET'])
def subscription_status():
    """Check current user's subscription status."""
    from models import Subscription, User
    from flask_login import current_user
    
    # Dev mode always has Pro access
    if session.get('dev_mode'):
        return jsonify({'tier': 'pro', 'status': 'active', 'is_pro': True, 'lifetime': True})
    
    user_id = None
    user_email = None
    if current_user.is_authenticated:
        user_id = current_user.id
        user_email = current_user.email
    else:
        user_id = session.get('dev_user_id')
    
    if not user_id:
        return jsonify({'tier': 'free', 'status': 'inactive', 'is_pro': False})
    
    # Lifetime Pro for specific email
    if user_email and user_email.lower() == 'alonbenmeir9@gmail.com':
        return jsonify({'tier': 'pro', 'status': 'active', 'is_pro': True, 'lifetime': True})
    
    sub = Subscription.query.filter_by(user_id=user_id).first()
    if sub and sub.is_active():
        return jsonify({
            'tier': sub.tier,
            'status': sub.status,
            'is_pro': True,
            'current_period_end': sub.current_period_end.isoformat() if sub.current_period_end else None
        })
    
    return jsonify({'tier': 'free', 'status': 'inactive', 'is_pro': False})


@app.route('/stripe-webhook', methods=['POST'])
def stripe_webhook():
    """Handle Stripe webhook events."""
    from models import Subscription
    from datetime import datetime
    
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
        
        if session_data.get('mode') == 'subscription':
            user_id = session_data.get('metadata', {}).get('user_id')
            subscription_id = session_data.get('subscription')
            customer_id = session_data.get('customer')
            
            if user_id:
                sub = Subscription.query.filter_by(user_id=user_id).first()
                if not sub:
                    sub = Subscription(user_id=user_id)
                    db.session.add(sub)
                
                sub.stripe_customer_id = customer_id
                sub.stripe_subscription_id = subscription_id
                sub.tier = 'pro'
                sub.status = 'active'
                db.session.commit()
        else:
            token_amount = int(session_data.get('metadata', {}).get('token_amount', 0))
            if token_amount > 0:
                token_entry = UserTokens.query.first()
                if token_entry:
                    token_entry.balance += token_amount
                    db.session.commit()
    
    elif event['type'] == 'customer.subscription.updated':
        subscription_data = event['data']['object']
        stripe_sub_id = subscription_data.get('id')
        status = subscription_data.get('status')
        period_end = subscription_data.get('current_period_end')
        
        sub = Subscription.query.filter_by(stripe_subscription_id=stripe_sub_id).first()
        if sub:
            sub.status = 'active' if status == 'active' else 'inactive'
            if period_end:
                sub.current_period_end = datetime.fromtimestamp(period_end)
            db.session.commit()
    
    elif event['type'] == 'customer.subscription.deleted':
        subscription_data = event['data']['object']
        stripe_sub_id = subscription_data.get('id')
        
        sub = Subscription.query.filter_by(stripe_subscription_id=stripe_sub_id).first()
        if sub:
            sub.status = 'canceled'
            sub.tier = 'free'
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

# NSFW content blocklist - reject any media with these terms in title/description/categories
NSFW_BLOCKLIST = [
    'nude', 'nudity', 'naked', 'nsfw', 'xxx', 'porn', 'pornograph', 'erotic', 'erotica',
    'sex', 'sexual', 'genital', 'penis', 'vagina', 'breast', 'nipple', 'topless',
    'underwear', 'lingerie', 'bra', 'panties', 'fetish', 'bondage', 'bdsm',
    'adult content', 'explicit', 'mature content', '18+', 'r-rated',
    'playboy', 'hustler', 'penthouse', 'onlyfans',
    'stripper', 'striptease', 'burlesque', 'provocative',
    'masturbat', 'orgasm', 'intercourse', 'coitus',
    'hentai', 'ecchi', 'yaoi', 'yuri',
    'stockings', 'garter', 'corset', 'thong', 'bikini model',
    'pin-up', 'pinup', 'glamour model', 'glamor model',
    'body paint', 'body-paint', 'implied nude'
]

def is_nsfw_content(title, description='', categories=None):
    """Check if content appears to be NSFW based on title, description, and categories."""
    text_to_check = f"{title} {description} {' '.join(categories or [])}".lower()
    for term in NSFW_BLOCKLIST:
        if term in text_to_check:
            return True, f"Blocked: contains '{term}'"
    return False, None

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

@app.route('/search-wikimedia', methods=['POST'])
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
    media_type = data.get('media_type', 'all')  # 'video', 'image', or 'all'
    
    try:
        wiki_headers = {'User-Agent': 'KrakdPostAssembler/1.0 (https://replit.com; contact@krakd.app)'}
        search_url = 'https://commons.wikimedia.org/w/api.php'
        
        # Step 1: Search in File namespace (namespace=6) using generator=search
        search_params = {
            'action': 'query',
            'format': 'json',
            'generator': 'search',
            'gsrnamespace': 6,  # File namespace only
            'gsrsearch': query,
            'gsrlimit': limit * 2,  # Request more to account for filtering
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
            
            # Filter by media type
            is_video = mime.startswith('video/') or mediatype in ['VIDEO', 'AUDIO']
            is_image = mime.startswith('image/') and not mime.endswith('/gif')
            
            # Allow video MIME types: webm, ogg, mp4
            allowed_video_mimes = ['video/webm', 'video/ogg', 'video/mp4', 'application/ogg']
            allowed_image_mimes = ['image/jpeg', 'image/png', 'image/webp', 'image/svg+xml']
            
            if media_type == 'video' and not (is_video or mime in allowed_video_mimes):
                continue
            elif media_type == 'image' and not (is_image or mime in allowed_image_mimes):
                continue
            elif media_type == 'all' and not (is_video or is_image or mime in allowed_video_mimes + allowed_image_mimes):
                continue
            
            # Get license info
            license_short = extmeta.get('LicenseShortName', {}).get('value', '')
            license_url = extmeta.get('LicenseUrl', {}).get('value', '')
            
            # Validate license
            is_valid, our_license, _ = validate_license(license_short)
            if not is_valid:
                continue
            
            # NSFW content filter
            title = page.get('title', '')
            description_raw = extmeta.get('ImageDescription', {}).get('value', '')
            categories = extmeta.get('Categories', {}).get('value', '').split('|') if extmeta.get('Categories', {}).get('value') else []
            is_nsfw, nsfw_reason = is_nsfw_content(title, description_raw, categories)
            if is_nsfw:
                print(f"[NSFW Filter] Blocked: {title} - {nsfw_reason}")
                continue
            
            # Get attribution
            artist_html = extmeta.get('Artist', {}).get('value', 'Unknown')
            artist = re.sub('<[^<]+?>', '', artist_html).strip()
            if not artist or artist == 'Unknown':
                artist = extmeta.get('Credit', {}).get('value', 'Unknown')
                artist = re.sub('<[^<]+?>', '', artist).strip()
            
            attribution_required = our_license not in ['CC0', 'Public Domain']
            content_type = 'video' if (is_video or mime in allowed_video_mimes) else 'image'
            
            # Get description
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


@app.route('/search-wikimedia-videos', methods=['POST'])
def search_wikimedia_videos():
    """Legacy endpoint - calls new search with video filter."""
    req_data = request.get_json() or {}
    query = req_data.get('query', '')
    limit = req_data.get('limit', 10)
    
    # Reuse the new search logic
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
            
            # Video only
            if not mime.startswith('video/') and mime not in ['video/webm', 'video/ogg', 'video/mp4', 'application/ogg']:
                continue
            
            extmeta = imageinfo.get('extmetadata', {})
            license_short = extmeta.get('LicenseShortName', {}).get('value', '')
            
            is_valid, our_license, _ = validate_license(license_short)
            if not is_valid:
                continue
            
            # NSFW content filter
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


@app.route('/search-unsplash', methods=['POST'])
def search_unsplash():
    """
    Search Unsplash for high-quality artistic photos.
    Unsplash has more editorial/artistic content than Pexels.
    """
    req_data = request.get_json()
    query = req_data.get('query', '')
    limit = req_data.get('per_page', 15)
    orientation = req_data.get('orientation', 'portrait')  # portrait, landscape, squarish
    
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
            # Unsplash license is always free for commercial use
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
                'attribution_required': False,  # Not required but appreciated
                'attribution_text': f"Photo by {photo.get('user', {}).get('name', 'Unknown')} on Unsplash"
            })
        
        return jsonify({'success': True, 'assets': results, 'count': len(results)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e), 'assets': []})


@app.route('/search-all-sources', methods=['POST'])
def search_all_sources():
    """
    Search all sources for legal media - PRIORITIZES Wikimedia Commons over Pexels.
    Implements fallback ladder: Wikimedia (primary) → Pexels (fallback if <6 results) → query expansion.
    """
    data = request.get_json()
    query = data.get('query', '')
    limit = data.get('limit', 15)  # Default to 15 for more results
    media_type = data.get('media_type', 'all')  # 'video', 'image', 'all'
    
    all_results = []
    sources_searched = []
    
    # PRIORITY 1: Search Wikimedia Commons (most authentic/documentary content)
    try:
        wiki_headers = {'User-Agent': 'KrakdPostAssembler/1.0 (https://replit.com; contact@krakd.app)'}
        search_url = 'https://commons.wikimedia.org/w/api.php'
        
        search_params = {
            'action': 'query',
            'format': 'json',
            'generator': 'search',
            'gsrnamespace': 6,
            'gsrsearch': query,
            'gsrlimit': max(limit, 15),  # Always fetch at least 15 from Wikimedia
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
            
            # Filter by media type
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
            
            # NSFW content filter
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
    
    # Note: Pexels removed - only using sources with explicit reuse rights (Wikimedia Commons, public domain)
    
    # FALLBACK 3: Query expansion if still too few results
    if len(all_results) < 4 and ' ' in query:
        # Try simpler query (remove adjectives, use core noun)
        words = query.split()
        simple_query = words[-1] if len(words) > 1 else query  # Use last word (usually the noun)
        
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
                    # NSFW content filter for fallback results
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
        'videos': all_results,  # Backward compatibility
        'count': len(all_results),
        'sources': sources_searched
    })


# Pexels endpoint removed - only using Wikimedia Commons and public domain sources

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
    
    # IDEA-DRIVEN visual curation - visuals serve the MESSAGE, not the scene setting
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
        
        # FALLBACK: Parse durations directly from script if AI missed them
        import re as duration_re
        scene_durations = {}
        scene_pattern = r'SCENE\s+(\d+)\s*\[(\d+(?:-\d+)?)\s*s?\]'
        for match in duration_re.finditer(scene_pattern, script, duration_re.IGNORECASE):
            scene_num = int(match.group(1)) - 1  # 0-indexed
            duration_str = match.group(2)
            # Handle range like "3-4" -> take average
            if '-' in duration_str:
                parts = duration_str.split('-')
                duration = (int(parts[0]) + int(parts[1])) / 2
            else:
                duration = int(duration_str)
            scene_durations[scene_num] = duration
        
        # Apply parsed durations as fallback
        for i, section in enumerate(visual_board.get('sections', [])):
            if not section.get('duration_seconds') and i in scene_durations:
                section['duration_seconds'] = scene_durations[i]
            elif not section.get('duration_seconds'):
                section['duration_seconds'] = 4  # Default
        
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
                            'is_popular': use_count >= 3  # Mark as popular if used 3+ times
                        })
            
            # Add cached assets first (deduplicated)
            seen_ids = set()
            for asset in cached_assets:
                if asset['id'] not in seen_ids:
                    section['suggested_videos'].append(asset)
                    seen_ids.add(asset['id'])
            
            # STEP 2: Search Wikimedia Commons if we need more options (Pexels removed - only legal sources)
            if len(section['suggested_videos']) < 4:
                for query in section.get('search_queries', [])[:2]:
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
                            
                            # NSFW content filter
                            title = page.get('title', '')
                            description_raw = extmeta.get('ImageDescription', {}).get('value', '')
                            categories = extmeta.get('Categories', {}).get('value', '').split('|') if extmeta.get('Categories', {}).get('value') else []
                            is_nsfw, nsfw_reason = is_nsfw_content(title, description_raw, categories)
                            if is_nsfw:
                                print(f"[NSFW Filter] Blocked in curation: {title} - {nsfw_reason}")
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
                status='safe',
                use_count=1
            )
            db.session.add(new_asset)
            db.session.commit()  # Commit asset first before adding keyword associations
        else:
            # Increment use count for existing asset
            existing.use_count = (existing.use_count or 0) + 1
            db.session.commit()
        
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
    
    # Note: Pexels removed - only using sources with explicit reuse rights
    
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
    allowed_domains = ['wikimedia.org', 'upload.wikimedia.org', 'archive.org', 'commons.wikimedia.org']
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


@app.route('/remove-background', methods=['POST'])
def remove_background():
    """
    Remove background from an image using threshold-based alpha extraction.
    Returns a PNG with transparent background.
    """
    from PIL import Image
    import io
    import base64
    
    data = request.get_json()
    image_url = data.get('image_url')
    image_base64 = data.get('image_base64')
    character_name = data.get('character_name', 'Subject')
    
    if not image_url and not image_base64:
        return jsonify({'error': 'No image provided'}), 400
    
    try:
        if image_base64:
            image_data = base64.b64decode(image_base64)
            img = Image.open(io.BytesIO(image_data))
        else:
            from urllib.parse import urlparse
            allowed_domains = ['wikimedia.org', 'upload.wikimedia.org', 'pexels.com', 'images.pexels.com']
            parsed = urlparse(image_url)
            if not any(domain in parsed.netloc for domain in allowed_domains):
                return jsonify({'error': 'Image URL not from approved source'}), 403
            
            resp = requests.get(image_url, timeout=30)
            resp.raise_for_status()
            img = Image.open(io.BytesIO(resp.content))
        
        img = img.convert('RGBA')
        pixels = img.load()
        width, height = img.size
        
        for y in range(height):
            for x in range(width):
                r, g, b, a = pixels[x, y]
                brightness = (r + g + b) / 3
                if brightness > 240:
                    pixels[x, y] = (r, g, b, 0)
                elif brightness > 220:
                    pixels[x, y] = (r, g, b, int(a * 0.5))
        
        output_filename = f"{uuid.uuid4()}_extracted.png"
        output_path = os.path.join('output', output_filename)
        img.save(output_path, 'PNG')
        
        buffered = io.BytesIO()
        img.save(buffered, format='PNG')
        result_base64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
        
        return jsonify({
            'success': True,
            'image_base64': result_base64,
            'local_path': output_path,
            'character_name': character_name,
            'dimensions': {'width': width, 'height': height}
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/generate-character-image', methods=['POST'])
def generate_character_image():
    """
    Generate a character image using DALL-E for use in video compositions.
    """
    from openai import OpenAI
    from PIL import Image
    import io
    import base64
    
    data = request.get_json()
    character_description = data.get('description', '')
    character_name = data.get('name', 'Character')
    style = data.get('style', 'realistic')
    
    if not character_description:
        return jsonify({'error': 'No character description provided'}), 400
    
    try:
        client = OpenAI()
        
        style_prompts = {
            'realistic': 'photorealistic, high quality, professional photography',
            'cartoon': 'cartoon style, vibrant colors, clean lines',
            'anime': 'anime style, Japanese animation aesthetic',
            'sketch': 'pencil sketch, black and white, artistic',
            'cinematic': 'cinematic, dramatic lighting, film quality'
        }
        
        style_modifier = style_prompts.get(style, style_prompts['realistic'])
        full_prompt = f"{character_description}, {style_modifier}, portrait, clean background suitable for background removal, high contrast"
        
        response = client.images.generate(
            model="dall-e-3",
            prompt=full_prompt,
            size="1024x1024",
            quality="standard",
            n=1
        )
        
        image_url = response.data[0].url
        
        img_response = requests.get(image_url, timeout=60)
        img_response.raise_for_status()
        
        output_filename = f"{uuid.uuid4()}_character.png"
        output_path = os.path.join('output', output_filename)
        
        with open(output_path, 'wb') as f:
            f.write(img_response.content)
        
        buffered = io.BytesIO(img_response.content)
        result_base64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
        
        return jsonify({
            'success': True,
            'image_url': image_url,
            'image_base64': result_base64,
            'local_path': output_path,
            'character_name': character_name,
            'prompt_used': full_prompt
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/source/preview', methods=['POST'])
def source_preview():
    """
    Generate a preview image for a source document.
    3-tier fallback: official_preview → rendered_snapshot → title_card
    """
    data = request.get_json()
    url = data.get('url', '').strip()
    doc_type = data.get('type', 'auto')
    
    if not url:
        return jsonify({'ok': False, 'error': 'URL required'}), 400
    
    # Check if we already have this source cached
    existing = SourceDocument.query.filter_by(url=url).first()
    if existing and existing.preview_image_path and os.path.exists(existing.preview_image_path):
        return jsonify({
            'ok': True,
            'method': existing.preview_method,
            'image_url': f'/output/{os.path.basename(existing.preview_image_path)}',
            'meta': {
                'title': existing.title,
                'source': existing.publisher,
                'author': existing.author,
                'date': existing.publish_date,
                'excerpts': existing.excerpts
            }
        })
    
    # Detect document type
    parsed_url = urlparse(url)
    is_pdf = url.lower().endswith('.pdf') or doc_type == 'pdf'
    
    meta = {
        'title': None,
        'source': parsed_url.netloc.replace('www.', ''),
        'author': None,
        'date': None,
        'excerpts': []
    }
    preview_method = 'title_card'
    preview_image_path = None
    og_image = None
    
    try:
        # TIER 1: PDF - render first page
        if is_pdf:
            try:
                from pdf2image import convert_from_bytes
                resp = requests.get(url, timeout=30, headers={'User-Agent': 'Mozilla/5.0'})
                resp.raise_for_status()
                
                images = convert_from_bytes(resp.content, first_page=1, last_page=1, dpi=150)
                if images:
                    preview_method = 'official_preview'
                    preview_filename = f"source_preview_{uuid.uuid4().hex[:8]}.png"
                    preview_image_path = os.path.join('output', preview_filename)
                    images[0].save(preview_image_path, 'PNG')
                    meta['title'] = url.split('/')[-1].replace('.pdf', '').replace('_', ' ').title()
            except Exception as pdf_err:
                print(f"PDF render failed: {pdf_err}")
        
        # TIER 1 (continued): Article - check for og:image
        if not is_pdf and preview_method == 'title_card':
            try:
                resp = requests.get(url, timeout=15, headers={'User-Agent': 'Mozilla/5.0'})
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, 'html.parser')
                
                # Extract metadata
                og_title = soup.find('meta', property='og:title')
                if og_title:
                    meta['title'] = og_title.get('content', '')[:200]
                if not meta['title']:
                    title_tag = soup.find('title')
                    if title_tag:
                        meta['title'] = title_tag.text.strip()[:200]
                
                og_site = soup.find('meta', property='og:site_name')
                if og_site:
                    meta['source'] = og_site.get('content', meta['source'])
                
                # Look for author
                author_meta = soup.find('meta', attrs={'name': 'author'})
                if author_meta:
                    meta['author'] = author_meta.get('content', '')[:100]
                
                # Look for date
                date_meta = soup.find('meta', property='article:published_time')
                if date_meta:
                    meta['date'] = date_meta.get('content', '')[:30]
                if not meta['date']:
                    time_tag = soup.find('time')
                    if time_tag:
                        meta['date'] = time_tag.get('datetime', time_tag.text)[:30]
                
                # Extract short excerpts (<=25 words each)
                paragraphs = soup.find_all('p')
                for p in paragraphs[:10]:
                    text = p.get_text().strip()
                    words = text.split()
                    if 10 <= len(words) <= 40:
                        excerpt = ' '.join(words[:25])
                        if len(words) > 25:
                            excerpt += '...'
                        meta['excerpts'].append(excerpt)
                        if len(meta['excerpts']) >= 3:
                            break
                
                # Check for og:image
                og_img = soup.find('meta', property='og:image')
                if og_img:
                    og_image = og_img.get('content')
                    if og_image and og_image.startswith('http'):
                        preview_method = 'official_preview'
                        preview_filename = f"source_preview_{uuid.uuid4().hex[:8]}.jpg"
                        preview_image_path = os.path.join('output', preview_filename)
                        img_resp = requests.get(og_image, timeout=15)
                        img_resp.raise_for_status()
                        with open(preview_image_path, 'wb') as f:
                            f.write(img_resp.content)
            except Exception as article_err:
                print(f"Article metadata extraction failed: {article_err}")
        
        # TIER 2: Rendered snapshot if no official preview
        if preview_method == 'title_card' and meta['title']:
            try:
                preview_method = 'rendered_snapshot'
                preview_filename = f"source_snapshot_{uuid.uuid4().hex[:8]}.png"
                preview_image_path = os.path.join('output', preview_filename)
                
                # Create document-style image
                width, height = 800, 600
                img = Image.new('RGB', (width, height), color='#f8f9fa')
                draw = ImageDraw.Draw(img)
                
                # Use default font (Pillow's built-in)
                try:
                    title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 28)
                    meta_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
                    excerpt_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
                except:
                    title_font = ImageFont.load_default()
                    meta_font = ImageFont.load_default()
                    excerpt_font = ImageFont.load_default()
                
                # Draw document border
                draw.rectangle([20, 20, width-20, height-20], outline='#dee2e6', width=2)
                draw.rectangle([30, 30, width-30, height-30], outline='#e9ecef', width=1)
                
                # Draw header line
                draw.line([(40, 100), (width-40, 100)], fill='#0a1f14', width=2)
                
                y = 50
                # Title
                title_text = meta['title'][:80] + ('...' if len(meta['title']) > 80 else '')
                draw.text((50, y), title_text, fill='#0a1f14', font=title_font)
                y = 120
                
                # Source and date
                source_line = f"{meta['source']}"
                if meta['author']:
                    source_line += f" • {meta['author'][:40]}"
                if meta['date']:
                    source_line += f" • {meta['date'][:20]}"
                draw.text((50, y), source_line, fill='#6c757d', font=meta_font)
                y += 40
                
                # Excerpts
                for excerpt in meta['excerpts'][:3]:
                    draw.text((50, y), f'"{excerpt[:100]}"', fill='#495057', font=excerpt_font)
                    y += 60
                
                # URL footer
                draw.line([(40, height-70), (width-40, height-70)], fill='#dee2e6', width=1)
                draw.text((50, height-55), url[:90], fill='#adb5bd', font=excerpt_font)
                
                # Verified badge
                draw.rectangle([width-150, height-60, width-40, height-35], fill='#0a1f14')
                draw.text((width-140, height-55), "VERIFIED SOURCE", fill='#ffd60a', font=excerpt_font)
                
                img.save(preview_image_path, 'PNG')
            except Exception as render_err:
                print(f"Snapshot render failed: {render_err}")
                preview_method = 'title_card'
        
        # TIER 3: Simple title card fallback
        if preview_method == 'title_card' or not preview_image_path:
            preview_method = 'title_card'
            preview_filename = f"source_card_{uuid.uuid4().hex[:8]}.png"
            preview_image_path = os.path.join('output', preview_filename)
            
            width, height = 600, 300
            img = Image.new('RGB', (width, height), color='#0a1f14')
            draw = ImageDraw.Draw(img)
            
            try:
                title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 24)
                meta_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
            except:
                title_font = ImageFont.load_default()
                meta_font = ImageFont.load_default()
            
            # Source name
            draw.text((30, 30), meta['source'].upper(), fill='#ffd60a', font=meta_font)
            
            # Title
            title_display = (meta['title'] or 'Source Document')[:60]
            draw.text((30, 70), title_display, fill='white', font=title_font)
            
            # Date and URL
            if meta['date']:
                draw.text((30, 120), meta['date'], fill='#adb5bd', font=meta_font)
            draw.text((30, height-40), url[:70], fill='#6c757d', font=meta_font)
            
            img.save(preview_image_path, 'PNG')
        
        # Save to database
        source_doc = SourceDocument.query.filter_by(url=url).first()
        if not source_doc:
            source_doc = SourceDocument(url=url)
        
        source_doc.doc_type = 'pdf' if is_pdf else 'article'
        source_doc.title = meta['title']
        source_doc.author = meta['author']
        source_doc.publisher = meta['source']
        source_doc.publish_date = meta['date']
        source_doc.preview_method = preview_method
        source_doc.preview_image_path = preview_image_path
        source_doc.excerpts = meta['excerpts']
        source_doc.og_image = og_image
        
        db.session.add(source_doc)
        db.session.commit()
        
        return jsonify({
            'ok': True,
            'method': preview_method,
            'image_url': f'/output/{os.path.basename(preview_image_path)}',
            'meta': meta
        })
        
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


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


# === PROJECT & AI LEARNING ENDPOINTS ===

def get_user_id():
    """Get user ID - supports both authenticated users and dev mode."""
    from flask_login import current_user
    if current_user.is_authenticated:
        return current_user.id
    if session.get('dev_mode'):
        return 'dev_user'
    return None


@app.route('/projects', methods=['GET'])
def get_projects():
    """Get all projects for the current user."""
    from models import Project, AILearning, User
    
    user_id = get_user_id()
    if not user_id:
        return jsonify({'projects': [], 'ai_learning': {'learning_progress': 0, 'total_projects': 0, 'successful_projects': 0, 'can_auto_generate': False}})
    
    # Ensure dev user exists
    if user_id == 'dev_user':
        dev_user = User.query.filter_by(id='dev_user').first()
        if not dev_user:
            dev_user = User(id='dev_user', first_name='Developer', tokens=1000)
            db.session.add(dev_user)
            db.session.commit()
    
    projects = Project.query.filter_by(user_id=user_id).order_by(Project.updated_at.desc()).all()
    
    ai_learning = AILearning.query.filter_by(user_id=user_id).first()
    if not ai_learning:
        ai_learning = AILearning(user_id=user_id)
        db.session.add(ai_learning)
        db.session.commit()
    
    return jsonify({
        'projects': [{
            'id': p.id,
            'name': p.name,
            'description': p.description,
            'status': p.status,
            'is_successful': p.is_successful,
            'success_score': p.success_score,
            'created_at': p.created_at.isoformat() if p.created_at else None,
            'updated_at': p.updated_at.isoformat() if p.updated_at else None
        } for p in projects],
        'ai_learning': {
            'total_projects': ai_learning.total_projects,
            'successful_projects': ai_learning.successful_projects,
            'learning_progress': ai_learning.learning_progress,
            'can_auto_generate': ai_learning.can_auto_generate
        }
    })


@app.route('/projects', methods=['POST'])
def create_project():
    """Create a new project."""
    from models import Project, AILearning, User
    
    user_id = get_user_id()
    if not user_id:
        return jsonify({'error': 'Not authenticated'}), 401
    
    # Ensure dev user exists
    if user_id == 'dev_user':
        dev_user = User.query.filter_by(id='dev_user').first()
        if not dev_user:
            dev_user = User(id='dev_user', first_name='Developer', tokens=1000)
            db.session.add(dev_user)
            db.session.commit()
    
    data = request.get_json() or {}
    name = data.get('name', 'Untitled Project')
    description = data.get('description', '')
    
    project = Project(
        user_id=user_id,
        name=name,
        description=description,
        status='draft'
    )
    db.session.add(project)
    
    ai_learning = AILearning.query.filter_by(user_id=user_id).first()
    if ai_learning:
        ai_learning.total_projects += 1
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'project': {
            'id': project.id,
            'name': project.name,
            'status': project.status
        }
    })


@app.route('/projects/<int:project_id>', methods=['GET'])
def get_project(project_id):
    """Get a specific project."""
    from models import Project
    
    user_id = get_user_id()
    if not user_id:
        return jsonify({'error': 'Not authenticated'}), 401
    
    project = Project.query.filter_by(id=project_id, user_id=user_id).first()
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    
    return jsonify({
        'id': project.id,
        'name': project.name,
        'description': project.description,
        'status': project.status,
        'script': project.script,
        'visual_plan': project.visual_plan,
        'voice_assignments': project.voice_assignments,
        'caption_settings': project.caption_settings,
        'video_path': project.video_path,
        'is_successful': project.is_successful,
        'success_score': project.success_score,
        'created_at': project.created_at.isoformat() if project.created_at else None,
        'updated_at': project.updated_at.isoformat() if project.updated_at else None
    })


@app.route('/projects/<int:project_id>', methods=['PUT'])
def update_project(project_id):
    """Update a project."""
    from models import Project
    
    user_id = get_user_id()
    if not user_id:
        return jsonify({'error': 'Not authenticated'}), 401
    
    project = Project.query.filter_by(id=project_id, user_id=user_id).first()
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    
    data = request.get_json() or {}
    
    if 'name' in data:
        project.name = data['name']
    if 'description' in data:
        project.description = data['description']
    if 'status' in data:
        project.status = data['status']
    if 'script' in data:
        project.script = data['script']
    if 'visual_plan' in data:
        project.visual_plan = data['visual_plan']
    if 'voice_assignments' in data:
        project.voice_assignments = data['voice_assignments']
    if 'caption_settings' in data:
        project.caption_settings = data['caption_settings']
    if 'video_path' in data:
        project.video_path = data['video_path']
    
    db.session.commit()
    
    return jsonify({'success': True, 'project_id': project.id})


@app.route('/projects/<int:project_id>/mark-successful', methods=['POST'])
def mark_project_successful(project_id):
    """Mark a project as successful - rewards the AI for learning."""
    from models import Project, AILearning, GlobalPattern
    import json
    
    user_id = get_user_id()
    if not user_id:
        return jsonify({'error': 'Not authenticated'}), 401
    
    project = Project.query.filter_by(id=project_id, user_id=user_id).first()
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    
    data = request.get_json() or {}
    success_score = data.get('score', 1)
    
    project.is_successful = True
    project.success_score = success_score
    project.status = 'completed'
    
    ai_learning = AILearning.query.filter_by(user_id=user_id).first()
    if ai_learning:
        ai_learning.successful_projects += 1
        
        new_progress = min(100, int((ai_learning.successful_projects / max(ai_learning.total_projects, 1)) * 100) + (ai_learning.successful_projects * 5))
        ai_learning.learning_progress = new_progress
        
        if ai_learning.successful_projects >= 5 and ai_learning.learning_progress >= 50:
            ai_learning.can_auto_generate = True
        
        if project.script:
            hooks = ai_learning.learned_hooks or []
            first_line = project.script.split('\n')[0][:100] if project.script else ''
            if first_line and first_line not in hooks:
                hooks.append(first_line)
                ai_learning.learned_hooks = hooks[:20]
        
        if project.voice_assignments:
            voices = ai_learning.learned_voices or []
            for voice in (project.voice_assignments.values() if isinstance(project.voice_assignments, dict) else []):
                if voice and voice not in voices:
                    voices.append(voice)
            ai_learning.learned_voices = voices[:10]
    
    if project.script:
        hook_pattern = GlobalPattern.query.filter_by(pattern_type='hook').first()
        if not hook_pattern:
            hook_pattern = GlobalPattern(pattern_type='hook', pattern_data={'hooks': []})
            db.session.add(hook_pattern)
        hook_pattern.success_count += 1
        hook_pattern.usage_count += 1
        hook_pattern.success_rate = hook_pattern.success_count / max(hook_pattern.usage_count, 1)
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': 'Project marked as successful! AI learning updated.',
        'learning_progress': ai_learning.learning_progress if ai_learning else 0,
        'can_auto_generate': ai_learning.can_auto_generate if ai_learning else False
    })


@app.route('/ai-learning', methods=['GET'])
def get_ai_learning():
    """Get the AI learning progress for the current user."""
    from models import AILearning
    
    user_id = get_user_id()
    if not user_id:
        return jsonify({'learning_progress': 0, 'total_projects': 0, 'successful_projects': 0, 'can_auto_generate': False})
    
    ai_learning = AILearning.query.filter_by(user_id=user_id).first()
    if not ai_learning:
        ai_learning = AILearning(user_id=user_id)
        db.session.add(ai_learning)
        db.session.commit()
    
    return jsonify({
        'total_projects': ai_learning.total_projects,
        'successful_projects': ai_learning.successful_projects,
        'learning_progress': ai_learning.learning_progress,
        'learned_hooks': ai_learning.learned_hooks or [],
        'learned_voices': ai_learning.learned_voices or [],
        'learned_styles': ai_learning.learned_styles or [],
        'learned_topics': ai_learning.learned_topics or [],
        'can_auto_generate': ai_learning.can_auto_generate
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
    """Add cache-busting headers to prevent stale JavaScript."""
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@app.route('/')
def index():
    from flask_login import current_user
    if current_user.is_authenticated:
        return render_template('index.html', user=current_user)
    return render_template('landing.html')

@app.route('/pricing')
def pricing():
    return render_template('pricing.html')

@app.route('/dev')
def dev_mode():
    session['dev_mode'] = True
    return render_template('index.html', user=None, dev_mode=True)

@app.route('/logout')
def logout():
    from flask_login import logout_user
    from flask import redirect
    logout_user()
    session.clear()
    return redirect('/')


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
    
    system_prompt = """You are Krakd — a thinking system that produces post-ready content.

PURPOSE:
Turn ideas into clear, honest, human-feeling video scripts.
Optimize for clarity, integrity, and resonance — never outrage or spectacle.

CORE PHILOSOPHY:
1. Language matters more than volume — say the right thing, not more things
2. Ideas fail when ignored, not when challenged — explain resistance precisely
3. Stability without legitimacy does not last
4. Coexistence is logic, not sentiment — durable outcomes from shared stakes
5. Discourse ≠ politics — reason and explain, don't perform identity theater

BEFORE WRITING (MANDATORY):
1. What is the core claim being made?
2. What is being misunderstood or ignored?
3. Who needs to understand this — and why might they resist?
4. What wording would reduce resistance instead of escalating it?
If unclear, ask ONE concise clarifying question. Then write.

TONE (STRICT):
- Calm, clear, grounded, subtly witty when appropriate, confident without arrogance
- NEVER: sarcastic, smug, preachy, outraged, juvenile, crude, sexual, graphic, meme-brained
- If humor appears, it is sly, intelligent, and brief — never the point
- If content gets graphic: "The story gets graphic here — we're skipping that part."

VIDEO DROPS:
Pull the gold. Skip the filler.
- [CLIP: 00:30-01:15] "money quote here"
- Max 4 clips. State the angle.

SCRIPT FORMAT (PLAIN TEXT SCREENPLAY):

================================================
                    TITLE HERE
================================================

SCENE 1 [3-4s]
EXT. LOCATION - TIME
________________________________________________

[CHARACTER NAME]: Dialogue line goes here. Keep it punchy.

VISUAL: keyword keyword keyword
CUT: wide establishing shot, slow zoom

SCENE 2 [4-5s]
INT. LOCATION - TIME
________________________________________________

[SECOND CHARACTER]: Next dialogue line here.

[CHARACTER NAME]: Response dialogue here.

VISUAL: keyword keyword keyword
CUT: medium shot, static hold


================================================
CHARACTERS: Name1, Name2
VOICES?
================================================

DIALOGUE FORMAT (CRITICAL):
- ALWAYS use [CHARACTER NAME]: dialogue format
- Character names in CAPS inside square brackets
- Dialogue follows the colon on the same line
- This enables automatic voice detection and assignment
- Example: [NEWS ANCHOR]: The market crashed today.
- Example: [WOLF]: Time to buy the dip!

SCENE EDITING RULES:
- Each scene: [Xs] = suggested duration in seconds
- CUT line: shot type (wide/medium/close-up) and motion (static/pan/zoom)
- Action scenes: 2-3s cuts. Emotional scenes: 5-7s holds.
- Total video: 35-75s for shorts format (target 35-45s, max 1:15)

FORMATTING RULES:
- ======= for title/footer bars, _______ under scene headers
- CENTER character names and dialogue
- VISUAL tags centered below dialogue
- NO markdown (no **, no >, no ---)

POLITICAL/SOCIAL RULES:
- Recognize power imbalances — don't flatten dynamics with "both sides" framing
- Critique state policy and dominance structures without demonizing individuals
- A solution is invalid if affected peoples do not accept it
- Ending should be philosophical challenge, not motivational poster

SELF-CORRECTION:
- ERROR A: Generic peace-commercial tone instead of sharp argument
- ERROR B: Flattened power dynamics (treating unequal actors as equal)
- ERROR C: Missing the core logical strike the user intended
- ERROR D: Wrong framing (drifting to secular when spiritual was needed)
- ERROR E: Unrealistic jumps without acknowledging difficulty

If slipping into generic unity language or equal-blame framing, STOP and rewrite.

OUTPUT STANDARD:
- Intentional — every line has a reason
- Restrained — no excess, no padding
- Human-written — natural flow, not model-shaped
- Punchy — clarity without dilution

FAIL CONDITION:
If output could be mistaken for generic social media commentary, activist slogans, empty neutrality, or AI filler — redo it.

Never explain what you're doing. Just write."""

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
        voice_actor_script = None
        if script_ready:
            refined_script = reply
            if "SCRIPT READY:" in reply.upper():
                parts = reply.upper().split("SCRIPT READY:")
                if len(parts) > 1:
                    refined_script = reply[reply.upper().find("SCRIPT READY:") + 13:].strip()
            
            # Extract voice actor script (only dialogue and scene headers)
            voice_actor_script = extract_voice_actor_script(refined_script or reply)
        
        # Parse character lines for multi-character voice generation
        character_lines = []
        characters_detected = []
        if refined_script or reply:
            character_lines = parse_character_lines(refined_script or reply)
            characters_detected = list(set(entry['character'] for entry in character_lines))
        
        return jsonify({
            'success': True,
            'reply': reply,
            'has_question': has_question,
            'script_ready': script_ready,
            'refined_script': refined_script or reply,
            'voice_actor_script': voice_actor_script,
            'character_lines': character_lines,
            'characters_detected': characters_detected,
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

Write a 35-75 second Reel/TikTok script with:
- HOOK: First 3-5 seconds to grab attention (punchy, provocative, or surprising)
- BODY: The main content (25-55 seconds)
- PAYOFF: The ending that makes them think/share (5-10 seconds)

Output as JSON:
{{"hook": "...", "body": "...", "payoff": "...", "duration": "45 seconds", "keywords": ["keyword1", "keyword2", "keyword3"]}}"""

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
    from models import Subscription, User
    from flask_login import current_user
    
    user_id = None
    is_dev_mode = os.environ.get('FLASK_ENV') == 'development' or os.environ.get('DEV_MODE') == 'true'
    
    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('dev_user_id')
    
    # Dev mode (server-side flag): fully free
    if is_dev_mode:
        print("[generate-video] Dev mode - free access")
    else:
        # Check subscription only (free tier deducted in render-video to avoid double-charge)
        sub = Subscription.query.filter_by(user_id=user_id).first() if user_id else None
        user = User.query.get(user_id) if user_id else None
        
        has_active_sub = sub and sub.is_active()
        has_free_generation = user and hasattr(user, 'free_video_generations') and (user.free_video_generations or 0) > 0
        
        if not has_active_sub and not has_free_generation:
            return jsonify({
                'error': 'Pro subscription required',
                'requires_subscription': True,
                'message': 'Video generation requires a Pro subscription ($10/month). Your free generation has been used.'
            }), 403
    
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
        
        # Allowed domains for video downloads (security: prevent SSRF) - only legal sources
        allowed_domains = ['wikimedia.org', 'upload.wikimedia.org', 'commons.wikimedia.org', 'archive.org']
        
        if stock_videos and len(stock_videos) > 0:
            for i, video in enumerate(stock_videos[:5]):
                video_url = video.get('download_url') or video.get('url') or video.get('video_url')
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
            caption_color = captions.get('textColor', captions.get('color', '#FFFFFF')).replace('#', '')
            caption_size = captions.get('size', 'medium')
            caption_weight = captions.get('weight', 'bold')
            caption_outline = captions.get('outline', True)
            caption_shadow = captions.get('shadow', True)
            caption_background = captions.get('background', False)
            caption_uppercase = captions.get('uppercase', False)
            caption_animation = captions.get('animation', 'highlight')
            caption_highlight_color = captions.get('highlightColor', '#FFD60A').replace('#', '')
            
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
            
            # Get video duration for word timing
            import re
            duration_cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', final_video]
            duration_result = subprocess.run(duration_cmd, capture_output=True, text=True, timeout=10)
            try:
                video_duration = float(duration_result.stdout.strip())
            except:
                video_duration = 30.0  # Default fallback
            
            # Clean and split script into words
            clean_script = re.sub(r'[\n\r]+', ' ', script)
            clean_script = re.sub(r'\s+', ' ', clean_script).strip()
            
            if caption_uppercase:
                clean_script = clean_script.upper()
            
            words = clean_script.split()
            
            # Group words into phrases (3-4 words each for readability)
            words_per_group = 4 if caption_animation == 'none' else 3  # Smaller groups for animated captions
            word_groups = []
            for i in range(0, len(words), words_per_group):
                group = words[i:i + words_per_group]
                word_groups.append(group)
            
            # Calculate timing for each word group
            if len(word_groups) > 0:
                time_per_group = video_duration / len(word_groups)
            else:
                time_per_group = video_duration
            
            # Build filter chain with timed word groups
            filter_chain = []
            
            for idx, group_words in enumerate(word_groups):
                start_time = idx * time_per_group
                end_time = (idx + 1) * time_per_group
                
                if caption_animation in ['highlight', 'bounce', 'karaoke'] and len(group_words) > 1:
                    # Word-by-word animation: render each word separately with proper positioning
                    word_duration = time_per_group / len(group_words)
                    
                    # Estimate character width for positioning (approximate)
                    char_width = font_size * 0.5  # Rough estimate
                    space_width = font_size * 0.25
                    
                    # Calculate word widths and total width
                    word_widths = [len(w) * char_width for w in group_words]
                    total_width = sum(word_widths) + (len(group_words) - 1) * space_width
                    
                    for word_idx, word in enumerate(group_words):
                        word_start = start_time + (word_idx * word_duration)
                        word_end = (idx + 1) * time_per_group  # Show until end of group
                        
                        # Sanitize text for ffmpeg
                        safe_word = word.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:").replace("%", "\\%")
                        
                        # Calculate x position for this word (centered as a group)
                        # x_offset: sum of previous word widths + spaces
                        x_offset = sum(word_widths[:word_idx]) + word_idx * space_width
                        
                        # During this word's highlight time, show it in highlight color
                        # Before: show in regular color, After: show in regular color
                        
                        # Each word needs 3 drawtext filters: before highlight, during highlight, after highlight
                        word_highlight_start = start_time + (word_idx * word_duration)
                        word_highlight_end = start_time + ((word_idx + 1) * word_duration)
                        
                        # Helper to add common styling to parts
                        def add_common_styling(parts_list):
                            if caption_outline:
                                parts_list.extend(["borderw=3", "bordercolor=black"])
                            if caption_shadow:
                                parts_list.extend(["shadowcolor=black@0.7", "shadowx=2", "shadowy=2"])
                            if caption_background:
                                parts_list.extend(["box=1", "boxcolor=black@0.6", "boxborderw=5"])
                        
                        # Show word in regular color before its highlight time
                        if word_idx > 0:
                            parts_before = [
                                f"drawtext=text='{safe_word}'",
                                f"fontsize={font_size}",
                                f"fontcolor=#{caption_color}",
                                f"font={font_name}",
                                f"x=(w-{total_width:.0f})/2+{x_offset:.0f}",
                                f"y={y_pos}",
                                f"enable='between(t,{start_time:.2f},{word_highlight_start:.2f})'"
                            ]
                            add_common_styling(parts_before)
                            filter_chain.append(":".join(parts_before))
                        
                        # Show word in HIGHLIGHT color during its time
                        parts_highlight = [
                            f"drawtext=text='{safe_word}'",
                            f"fontsize={font_size}",
                            f"fontcolor=#{caption_highlight_color}",
                            f"font={font_name}",
                            f"x=(w-{total_width:.0f})/2+{x_offset:.0f}",
                            f"y={y_pos}",
                            f"enable='between(t,{word_highlight_start:.2f},{word_highlight_end:.2f})'"
                        ]
                        add_common_styling(parts_highlight)
                        filter_chain.append(":".join(parts_highlight))
                        
                        # Show word in regular color after its highlight time
                        if word_highlight_end < end_time:
                            parts_after = [
                                f"drawtext=text='{safe_word}'",
                                f"fontsize={font_size}",
                                f"fontcolor=#{caption_color}",
                                f"font={font_name}",
                                f"x=(w-{total_width:.0f})/2+{x_offset:.0f}",
                                f"y={y_pos}",
                                f"enable='between(t,{word_highlight_end:.2f},{end_time:.2f})'"
                            ]
                            add_common_styling(parts_after)
                            filter_chain.append(":".join(parts_after))
                else:
                    # No animation - show full group
                    group_text = ' '.join(group_words)
                    safe_text = group_text.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:").replace("%", "\\%")
                    
                    parts = [
                        f"drawtext=text='{safe_text}'",
                        f"fontsize={font_size}",
                        f"fontcolor=#{caption_color}",
                        f"font={font_name}",
                        f"x=(w-text_w)/2",
                        f"y={y_pos}",
                        f"enable='between(t,{start_time:.2f},{end_time:.2f})'"
                    ]
                    
                    if caption_outline:
                        parts.append("borderw=3")
                        parts.append("bordercolor=black")
                    
                    if caption_shadow:
                        parts.append("shadowcolor=black@0.7")
                        parts.append("shadowx=2")
                        parts.append("shadowy=2")
                    
                    if caption_background:
                        parts.append("box=1")
                        parts.append("boxcolor=black@0.6")
                        parts.append("boxborderw=10")
                    
                    filter_chain.append(":".join(parts))
            
            # Combine all drawtext filters
            font_filter = ",".join(filter_chain) if filter_chain else f"drawtext=text='':fontsize={font_size}"
            
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


CHARACTER_VOICE_CONFIG = {
    # ElevenLabs Premade Voices with Persona Names (All Verified IDs)
    'the_anchor': {
        'base_voice': 'onyx',
        'elevenlabs_voice_id': 'pNInz6obpgDQGcFmaJgB',  # Adam - deep, American, narration
        'prompt': "You are a professional news anchor delivering breaking news. Speak with authority, gravitas, and measured pacing. Be serious, credible, and commanding. Use the classic newsroom delivery style."
    },
    'british_authority': {
        'base_voice': 'nova',
        'elevenlabs_voice_id': 'Xb7hH8MSUJpSbSDYk0k2',  # Alice - confident, British, news
        'prompt': "You are a confident British news presenter. Speak with authority, poise, and the gravitas of a seasoned broadcaster. Your delivery is polished and commanding."
    },
    'the_storyteller': {
        'base_voice': 'onyx',
        'elevenlabs_voice_id': 'nPczCjzI2devNBz1zQrb',  # Brian - deep, American, narration
        'prompt': "You are a masterful storyteller with warmth and emotional depth. Speak with perfect pacing, build tension naturally, and let moments land. Your voice makes people feel connected to the narrative."
    },
    'aussie_casual': {
        'base_voice': 'fable',
        'elevenlabs_voice_id': 'IKne3meq5aSn9XLyUdCD',  # Charlie - casual, Australian, conversational
        'prompt': "You are a laid-back Australian narrator with natural charisma. Speak casually but engagingly, like you're sharing an interesting story with a friend. Keep it real and relatable."
    },
    'power_exec': {
        'base_voice': 'nova',
        'elevenlabs_voice_id': 'EXAVITQu4vr4xnSDxMaL',  # Sarah - soft, American, news
        'prompt': "You are a powerful female executive - confident, sharp, no-nonsense. Speak with authority and precision. Every word is deliberate. You command respect and radiate competence."
    },
    'documentary_pro': {
        'base_voice': 'onyx',
        'elevenlabs_voice_id': 'ZQe5CZNOzWyzPSCn5a3c',  # James - calm, Australian, news
        'prompt': "You are a prestigious documentary narrator. Speak with calm authority and gravitas. Deep, measured, thoughtful. Every fact lands with weight. You educate and captivate simultaneously."
    },
    'hype_machine': {
        'base_voice': 'alloy',
        'elevenlabs_voice_id': 'TX3LPaxmHKxFdv7VOQHJ',  # Liam - young, American, narration
        'prompt': "You are an energetic hype machine! Speak with maximum energy, excitement, and urgency. Build hype! Use phrases like 'let's go', 'are you ready', 'this is gonna be huge'. Be the energy the room needs!"
    },
    'cinema_epic': {
        'base_voice': 'onyx',
        'elevenlabs_voice_id': 'JBFqnCBsd6RMkjVDRZzb',  # George - raspy, British, narration
        'prompt': "You are the epic movie trailer voice. Deep, resonant, dramatic. Build tension with pauses. Every line lands like a dramatic reveal. Be EPIC and cinematic!"
    },
    'whisper_intimate': {
        'base_voice': 'shimmer',
        'elevenlabs_voice_id': 'piTKgcLEGmPE4e6mEKli',  # Nicole - whisper, American, audiobook
        'prompt': "You speak in a soft, intimate whisper. Gentle, calming, and deeply personal. Every word is like a secret shared just with the listener. Create a sense of closeness and comfort."
    },
    'zen_guide': {
        'base_voice': 'shimmer',
        'elevenlabs_voice_id': 'LcfcDJNUP1GQjkzn1xUU',  # Emily - calm, American, meditation
        'prompt': "You are a meditation and wellness guide. Speak with serenity, calm, and gentle wisdom. Your voice brings peace and clarity. Guide the listener to a place of inner stillness."
    },
    'warm_narrator': {
        'base_voice': 'nova',
        'elevenlabs_voice_id': 'XrExE9yKIg1WjnnlVkGX',  # Matilda - warm, American, audiobook
        'prompt': "You are a warm, inviting narrator perfect for audiobooks and heartfelt content. Speak with genuine warmth and emotional connection. Make listeners feel at home with your voice."
    },
    'countdown_king': {
        'base_voice': 'echo',
        'elevenlabs_voice_id': 'VR6AewLTigWG4xSOukaG',  # Arnold - crisp, American, narration
        'prompt': "You are the voice of countdown and ranking videos. Build anticipation with each number. Every reveal is exciting. Keep the energy climbing as you count down. Think WatchMojo energy!"
    },
    'custom': {
        'base_voice': 'alloy',
        'elevenlabs_voice_id': 'JBFqnCBsd6RMkjVDRZzb',  # George - versatile narrator
        'prompt': "You are a professional voiceover artist. Read the following script naturally and engagingly with perfect pacing and clarity."
    }
}

ELEVENLABS_VOICE_SETTINGS = {
    'stability': 0.25,
    'similarity_boost': 0.85,
    'style': 0.85,
    'use_speaker_boost': True
}

def get_voice_config(voice):
    """Get base voice, ElevenLabs voice ID, and system prompt for a voice type."""
    if voice in CHARACTER_VOICE_CONFIG:
        config = CHARACTER_VOICE_CONFIG[voice]
        return config['base_voice'], config.get('elevenlabs_voice_id', 'JBFqnCBsd6RMkjVDRZzb'), config['prompt']
    return voice, 'JBFqnCBsd6RMkjVDRZzb', "You are a professional voiceover artist. Read the following script naturally and engagingly."


@app.route('/preview-voice-chars', methods=['POST'])
def preview_voice_chars():
    """Preview how many characters will be sent to voice API - helps user estimate cost."""
    data = request.get_json()
    script = data.get('script', '')
    
    if not script:
        return jsonify({'chars': 0, 'dialogue': ''})
    
    # Extract only the dialogue that would be sent to voice API
    dialogue = extract_dialogue_only(script)
    
    return jsonify({
        'chars': len(dialogue),
        'dialogue': dialogue[:500] + ('...' if len(dialogue) > 500 else ''),  # Preview first 500 chars
        'estimated_cost': f"~{len(dialogue)} characters for ElevenLabs"
    })


@app.route('/estimate-clip-duration', methods=['POST'])
def estimate_clip_duration():
    """Estimate video duration from script - show before visual curation."""
    data = request.get_json()
    script = data.get('script', '')
    
    if not script:
        return jsonify({'duration_seconds': 0, 'duration_display': '0:00', 'word_count': 0})
    
    # Extract dialogue only
    dialogue = extract_dialogue_only(script)
    word_count = len(dialogue.split()) if dialogue else 0
    
    # Estimate: ~2.5 words per second for clear, engaging narration
    # This gives ~150 words per minute
    estimated_seconds = word_count / 2.5
    
    # Format as mm:ss
    minutes = int(estimated_seconds // 60)
    seconds = int(estimated_seconds % 60)
    duration_display = f"{minutes}:{seconds:02d}"
    
    # Check against target range (35-75 seconds)
    status = 'good'
    message = 'Duration looks good!'
    if estimated_seconds < 35:
        status = 'short'
        message = f'Script is short ({duration_display}). Target: 35s-1:15. Consider adding more content.'
    elif estimated_seconds > 75:
        status = 'long'
        message = f'Script is long ({duration_display}). Target: 35s-1:15. Consider trimming.'
    
    return jsonify({
        'duration_seconds': round(estimated_seconds, 1),
        'duration_display': duration_display,
        'word_count': word_count,
        'status': status,
        'message': message
    })


@app.route('/generate-voiceover', methods=['POST'])
def generate_voiceover():
    """Generate voiceover audio from script text using ElevenLabs (primary) or OpenAI (fallback)."""
    import os
    import uuid
    
    data = request.get_json()
    text = data.get('text', '')
    voice = data.get('voice', 'alloy')
    use_elevenlabs = data.get('use_elevenlabs', True)
    
    if not text:
        return jsonify({'error': 'No text provided'}), 400
    
    # Filter out visual directions, stage directions - only keep spoken dialogue
    text = extract_dialogue_only(text)
    if not text:
        return jsonify({'error': 'No dialogue found in script'}), 400
    
    # Get voice config
    base_voice, elevenlabs_voice_id, system_prompt = get_voice_config(voice)
    
    elevenlabs_key = os.environ.get("ELEVENLABS_API_KEY")
    
    # Try ElevenLabs first for premium enthusiastic voices
    if use_elevenlabs and elevenlabs_key:
        try:
            from elevenlabs.client import ElevenLabs
            
            client = ElevenLabs(api_key=elevenlabs_key)
            
            # Generate with enthusiastic, confident voice settings
            audio = client.text_to_speech.convert(
                text=text,
                voice_id=elevenlabs_voice_id,
                model_id="eleven_multilingual_v2",
                output_format="mp3_44100_128",
                voice_settings={
                    "stability": ELEVENLABS_VOICE_SETTINGS['stability'],
                    "similarity_boost": ELEVENLABS_VOICE_SETTINGS['similarity_boost'],
                    "style": ELEVENLABS_VOICE_SETTINGS['style'],
                    "use_speaker_boost": ELEVENLABS_VOICE_SETTINGS['use_speaker_boost']
                }
            )
            
            filename = f"voiceover_{uuid.uuid4().hex[:8]}.mp3"
            filepath = os.path.join(app.config['OUTPUT_FOLDER'], filename)
            
            # Write audio chunks to file
            audio_written = False
            with open(filepath, 'wb') as f:
                for chunk in audio:
                    if isinstance(chunk, bytes):
                        f.write(chunk)
                        audio_written = True
            
            # Verify file was written and has content
            if audio_written and os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                return jsonify({
                    'success': True,
                    'audio_path': filepath,
                    'audio_url': f'/output/{filename}',
                    'duration_estimate': len(text.split()) / 2.5,
                    'engine': 'elevenlabs'
                })
            else:
                print("ElevenLabs produced empty audio, falling back to OpenAI")
                
        except Exception as e:
            print(f"ElevenLabs error, falling back to OpenAI: {e}")
    
    # Fallback to OpenAI TTS
    try:
        from openai import OpenAI
        
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        
        response = client.audio.speech.create(
            model="tts-1-hd",
            voice=base_voice,
            input=text,
            speed=1.25
        )
        
        filename = f"voiceover_{uuid.uuid4().hex[:8]}.mp3"
        filepath = os.path.join(app.config['OUTPUT_FOLDER'], filename)
        response.stream_to_file(filepath)
        
        return jsonify({
            'success': True,
            'audio_path': filepath,
            'audio_url': f'/output/{filename}',
            'duration_estimate': len(text.split()) / 2.5,
            'engine': 'openai'
        })
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/preview-voice', methods=['POST'])
def preview_voice():
    """Generate a short voice preview sample using ElevenLabs (primary) or OpenAI (fallback)."""
    import uuid
    
    data = request.get_json()
    text = data.get('text', '')
    voice = data.get('voice', 'alloy')
    
    if not text:
        return jsonify({'error': 'No text provided'}), 400
    
    # Get voice config
    base_voice, elevenlabs_voice_id, system_prompt = get_voice_config(voice)
    
    elevenlabs_key = os.environ.get("ELEVENLABS_API_KEY")
    
    # Try ElevenLabs first
    if elevenlabs_key:
        try:
            from elevenlabs.client import ElevenLabs
            
            client = ElevenLabs(api_key=elevenlabs_key)
            
            audio = client.text_to_speech.convert(
                text=text,
                voice_id=elevenlabs_voice_id,
                model_id="eleven_flash_v2_5",  # Fast model for preview
                output_format="mp3_44100_128",
                voice_settings={
                    "stability": ELEVENLABS_VOICE_SETTINGS['stability'],
                    "similarity_boost": ELEVENLABS_VOICE_SETTINGS['similarity_boost'],
                    "style": ELEVENLABS_VOICE_SETTINGS['style'],
                    "use_speaker_boost": ELEVENLABS_VOICE_SETTINGS['use_speaker_boost']
                }
            )
            
            filename = f"preview_{voice}_{uuid.uuid4().hex[:6]}.mp3"
            filepath = os.path.join(app.config['OUTPUT_FOLDER'], filename)
            
            audio_written = False
            with open(filepath, 'wb') as f:
                for chunk in audio:
                    if isinstance(chunk, bytes):
                        f.write(chunk)
                        audio_written = True
            
            # Verify file was written and has content
            if audio_written and os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                return jsonify({
                    'success': True,
                    'audio_url': f'/output/{filename}',
                    'engine': 'elevenlabs'
                })
            else:
                print("ElevenLabs preview produced empty audio, falling back to OpenAI")
                
        except Exception as e:
            print(f"ElevenLabs preview error, falling back to OpenAI: {e}")
    
    # Fallback to OpenAI
    try:
        from openai import OpenAI
        
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        
        response = client.audio.speech.create(
            model="tts-1",
            voice=base_voice,
            input=text,
            speed=1.25
        )
        
        filename = f"preview_{voice}_{uuid.uuid4().hex[:6]}.mp3"
        filepath = os.path.join(app.config['OUTPUT_FOLDER'], filename)
        response.stream_to_file(filepath)
        
        return jsonify({
            'success': True,
            'audio_url': f'/output/{filename}'
        })
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/generate-multi-character-voiceover', methods=['POST'])
def generate_multi_character_voiceover():
    """
    Generate voiceover for multi-character script.
    Each character's lines are generated separately with their assigned voice,
    then assembled in script order.
    """
    from openai import OpenAI
    import uuid
    
    data = request.get_json()
    script = data.get('script', '')
    voice_assignments = data.get('voice_assignments', {})
    
    if not script:
        return jsonify({'error': 'No script provided'}), 400
    
    # Parse character lines from script
    character_lines = parse_character_lines(script)
    
    if not character_lines:
        return jsonify({'error': 'No character dialogue found in script'}), 400
    
    # Use OpenAI for audio generation
    client = OpenAI(
        api_key=os.environ.get("OPENAI_API_KEY")
    )
    
    clip_paths = []
    clip_info = []
    
    try:
        for entry in character_lines:
            character = entry['character']
            line = entry['line']
            order = entry['order']
            
            # Get assigned voice for this character
            # Try exact match first, then normalized character name
            voice_key = voice_assignments.get(character) or voice_assignments.get(character.upper())
            if not voice_key:
                # Default to 'alloy' for unassigned characters
                base_voice = 'alloy'
                elevenlabs_voice_id = 'JBFqnCBsd6RMkjVDRZzb'
            else:
                base_voice, elevenlabs_voice_id, _ = get_voice_config(voice_key)
            
            # Try ElevenLabs first
            elevenlabs_key = os.environ.get("ELEVENLABS_API_KEY")
            generated = False
            
            if elevenlabs_key:
                try:
                    from elevenlabs.client import ElevenLabs as ElevenLabsClient
                    
                    el_client = ElevenLabsClient(api_key=elevenlabs_key)
                    audio = el_client.text_to_speech.convert(
                        text=line,
                        voice_id=elevenlabs_voice_id,
                        model_id="eleven_multilingual_v2",
                        output_format="mp3_44100_128",
                        voice_settings={
                            "stability": ELEVENLABS_VOICE_SETTINGS['stability'],
                            "similarity_boost": ELEVENLABS_VOICE_SETTINGS['similarity_boost'],
                            "style": ELEVENLABS_VOICE_SETTINGS['style'],
                            "use_speaker_boost": ELEVENLABS_VOICE_SETTINGS['use_speaker_boost']
                        }
                    )
                    
                    clip_filename = f"clip_{order}_{uuid.uuid4().hex[:6]}.mp3"
                    clip_filepath = os.path.join(app.config['OUTPUT_FOLDER'], clip_filename)
                    
                    with open(clip_filepath, 'wb') as f:
                        for chunk in audio:
                            if isinstance(chunk, bytes):
                                f.write(chunk)
                    
                    clip_paths.append(clip_filepath)
                    clip_info.append({
                        'character': character,
                        'line': line,
                        'order': order,
                        'voice': voice_key,
                        'clip_url': f'/output/{clip_filename}',
                        'engine': 'elevenlabs'
                    })
                    generated = True
                except Exception as e:
                    print(f"ElevenLabs multi-char error: {e}")
            
            # Fallback to OpenAI
            if not generated:
                response = client.audio.speech.create(
                    model="tts-1-hd",
                    voice=base_voice,
                    input=line,
                    speed=1.25
                )
                
                # Save individual clip
                clip_filename = f"clip_{order}_{uuid.uuid4().hex[:6]}.mp3"
                clip_filepath = os.path.join(app.config['OUTPUT_FOLDER'], clip_filename)
                response.stream_to_file(clip_filepath)
                
                clip_paths.append(clip_filepath)
                clip_info.append({
                    'character': character,
                    'line': line,
                    'order': order,
                    'voice': voice_key,
                    'clip_url': f'/output/{clip_filename}',
                    'engine': 'openai'
                })
        
        # Assemble all clips into final audio
        final_filename = f"voiceover_multi_{uuid.uuid4().hex[:8]}.mp3"
        final_filepath = os.path.join(app.config['OUTPUT_FOLDER'], final_filename)
        
        assembled = assemble_audio_clips(clip_paths, final_filepath)
        
        if not assembled:
            return jsonify({'error': 'Failed to assemble audio clips'}), 500
        
        # Calculate duration estimate
        total_words = sum(len(entry['line'].split()) for entry in character_lines)
        
        return jsonify({
            'success': True,
            'audio_url': f'/output/{final_filename}',
            'audio_path': final_filepath,
            'clips': clip_info,
            'characters_detected': list(set(e['character'] for e in character_lines)),
            'duration_estimate': total_words / 2.5
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/extract-character-lines', methods=['POST'])
def extract_character_lines_endpoint():
    """
    Extract character lines from a script for preview/editing.
    Returns parsed lines showing which character says what.
    """
    data = request.get_json()
    script = data.get('script', '')
    
    if not script:
        return jsonify({'error': 'No script provided'}), 400
    
    character_lines = parse_character_lines(script)
    
    # Group lines by character for UI display
    by_character = {}
    for entry in character_lines:
        char = entry['character']
        if char not in by_character:
            by_character[char] = []
        by_character[char].append({
            'line': entry['line'],
            'order': entry['order']
        })
    
    return jsonify({
        'success': True,
        'lines': character_lines,
        'by_character': by_character,
        'characters': list(by_character.keys())
    })


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
        
        content = response.choices[0].message.content
        
        # Try to parse JSON, with fallback for malformed responses
        try:
            result = json.loads(content)
        except json.JSONDecodeError:
            # Try to extract JSON from response
            import re
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                try:
                    result = json.loads(json_match.group())
                except:
                    result = {"characters": [{"name": "NARRATOR", "personality": "Calm, clear", "sample_line": "Narration..."}]}
            else:
                result = {"characters": [{"name": "NARRATOR", "personality": "Calm, clear", "sample_line": "Narration..."}]}
        
        characters = result.get('characters', [])
        
        # Default to NARRATOR if no characters detected
        if not characters:
            characters = [{"name": "NARRATOR", "personality": "Calm, authoritative", "sample_line": "Let me tell you a story..."}]
        
        # Sanitize character data to prevent JS errors
        for char in characters:
            if 'sample_line' in char:
                char['sample_line'] = char['sample_line'].replace("'", "\\'").replace('"', '\\"')[:100]
        
        return jsonify({'success': True, 'characters': characters})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/generate-stage-directions', methods=['POST'])
def generate_stage_directions():
    """Generate stage directions from a script using AI."""
    from openai import OpenAI
    import os
    
    data = request.get_json()
    script = data.get('script', '')
    
    if not script:
        return jsonify({'success': False, 'error': 'No script provided'}), 400
    
    client = OpenAI(
        api_key=os.environ.get("XAI_API_KEY"),
        base_url="https://api.x.ai/v1"
    )
    
    prompt = f"""Analyze this script and generate stage directions (audio effects, pauses, transitions).

SCRIPT:
{script}

Generate stage directions using these formats:

TIMING:
- [PAUSE 1s] - silence/pause for specified duration
- [BEAT] - short dramatic pause (0.5s)
- [SILENCE 2s] - extended silence
- [TRANSITION] - scene change (1s pause)

SOUND EFFECTS (auto-generated and mixed into final video):
- [SOUND: whoosh] - transition swoosh
- [SOUND: impact] - deep bass hit for emphasis
- [SOUND: tension] - suspenseful rising drone
- [SOUND: reveal] - bright discovery chime
- [SOUND: alarm] - alert/warning tone
- [SOUND: heartbeat] - rhythmic pulse
- [SOUND: static] - radio interference
- [SOUND: beep] - notification ping
- [SOUND: rumble] - low rumble/thunder
- [SOUND: wind] - ambient atmosphere

Add duration: [SOUND: tension 2s] for longer effects.

Rules:
1. Place SFX at key emotional moments (reveals, transitions, emphasis)
2. 3-6 sound effects per script is ideal - don't overdo it
3. Match the script's tone (tension vs reveal, impact vs whoosh)
4. SFX are automatically generated and mixed into the final video

Output ONLY the stage directions, one per line, in order of appearance.
Include a brief note about where each should occur."""
    
    try:
        response = client.chat.completions.create(
            model="grok-3",
            messages=[
                {"role": "system", "content": "You are an audio director for short-form video content."},
                {"role": "user", "content": prompt}
            ],
            max_completion_tokens=1024
        )
        
        directions = response.choices[0].message.content or ""
        return jsonify({'success': True, 'directions': directions})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/generate-voiceover-multi', methods=['POST'])
def generate_voiceover_multi():
    """Generate voiceover with multiple character voices and stage directions."""
    from openai import OpenAI
    import base64
    import uuid
    from pydub import AudioSegment
    import io
    import re as regex
    
    data = request.get_json()
    script = data.get('script', '')
    character_voices = data.get('character_voices', {})
    stage_directions = data.get('stage_directions', '')
    
    if not script:
        return jsonify({'error': 'No script provided'}), 400
    
    # Use OpenAI for audio generation - direct API with user's OpenAI key
    client = OpenAI(
        api_key=os.environ.get("OPENAI_API_KEY")
    )
    
    try:
        import re
        
        # Parse script into character lines - ONLY formatted dialogue lines
        lines = []
        in_script = False
        
        # AI commentary patterns - ONLY applied before script starts
        ai_meta_patterns = [
            r'^Understood', r'^I\'ll create', r'^Here\'s', r'^Let me create',
            r'^This script', r'^The script', r'^I\'ve', r'^I can create',
            r'^Let me know', r'^Would you like', r'^The message',
            r'^exaggerated personas', r'^With voices', r'^I hope this',
        ]
        
        for line in script.split('\n'):
            line = line.strip()
            if not line:
                continue
            
            # Detect when actual script starts (SCENE, [CHARACTER]:, or CHARACTER:)
            if re.match(r'^SCENE\s+\d+', line, re.IGNORECASE):
                in_script = True
                continue  # Skip scene header
            if re.match(r'^\[.+\]:', line) or re.match(r'^[A-Z][A-Z\-]+:', line):
                in_script = True
            
            # Before script: skip AI commentary and long prose
            if not in_script:
                if any(re.match(p, line, re.IGNORECASE) for p in ai_meta_patterns):
                    continue
                if len(line) > 80:
                    continue
            
            # Skip direction headers (always)
            if line.startswith('[VISUAL') or line.startswith('[CUT') or line.startswith('[FADE'):
                continue
            if line.startswith('VISUAL:') or line.startswith('CUT:'):
                continue
            if re.match(r'^(INT\.|EXT\.|TITLE:|CUT TO)', line):
                continue
            
            # Skip all-caps location lines
            if re.match(r'^[A-Z\s\-]+$', line) and len(line) < 50 and ':' not in line:
                continue
            
            # Pattern 1: [CHARACTER]: dialogue (brackets)
            match1 = re.match(r'^\[([^\]]+)\]:\s*(.+)$', line)
            if match1:
                char_name = match1.group(1).strip().upper()
                dialogue = match1.group(2).strip()
                dialogue = re.sub(r'\([^)]*\)', '', dialogue).strip()
                if dialogue:
                    lines.append({'character': char_name, 'text': dialogue})
                continue
            
            # Pattern 2: CHARACTER: dialogue (no brackets)
            match2 = re.match(r'^([A-Za-z][A-Za-z0-9\-\.\'\s]{0,25}):\s*(.+)$', line)
            if match2:
                char_name = match2.group(1).strip().upper()
                dialogue = match2.group(2).strip()
                if char_name in ['SCENE', 'VISUAL', 'CUT', 'FADE', 'INT', 'EXT', 'TITLE', 'CHARACTERS', 'VOICES']:
                    continue
                dialogue = re.sub(r'\([^)]*\)', '', dialogue).strip()
                if dialogue:
                    lines.append({'character': char_name, 'text': dialogue})
                continue
        
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
            
            # Get voice config for this character
            base_voice, elevenlabs_voice_id, _ = get_voice_config(voice)
            
            # Try ElevenLabs first for premium voices
            elevenlabs_key = os.environ.get("ELEVENLABS_API_KEY")
            audio_bytes = None
            
            if elevenlabs_key:
                try:
                    from elevenlabs.client import ElevenLabs as ElevenLabsClient
                    
                    el_client = ElevenLabsClient(api_key=elevenlabs_key)
                    audio = el_client.text_to_speech.convert(
                        text=text,
                        voice_id=elevenlabs_voice_id,
                        model_id="eleven_multilingual_v2",
                        output_format="mp3_44100_128",
                        voice_settings={
                            "stability": ELEVENLABS_VOICE_SETTINGS['stability'],
                            "similarity_boost": ELEVENLABS_VOICE_SETTINGS['similarity_boost'],
                            "style": ELEVENLABS_VOICE_SETTINGS['style'],
                            "use_speaker_boost": ELEVENLABS_VOICE_SETTINGS['use_speaker_boost']
                        }
                    )
                    
                    # Collect bytes from generator
                    audio_bytes = b''
                    for chunk in audio:
                        if isinstance(chunk, bytes):
                            audio_bytes += chunk
                except Exception as e:
                    print(f"ElevenLabs multi error: {e}")
            
            # Fallback to OpenAI TTS
            if not audio_bytes:
                response = client.audio.speech.create(
                    model="tts-1-hd",
                    voice=base_voice,
                    input=text,
                    speed=1.25
                )
                audio_bytes = response.content
            
            audio_segments.append(audio_bytes)
        
        # Parse stage directions to extract timing effects
        def parse_stage_directions(directions_text):
            """Parse stage directions into actionable effects."""
            effects = []
            if not directions_text:
                return effects
            
            for line in directions_text.split('\n'):
                line = line.strip()
                if not line:
                    continue
                
                # Parse [PAUSE Xs] - e.g., [PAUSE 1s], [PAUSE 2s]
                pause_match = regex.search(r'\[PAUSE\s*(\d+(?:\.\d+)?)\s*s?\]', line, regex.IGNORECASE)
                if pause_match:
                    effects.append({'type': 'pause', 'duration': float(pause_match.group(1)) * 1000})
                    continue
                
                # Parse [BEAT] - short dramatic pause (500ms)
                if '[BEAT]' in line.upper():
                    effects.append({'type': 'pause', 'duration': 500})
                    continue
                
                # Parse [SILENCE Xs]
                silence_match = regex.search(r'\[SILENCE\s*(\d+(?:\.\d+)?)\s*s?\]', line, regex.IGNORECASE)
                if silence_match:
                    effects.append({'type': 'pause', 'duration': float(silence_match.group(1)) * 1000})
                    continue
                
                # Parse [TRANSITION] - 1 second pause
                if '[TRANSITION]' in line.upper():
                    effects.append({'type': 'pause', 'duration': 1000})
                    continue
            
            return effects
        
        direction_effects = parse_stage_directions(stage_directions)
        
        # Combine all audio segments with stage direction effects
        if audio_segments:
            combined = AudioSegment.empty()
            effect_index = 0
            
            for i, seg_bytes in enumerate(audio_segments):
                seg = AudioSegment.from_mp3(io.BytesIO(seg_bytes))
                combined += seg
                
                # Add standard pause between lines
                pause_duration = 300
                
                # Apply stage direction effect if available
                if effect_index < len(direction_effects):
                    effect = direction_effects[effect_index]
                    if effect['type'] == 'pause':
                        pause_duration = max(pause_duration, int(effect['duration']))
                    effect_index += 1
                
                combined += AudioSegment.silent(duration=pause_duration)
            
            # Boost volume by 6dB for louder, clearer audio
            combined = combined + 6
            
            filename = f"voiceover_multi_{uuid.uuid4().hex[:8]}.mp3"
            filepath = os.path.join(app.config['OUTPUT_FOLDER'], filename)
            combined.export(filepath, format='mp3', bitrate='192k')
            
            return jsonify({
                'success': True,
                'audio_url': f'/output/{filename}',
                'audio_path': filepath,
                'segments': len(audio_segments),
                'effects_applied': len(direction_effects)
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
    """Direct chat with Krakd AI with conversation memory and unified content engine."""
    from openai import OpenAI
    from context_engine import save_conversation, build_personalized_prompt, get_source_learning_context
    from flask_login import current_user
    import os
    
    data = request.get_json()
    message = data.get('message')
    conversation = data.get('conversation', [])
    use_unified_engine = data.get('use_unified_engine', False)
    mode = data.get('mode', 'auto')
    
    user_id = current_user.id if current_user.is_authenticated else 'dev_user'
    
    if not message:
        return jsonify({'error': 'No message provided'}), 400
    
    if use_unified_engine:
        try:
            result = unified_content_engine(message, user_id, mode)
            return jsonify({
                'success': True,
                'unified_result': result,
                'mode': result.get('mode', 'create')
            })
        except Exception as e:
            logging.error(f"Unified engine error in chat: {e}")
    
    client = OpenAI(
        api_key=os.environ.get("XAI_API_KEY"),
        base_url="https://api.x.ai/v1"
    )
    
    source_learning = get_source_learning_context(user_id)
    
    system_prompt = """You are Krakd — a unified thinking and clipping system.

PURPOSE:
Turn ideas, transcripts, or source material into clear, honest, human-feeling content.
You can BOTH create from ideas AND clip from source material.

THESIS-DRIVEN ARCHITECTURE:
Every piece of content you create must serve ONE CORE THESIS.
Before generating anything, identify or confirm the thesis.
If the user's input is unclear, ask ONE clarifying question about their core claim.

ANCHOR-BASED SCRIPTS:
Structure arguments around ANCHOR POINTS:
- HOOK: First statement that grabs attention
- CLAIM: Direct assertions supporting thesis
- EVIDENCE: Facts or examples proving claims
- PIVOT: Transitions to new supporting points
- CLOSER: Final statement reinforcing thesis

THOUGHT-CHANGE CLIPPING:
When analyzing content for clips:
- Identify where ideas shift
- Only recommend cuts that IMPROVE clarity or retention
- If continuous flow works better, keep it continuous

MODES:
1. CREATE MODE: User gives idea → You extract thesis → Generate anchor-based script
2. CLIP MODE: User gives transcript/source → You find thesis → Suggest clips at thought-changes

CORE PHILOSOPHY:
1. Language matters more than volume — say the right thing, not more things
2. Ideas fail when ignored, not when challenged — explain resistance precisely
3. Coexistence is logic, not sentiment — durable outcomes from shared stakes

TONE (STRICT):
- Calm, clear, grounded, subtly witty when appropriate, confident without arrogance
- NEVER: sarcastic, smug, preachy, outraged, juvenile, crude, sexual, graphic

SCRIPT FORMAT:
- INT./EXT. scene headings, CHARACTER NAMES in caps, no markdown
- Include [VISUAL: description] notes for B-roll throughout
- Every line serves the thesis
- Ending closes the loop

OUTPUT STANDARD:
- Intentional — every line has a reason
- Restrained — no excess, no padding
- Human-written — natural flow
- Punchy — clarity without dilution

Never explain what you're doing. Just write."""

    if source_learning:
        system_prompt += f"\n\n{source_learning}"

    personalized_prompt = build_personalized_prompt(user_id, system_prompt)
    
    messages = [{"role": "system", "content": personalized_prompt}]
    messages.extend(conversation)
    messages.append({"role": "user", "content": message})
    
    save_conversation(user_id, 'user', message)
    
    try:
        response = client.chat.completions.create(
            model="grok-3",
            messages=messages,
            max_tokens=2048
        )
        
        reply = response.choices[0].message.content or ""
        
        save_conversation(user_id, 'assistant', reply)
        
        return jsonify({
            'success': True,
            'reply': reply,
            'conversation': messages + [{"role": "assistant", "content": reply}]
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/unified-engine', methods=['POST'])
def unified_engine():
    """
    Unified content engine - handles both creation and clipping in one interface.
    Automatically detects mode from input, or accepts explicit mode parameter.
    """
    from flask_login import current_user
    from models import SourceContent, ProjectThesis, ScriptAnchor, ThoughtChange, Project
    
    data = request.get_json()
    user_input = data.get('input', '')
    mode = data.get('mode', 'auto')
    project_id = data.get('project_id')
    
    user_id = current_user.id if current_user.is_authenticated else 'dev_user'
    
    if not user_input:
        return jsonify({'error': 'No input provided'}), 400
    
    try:
        result = unified_content_engine(user_input, user_id, mode)
        
        if result.get('status') == 'ready':
            if result.get('mode') == 'clip':
                source = SourceContent(
                    user_id=user_id,
                    content_type='transcript',
                    transcript=user_input[:10000],
                    extracted_thesis=result.get('result', {}).get('thesis', {}).get('thesis_statement'),
                    extracted_anchors=result.get('result', {}).get('recommended_clips', []),
                    learned_hooks=result.get('result', {}).get('learnings', {}).get('learned_hooks'),
                    learned_pacing=result.get('result', {}).get('learnings', {}).get('learned_pacing'),
                    learned_structure=result.get('result', {}).get('learnings', {}).get('learned_structure'),
                    learned_style=result.get('result', {}).get('learnings', {}).get('learned_style')
                )
                db.session.add(source)
                db.session.commit()
                result['source_id'] = source.id
            
            elif result.get('mode') == 'create' and project_id:
                thesis_data = result.get('thesis', {})
                thesis = ProjectThesis(
                    project_id=project_id,
                    user_id=user_id,
                    thesis_statement=thesis_data.get('thesis_statement', ''),
                    thesis_type=thesis_data.get('thesis_type'),
                    core_claim=thesis_data.get('core_claim'),
                    target_audience=thesis_data.get('target_audience'),
                    intended_impact=thesis_data.get('intended_impact'),
                    confidence_score=thesis_data.get('confidence', 1.0)
                )
                db.session.add(thesis)
                
                for i, anchor in enumerate(result.get('anchors', [])):
                    if isinstance(anchor, dict):
                        anchor_obj = ScriptAnchor(
                            project_id=project_id,
                            anchor_text=anchor.get('anchor_text', ''),
                            anchor_type=anchor.get('anchor_type', 'CLAIM'),
                            position=anchor.get('position', i),
                            supports_thesis=anchor.get('supports_thesis', True),
                            is_hook=anchor.get('is_hook', False),
                            is_closer=anchor.get('is_closer', False),
                            visual_intent=anchor.get('visual_intent'),
                            emotional_beat=anchor.get('emotional_beat')
                        )
                        db.session.add(anchor_obj)
                
                for tc in result.get('thought_changes', []):
                    if isinstance(tc, dict):
                        tc_obj = ThoughtChange(
                            project_id=project_id,
                            position=tc.get('position', 0),
                            from_idea=tc.get('from_idea'),
                            to_idea=tc.get('to_idea'),
                            transition_type=tc.get('transition_type', 'pivot'),
                            should_clip=tc.get('should_clip', False),
                            clip_reasoning=tc.get('clip_reasoning'),
                            clarity_improvement=tc.get('clarity_improvement'),
                            retention_improvement=tc.get('retention_improvement')
                        )
                        db.session.add(tc_obj)
                
                db.session.commit()
        
        return jsonify({
            'success': True,
            'result': result
        })
    except Exception as e:
        db.session.rollback()
        logging.error(f"Unified engine error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/extract-thesis', methods=['POST'])
def api_extract_thesis():
    """Extract thesis from content."""
    data = request.get_json()
    content = data.get('content', '')
    content_type = data.get('content_type', 'idea')
    
    if not content:
        return jsonify({'error': 'No content provided'}), 400
    
    try:
        thesis = extract_thesis(content, content_type)
        return jsonify({
            'success': True,
            'thesis': thesis
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/identify-anchors', methods=['POST'])
def api_identify_anchors():
    """Identify anchor points in a script."""
    data = request.get_json()
    script = data.get('script', '')
    thesis = data.get('thesis', '')
    
    if not script or not thesis:
        return jsonify({'error': 'Script and thesis required'}), 400
    
    try:
        anchors = identify_anchors(script, thesis)
        return jsonify({
            'success': True,
            'anchors': anchors
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/detect-thought-changes', methods=['POST'])
def api_detect_thought_changes():
    """Detect thought transitions in content."""
    data = request.get_json()
    content = data.get('content', '')
    content_type = data.get('content_type', 'script')
    
    if not content:
        return jsonify({'error': 'No content provided'}), 400
    
    try:
        changes = detect_thought_changes(content, content_type)
        return jsonify({
            'success': True,
            'thought_changes': changes
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/clip-source', methods=['POST'])
def clip_source():
    """Process source material for intelligent clipping."""
    from flask_login import current_user
    from models import SourceContent
    
    data = request.get_json()
    transcript = data.get('transcript', '')
    source_url = data.get('source_url')
    
    user_id = current_user.id if current_user.is_authenticated else 'dev_user'
    
    if not transcript:
        return jsonify({'error': 'No transcript provided'}), 400
    
    try:
        result = process_source_for_clipping(transcript, source_url)
        
        if result.get('status') == 'ready':
            learnings = learn_from_source_content(transcript, result.get('recommended_clips', []))
            
            source = SourceContent(
                user_id=user_id,
                content_type='transcript',
                source_url=source_url,
                transcript=transcript[:10000],
                extracted_thesis=result.get('thesis', {}).get('thesis_statement'),
                extracted_anchors=result.get('recommended_clips', []),
                extracted_thought_changes=result.get('thought_changes', []),
                learned_hooks=learnings.get('learned_hooks'),
                learned_pacing=learnings.get('learned_pacing'),
                learned_structure=learnings.get('learned_structure'),
                learned_style=learnings.get('learned_style'),
                clips_generated=len(result.get('recommended_clips', [])),
                quality_score=result.get('overall_quality')
            )
            db.session.add(source)
            db.session.commit()
            
            result['source_id'] = source.id
            result['learnings'] = learnings
        
        return jsonify({
            'success': True,
            'result': result
        })
    except Exception as e:
        db.session.rollback()
        logging.error(f"Clip source error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/generate-thesis-script', methods=['POST'])
def api_generate_thesis_script():
    """Generate a thesis-driven script."""
    from flask_login import current_user
    from context_engine import get_user_context, get_source_learning_context
    from models import SourceContent
    
    data = request.get_json()
    thesis = data.get('thesis', {})
    
    user_id = current_user.id if current_user.is_authenticated else 'dev_user'
    
    if not thesis or not thesis.get('thesis_statement'):
        return jsonify({'error': 'Thesis statement required'}), 400
    
    try:
        user_context = get_user_context(user_id)
        source_learning = get_source_learning_context(user_id)
        full_context = f"{user_context}\n\n{source_learning}" if source_learning else user_context
        
        learned_patterns = {}
        try:
            sources = SourceContent.query.filter_by(user_id=user_id).limit(5).all()
            for src in sources:
                if src.learned_hooks:
                    learned_patterns['hooks'] = src.learned_hooks
                if src.learned_pacing:
                    learned_patterns['pacing'] = src.learned_pacing
                if src.learned_structure:
                    learned_patterns['structure'] = src.learned_structure
                if src.learned_style:
                    learned_patterns['style'] = src.learned_style
        except:
            pass
        
        script = generate_thesis_driven_script(thesis, full_context, learned_patterns)
        anchors = identify_anchors(script.get('full_script', ''), thesis.get('thesis_statement', ''))
        thought_changes = detect_thought_changes(script.get('full_script', ''))
        
        return jsonify({
            'success': True,
            'script': script,
            'anchors': anchors,
            'thought_changes': thought_changes,
            'learned_patterns_applied': bool(learned_patterns)
        })
    except Exception as e:
        logging.error(f"Generate thesis script error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/get-source-learnings', methods=['GET'])
def get_source_learnings():
    """Get accumulated learnings from all clipped content."""
    from flask_login import current_user
    from models import SourceContent
    
    user_id = current_user.id if current_user.is_authenticated else 'dev_user'
    
    try:
        sources = SourceContent.query.filter_by(user_id=user_id).order_by(
            SourceContent.created_at.desc()
        ).limit(20).all()
        
        learnings = {
            'total_sources': len(sources),
            'total_clips_generated': sum(s.clips_generated or 0 for s in sources),
            'hooks': [],
            'pacing': None,
            'structure': None,
            'style': None
        }
        
        for src in sources:
            if src.learned_hooks:
                if isinstance(src.learned_hooks, list):
                    learnings['hooks'].extend(src.learned_hooks)
                else:
                    learnings['hooks'].append(src.learned_hooks)
            if src.learned_pacing and not learnings['pacing']:
                learnings['pacing'] = src.learned_pacing
            if src.learned_structure and not learnings['structure']:
                learnings['structure'] = src.learned_structure
            if src.learned_style and not learnings['style']:
                learnings['style'] = src.learned_style
        
        if learnings['hooks']:
            learnings['hooks'] = sorted(
                [h for h in learnings['hooks'] if isinstance(h, dict)],
                key=lambda x: x.get('effectiveness', 0),
                reverse=True
            )[:5]
        
        return jsonify({
            'success': True,
            'learnings': learnings
        })
    except Exception as e:
        logging.error(f"Get source learnings error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/classify-content', methods=['POST'])
def classify_content():
    """Classify content type and generate visual plan."""
    from context_engine import classify_content_type, generate_visual_plan, identify_anchors
    
    data = request.get_json()
    script = data.get('script', '')
    thesis = data.get('thesis', '')
    
    if not script:
        return jsonify({'error': 'No script provided'}), 400
    
    try:
        # Get anchors for the script
        anchors = identify_anchors(script, thesis)
        
        # Generate full visual plan
        visual_plan = generate_visual_plan(script, thesis, anchors)
        
        return jsonify({
            'success': True,
            'classification': visual_plan.get('classification', {}),
            'content_type': visual_plan.get('classification', {}).get('content_type', 'informative'),
            'layers': visual_plan.get('layers', {}),
            'assets': visual_plan.get('assets', {}),
            'anchors': anchors
        })
    except Exception as e:
        logging.error(f"Content classification error: {e}")
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


@app.route('/render-video', methods=['POST'])
def render_video():
    """Render final video from selected scenes and voiceover."""
    import subprocess
    import uuid
    import urllib.request
    from models import Subscription, User
    from flask_login import current_user
    
    user_id = None
    is_dev_mode = os.environ.get('FLASK_ENV') == 'development' or os.environ.get('DEV_MODE') == 'true'
    
    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('dev_user_id')
    
    # Dev mode (server-side flag): fully free
    if is_dev_mode:
        print("[render-video] Dev mode - free access")
    else:
        # Check subscription or free tier
        sub = Subscription.query.filter_by(user_id=user_id).first() if user_id else None
        user = User.query.get(user_id) if user_id else None
        
        has_active_sub = sub and sub.is_active()
        has_free_generation = user and hasattr(user, 'free_video_generations') and (user.free_video_generations or 0) > 0
        
        if not has_active_sub and not has_free_generation:
            return jsonify({
                'error': 'Pro subscription required',
                'requires_subscription': True,
                'message': 'Video rendering requires a Pro subscription ($10/month). Your free generation has been used.'
            }), 403
        
        # Deduct free generation if using free tier (only here to avoid double-charge)
        if not has_active_sub and has_free_generation:
            user.free_video_generations = max(0, (user.free_video_generations or 1) - 1)
            db.session.commit()
            print(f"[render-video] Used free generation for user {user_id}, remaining: {user.free_video_generations}")
    
    data = request.get_json()
    scenes = data.get('scenes', [])
    audio_path = data.get('audio_path', '')
    video_format = data.get('format', '9:16')
    captions_data = data.get('captions', {})
    captions_enabled = captions_data.get('enabled', False) if isinstance(captions_data, dict) else bool(captions_data)
    caption_settings = captions_data if isinstance(captions_data, dict) else {}
    script_text = data.get('script', '')  # Script text for subtitles
    stage_directions = data.get('stage_directions', '')  # Stage directions with SFX
    preview_mode = data.get('preview', False)  # Quick preview at lower resolution
    
    if not scenes:
        return jsonify({'error': 'No scenes provided'}), 400
    
    # Create unique output filename
    output_id = str(uuid.uuid4())[:8]
    output_path = f'output/{"preview" if preview_mode else "final"}_{output_id}.mp4'
    
    # Ensure output directory exists
    os.makedirs('output', exist_ok=True)
    
    try:
        # Parse Sound FX from script and stage directions, then mix into audio
        sfx_requests = parse_sfx_from_directions(script_text, stage_directions)
        
        if sfx_requests and audio_path and os.path.exists(audio_path):
            print(f"[render-video] Found {len(sfx_requests)} sound effects to mix")
            # Calculate total script lines for accurate SFX positioning
            total_lines = len((script_text + '\n' + stage_directions).split('\n'))
            # Mix SFX into a new audio file
            mixed_audio_path = f'output/audio_with_sfx_{output_id}.mp3'
            audio_path = mix_sfx_into_audio(audio_path, sfx_requests, mixed_audio_path, total_lines)
            print(f"[render-video] SFX mixed into: {audio_path}")
        
        # Get audio duration to drive clip timing (audio-driven editing)
        audio_duration = None
        if audio_path and os.path.exists(audio_path):
            try:
                probe_cmd = [
                    'ffprobe', '-v', 'error',
                    '-show_entries', 'format=duration',
                    '-of', 'default=noprint_wrappers=1:nokey=1',
                    audio_path
                ]
                probe_result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=10)
                if probe_result.returncode == 0:
                    audio_duration = float(probe_result.stdout.strip())
                    print(f"Voiceover duration: {audio_duration:.2f}s")
            except Exception as e:
                print(f"Could not get audio duration: {e}")
        
        # Calculate clip durations based on audio length
        num_scenes = len([s for s in scenes if s.get('video_url')])
        if audio_duration and num_scenes > 0:
            # Distribute clips evenly across audio duration
            base_clip_duration = audio_duration / num_scenes
            print(f"Audio-driven clips: {base_clip_duration:.2f}s each for {num_scenes} scenes")
        else:
            base_clip_duration = None  # Fall back to scene-specified durations
        
        # Download video clips and trim to match audio - PARALLELIZED for speed
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        def download_and_trim_clip(args):
            """Download and trim a single clip - runs in parallel."""
            i, scene, duration, output_id = args
            video_url = scene.get('video_url', '')
            if not video_url:
                return None, i, duration
            
            raw_path = f'output/raw_{output_id}_{i}.mp4'
            clip_path = f'output/clip_{output_id}_{i}.mp4'
            
            try:
                # Download video clip
                req = urllib.request.Request(video_url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=20) as response:
                    with open(raw_path, 'wb') as f:
                        f.write(response.read())
                
                # Trim clip - no per-clip threading to avoid CPU oversubscription
                trim_cmd = [
                    'ffmpeg', '-y',
                    '-ss', '0',
                    '-i', os.path.abspath(raw_path),
                    '-t', str(duration),
                    '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '26',
                    '-an',
                    os.path.abspath(clip_path)
                ]
                result = subprocess.run(trim_cmd, capture_output=True, timeout=45)
                
                if result.returncode != 0:
                    import shutil
                    shutil.copy(raw_path, clip_path)
                
                if os.path.exists(raw_path):
                    os.remove(raw_path)
                
                if os.path.exists(clip_path):
                    return clip_path, i, duration
                return None, i, duration
            except Exception as e:
                print(f"Clip {i} error: {e}")
                for f in [raw_path, clip_path]:
                    if os.path.exists(f):
                        os.remove(f)
                return None, i, duration
        
        # Prepare download tasks
        download_tasks = []
        for i, scene in enumerate(scenes):
            if base_clip_duration:
                duration = base_clip_duration
            else:
                duration = scene.get('duration_seconds', scene.get('duration', 4))
                try:
                    duration = float(duration)
                    if duration <= 0 or duration > 30:
                        duration = 4
                except:
                    duration = 4
            download_tasks.append((i, scene, duration, output_id))
        
        # Execute downloads in parallel (max 4 concurrent)
        clip_results = {}
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(download_and_trim_clip, task): task[0] for task in download_tasks}
            for future in as_completed(futures):
                clip_path, idx, duration = future.result()
                if clip_path:
                    clip_results[idx] = (clip_path, duration)
        
        # Maintain original order and keep durations aligned with paths
        sorted_indices = sorted(clip_results.keys())
        clip_paths = [clip_results[i][0] for i in sorted_indices]
        clip_durations = [clip_results[i][1] for i in sorted_indices]
        print(f"Downloaded and trimmed {len(clip_paths)} clips in parallel")
        
        if not clip_paths:
            return jsonify({'error': 'Failed to download any video clips'}), 500
        
        # Determine video dimensions based on format (lower res for preview)
        if preview_mode:
            format_sizes = {
                '9:16': (360, 640),
                '1:1': (360, 360),
                '4:5': (360, 450),
                '16:9': (640, 360)
            }
        else:
            format_sizes = {
                '9:16': (1080, 1920),
                '1:1': (1080, 1080),
                '4:5': (1080, 1350),
                '16:9': (1920, 1080)
            }
        width, height = format_sizes.get(video_format, (360, 640) if preview_mode else (1080, 1920))
        
        # Create file list for FFmpeg concat (use absolute paths)
        list_path = os.path.abspath(f'output/clips_{output_id}.txt')
        with open(list_path, 'w') as f:
            for clip in clip_paths:
                f.write(f"file '{os.path.abspath(clip)}'\n")
        
        # clip_durations already populated from parallel download results
        print(f"Using {len(clip_durations)} clip durations from parallel processing")
        
        # First, concatenate clips with crossfade transitions
        concat_path = os.path.abspath(f'output/concat_{output_id}.mp4')
        
        if len(clip_paths) > 1:
            # Use xfade filter for smooth transitions between clips
            transition_duration = 0.5  # 0.5 second crossfade
            
            # Build complex filter for xfade transitions with pre-scaling
            inputs = []
            for i, clip in enumerate(clip_paths):
                inputs.extend(['-i', os.path.abspath(clip)])
            
            # Build xfade filter chain with scaling to normalize all clips
            filter_parts = []
            
            # First, scale all inputs to the target size
            for i in range(len(clip_paths)):
                filter_parts.append(f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1,fps=30[s{i}]")
            
            # Calculate cumulative offsets based on actual clip durations
            # Guard against clips shorter than transition duration
            transition_duration = min(transition_duration, min(clip_durations) * 0.8) if min(clip_durations) < 1 else transition_duration
            
            if len(clip_paths) == 2:
                # Simple case: 2 clips
                offset = max(0.1, clip_durations[0] - transition_duration)
                filter_parts.append(f"[s0][s1]xfade=transition=fade:duration={transition_duration}:offset={offset:.2f}[v]")
            else:
                # Multiple clips: chain xfade filters
                cumulative_duration = 0
                for i in range(len(clip_paths) - 1):
                    if i == 0:
                        # First transition
                        cumulative_duration = max(0.1, clip_durations[0] - transition_duration)
                        filter_parts.append(f"[s0][s1]xfade=transition=fade:duration={transition_duration}:offset={cumulative_duration:.2f}[v1]")
                    elif i == len(clip_paths) - 2:
                        # Last transition
                        cumulative_duration += max(0.1, clip_durations[i] - transition_duration)
                        filter_parts.append(f"[v{i}][s{i+1}]xfade=transition=fade:duration={transition_duration}:offset={cumulative_duration:.2f}[v]")
                    else:
                        # Middle transitions
                        cumulative_duration += max(0.1, clip_durations[i] - transition_duration)
                        filter_parts.append(f"[v{i}][s{i+1}]xfade=transition=fade:duration={transition_duration}:offset={cumulative_duration:.2f}[v{i+1}]")
            
            xfade_filter = ";".join(filter_parts)
            
            concat_cmd = ['ffmpeg', '-y'] + inputs + [
                '-filter_complex', xfade_filter,
                '-map', '[v]',
                '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '28', '-threads', '0',
                concat_path
            ]
            result = subprocess.run(concat_cmd, capture_output=True, timeout=180)
            
            if result.returncode != 0:
                print(f"Xfade error: {result.stderr.decode()[:500]}")
                # Fallback to concat with fade-in/fade-out per clip
                concat_cmd = [
                    'ffmpeg', '-y', '-f', 'concat', '-safe', '0',
                    '-i', list_path,
                    '-c', 'copy',
                    concat_path
                ]
                result = subprocess.run(concat_cmd, capture_output=True, timeout=120)
                if result.returncode != 0:
                    print(f"Concat fallback error: {result.stderr.decode()}")
                else:
                    print("Used simple concat (xfade failed)")
            else:
                print(f"Added fade transitions between {len(clip_paths)} clips")
        else:
            # Single clip - just copy it
            concat_cmd = [
                'ffmpeg', '-y', '-f', 'concat', '-safe', '0',
                '-i', list_path,
                '-c', 'copy',
                concat_path
            ]
            result = subprocess.run(concat_cmd, capture_output=True, timeout=120)
            if result.returncode != 0:
                print(f"Concat error: {result.stderr.decode()}")
        
        # Now scale and crop to format, add audio
        # Two-pass approach to avoid complex filter chain issues
        # Pass 1: Combine video + audio into a temp file
        # Pass 2: Add captions (if enabled)
        
        has_audio = audio_path and os.path.exists(audio_path)
        temp_combined = os.path.abspath(f'output/temp_combined_{output_id}.mp4')
        
        # Get audio duration to ensure video matches it
        audio_duration = None
        if has_audio:
            dur_cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', audio_path]
            dur_result = subprocess.run(dur_cmd, capture_output=True, text=True, timeout=10)
            try:
                audio_duration = float(dur_result.stdout.strip())
                print(f"Audio duration: {audio_duration:.1f}s")
            except:
                audio_duration = None
        
        # Pass 1: Combine video + audio with scaling
        # Loop video if shorter than audio to prevent audio cutoff
        pass1_cmd = ['ffmpeg', '-y']
        
        if has_audio and audio_duration:
            # Loop video input to match audio length
            pass1_cmd.extend(['-stream_loop', '-1', '-i', concat_path])
            pass1_cmd.extend(['-i', audio_path])
            # Use audio duration as the target length
            pass1_cmd.extend(['-t', str(audio_duration)])
        else:
            pass1_cmd.extend(['-i', concat_path])
            if has_audio:
                pass1_cmd.extend(['-i', audio_path])
        
        # Apply scaling - use ultrafast preset for speed
        pass1_cmd.extend([
            '-vf', f'scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}',
            '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '26', '-threads', '0',
        ])
        
        if has_audio:
            pass1_cmd.extend(['-c:a', 'aac', '-b:a', '128k'])
        else:
            pass1_cmd.extend(['-an'])
        
        pass1_cmd.append(temp_combined)
        
        print(f"Pass 1: Combining video + audio...")
        pass1_result = subprocess.run(pass1_cmd, capture_output=True, timeout=180)
        
        if pass1_result.returncode != 0:
            print(f"Pass 1 failed: {pass1_result.stderr.decode()[:500]}")
            # Fallback: just copy concat file
            import shutil
            if os.path.exists(concat_path):
                shutil.copy(concat_path, temp_combined)
        
        # Pass 2: Add captions if enabled
        caption_filters = []
        
        # Add word-synced captions if enabled and audio exists
        if captions_enabled and audio_path and os.path.exists(audio_path):
            try:
                # Use Whisper to transcribe voiceover with word-level timestamps
                from openai import OpenAI
                whisper_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
                
                with open(audio_path, 'rb') as audio_file:
                    transcription = whisper_client.audio.transcriptions.create(
                        model="whisper-1",
                        file=audio_file,
                        response_format="verbose_json",
                        timestamp_granularities=["word"]
                    )
                
                # Robust word extraction - handle both SDK response types
                words = []
                if hasattr(transcription, 'words') and transcription.words:
                    words = transcription.words
                elif hasattr(transcription, 'segments'):
                    # Fallback: extract words from segments
                    for segment in transcription.segments:
                        if hasattr(segment, 'words'):
                            words.extend(segment.words)
                
                print(f"Whisper returned {len(words)} word timestamps")
                
                if words:
                    # Get caption settings
                    caption_color = caption_settings.get('textColor', caption_settings.get('color', '#FFFFFF')).lstrip('#')
                    caption_position = caption_settings.get('position', 'bottom')
                    caption_uppercase = caption_settings.get('uppercase', False)
                    caption_outline = caption_settings.get('outline', True)
                    caption_shadow = caption_settings.get('shadow', True)
                    
                    # Calculate Y position
                    if caption_position == 'top':
                        y_pos = 'h*0.12'
                    elif caption_position == 'bottom':
                        y_pos = 'h*0.82'
                    else:  # center
                        y_pos = '(h-text_h)/2'
                    
                    # Font size based on resolution
                    fontsize = 56 if not preview_mode else 28
                    
                    # Group words into phrases (3-4 words per caption for readability)
                    phrases = []
                    current_phrase = []
                    current_start = None
                    current_end = 0
                    
                    for word_data in words:
                        # Handle both dict and object response types
                        if isinstance(word_data, dict):
                            word = word_data.get('word', '')
                            start = word_data.get('start', 0)
                            end = word_data.get('end', 0)
                        else:
                            word = getattr(word_data, 'word', '')
                            start = getattr(word_data, 'start', 0)
                            end = getattr(word_data, 'end', 0)
                        
                        # Normalize word (strip leading/trailing spaces)
                        word = word.strip()
                        if not word:
                            continue
                        
                        if current_start is None:
                            current_start = start
                        
                        current_phrase.append(word)
                        current_end = end
                        
                        # Break into phrases of 3-4 words for snappy captions
                        word_stripped = word.rstrip()
                        if len(current_phrase) >= 4 or (len(current_phrase) >= 2 and word_stripped.endswith(('.', '!', '?', ','))):
                            phrases.append({
                                'text': ' '.join(current_phrase),
                                'start': current_start,
                                'end': current_end
                            })
                            current_phrase = []
                            current_start = None
                    
                    # Add remaining words
                    if current_phrase:
                        phrases.append({
                            'text': ' '.join(current_phrase),
                            'start': current_start,
                            'end': current_end
                        })
                    
                    # Extend last caption to match audio duration (prevents caption cutoff)
                    if phrases and audio_duration:
                        phrases[-1]['end'] = audio_duration
                    
                    # Build drawtext filters with precise timing
                    caption_filters = []
                    for phrase in phrases:
                        text = phrase['text']
                        start_time = phrase['start']
                        end_time = phrase['end']
                        
                        # Proper FFmpeg drawtext text escaping
                        clean_text = text
                        if caption_uppercase:
                            clean_text = clean_text.upper()
                        # Escape special characters for FFmpeg drawtext text value
                        # Order matters: backslash first, then quotes, then colons
                        clean_text = clean_text.replace("\\", "\\\\")
                        clean_text = clean_text.replace("'", "\\'")
                        clean_text = clean_text.replace(":", "\\:")
                        
                        # Build drawtext filter with timing
                        border_params = ""
                        if caption_outline:
                            border_params = ":borderw=4:bordercolor=black"
                        if caption_shadow:
                            border_params += ":shadowcolor=black@0.7:shadowx=3:shadowy=3"
                        
                        # In filter_complex, commas inside between() must be escaped with backslash
                        # to prevent them being parsed as filter chain separators
                        drawtext = (
                            f"drawtext=text='{clean_text}'"
                            f":fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
                            f":fontsize={fontsize}"
                            f":fontcolor=#{caption_color}"
                            f":x=(w-text_w)/2:y={y_pos}"
                            f"{border_params}"
                            f":enable='between(t\\,{start_time:.3f}\\,{end_time:.3f})'"
                        )
                        caption_filters.append(drawtext)
                    
                    print(f"Added {len(caption_filters)} word-synced caption phrases")
                else:
                    print("No word timestamps returned from Whisper")
                    
            except Exception as e:
                print(f"Whisper transcription failed, skipping captions: {e}")
        
        # Pass 2: Add captions to the combined video (if any)
        if caption_filters and os.path.exists(temp_combined):
            print(f"Pass 2: Adding {len(caption_filters)} captions...")
            
            # Build caption filter chain - join with commas, each drawtext is separate
            # Since we're only doing captions now (no mixing with complex filters),
            # we can use -vf with proper escaping
            caption_chain = ",".join(caption_filters)
            
            pass2_cmd = [
                'ffmpeg', '-y',
                '-i', temp_combined,
                '-vf', caption_chain,
                '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '26', '-threads', '0',
                '-c:a', 'copy',
                output_path
            ]
            
            pass2_result = subprocess.run(pass2_cmd, capture_output=True, timeout=300)
            
            if pass2_result.returncode != 0:
                error_msg = pass2_result.stderr.decode()[:1000]
                print(f"Pass 2 (captions) failed: {error_msg}")
                # Fallback: use pass 1 output without captions
                import shutil
                shutil.copy(temp_combined, output_path)
                print("Using video without captions as fallback")
            else:
                print("Pass 2 succeeded - captions added")
        else:
            # No captions - just use pass 1 output
            import shutil
            if os.path.exists(temp_combined):
                shutil.copy(temp_combined, output_path)
            elif os.path.exists(concat_path):
                shutil.copy(concat_path, output_path)
        
        # Cleanup temp combined file
        try:
            if os.path.exists(temp_combined):
                os.remove(temp_combined)
        except:
            pass
        
        # Cleanup temp files
        for clip in clip_paths:
            try:
                os.remove(clip)
            except:
                pass
        try:
            os.remove(list_path)
            os.remove(concat_path)
        except:
            pass
        
        # Cleanup mixed SFX audio if created
        try:
            mixed_audio_path = f'output/audio_with_sfx_{output_id}.mp3'
            if os.path.exists(mixed_audio_path):
                os.remove(mixed_audio_path)
        except:
            pass
        
        if os.path.exists(output_path):
            return jsonify({
                'success': True,
                'video_path': '/' + output_path,
                'format': video_format
            })
        else:
            return jsonify({'error': 'Video render failed'}), 500
            
    except Exception as e:
        print(f"Render error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/submit-feedback', methods=['POST'])
def submit_feedback():
    """Submit project feedback and get AI self-assessment."""
    from models import ProjectFeedback, AILearning, Project
    from flask_login import current_user
    import os
    from openai import OpenAI
    
    data = request.json
    project_id = data.get('project_id')
    
    # Get user ID
    user_id = None
    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('anonymous_user_id', 'anonymous')
    
    # Build feedback summary for AI
    ratings_summary = []
    if data.get('script_rating'):
        ratings_summary.append(f"Script: {data['script_rating']}")
    if data.get('voice_rating'):
        ratings_summary.append(f"Voice: {data['voice_rating']}")
    if data.get('visuals_rating'):
        ratings_summary.append(f"Visuals: {data['visuals_rating']}")
    if data.get('soundfx_rating'):
        ratings_summary.append(f"Sound FX: {data['soundfx_rating']}")
    if data.get('overall_rating'):
        ratings_summary.append(f"Overall: {data['overall_rating']}")
    
    user_feedback = data.get('feedback_text', '')
    severity = data.get('severity', 'minor')
    script_used = data.get('script', '')
    
    # Generate AI self-assessment
    try:
        client = OpenAI(
            api_key=os.environ.get("XAI_API_KEY"),
            base_url="https://api.x.ai/v1"
        )
        
        reflection_prompt = f"""You are Echo Engine, an AI that creates video content. A user just finished a project and gave you feedback.

User's Ratings:
{chr(10).join(ratings_summary) if ratings_summary else 'No specific ratings given'}

User's Notes:
{user_feedback if user_feedback else 'No additional notes'}

Severity Level: {severity}

Script Used:
{script_used[:500] if script_used else 'Not provided'}...

Based on this feedback, provide TWO things:

1. WHAT YOU LEARNED (2-3 sentences): Be specific and honest about what this teaches you about this user's preferences. Reference specific elements if possible.

2. WHAT TO IMPROVE (2-3 sentences): Be honest about weaknesses and what you'll do differently next time.

Also estimate how much you learned:
- If feedback was mostly positive with minor notes: LOW learning (2-3%)
- If feedback was mixed with specific critiques: MEDIUM learning (4-6%)  
- If feedback was critical with actionable insights: HIGH learning (7-10%)

Respond in this exact JSON format:
{{"learned": "Your honest reflection on what you learned...", "improve": "What you will do differently...", "learning_points": 5}}

Be genuine and humble. Don't be generic - reference specific aspects of THIS project."""

        response = client.chat.completions.create(
            model="grok-3-fast",
            messages=[{"role": "user", "content": reflection_prompt}],
            max_tokens=400
        )
        
        reflection_text = response.choices[0].message.content.strip()
        
        # Parse JSON response
        import json
        import re
        json_match = re.search(r'\{[\s\S]*\}', reflection_text)
        if json_match:
            reflection_data = json.loads(json_match.group())
            ai_learned = reflection_data.get('learned', 'I processed your feedback.')
            ai_to_improve = reflection_data.get('improve', 'I will apply these insights.')
        else:
            ai_learned = "I noted your feedback for future reference."
            ai_to_improve = "I'll work on being more aligned with your preferences."
            
    except Exception as e:
        print(f"AI reflection error: {e}")
        ai_learned = "I received your feedback and will learn from it."
        ai_to_improve = "I'll focus on improving based on your notes."
    
    # Calculate learning points server-side based on severity (2-10% range)
    import random
    if severity == 'critical':
        learning_points = random.randint(7, 10)
    elif severity == 'moderate':
        learning_points = random.randint(4, 6)
    else:
        learning_points = random.randint(2, 3)
    
    # Get or create AI learning record
    try:
        ai_learning = AILearning.query.filter_by(user_id=user_id).first()
        was_already_unlocked = False
        old_progress = 0
        
        if ai_learning:
            old_progress = ai_learning.learning_progress
            was_already_unlocked = ai_learning.can_auto_generate
        else:
            ai_learning = AILearning(
                user_id=user_id,
                total_projects=0,
                successful_projects=0,
                learning_progress=0,
                learned_hooks=[],
                learned_voices=[],
                learned_styles=[],
                learned_topics=[],
                can_auto_generate=False
            )
            db.session.add(ai_learning)
        
        # Update learning progress
        ai_learning.total_projects += 1
        new_progress = min(ai_learning.learning_progress + learning_points, 100)
        ai_learning.learning_progress = new_progress
        
        # Check for success (good overall rating)
        if data.get('overall_rating') in ['great', 'ok']:
            ai_learning.successful_projects += 1
        
        # Check if auto-generation should be unlocked
        can_auto_generate = (
            ai_learning.successful_projects >= 5 and 
            ai_learning.learning_progress >= 50
        )
        ai_learning.can_auto_generate = can_auto_generate
        
        # Save feedback to database (project_id is nullable)
        feedback = ProjectFeedback(
            user_id=user_id,
            project_id=project_id if project_id else None,
            script_rating=data.get('script_rating'),
            voice_rating=data.get('voice_rating'),
            visuals_rating=data.get('visuals_rating'),
            soundfx_rating=data.get('soundfx_rating'),
            overall_rating=data.get('overall_rating'),
            feedback_text=user_feedback,
            severity=severity,
            ai_learned=ai_learned,
            ai_to_improve=ai_to_improve,
            learning_points_gained=learning_points
        )
        db.session.add(feedback)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'ai_learned': ai_learned,
            'ai_to_improve': ai_to_improve,
            'learning_points_gained': learning_points,
            'old_progress': old_progress,
            'new_progress': new_progress,
            'can_auto_generate': can_auto_generate,
            'was_already_unlocked': was_already_unlocked
        })
        
    except Exception as e:
        print(f"Feedback save error: {e}")
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': 'Failed to save feedback to database',
            'ai_learned': ai_learned,
            'ai_to_improve': ai_to_improve,
            'learning_points_gained': 0
        }), 500


@app.route('/host-video', methods=['POST'])
def host_video():
    """Host a video with a public shareable URL (Pro subscribers only)."""
    import uuid
    from models import Subscription, HostedVideo
    from flask_login import current_user
    
    user_id = None
    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('dev_user_id')
    
    sub = Subscription.query.filter_by(user_id=user_id).first() if user_id else None
    if not sub or not sub.is_active():
        return jsonify({
            'error': 'Pro subscription required',
            'requires_subscription': True
        }), 403
    
    data = request.get_json()
    video_path = data.get('video_path')
    title = data.get('title', 'Untitled Video')
    project_id = data.get('project_id')
    
    if not video_path:
        return jsonify({'error': 'Video path required'}), 400
    
    public_id = uuid.uuid4().hex[:12]
    
    hosted = HostedVideo(
        user_id=user_id,
        project_id=project_id,
        title=title,
        public_id=public_id,
        video_path=video_path
    )
    db.session.add(hosted)
    db.session.commit()
    
    domains = os.environ.get('REPLIT_DOMAINS', 'localhost:5000')
    domain = domains.split(',')[0] if domains else 'localhost:5000'
    protocol = 'https' if 'replit' in domain else 'http'
    
    return jsonify({
        'success': True,
        'public_id': public_id,
        'share_url': f'{protocol}://{domain}/v/{public_id}',
        'title': title
    })


@app.route('/my-hosted-videos', methods=['GET'])
def my_hosted_videos():
    """Get list of user's hosted videos."""
    from models import HostedVideo
    from flask_login import current_user
    
    user_id = None
    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('dev_user_id')
    
    if not user_id:
        return jsonify({'videos': []})
    
    videos = HostedVideo.query.filter_by(user_id=user_id).order_by(HostedVideo.created_at.desc()).all()
    
    domains = os.environ.get('REPLIT_DOMAINS', 'localhost:5000')
    domain = domains.split(',')[0] if domains else 'localhost:5000'
    protocol = 'https' if 'replit' in domain else 'http'
    
    return jsonify({
        'videos': [{
            'id': v.id,
            'title': v.title,
            'public_id': v.public_id,
            'share_url': f'{protocol}://{domain}/v/{v.public_id}',
            'views': v.views,
            'is_public': v.is_public,
            'created_at': v.created_at.isoformat()
        } for v in videos]
    })


@app.route('/v/<public_id>')
def view_hosted_video(public_id):
    """Public video view page."""
    from models import HostedVideo
    
    video = HostedVideo.query.filter_by(public_id=public_id, is_public=True).first()
    if not video:
        return "Video not found", 404
    
    video.views += 1
    db.session.commit()
    
    return render_template('video_view.html', video=video)


@app.route('/feed/items', methods=['GET'])
def get_feed_items():
    """Get AI-generated content for the swipe feed."""
    from models import FeedItem, SwipeFeedback
    from flask_login import current_user
    from sqlalchemy import or_
    
    user_id = None
    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('dev_user_id')
    
    already_swiped = []
    if user_id:
        already_swiped = [f.feed_item_id for f in SwipeFeedback.query.filter_by(user_id=user_id).all()]
    
    query = FeedItem.query
    if user_id:
        query = query.filter(or_(FeedItem.is_global == True, FeedItem.user_id == user_id))
    else:
        query = query.filter(FeedItem.is_global == True)
    
    if already_swiped:
        query = query.filter(FeedItem.id.notin_(already_swiped))
    
    items = query.order_by(FeedItem.created_at.desc()).limit(20).all()
    
    return jsonify({
        'items': [{
            'id': item.id,
            'content_type': item.content_type,
            'title': item.title,
            'script': item.script,
            'visual_preview': item.visual_preview,
            'video_path': item.video_path,
            'topic': item.topic,
            'hook_style': item.hook_style,
            'voice_style': item.voice_style
        } for item in items]
    })


@app.route('/feed/generate', methods=['POST'])
def generate_feed_content():
    """Generate AI content for the feed based on user's existing projects."""
    from models import FeedItem, AILearning, Project
    from flask_login import current_user
    
    user_id = None
    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('dev_user_id')
    
    data = request.get_json() or {}
    
    user_projects = []
    if user_id:
        projects = Project.query.filter_by(user_id=user_id).order_by(Project.updated_at.desc()).limit(5).all()
        for p in projects:
            if p.script:
                user_projects.append({
                    'title': p.title,
                    'script': p.script[:500]
                })
    
    user_preferences = None
    if user_id:
        learning = AILearning.query.filter_by(user_id=user_id).first()
        if learning:
            user_preferences = {
                'hooks': learning.learned_hooks,
                'voices': learning.learned_voices,
                'styles': learning.learned_styles,
                'topics': learning.learned_topics
            }
    
    recent_feedback = None
    if user_id:
        from models import SwipeFeedback
        feedback_entries = SwipeFeedback.query.filter(
            SwipeFeedback.user_id == user_id,
            SwipeFeedback.feedback_text != None,
            SwipeFeedback.feedback_text != ''
        ).order_by(SwipeFeedback.created_at.desc()).limit(5).all()
        if feedback_entries:
            recent_feedback = [f.feedback_text for f in feedback_entries]
    
    try:
        if user_projects:
            system_prompt = """You are a short-form video script generator. Based on the user's existing projects and style, create a NEW script idea that matches their voice and interests.

The user has created these projects:
""" + "\n".join([f"- {p['title']}: {p['script'][:200]}..." for p in user_projects[:3]])
            
            system_prompt += """

Create a fresh script idea inspired by their style but on a new angle or topic.

Return JSON with:
- title: Catchy title (max 60 chars)
- script: The full script with clear hooks and pacing
- hook_style: The hook type used (question, stat, story, controversy)
- topic: The main topic category
- inspiration: Brief note on which project inspired this"""
        else:
            system_prompt = """You are a short-form video script generator. Create a punchy, engaging script for a 30-60 second video.
        
Return JSON with:
- title: Catchy title (max 60 chars)
- script: The full script with clear hooks and pacing
- hook_style: The hook type used (question, stat, story, controversy)
- topic: The main topic category"""

        personalization_notes = []
        if user_preferences:
            if user_preferences.get('hooks'):
                personalization_notes.append(f"Hook styles they like: {', '.join(user_preferences['hooks'][:3])}")
            if user_preferences.get('topics'):
                personalization_notes.append(f"Topics they enjoy: {', '.join(user_preferences['topics'][:3])}")
            if user_preferences.get('voices'):
                personalization_notes.append(f"Voice styles they prefer: {', '.join(user_preferences['voices'][:3])}")
            if user_preferences.get('styles'):
                personalization_notes.append(f"Content styles they like: {', '.join(user_preferences['styles'][:3])}")
        
        if recent_feedback:
            personalization_notes.append(f"Recent feedback on content: {'; '.join(recent_feedback[:3])}")
        
        if personalization_notes:
            system_prompt += "\n\nUser preferences to incorporate:\n" + "\n".join(personalization_notes)
        
        prompt_message = "Create a new script idea" if user_projects else "Create a viral short-form script about: trending news"
        
        response = xai_client.chat.completions.create(
            model="grok-3",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt_message}
            ],
            response_format={"type": "json_object"}
        )
        
        result = json.loads(response.choices[0].message.content)
        
        feed_item = FeedItem(
            user_id=user_id,
            content_type='script',
            title=result.get('title', topic)[:255],
            script=result.get('script', ''),
            topic=result.get('topic', topic)[:100],
            hook_style=result.get('hook_style', 'question')[:50],
            is_global=user_id is None
        )
        db.session.add(feed_item)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'item': {
                'id': feed_item.id,
                'title': feed_item.title,
                'script': feed_item.script,
                'topic': feed_item.topic,
                'hook_style': feed_item.hook_style
            }
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/feed/swipe', methods=['POST'])
def record_swipe():
    """Record a swipe action (like/skip) and optional feedback."""
    from models import SwipeFeedback, FeedItem, AILearning
    from flask_login import current_user
    
    user_id = None
    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('dev_user_id')
    
    if not user_id:
        return jsonify({'error': 'User required'}), 401
    
    data = request.get_json()
    item_id = data.get('item_id')
    action = data.get('action')
    feedback_text = data.get('feedback', '')
    
    if not item_id or action not in ['like', 'skip']:
        return jsonify({'error': 'Invalid swipe data'}), 400
    
    item = FeedItem.query.get(item_id)
    if not item:
        return jsonify({'error': 'Item not found'}), 404
    
    feedback = SwipeFeedback(
        user_id=user_id,
        feed_item_id=item_id,
        action=action,
        feedback_text=feedback_text
    )
    db.session.add(feedback)
    
    if action == 'like':
        learning = AILearning.query.filter_by(user_id=user_id).first()
        if not learning:
            learning = AILearning(user_id=user_id)
            db.session.add(learning)
        
        if item.hook_style and item.hook_style not in (learning.learned_hooks or []):
            hooks = learning.learned_hooks or []
            hooks.append(item.hook_style)
            learning.learned_hooks = hooks[-10:]
        
        if item.topic and item.topic not in (learning.learned_topics or []):
            topics = learning.learned_topics or []
            topics.append(item.topic)
            learning.learned_topics = topics[-10:]
    
    db.session.commit()
    
    return jsonify({'success': True, 'action': action})


@app.route('/feed/liked', methods=['GET'])
def get_liked_items():
    """Get user's liked feed items."""
    from models import SwipeFeedback, FeedItem
    from flask_login import current_user
    
    user_id = None
    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('dev_user_id')
    
    if not user_id:
        return jsonify({'items': []})
    
    liked = SwipeFeedback.query.filter_by(user_id=user_id, action='like').order_by(SwipeFeedback.created_at.desc()).all()
    item_ids = [l.feed_item_id for l in liked]
    items = FeedItem.query.filter(FeedItem.id.in_(item_ids)).all() if item_ids else []
    
    return jsonify({
        'items': [{
            'id': item.id,
            'title': item.title,
            'script': item.script,
            'topic': item.topic,
            'hook_style': item.hook_style
        } for item in items]
    })


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
