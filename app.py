from flask import Flask, render_template, request, jsonify, send_from_directory, session, url_for, Response
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
    learn_from_source_content, unified_content_engine,
    call_ai, SYSTEM_GUARDRAILS,
    analyze_editing_patterns_global, store_global_patterns, get_global_learned_patterns
)
from extensions import db, login_manager
from functools import wraps
from collections import defaultdict
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

logging.basicConfig(level=logging.DEBUG)

# Simple in-memory rate limiting
rate_limit_store = defaultdict(list)

def rate_limit(limit=30, window=60):
    """Rate limiting decorator. Default: 30 requests per 60 seconds."""
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            # Get client identifier (user_id or IP)
            from flask_login import current_user
            if current_user.is_authenticated:
                key = f"user:{current_user.id}"
            else:
                key = f"ip:{request.remote_addr}"
            
            now = time.time()
            # Clean old entries
            rate_limit_store[key] = [t for t in rate_limit_store[key] if now - t < window]
            
            if len(rate_limit_store[key]) >= limit:
                return jsonify({'error': 'Rate limit exceeded. Please slow down.'}), 429
            
            rate_limit_store[key].append(now)
            return f(*args, **kwargs)
        return wrapped
    return decorator

import threading
from concurrent.futures import ThreadPoolExecutor

background_render_jobs = {}
render_executor = ThreadPoolExecutor(max_workers=3)

def send_render_complete_email(user_id, video_url, project_name):
    """Send email notification when video rendering is complete."""
    try:
        from models import User, EmailNotification
        from flask import current_app
        
        with current_app.app_context():
            user = User.query.get(user_id)
            if not user or not user.email:
                return
            
            email_pref = EmailNotification.query.filter_by(
                user_id=user_id, 
                notification_type='video_ready'
            ).first()
            
            if email_pref and not email_pref.enabled:
                return
            
            domain = os.environ.get('REPLIT_DEV_DOMAIN', 'framd.app')
            full_video_url = f"https://{domain}{video_url}"
            
            print(f"[Email] Would send render complete email to {user.email} for {project_name}")
            print(f"[Email] Video URL: {full_video_url}")
            
    except Exception as e:
        print(f"[Email] Error sending notification: {e}")


def background_render_task(job_id, render_params, user_id, app_context):
    """
    Execute video render in background thread.
    
    Scope: Processes scenes, concatenates clips, adds audio, and applies template visual FX.
    Note: Captions overlay is handled by the main render pipeline for real-time preview.
    Background renders focus on quick assembly with FX for users who close their tab.
    """
    import subprocess
    import uuid
    
    try:
        background_render_jobs[job_id]['status'] = 'rendering'
        background_render_jobs[job_id]['progress'] = 5
        
        with app_context:
            scenes = render_params.get('scenes', [])
            audio_path = render_params.get('audio_path', '')
            video_format = render_params.get('format', '9:16')
            project_name = render_params.get('project_name', 'Untitled')
            template = render_params.get('template', 'start_from_scratch')
            
            output_id = str(uuid.uuid4())[:8]
            output_path = f'output/background_{output_id}.mp4'
            os.makedirs('output', exist_ok=True)
            
            format_dims = {
                '9:16': (1080, 1920),
                '16:9': (1920, 1080),
                '1:1': (1080, 1080),
                '4:5': (1080, 1350)
            }
            width, height = format_dims.get(video_format, (1080, 1920))
            
            # Step 1: Prepare scene clips (20%)
            background_render_jobs[job_id]['progress'] = 20
            background_render_jobs[job_id]['status'] = 'processing_scenes'
            
            clip_paths = []
            for i, scene in enumerate(scenes):
                scene_url = scene.get('url', '')
                scene_duration = scene.get('duration', 4)
                scene_path = scene_url.lstrip('/') if scene_url.startswith('/') else scene_url
                
                if scene_path and os.path.exists(scene_path):
                    # Create a clip from the scene
                    clip_path = f'output/bg_clip_{output_id}_{i}.mp4'
                    cmd = [
                        'ffmpeg', '-y', '-loop', '1', '-i', scene_path,
                        '-t', str(scene_duration), '-c:v', 'libx264', '-preset', 'fast',
                        '-vf', f'scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}',
                        clip_path
                    ]
                    result = subprocess.run(cmd, capture_output=True, timeout=120)
                    if result.returncode == 0 and os.path.exists(clip_path):
                        clip_paths.append(clip_path)
            
            if not clip_paths:
                background_render_jobs[job_id]['status'] = 'error'
                background_render_jobs[job_id]['error'] = 'No valid scenes to render'
                return
            
            # Step 2: Concatenate clips (40%)
            background_render_jobs[job_id]['progress'] = 40
            background_render_jobs[job_id]['status'] = 'concatenating'
            
            concat_file = f'output/bg_concat_{output_id}.txt'
            with open(concat_file, 'w') as f:
                for clip in clip_paths:
                    f.write(f"file '{os.path.abspath(clip)}'\n")
            
            concat_output = f'output/bg_concat_{output_id}.mp4'
            concat_cmd = [
                'ffmpeg', '-y', '-f', 'concat', '-safe', '0',
                '-i', concat_file, '-c', 'copy', concat_output
            ]
            subprocess.run(concat_cmd, capture_output=True, timeout=120)
            
            # Step 3: Add audio if available (60%)
            background_render_jobs[job_id]['progress'] = 60
            background_render_jobs[job_id]['status'] = 'adding_audio'
            
            video_with_audio = concat_output
            if audio_path and os.path.exists(audio_path):
                video_with_audio = f'output/bg_audio_{output_id}.mp4'
                audio_cmd = [
                    'ffmpeg', '-y', '-i', concat_output, '-i', audio_path,
                    '-c:v', 'copy', '-c:a', 'aac', '-shortest', video_with_audio
                ]
                result = subprocess.run(audio_cmd, capture_output=True, timeout=120)
                if result.returncode != 0:
                    video_with_audio = concat_output
            
            # Step 4: Apply template visual FX + Source Merging Engine (80%)
            background_render_jobs[job_id]['progress'] = 80
            background_render_jobs[job_id]['status'] = 'applying_fx'
            
            try:
                from context_engine import build_visual_fx_filter
                from visual_director import apply_merging_to_ffmpeg_command
                
                base_filter = build_visual_fx_filter(template)
                
                # Detect content type from template
                content_type_map = {
                    'hot_take': 'hot_take',
                    'explainer': 'explainer',
                    'meme_funny': 'meme',
                    'make_an_ad': 'ad',
                    'story': 'story'
                }
                content_type = content_type_map.get(template, 'general')
                
                # Apply Source Merging Engine filters (color grading, grain, transitions)
                vf_filter = apply_merging_to_ffmpeg_command(
                    base_filter,
                    content_type=content_type,
                    color_style=None,
                    film_grain=True
                )
                
                if vf_filter and vf_filter != '':
                    fx_cmd = [
                        'ffmpeg', '-y', '-i', video_with_audio,
                        '-vf', vf_filter, '-c:a', 'copy', output_path
                    ]
                    result = subprocess.run(fx_cmd, capture_output=True, timeout=300)
                    if result.returncode != 0:
                        # Fallback: copy without FX
                        import shutil
                        shutil.copy(video_with_audio, output_path)
                else:
                    import shutil
                    shutil.copy(video_with_audio, output_path)
            except Exception as fx_error:
                print(f"[Background Render] FX error: {fx_error}")
                import shutil
                shutil.copy(video_with_audio, output_path)
            
            # Cleanup temp files
            for clip in clip_paths:
                if os.path.exists(clip):
                    os.remove(clip)
            if os.path.exists(concat_file):
                os.remove(concat_file)
            if os.path.exists(concat_output) and concat_output != output_path:
                os.remove(concat_output)
            if video_with_audio != concat_output and video_with_audio != output_path:
                if os.path.exists(video_with_audio):
                    os.remove(video_with_audio)
            
            background_render_jobs[job_id]['progress'] = 100
            
            if os.path.exists(output_path):
                background_render_jobs[job_id]['status'] = 'complete'
                background_render_jobs[job_id]['video_url'] = '/' + output_path
                
                send_render_complete_email(user_id, '/' + output_path, project_name)
            else:
                background_render_jobs[job_id]['status'] = 'error'
                background_render_jobs[job_id]['error'] = 'Final render failed'
                
    except Exception as e:
        background_render_jobs[job_id]['status'] = 'error'
        background_render_jobs[job_id]['error'] = str(e)
        print(f"[Background Render] Error: {e}")
        import traceback
        traceback.print_exc()


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

from models import (
    User, OAuth, Conversation, UserPreference, Project, VideoFeedback,
    AILearning, GeneratedDraft, GlobalPattern, Subscription, VideoHistory,
    UserTokens, MediaAsset, KeywordAssetCache, SourceDocument, VideoTemplate,
    TemplateElement, GeneratedAsset
)

with app.app_context():
    db.create_all()
    if not UserTokens.query.first():
        token_entry = UserTokens()
        token_entry.balance = 120
        db.session.add(token_entry)
        db.session.commit()
    
    # Ensure new columns exist for video feedback system (PostgreSQL only)
    try:
        if 'postgresql' in str(db.engine.url):
            from sqlalchemy import text
            with db.engine.connect() as conn:
                # Check and add revision_count column to projects
                result = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='projects' AND column_name='revision_count'"))
                if not result.fetchone():
                    conn.execute(text("ALTER TABLE projects ADD COLUMN revision_count INTEGER DEFAULT 0"))
                    conn.commit()
                
                # Check and add liked column to projects
                result = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='projects' AND column_name='liked'"))
                if not result.fetchone():
                    conn.execute(text("ALTER TABLE projects ADD COLUMN liked BOOLEAN DEFAULT NULL"))
                    conn.commit()
                
                # Check and add sound_plan column to projects
                result = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='projects' AND column_name='sound_plan'"))
                if not result.fetchone():
                    conn.execute(text("ALTER TABLE projects ADD COLUMN sound_plan JSONB"))
                    conn.commit()
                
                # Check if video_feedbacks table exists
                result = conn.execute(text("SELECT table_name FROM information_schema.tables WHERE table_name='video_feedbacks'"))
                if not result.fetchone():
                    conn.execute(text("""
                        CREATE TABLE video_feedbacks (
                            id SERIAL PRIMARY KEY,
                            project_id INTEGER REFERENCES projects(id),
                            user_id VARCHAR NOT NULL,
                            liked BOOLEAN NOT NULL,
                            comment TEXT,
                            script_version TEXT,
                            revision_number INTEGER DEFAULT 0,
                            ai_analysis JSON,
                            created_at TIMESTAMP DEFAULT NOW()
                        )
                    """))
                    conn.commit()
                
                # Check if generator_settings table exists
                result = conn.execute(text("SELECT table_name FROM information_schema.tables WHERE table_name='generator_settings'"))
                if not result.fetchone():
                    conn.execute(text("""
                        CREATE TABLE generator_settings (
                            id SERIAL PRIMARY KEY,
                            user_id VARCHAR UNIQUE NOT NULL,
                            tone VARCHAR(50) DEFAULT 'neutral',
                            format_type VARCHAR(50) DEFAULT 'explainer',
                            target_length INTEGER DEFAULT 45,
                            voice_style VARCHAR(50) DEFAULT 'news_anchor',
                            enabled_topics JSON DEFAULT '[]',
                            auto_enabled BOOLEAN DEFAULT FALSE,
                            created_at TIMESTAMP DEFAULT NOW(),
                            updated_at TIMESTAMP DEFAULT NOW()
                        )
                    """))
                    conn.commit()
    except Exception as e:
        logging.warning(f"Schema migration check: {e}")
    
    logging.info("Database tables created")

from routes import auth_bp, payments_bp, projects_bp, video_bp, chat_bp, api_bp, pages_bp
app.register_blueprint(auth_bp, url_prefix='/v2')
app.register_blueprint(payments_bp, url_prefix='/v2')
app.register_blueprint(projects_bp, url_prefix='/v2')
app.register_blueprint(video_bp, url_prefix='/v2')
app.register_blueprint(chat_bp)
app.register_blueprint(api_bp)
app.register_blueprint(pages_bp)

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


def generate_sound_effect_elevenlabs(effect_description, output_path, duration=2.0):
    """
    Generate a sound effect using ElevenLabs Sound Effects API.
    Falls back to FFmpeg synthesis if ElevenLabs is unavailable.
    """
    elevenlabs_key = os.environ.get("ELEVENLABS_API_KEY")
    
    if elevenlabs_key:
        try:
            from elevenlabs.client import ElevenLabs
            
            client = ElevenLabs(api_key=elevenlabs_key)
            
            result = client.text_to_sound_effects.convert(
                text=effect_description,
                duration_seconds=min(duration, 22.0),
                prompt_influence=0.3
            )
            
            os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else 'output', exist_ok=True)
            
            with open(output_path, 'wb') as f:
                for chunk in result:
                    f.write(chunk)
            
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                print(f"Generated ElevenLabs SFX: {effect_description[:30]}...")
                return output_path
        except Exception as e:
            print(f"ElevenLabs SFX error: {e}, falling back to FFmpeg")
    
    return None


def generate_sound_effect(effect_type, output_path, duration=1.0):
    """
    Generate a sound effect using ElevenLabs (preferred) or FFmpeg synthesis (fallback).
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
    
    effect_descriptions = {
        'whoosh': 'quick cinematic swoosh transition sound effect',
        'impact': 'deep bass impact hit sound effect for emphasis',
        'tension': 'rising tension drone suspenseful atmosphere',
        'reveal': 'bright reveal sting chime sound effect',
        'alarm': 'alert warning notification tone',
        'heartbeat': 'rhythmic heartbeat pulse sound',
        'static': 'radio TV static interference noise',
        'beep': 'simple digital beep notification',
        'rumble': 'low deep rumble earthquake bass',
        'wind': 'ambient wind atmospheric whoosh'
    }
    
    description = effect_descriptions.get(effect_type.lower(), effect_type)
    elevenlabs_result = generate_sound_effect_elevenlabs(description, output_path, duration)
    if elevenlabs_result:
        return elevenlabs_result
    
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


def build_visual_fx_filter(visual_fx, width, height):
    """
    Build FFmpeg filter string based on template visual FX settings.
    Returns a filter string to apply color grading, vignette, etc.
    """
    filters = []
    
    color_grade = visual_fx.get('color_grade', 'natural')
    vignette = visual_fx.get('vignette', 0)
    
    color_grade_filters = {
        'high_contrast': 'eq=contrast=1.3:brightness=0.05:saturation=1.2',
        'clean_bright': 'eq=contrast=1.1:brightness=0.08:saturation=1.1',
        'warm_cinematic': 'colorbalance=rs=0.1:gs=0.05:bs=-0.1,eq=contrast=1.15:saturation=1.1',
        'neutral_sharp': 'eq=contrast=1.2:saturation=0.95,unsharp=5:5:1',
        'warm_intimate': 'colorbalance=rs=0.15:gs=0.08:bs=-0.05,eq=contrast=1.05:brightness=0.03',
        'saturated_pop': 'eq=saturation=1.4:contrast=1.2:brightness=0.05',
        'polished_commercial': 'eq=contrast=1.1:brightness=0.05:saturation=1.15',
        'vibrant_social': 'eq=saturation=1.35:contrast=1.15',
        'natural': 'eq=contrast=1.05:saturation=1.0'
    }
    
    if color_grade in color_grade_filters:
        filters.append(color_grade_filters[color_grade])
    
    if vignette > 0:
        vignette_angle = 3.14159 / (2 + vignette * 3)
        filters.append(f'vignette=PI/{2 + int(vignette * 3)}')
    
    if not filters:
        return ''
    
    return ','.join(filters)


CAPTION_TEMPLATES = {
    'bold_pop': {
        'name': 'Bold Pop',
        'font': 'Arial',
        'base_size': 52,
        'highlight_size': 62,
        'primary_color': '&H00FFFFFF',
        'highlight_color': '&H0000D4FF',
        'outline_color': '&H00000000',
        'outline': 4,
        'shadow': 3,
        'bold': True,
        'animation': 'pop'
    },
    'clean_minimal': {
        'name': 'Clean Minimal',
        'font': 'Arial',
        'base_size': 44,
        'highlight_size': 48,
        'primary_color': '&H00FFFFFF',
        'highlight_color': '&H00FFFFFF',
        'outline_color': '&H80000000',
        'outline': 2,
        'shadow': 1,
        'bold': False,
        'animation': 'fade'
    },
    'gradient_glow': {
        'name': 'Gradient Glow',
        'font': 'Arial',
        'base_size': 48,
        'highlight_size': 56,
        'primary_color': '&H00FFFFFF',
        'highlight_color': '&H00FFD700',
        'outline_color': '&H00000000',
        'outline': 3,
        'shadow': 4,
        'bold': True,
        'animation': 'glow'
    },
    'street_style': {
        'name': 'Street Style',
        'font': 'Impact',
        'base_size': 56,
        'highlight_size': 64,
        'primary_color': '&H00FFFFFF',
        'highlight_color': '&H0000FF00',
        'outline_color': '&H00000000',
        'outline': 5,
        'shadow': 2,
        'bold': True,
        'animation': 'bounce'
    },
    'boxed': {
        'name': 'Boxed',
        'font': 'Arial',
        'base_size': 42,
        'highlight_size': 46,
        'primary_color': '&H00000000',
        'highlight_color': '&H00000000',
        'back_color': '&H80FFFFFF',
        'outline_color': '&H00000000',
        'outline': 0,
        'shadow': 0,
        'bold': True,
        'animation': 'slide'
    }
}


def create_whisper_synced_captions(audio_path, output_path, template='bold_pop', position='bottom', video_width=1080, video_height=1920, uppercase=False):
    """
    Create ASS subtitle file with word-by-word captions synced to actual voiceover audio using Whisper.
    Returns (output_path, success) tuple.
    """
    from openai import OpenAI
    import re
    
    try:
        whisper_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        
        with open(audio_path, 'rb') as audio_file:
            transcription = whisper_client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                response_format="verbose_json",
                timestamp_granularities=["word"]
            )
        
        # Extract words with timestamps
        words = []
        if hasattr(transcription, 'words') and transcription.words:
            words = transcription.words
        elif hasattr(transcription, 'segments'):
            for segment in transcription.segments:
                if hasattr(segment, 'words'):
                    words.extend(segment.words)
        
        if not words:
            print("Whisper returned no word timestamps, falling back to estimated timing")
            return None, False
        
        print(f"Whisper returned {len(words)} word timestamps for caption sync")
        
        style = CAPTION_TEMPLATES.get(template, CAPTION_TEMPLATES['bold_pop'])
        
        margin_v = {'top': 100, 'center': int(video_height/2 - 50), 'bottom': 150}.get(position, 150)
        alignment = {'top': 8, 'center': 5, 'bottom': 2}.get(position, 2)
        
        bold_val = -1 if style['bold'] else 0
        back_color = style.get('back_color', '&H00000000')
        border_style = 3 if template == 'boxed' else 1
        
        ass_header = f"""[Script Info]
Title: Whisper-Synced Captions
ScriptType: v4.00+
PlayResX: {video_width}
PlayResY: {video_height}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{style['font']},{style['base_size']},{style['primary_color']},&H000000FF,{style['outline_color']},{back_color},{bold_val},0,0,0,100,100,0,0,{border_style},{style['outline']},{style['shadow']},{alignment},40,40,{margin_v},1
Style: Highlight,{style['font']},{style['highlight_size']},{style['highlight_color']},&H000000FF,{style['outline_color']},{back_color},{bold_val},0,0,0,100,100,0,0,{border_style},{style['outline']},{style['shadow']},{alignment},40,40,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        
        def format_ass_time(seconds):
            h = int(seconds // 3600)
            m = int((seconds % 3600) // 60)
            s = seconds % 60
            return f"{h}:{m:02d}:{s:05.2f}"
        
        # Animation effect based on style
        if style['animation'] == 'pop':
            anim_effect = r"\fscx110\fscy110\t(0,100,\fscx100\fscy100)"
        elif style['animation'] == 'bounce':
            anim_effect = r"\fscx120\fscy120\t(0,80,\fscx100\fscy100)"
        elif style['animation'] == 'glow':
            anim_effect = r"\blur3\t(0,150,\blur0)"
        elif style['animation'] == 'fade':
            anim_effect = r"\alpha&HFF&\t(0,100,\alpha&H00&)"
        elif style['animation'] == 'slide':
            anim_effect = r"\fscx105\t(0,100,\fscx100)"
        else:
            anim_effect = ""
        
        events = []
        chunk_size = 4
        
        # Group words into phrases using actual Whisper timestamps
        phrases = []
        current_phrase = []
        current_start = None
        current_end = 0
        
        for word_data in words:
            if isinstance(word_data, dict):
                word = word_data.get('word', '').strip()
                start = word_data.get('start', 0)
                end = word_data.get('end', 0)
            else:
                word = getattr(word_data, 'word', '').strip()
                start = getattr(word_data, 'start', 0)
                end = getattr(word_data, 'end', 0)
            
            if not word:
                continue
            
            if uppercase:
                word = word.upper()
            
            if current_start is None:
                current_start = start
            
            current_phrase.append({'word': word, 'start': start, 'end': end})
            current_end = end
            
            # Break into phrases at punctuation or every 4 words
            if len(current_phrase) >= chunk_size or word.rstrip().endswith(('.', '!', '?', ',')):
                phrases.append({
                    'words': current_phrase,
                    'start': current_start,
                    'end': current_end
                })
                current_phrase = []
                current_start = None
        
        if current_phrase:
            phrases.append({
                'words': current_phrase,
                'start': current_start,
                'end': current_end
            })
        
        # Generate ASS events with word-by-word highlighting using actual timestamps
        for phrase in phrases:
            phrase_words = phrase['words']
            
            for i, word_data in enumerate(phrase_words):
                word_start = word_data['start']
                word_end = word_data['end']
                
                before_words = [w['word'] for w in phrase_words[:i]]
                after_words = [w['word'] for w in phrase_words[i+1:]]
                current_word = word_data['word']
                
                text_parts = []
                if before_words:
                    text_parts.append("{\\rDefault}" + ' '.join(before_words) + " ")
                text_parts.append("{\\rHighlight" + anim_effect + "}" + current_word)
                if after_words:
                    text_parts.append("{\\rDefault} " + ' '.join(after_words))
                
                full_text = ''.join(text_parts)
                
                event_line = f"Dialogue: 0,{format_ass_time(word_start)},{format_ass_time(word_end)},Default,,0,0,0,,{full_text}"
                events.append(event_line)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(ass_header)
            f.write('\n'.join(events))
        
        print(f"Created Whisper-synced captions with {len(events)} events: {output_path}")
        return output_path, True
        
    except Exception as e:
        print(f"Whisper caption sync failed: {e}")
        return None, False


def create_dynamic_captions_ass(script_text, audio_duration, output_path, template='bold_pop', position='bottom', video_width=1080, video_height=1920):
    """
    Create ASS subtitle file with word-by-word animated captions.
    Features pop/scale animations synced to audio timing.
    NOTE: This uses ESTIMATED timing. Use create_whisper_synced_captions for true audio sync.
    """
    import re
    
    style = CAPTION_TEMPLATES.get(template, CAPTION_TEMPLATES['bold_pop'])
    
    clean_text = re.sub(r'\[.*?\]', '', script_text)
    clean_text = re.sub(r'\s+', ' ', clean_text).strip()
    words = clean_text.split()
    
    margin_v = {'top': 100, 'center': int(video_height/2 - 50), 'bottom': 150}.get(position, 150)
    alignment = {'top': 8, 'center': 5, 'bottom': 2}.get(position, 2)
    
    bold_val = -1 if style['bold'] else 0
    back_color = style.get('back_color', '&H00000000')
    
    border_style = 3 if template == 'boxed' else 1
    
    ass_header = f"""[Script Info]
Title: Dynamic Captions
ScriptType: v4.00+
PlayResX: {video_width}
PlayResY: {video_height}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{style['font']},{style['base_size']},{style['primary_color']},&H000000FF,{style['outline_color']},{back_color},{bold_val},0,0,0,100,100,0,0,{border_style},{style['outline']},{style['shadow']},{alignment},40,40,{margin_v},1
Style: Highlight,{style['font']},{style['highlight_size']},{style['highlight_color']},&H000000FF,{style['outline_color']},{back_color},{bold_val},0,0,0,100,100,0,0,{border_style},{style['outline']},{style['shadow']},{alignment},40,40,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    
    if not words:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(ass_header)
        return output_path
    
    seconds_per_word = audio_duration / len(words)
    
    def format_ass_time(seconds):
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = seconds % 60
        return f"{h}:{m:02d}:{s:05.2f}"
    
    if style['animation'] == 'pop':
        anim_effect = r"\fscx110\fscy110\t(0,100,\fscx100\fscy100)"
    elif style['animation'] == 'bounce':
        anim_effect = r"\fscx120\fscy120\t(0,80,\fscx100\fscy100)"
    elif style['animation'] == 'glow':
        anim_effect = r"\blur3\t(0,150,\blur0)"
    elif style['animation'] == 'fade':
        anim_effect = r"\alpha&HFF&\t(0,100,\alpha&H00&)"
    elif style['animation'] == 'slide':
        anim_effect = r"\fscx105\t(0,100,\fscx100)"
    else:
        anim_effect = ""
    
    events = []
    chunk_size = 4
    
    for chunk_idx in range(0, len(words), chunk_size):
        chunk_words = words[chunk_idx:chunk_idx + chunk_size]
        chunk_start = chunk_idx * seconds_per_word
        chunk_end = min((chunk_idx + len(chunk_words)) * seconds_per_word, audio_duration)
        
        for word_offset, word in enumerate(chunk_words):
            word_start = chunk_start + (word_offset * seconds_per_word)
            word_end = min(word_start + seconds_per_word, chunk_end)
            
            before_words = chunk_words[:word_offset]
            after_words = chunk_words[word_offset + 1:]
            
            text_parts = []
            if before_words:
                text_parts.append("{\\rDefault}" + ' '.join(before_words) + " ")
            text_parts.append("{\\rHighlight" + anim_effect + "}" + word)
            if after_words:
                text_parts.append("{\\rDefault} " + ' '.join(after_words))
            
            full_text = ''.join(text_parts)
            
            event_line = f"Dialogue: 0,{format_ass_time(word_start)},{format_ass_time(word_end)},Default,,0,0,0,,{full_text}"
            events.append(event_line)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(ass_header)
        f.write('\n'.join(events))
    
    return output_path


def create_word_synced_subtitles(script_text, audio_duration, output_path):
    """
    Create SRT subtitle file with word-level timing based on audio duration.
    Distributes words evenly across the audio duration.
    """
    import re
    
    clean_text = re.sub(r'\[.*?\]', '', script_text)
    clean_text = re.sub(r'\s+', ' ', clean_text).strip()
    words = clean_text.split()
    
    if not words:
        return output_path
    
    words_per_second = len(words) / max(audio_duration, 1)
    seconds_per_word = 1 / max(words_per_second, 0.5)
    
    chunk_size = 4
    chunks = []
    for i in range(0, len(words), chunk_size):
        chunks.append(' '.join(words[i:i+chunk_size]))
    
    with open(output_path, 'w', encoding='utf-8') as f:
        for i, chunk in enumerate(chunks):
            start_time = i * chunk_size * seconds_per_word
            end_time = min((i + 1) * chunk_size * seconds_per_word, audio_duration)
            
            def format_time(seconds):
                h = int(seconds // 3600)
                m = int((seconds % 3600) // 60)
                s = int(seconds % 60)
                ms = int((seconds % 1) * 1000)
                return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
            
            f.write(f"{i+1}\n")
            f.write(f"{format_time(start_time)} --> {format_time(end_time)}\n")
            f.write(f"{chunk}\n\n")
    
    return output_path


def generate_video_description(script_text, max_length=280):
    """
    Generate a social media description from script text.
    Returns a concise, engaging caption with hashtags.
    """
    import re
    
    # Clean script text
    clean_text = re.sub(r'\[.*?\]', '', script_text)  # Remove tags
    clean_text = re.sub(r'\s+', ' ', clean_text).strip()
    
    # Extract first sentence or two as hook
    sentences = re.split(r'[.!?]', clean_text)
    hook = sentences[0].strip() if sentences else clean_text[:100]
    
    if len(hook) > max_length - 50:
        hook = hook[:max_length - 50] + '...'
    
    # Add relevant hashtags based on common themes
    hashtags = '#Framd #ContentCreation #VideoMaker'
    
    description = f"{hook}\n\n{hashtags}"
    
    return description[:max_length * 2]  # Allow some overflow for hashtags


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
    50: 500,     # 50 tokens = $5.00
    100: 200,    # 100 tokens = $2.00 (legacy)
    150: 1200,   # 150 tokens = $12.00
    400: 2500,   # 400 tokens = $25.00
    500: 800,    # 500 tokens = $8.00 (legacy)
    1000: 5000,  # 1000 tokens = $50.00
    2000: 2500   # 2000 tokens = $25.00 (legacy)
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

@app.route('/create-token-checkout', methods=['POST'])
def create_token_checkout():
    """Create a Stripe checkout session for direct token purchase."""
    try:
        data = request.get_json()
        token_amount = data.get('tokens')
        
        if not token_amount:
            return jsonify({'error': 'Missing token amount'}), 400
        
        # Server-side price lookup only - ignore any client-provided price
        if token_amount not in TOKEN_PACKAGES:
            return jsonify({'error': 'Invalid token amount'}), 400
        
        price_cents = TOKEN_PACKAGES[token_amount]
        
        _, secret_key = get_stripe_credentials()
        if not secret_key:
            return jsonify({'error': 'Payment not configured'}), 500
        
        stripe.api_key = secret_key
        
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
                        'name': f'{token_amount} Framd Tokens',
                        'description': f'Unlock video rendering, AI voices, auto-generator & all premium features. Tokens never expire.',
                    },
                    'unit_amount': price_cents,
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=f'{base_url}/?success=true&tokens={token_amount}',
            cancel_url=f'{base_url}/?canceled=true',
            metadata={
                'token_amount': str(token_amount),
                'purchase_type': 'token_pack'
            }
        )
        
        return jsonify({'url': checkout_session.url})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

SUBSCRIPTION_TIERS = {
    'creator': {
        'name': 'Framd Creator',
        'price_cents': 1000,  # $10/month
        'tokens': 300,
        'description': '300 tokens/month, video export, premium voices'
    },
    'pro': {
        'name': 'Framd Pro',
        'price_cents': 2500,  # $25/month
        'tokens': 1000,
        'description': '1000 tokens/month, unlimited revisions, auto-generator'
    }
}

@app.route('/create-subscription', methods=['POST'])
def create_subscription():
    """Create a Stripe subscription checkout session for Creator or Pro tier."""
    try:
        from models import Subscription
        from flask_login import current_user
        
        data = request.get_json() or {}
        tier = data.get('tier', 'pro')
        
        if tier not in SUBSCRIPTION_TIERS:
            return jsonify({'error': 'Invalid tier'}), 400
        
        tier_info = SUBSCRIPTION_TIERS[tier]
        
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
                        'name': tier_info['name'],
                        'description': tier_info['description'],
                    },
                    'unit_amount': tier_info['price_cents'],
                    'recurring': {
                        'interval': 'month',
                    },
                },
                'quantity': 1,
            }],
            mode='subscription',
            success_url=f'{base_url}/?subscription=success&tier={tier}',
            cancel_url=f'{base_url}/?subscription=canceled',
            metadata={
                'user_id': user_id,
                'plan': tier
            }
        )
        
        return jsonify({'url': checkout_session.url})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/subscribe')
def subscribe_redirect():
    """Redirect to subscription checkout based on tier query param."""
    tier = request.args.get('tier', 'pro')
    
    if tier not in SUBSCRIPTION_TIERS:
        tier = 'pro'
    
    tier_info = SUBSCRIPTION_TIERS[tier]
    
    _, secret_key = get_stripe_credentials()
    if not secret_key:
        return redirect('/?error=payment_not_configured')
    
    stripe.api_key = secret_key
    
    domains = os.environ.get('REPLIT_DOMAINS', 'localhost:5000')
    domain = domains.split(',')[0] if domains else 'localhost:5000'
    protocol = 'https' if 'replit' in domain else 'http'
    base_url = f"{protocol}://{domain}"
    
    from flask_login import current_user
    user_id = None
    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('dev_user_id', 'dev_user')
    
    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': tier_info['name'],
                        'description': tier_info['description'],
                    },
                    'unit_amount': tier_info['price_cents'],
                    'recurring': {
                        'interval': 'month',
                    },
                },
                'quantity': 1,
            }],
            mode='subscription',
            success_url=f'{base_url}/?subscription=success&tier={tier}',
            cancel_url=f'{base_url}/?subscription=canceled',
            metadata={
                'user_id': user_id,
                'plan': tier
            }
        )
        return redirect(checkout_session.url)
    except Exception as e:
        print(f"Stripe error: {e}")
        return redirect(f'/?error=checkout_failed')


@app.route('/create-customer-portal', methods=['POST'])
def customer_portal():
    """Create a Stripe Customer Portal session for managing subscriptions."""
    from models import Subscription
    from flask_login import current_user
    
    _, secret_key = get_stripe_credentials()
    if not secret_key:
        return jsonify({'error': 'Payment not configured'}), 500
    
    stripe.api_key = secret_key
    
    user_id = None
    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('dev_user_id')
    
    if not user_id:
        return jsonify({'error': 'Not logged in'}), 401
    
    sub = Subscription.query.filter_by(user_id=user_id).first()
    if not sub or not sub.stripe_customer_id:
        return jsonify({'error': 'No active subscription found'}), 404
    
    domains = os.environ.get('REPLIT_DOMAINS', 'localhost:5000')
    domain = domains.split(',')[0] if domains else 'localhost:5000'
    protocol = 'https' if 'replit' in domain else 'http'
    base_url = f"{protocol}://{domain}"
    
    try:
        portal_session = stripe.billing_portal.Session.create(
            customer=sub.stripe_customer_id,
            return_url=f'{base_url}/?settings=billing'
        )
        return jsonify({'url': portal_session.url})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/subscription-status', methods=['GET'])
def subscription_status():
    """Check current user's subscription status and token balance."""
    from models import Subscription, User
    from flask_login import current_user
    from datetime import datetime
    
    # Dev mode always has Pro access
    if session.get('dev_mode'):
        return jsonify({
            'tier': 'pro', 
            'status': 'active', 
            'is_pro': True, 
            'lifetime': True,
            'token_balance': 1000,
            'monthly_tokens': 1000
        })
    
    user_id = None
    user_email = None
    if current_user.is_authenticated:
        user_id = current_user.id
        user_email = current_user.email
    else:
        user_id = session.get('dev_user_id')
    
    if not user_id:
        return jsonify({
            'tier': 'free', 
            'status': 'inactive', 
            'is_pro': False,
            'token_balance': 50,
            'monthly_tokens': 50
        })
    
    # Lifetime Pro for specific email
    if user_email and user_email.lower() == 'alonbenmeir9@gmail.com':
        return jsonify({
            'tier': 'pro', 
            'status': 'active', 
            'is_pro': True, 
            'lifetime': True,
            'token_balance': 1000,
            'monthly_tokens': 1000
        })
    
    sub = Subscription.query.filter_by(user_id=user_id).first()
    if sub:
        tier_tokens = {'free': 50, 'creator': 300, 'pro': 1000}
        monthly = tier_tokens.get(sub.tier, 50)
        
        # Initialize token balance if needed
        if sub.token_balance is None:
            sub.token_balance = monthly
            db.session.commit()
        
        return jsonify({
            'tier': sub.tier,
            'status': sub.status,
            'is_pro': sub.tier == 'pro' and sub.status == 'active',
            'is_creator': sub.tier == 'creator' and sub.status == 'active',
            'token_balance': sub.token_balance,
            'monthly_tokens': monthly,
            'current_period_end': sub.current_period_end.isoformat() if sub.current_period_end else None
        })
    
    return jsonify({
        'tier': 'free', 
        'status': 'inactive', 
        'is_pro': False,
        'token_balance': 50,
        'monthly_tokens': 50
    })


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
    
    # Token amounts per tier
    TIER_TOKENS = {'free': 50, 'creator': 300, 'pro': 1000}
    
    if event['type'] == 'checkout.session.completed':
        session_data = event['data']['object']
        
        if session_data.get('mode') == 'subscription':
            user_id = session_data.get('metadata', {}).get('user_id')
            plan = session_data.get('metadata', {}).get('plan', 'pro')
            subscription_id = session_data.get('subscription')
            customer_id = session_data.get('customer')
            
            if user_id:
                sub = Subscription.query.filter_by(user_id=user_id).first()
                if not sub:
                    sub = Subscription(user_id=user_id)
                    db.session.add(sub)
                
                sub.stripe_customer_id = customer_id
                sub.stripe_subscription_id = subscription_id
                sub.tier = plan  # 'creator' or 'pro'
                sub.status = 'active'
                sub.token_balance = TIER_TOKENS.get(plan, 300)
                sub.token_refresh_date = datetime.now()
                db.session.commit()
                print(f"[stripe-webhook] New {plan} subscription for {user_id}, {sub.token_balance} tokens")
        else:
            token_amount = int(session_data.get('metadata', {}).get('token_amount', 0))
            if token_amount > 0:
                token_entry = UserTokens.query.first()
                if token_entry:
                    token_entry.balance += token_amount
                    db.session.commit()
    
    elif event['type'] == 'invoice.paid':
        # Subscription renewal - refresh tokens
        invoice_data = event['data']['object']
        subscription_id = invoice_data.get('subscription')
        
        if subscription_id:
            sub = Subscription.query.filter_by(stripe_subscription_id=subscription_id).first()
            if sub and sub.status == 'active':
                # Refresh tokens to full monthly allowance
                sub.token_balance = TIER_TOKENS.get(sub.tier, 50)
                sub.token_refresh_date = datetime.now()
                db.session.commit()
                print(f"[stripe-webhook] Token refresh for {sub.user_id}: {sub.token_balance} tokens")
    
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
            sub.token_balance = TIER_TOKENS['free']  # Reset to free tier tokens
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
    TIER_TOKENS = {'free': 50, 'creator': 300, 'pro': 1000}
    user_id = get_user_id()
    if user_id:
        user = User.query.get(user_id)
        if user:
            # Check for subscription to get monthly tokens
            sub = Subscription.query.filter_by(user_id=user_id).first()
            tier = sub.tier if sub and sub.status == 'active' else 'free'
            monthly_tokens = TIER_TOKENS.get(tier, 50)
            return jsonify({
                'success': True,
                'balance': user.tokens or 0,
                'monthly_tokens': monthly_tokens,
                'tier': tier
            })
    # Fallback for unauthenticated or legacy
    token_entry = UserTokens.query.first()
    return jsonify({
        'success': True,
        'balance': token_entry.balance if token_entry else 0,
        'monthly_tokens': 50,
        'tier': 'free'
    })

@app.route('/deduct-tokens', methods=['POST'])
def deduct_tokens():
    data = request.get_json()
    amount = data.get('amount', 35)
    user_id = get_user_id()
    if user_id:
        user = User.query.get(user_id)
        if user:
            user.tokens = max(0, (user.tokens or 0) - amount)
            db.session.commit()
            return jsonify({'success': True, 'balance': user.tokens})
    # Fallback for legacy
    token_entry = UserTokens.query.first()
    if token_entry:
        token_entry.balance -= amount
        db.session.commit()
        return jsonify({'success': True, 'balance': token_entry.balance})
    return jsonify({'success': False, 'error': 'No token record'}), 400

# Asset Library - Legal Media with Licensing
ALLOWED_LICENSES = ['CC0', 'Public Domain', 'CC BY', 'CC BY-SA', 'CC BY 4.0', 'CC BY-SA 4.0', 'Unsplash License', 'Pixabay License', 'Pexels License']

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
    
    # STEP 2: Check for allowed licenses (more permissive patterns)
    if 'cc0' in license_lower or 'cc-zero' in license_lower or 'cc zero' in license_lower:
        return True, 'CC0', None
    if 'public domain' in license_lower or license_lower == 'pd' or 'pd-' in license_lower:
        return True, 'Public Domain', None
    if 'cc-by-sa' in license_lower or 'cc by-sa' in license_lower or 'ccbysa' in license_lower:
        return True, 'CC BY-SA', None
    if 'cc-by' in license_lower or 'cc by' in license_lower or 'ccby' in license_lower:
        return True, 'CC BY', None
    if 'pexels' in license_lower:
        return True, 'Pexels License', None
    if 'pixabay' in license_lower:
        return True, 'Pixabay License', None
    if 'unsplash' in license_lower:
        return True, 'Unsplash License', None
    # Generic CC pattern (just "cc" followed by version)
    if license_lower.startswith('cc ') or license_lower.startswith('cc-'):
        return True, 'Creative Commons', None
    # FAL (Free Art License)
    if 'fal' in license_lower or 'free art' in license_lower:
        return True, 'FAL', None
    # GFDL (GNU Free Documentation License) - commonly used on Wikimedia
    if 'gfdl' in license_lower or 'gnu free documentation' in license_lower:
        return True, 'GFDL', None
    
    # Unknown license - allow but mark it (less restrictive for better visual coverage)
    if license_lower and len(license_lower) > 0:
        return True, license_short[:20], None
    
    return False, None, f'Empty license: {license_short}'

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
    import re as regex
    
    data = request.get_json()
    script = data.get('script', '')
    user_guidance = data.get('user_guidance', '')  # Optional user direction
    
    if not script:
        return jsonify({'success': False, 'error': 'No script provided'}), 400
    
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
        visual_board = call_ai(user_content, system_prompt, json_output=True, max_tokens=1500)
        if not visual_board:
            visual_board = {"sections": []}
        
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
            
            # STEP 2: Search Wikimedia Commons for images (primary) and videos (secondary)
            if len(section['suggested_videos']) < 4:
                for query in section.get('search_queries', [])[:2]:
                    try:
                        search_url = 'https://commons.wikimedia.org/w/api.php'
                        wiki_headers = {'User-Agent': 'EchoEngine/1.0 (content creation tool)'}
                        
                        pages = {}
                        
                        # Strategy 1: Search for IMAGES first (much more content available)
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
                            
                            # Use thumbnail if available, otherwise use full URL
                            thumbnail_url = imageinfo.get('thumburl') or imageinfo.get('url')
                            download_url = imageinfo.get('url')
                            
                            # Skip if no valid URLs
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
            
            # STEP 3: Fallback to Pexels if still not enough visuals
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
            
            # Log how many visuals were found for this section
            print(f"[Visual Board] Section has {len(section['suggested_videos'])} visuals")
        
        # Log total sections
        total_visuals = sum(len(s.get('suggested_videos', [])) for s in visual_board.get('sections', []))
        print(f"[Visual Board] Total: {len(visual_board.get('sections', []))} sections, {total_visuals} visuals")
        
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
            allowed_domains = ['wikimedia.org', 'upload.wikimedia.org', 'unsplash.com', 'images.unsplash.com', 'pixabay.com', 'pexels.com', 'images.pexels.com']
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
            'workflow_step': getattr(p, 'workflow_step', 1) or 1,
            'is_successful': p.is_successful,
            'success_score': p.success_score,
            'auto_generate_enabled': getattr(p, 'auto_generate_enabled', False) or False,
            'liked': getattr(p, 'liked', None),
            'template_type': getattr(p, 'template_type', 'start_from_scratch') or 'start_from_scratch',
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
    template_type = data.get('template_type', 'start_from_scratch')
    
    project = Project(
        user_id=user_id,
        name=name,
        description=description,
        template_type=template_type,
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


@app.route('/generate-project-metadata', methods=['POST'])
def generate_project_metadata():
    """Generate AI project name (3 words max) and description from idea/script."""
    data = request.get_json() or {}
    idea = data.get('idea', '')
    script = data.get('script', '')
    
    content = script if script else idea
    if not content:
        return jsonify({'success': False, 'error': 'No content provided'})
    
    prompt = f"""Based on this content, generate a project name and description.

Content: {content[:1500]}

Rules:
1. Project name: Maximum 3 words, punchy and memorable (like "Oslo Accord Truth" or "Power Dynamics")
2. Description: One sentence, under 15 words, capturing the core idea

Return ONLY valid JSON:
{{"name": "Three Word Name", "description": "One sentence description here."}}"""

    import re
    
    # Try Claude first (primary AI)
    try:
        print(f"[ProjectMetadata] Generating title with Claude for: {content[:50]}...")
        response = anthropic_client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}]
        )
        result_text = response.content[0].text.strip()
        print(f"[ProjectMetadata] Claude response: {result_text}")
        
        json_match = re.search(r'\{[^}]+\}', result_text)
        if json_match:
            metadata = json.loads(json_match.group())
            name = metadata.get('name', 'Untitled')[:50]
            print(f"[ProjectMetadata] Generated name: {name}")
            return jsonify({
                'success': True,
                'name': name,
                'description': metadata.get('description', '')[:200]
            })
    except Exception as e:
        print(f"[ProjectMetadata] Claude failed: {e}")
    
    # Fallback to xAI
    try:
        print(f"[ProjectMetadata] Trying xAI fallback...")
        response = xai_client.chat.completions.create(
            model="grok-3",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=100,
            temperature=0.7
        )
        result_text = response.choices[0].message.content.strip()
        print(f"[ProjectMetadata] xAI response: {result_text}")
        
        json_match = re.search(r'\{[^}]+\}', result_text)
        if json_match:
            metadata = json.loads(json_match.group())
            name = metadata.get('name', 'Untitled')[:50]
            print(f"[ProjectMetadata] Generated name: {name}")
            return jsonify({
                'success': True,
                'name': name,
                'description': metadata.get('description', '')[:200]
            })
    except Exception as e:
        print(f"[ProjectMetadata] xAI failed: {e}")
    
    # Final fallback: extract first few words as name
    words = content.split()[:3]
    fallback_name = ' '.join(words)[:50]
    print(f"[ProjectMetadata] Using fallback name: {fallback_name}")
    return jsonify({
        'success': True,
        'name': fallback_name,
        'description': content[:100]
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
    import re
    
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
        # Auto-generate title from script if still untitled
        if project.name in ['Untitled', 'Untitled Project', 'New Project', '']:
            script_text = data['script']
            # Try to extract hook (first meaningful line)
            lines = [l.strip() for l in script_text.split('\n') if l.strip() and not l.strip().startswith('[')]
            if lines:
                first_line = lines[0]
                # Remove character prefixes like "NARRATOR:" or "HOST:"
                first_line = re.sub(r'^[A-Z]+:\s*', '', first_line)
                # Truncate to 50 chars max
                if len(first_line) > 50:
                    first_line = first_line[:47] + '...'
                project.name = first_line
    if 'visual_plan' in data:
        project.visual_plan = data['visual_plan']
    if 'voice_assignments' in data:
        project.voice_assignments = data['voice_assignments']
    if 'caption_settings' in data:
        project.caption_settings = data['caption_settings']
    if 'video_path' in data:
        project.video_path = data['video_path']
    
    db.session.commit()
    
    return jsonify({'success': True, 'project_id': project.id, 'name': project.name})


@app.route('/project/<int:project_id>', methods=['DELETE'])
def delete_project(project_id):
    """Delete a project."""
    from models import Project
    
    user_id = get_user_id()
    if not user_id:
        return jsonify({'error': 'Not authenticated'}), 401
    
    project = Project.query.filter_by(id=project_id, user_id=user_id).first()
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    
    db.session.delete(project)
    db.session.commit()
    
    return jsonify({'success': True, 'message': 'Project deleted'})


@app.route('/projects/<int:project_id>/workflow-step', methods=['POST'])
def update_project_workflow_step(project_id):
    """Update the workflow step for a project."""
    from models import Project
    
    user_id = get_user_id()
    if not user_id:
        return jsonify({'error': 'Not authenticated'}), 401
    
    project = Project.query.filter_by(id=project_id, user_id=user_id).first()
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    
    data = request.get_json() or {}
    step = data.get('step', 1)
    
    project.workflow_step = min(max(step, 1), 8)  # Clamp between 1-8
    db.session.commit()
    
    return jsonify({'success': True, 'workflow_step': project.workflow_step})


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


@app.route('/projects/<int:project_id>/toggle-auto-generate', methods=['POST'])
def toggle_auto_generate(project_id):
    """Toggle auto-generate for a project. Requires Pro subscription and 5+ liked videos."""
    from models import Project, Subscription
    
    user_id = get_user_id()
    if not user_id:
        return jsonify({'error': 'Not authenticated'}), 401
    
    project = Project.query.filter_by(id=project_id, user_id=user_id).first()
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    
    sub = Subscription.query.filter_by(user_id=user_id).first()
    if not sub or sub.tier != 'pro':
        return jsonify({'error': 'Pro subscription required for auto-generation'}), 403
    
    liked_count = Project.query.filter_by(user_id=user_id, liked=True).count()
    if liked_count < 5:
        return jsonify({'error': f'Need 5 liked videos to unlock auto-generation ({liked_count}/5)'}), 403
    
    data = request.get_json() or {}
    if 'enable' in data:
        project.auto_generate_enabled = bool(data['enable'])
    else:
        project.auto_generate_enabled = not project.auto_generate_enabled
    db.session.commit()
    
    return jsonify({
        'success': True,
        'auto_generate_enabled': project.auto_generate_enabled
    })


@app.route('/projects/<int:project_id>/generated-drafts', methods=['GET'])
def get_generated_drafts(project_id):
    """Get all generated drafts for a project."""
    from models import Project, GeneratedDraft
    
    user_id = get_user_id()
    if not user_id:
        return jsonify({'error': 'Not authenticated'}), 401
    
    project = Project.query.filter_by(id=project_id, user_id=user_id).first()
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    
    drafts = GeneratedDraft.query.filter_by(project_id=project_id, status='pending').order_by(GeneratedDraft.created_at.desc()).limit(3).all()
    
    return jsonify({
        'drafts': [{
            'id': d.id,
            'script': d.script,
            'visual_plan': d.visual_plan,
            'sound_plan': d.sound_plan,
            'angle_used': d.angle_used,
            'vibe_used': d.vibe_used,
            'hook_type': d.hook_type,
            'clips_used': d.clips_used,
            'trend_data': d.trend_data,
            'created_at': d.created_at.isoformat() if d.created_at else None
        } for d in drafts],
        'can_generate_more': len(drafts) < 3
    })


@app.route('/projects/<int:project_id>/generate-drafts', methods=['POST'])
def generate_drafts(project_id):
    """Generate new AI drafts for a project using trend research and learned patterns."""
    from models import Project, GeneratedDraft, AILearning, Subscription
    import json
    
    user_id = get_user_id()
    if not user_id:
        return jsonify({'error': 'Not authenticated'}), 401
    
    project = Project.query.filter_by(id=project_id, user_id=user_id).first()
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    
    sub = Subscription.query.filter_by(user_id=user_id).first()
    if not sub or sub.tier != 'pro':
        return jsonify({'error': 'Pro subscription required'}), 403
    
    from datetime import date
    ai_learning = AILearning.query.filter_by(user_id=user_id).first()
    if not ai_learning:
        ai_learning = AILearning(user_id=user_id)
        db.session.add(ai_learning)
    
    if ai_learning.last_draft_reset != date.today():
        ai_learning.drafts_generated_today = 0
        ai_learning.last_draft_reset = date.today()
    
    db.session.commit()
    
    daily_limit = ai_learning.daily_draft_limit or 3
    generated_today = ai_learning.drafts_generated_today or 0
    
    if generated_today >= daily_limit:
        return jsonify({
            'error': 'Daily draft limit reached',
            'daily_limit': daily_limit,
            'generated_today': generated_today,
            'remaining': 0
        }), 429
    
    pending_count = GeneratedDraft.query.filter_by(project_id=project_id, status='pending').count()
    if pending_count >= 3:
        return jsonify({'error': 'Maximum 3 pending drafts. Approve or skip existing drafts first.'}), 400
    
    drafts_to_generate = 3 - pending_count
    
    existing_drafts = GeneratedDraft.query.filter_by(project_id=project_id).all()
    used_angles = [d.angle_used for d in existing_drafts if d.angle_used]
    used_vibes = [d.vibe_used for d in existing_drafts if d.vibe_used]
    used_hooks = [d.hook_type for d in existing_drafts if d.hook_type]
    
    learned_patterns = {
        'hooks': ai_learning.learned_hooks if ai_learning else [],
        'voices': ai_learning.learned_voices if ai_learning else [],
        'styles': ai_learning.learned_styles if ai_learning else [],
        'topics': ai_learning.learned_topics if ai_learning else []
    }
    
    topic = project.description or project.name or "general content"
    trend_data = None
    try:
        from context_engine import research_trends
        trend_data = research_trends(topic)
    except Exception as e:
        logging.warning(f"Trend research failed: {e}")
        trend_data = {'hooks': [], 'formats': [], 'visuals': [], 'sounds': []}
    
    all_angles = ['contrarian', 'evidence-first', 'story-driven', 'philosophical', 'urgent', 'reflective', 'satirical', 'educational']
    all_vibes = ['serious', 'playful', 'urgent', 'reflective', 'provocative', 'calm', 'intense', 'witty']
    all_hook_types = ['question', 'bold-claim', 'statistic', 'story-opener', 'controversy', 'revelation', 'challenge', 'prediction']
    
    available_angles = [a for a in all_angles if a not in used_angles]
    available_vibes = [v for v in all_vibes if v not in used_vibes]
    available_hooks = [h for h in all_hook_types if h not in used_hooks]
    
    if not available_angles:
        available_angles = all_angles
    if not available_vibes:
        available_vibes = all_vibes
    if not available_hooks:
        available_hooks = all_hook_types
    
    import random
    generated_drafts = []
    
    from context_engine import get_template_guidelines
    template_type = project.template_type or 'start_from_scratch'
    template_dna = get_template_guidelines(template_type)
    
    for i in range(drafts_to_generate):
        angle = available_angles[i % len(available_angles)]
        vibe = available_vibes[i % len(available_vibes)]
        hook_type = available_hooks[i % len(available_hooks)]
        
        prompt = f"""Generate a 35-75 second video script for the topic: "{topic}"

TEMPLATE: {template_type.upper().replace('_', ' ')}
TEMPLATE TONE: {template_dna['tone']}
TEMPLATE VOICE: {template_dna['voice']}
TEMPLATE HOOK STYLE: {template_dna['hook_style']}
TEMPLATE PACING: {template_dna['pacing']}
HOW TO APPLY TRENDS: {template_dna['trend_application']}
ALLOWED FOR THIS TEMPLATE: {', '.join(template_dna['allowed_overrides'])}

TREND RESEARCH (apply WITHIN the template tone):
{json.dumps(trend_data, indent=2) if trend_data else 'No trend data available - lean on template defaults'}

USER'S LEARNED PATTERNS (incorporate their style):
{json.dumps(learned_patterns, indent=2)}

CONSTRAINTS FOR THIS DRAFT:
- Angle: {angle} (the perspective/approach)
- Vibe: {vibe} (the emotional tone)
- Hook Type: {hook_type} (how to start)

IMPORTANT: Stay in the template's voice. Trends inform HOW you execute, not WHAT tone you use.

UPLOADED CLIPS TO REFERENCE:
{json.dumps(project.uploaded_clips or [], indent=2)}

Generate a complete script with:
1. A strong hook using the {hook_type} format
2. Clear anchor points: HOOK, CLAIM, EVIDENCE, PIVOT, COUNTER, CLOSER
3. Natural, human-sounding dialogue
4. Visual suggestions that match trending formats
5. Sound/music suggestions based on what's working (only if it genuinely helps)

Output as JSON:
{{
  "script": "The full script text with speaker labels if multi-character",
  "visual_plan": [{{"scene": 1, "description": "...", "source_suggestion": "..."}}],
  "sound_plan": {{"music_vibe": "...", "sfx_suggestions": ["..."], "reasoning": "why these sounds work for this content"}}
}}"""

        try:
            from context_engine import call_ai
            response = call_ai(prompt)
            
            try:
                if '```json' in response:
                    response = response.split('```json')[1].split('```')[0]
                elif '```' in response:
                    response = response.split('```')[1].split('```')[0]
                draft_data = json.loads(response.strip())
            except json.JSONDecodeError:
                draft_data = {
                    'script': response,
                    'visual_plan': [],
                    'sound_plan': {}
                }
            
            draft = GeneratedDraft(
                project_id=project_id,
                user_id=user_id,
                script=draft_data.get('script', ''),
                visual_plan=draft_data.get('visual_plan'),
                sound_plan=draft_data.get('sound_plan'),
                angle_used=angle,
                vibe_used=vibe,
                hook_type=hook_type,
                clips_used=project.uploaded_clips,
                trend_data=trend_data
            )
            db.session.add(draft)
            generated_drafts.append(draft)
            
        except Exception as e:
            logging.error(f"Draft generation failed: {e}")
            continue
    
    if generated_drafts:
        ai_learning.drafts_generated_today = (ai_learning.drafts_generated_today or 0) + len(generated_drafts)
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'drafts_generated': len(generated_drafts),
        'daily_limit': daily_limit,
        'generated_today': ai_learning.drafts_generated_today,
        'remaining': max(0, daily_limit - ai_learning.drafts_generated_today),
        'drafts': [{
            'id': d.id,
            'script': d.script,
            'visual_plan': d.visual_plan,
            'sound_plan': d.sound_plan,
            'angle_used': d.angle_used,
            'vibe_used': d.vibe_used,
            'hook_type': d.hook_type
        } for d in generated_drafts]
    })


@app.route('/generated-drafts/<int:draft_id>/action', methods=['POST'])
def draft_action(draft_id):
    """Handle draft feedback - like (approve) or dislike (skip with AI self-analysis)."""
    from models import GeneratedDraft, Project, AILearning
    import json
    
    user_id = get_user_id()
    if not user_id:
        return jsonify({'error': 'Not authenticated'}), 401
    
    draft = GeneratedDraft.query.filter_by(id=draft_id, user_id=user_id).first()
    if not draft:
        return jsonify({'error': 'Draft not found'}), 404
    
    data = request.get_json() or {}
    action = data.get('action')
    
    if action not in ['approve', 'skip']:
        return jsonify({'error': 'Invalid action. Use "approve" or "skip"'}), 400
    
    ai_learning = AILearning.query.filter_by(user_id=user_id).first()
    
    if action == 'approve':
        project = Project.query.get(draft.project_id)
        if project:
            project.script = draft.script
            project.visual_plan = draft.visual_plan
        draft.status = 'approved'
        
        if ai_learning:
            learned_hooks = ai_learning.learned_hooks or []
            first_line = draft.script.split('\n')[0][:100] if draft.script else ''
            if first_line and first_line not in learned_hooks:
                learned_hooks.append(first_line)
                ai_learning.learned_hooks = learned_hooks[:30]
            
            learned_styles = ai_learning.learned_styles or []
            style_pattern = {
                'angle': draft.angle_used,
                'vibe': draft.vibe_used,
                'hook_type': draft.hook_type,
                'success': True
            }
            learned_styles.append(style_pattern)
            ai_learning.learned_styles = learned_styles[-50:]
    else:
        draft.status = 'skipped'
        
        if ai_learning:
            try:
                from context_engine import call_ai
                analysis_prompt = f"""You generated a draft that was rejected. Analyze internally why it failed based on these guidelines:

CORE RULES:
- Hooks must be direct, not clickbait
- No filler, no buzzwords, no trend-chasing language
- Every line logically leads to the next
- Ending must close the loop
- Calm, clear, grounded tone - never sarcastic, smug, or preachy

THE REJECTED DRAFT:
Angle: {draft.angle_used}
Vibe: {draft.vibe_used}
Hook Type: {draft.hook_type}
Script (first 500 chars): {(draft.script or '')[:500]}

Analyze in 2-3 sentences what likely went wrong. Be specific about which guideline was violated. Output JSON:
{{"likely_issue": "...", "guideline_violated": "...", "avoid_in_future": "..."}}"""
                
                analysis = call_ai(analysis_prompt)
                try:
                    if '```json' in analysis:
                        analysis = analysis.split('```json')[1].split('```')[0]
                    elif '```' in analysis:
                        analysis = analysis.split('```')[1].split('```')[0]
                    analysis_data = json.loads(analysis.strip())
                except:
                    analysis_data = {'likely_issue': 'Could not parse analysis', 'raw': analysis[:200]}
                
                dislike_learnings = ai_learning.dislike_learnings or []
                dislike_learnings.append({
                    'draft_id': draft_id,
                    'angle': draft.angle_used,
                    'vibe': draft.vibe_used,
                    'hook_type': draft.hook_type,
                    'analysis': analysis_data
                })
                ai_learning.dislike_learnings = dislike_learnings[-20:]
            except Exception as e:
                logging.warning(f"AI self-analysis failed: {e}")
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'action': action,
        'project_id': draft.project_id
    })


@app.route('/draft-settings', methods=['GET'])
def get_draft_settings():
    """Get user's draft generation settings."""
    from models import AILearning
    from datetime import date
    
    user_id = get_user_id()
    if not user_id:
        return jsonify({'daily_limit': 3, 'generated_today': 0, 'remaining': 3})
    
    ai_learning = AILearning.query.filter_by(user_id=user_id).first()
    if not ai_learning:
        return jsonify({'daily_limit': 3, 'generated_today': 0, 'remaining': 3})
    
    if ai_learning.last_draft_reset != date.today():
        ai_learning.drafts_generated_today = 0
        ai_learning.last_draft_reset = date.today()
        db.session.commit()
    
    daily_limit = ai_learning.daily_draft_limit or 3
    generated = ai_learning.drafts_generated_today or 0
    
    return jsonify({
        'daily_limit': daily_limit,
        'generated_today': generated,
        'remaining': max(0, daily_limit - generated)
    })


@app.route('/draft-settings', methods=['POST'])
def update_draft_settings():
    """Update user's daily draft limit (1-10)."""
    from models import AILearning
    
    user_id = get_user_id()
    if not user_id:
        return jsonify({'error': 'Not authenticated'}), 401
    
    data = request.get_json() or {}
    new_limit = data.get('daily_limit')
    
    if not isinstance(new_limit, int) or new_limit < 1 or new_limit > 10:
        return jsonify({'error': 'Daily limit must be between 1 and 10'}), 400
    
    ai_learning = AILearning.query.filter_by(user_id=user_id).first()
    if not ai_learning:
        ai_learning = AILearning(user_id=user_id, daily_draft_limit=new_limit)
        db.session.add(ai_learning)
    else:
        ai_learning.daily_draft_limit = new_limit
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'daily_limit': new_limit
    })


@app.route('/auto-generate-status', methods=['GET'])
def get_auto_generate_status():
    """Get user's auto-generate eligibility status."""
    from models import Project, Subscription
    
    user_id = get_user_id()
    if not user_id:
        return jsonify({
            'eligible': False,
            'reason': 'not_authenticated',
            'liked_count': 0,
            'required_likes': 5,
            'has_pro': False
        })
    
    sub = Subscription.query.filter_by(user_id=user_id).first()
    has_pro = sub and sub.tier == 'pro'
    
    liked_count = Project.query.filter_by(user_id=user_id, liked=True).count()
    
    eligible = has_pro and liked_count >= 5
    
    if not has_pro:
        reason = 'needs_pro'
    elif liked_count < 5:
        reason = 'needs_likes'
    else:
        reason = 'eligible'
    
    return jsonify({
        'eligible': eligible,
        'reason': reason,
        'liked_count': liked_count,
        'required_likes': 5,
        'has_pro': has_pro
    })


@app.route('/save-caption-preferences', methods=['POST'])
def save_caption_preferences():
    """Save user's caption style preferences for AI learning."""
    from models import AILearning
    
    user_id = get_user_id()
    if not user_id:
        return jsonify({'error': 'Authentication required'}), 401
    
    data = request.get_json() or {}
    
    ai_learning = AILearning.query.filter_by(user_id=user_id).first()
    if not ai_learning:
        ai_learning = AILearning(user_id=user_id)
        db.session.add(ai_learning)
    
    # Store caption preferences in learned_styles
    caption_prefs = {
        'caption_position': data.get('caption_position', 'bottom'),
        'caption_offset': data.get('caption_offset', 10),
        'caption_size': data.get('caption_size', 22),
        'caption_opacity': data.get('caption_opacity', 80),
        'caption_color': data.get('caption_color', '#ffffff')
    }
    
    current_styles = ai_learning.learned_styles or []
    # Update or add caption preferences
    style_updated = False
    for i, style in enumerate(current_styles):
        if isinstance(style, dict) and style.get('type') == 'caption_prefs':
            current_styles[i] = {'type': 'caption_prefs', **caption_prefs}
            style_updated = True
            break
    
    if not style_updated:
        current_styles.append({'type': 'caption_prefs', **caption_prefs})
    
    ai_learning.learned_styles = current_styles
    db.session.commit()
    
    return jsonify({'success': True, 'message': 'Caption preferences saved'})


@app.route('/get-caption-preferences', methods=['GET'])
def get_caption_preferences():
    """Get user's saved caption style preferences."""
    from models import AILearning
    
    user_id = get_user_id()
    if not user_id:
        return jsonify({})
    
    ai_learning = AILearning.query.filter_by(user_id=user_id).first()
    if not ai_learning:
        return jsonify({})
    
    # Find caption preferences in learned_styles
    for style in (ai_learning.learned_styles or []):
        if isinstance(style, dict) and style.get('type') == 'caption_prefs':
            return jsonify({
                'caption_position': style.get('caption_position', 'bottom'),
                'caption_offset': style.get('caption_offset', 10),
                'caption_size': style.get('caption_size', 22),
                'caption_opacity': style.get('caption_opacity', 80),
                'caption_color': style.get('caption_color', '#ffffff')
            })
    
    return jsonify({})


@app.route('/video-history', methods=['GET'])
def get_video_history():
    """Get user's video download history."""
    from models import VideoHistory
    
    user_id = get_user_id()
    if not user_id:
        return jsonify({'videos': []})
    
    videos = VideoHistory.query.filter_by(user_id=user_id).order_by(VideoHistory.created_at.desc()).limit(50).all()
    
    return jsonify({
        'videos': [{
            'id': v.id,
            'project_name': v.project_name,
            'video_path': v.video_path,
            'thumbnail_path': v.thumbnail_path,
            'duration_seconds': v.duration_seconds,
            'format': v.format,
            'created_at': v.created_at.isoformat() if v.created_at else None
        } for v in videos]
    })


@app.route('/save-video-history', methods=['POST'])
def save_video_history():
    """Save a generated video to download history."""
    from models import VideoHistory
    
    user_id = get_user_id()
    if not user_id:
        return jsonify({'error': 'Authentication required'}), 401
    
    data = request.get_json() or {}
    
    video_history = VideoHistory(
        user_id=user_id,
        project_id=data.get('project_id'),
        project_name=data.get('project_name', 'Untitled Video'),
        video_path=data.get('video_path', ''),
        thumbnail_path=data.get('thumbnail_path'),
        duration_seconds=data.get('duration_seconds'),
        format=data.get('format', '9:16'),
        file_size_bytes=data.get('file_size_bytes'),
        captions_data=data.get('captions_data')
    )
    
    db.session.add(video_history)
    db.session.commit()
    
    return jsonify({'success': True, 'id': video_history.id})


@app.route('/email-preferences', methods=['GET'])
def get_email_preferences():
    """Get user's email notification preferences."""
    from models import EmailNotification
    
    user_id = get_user_id()
    if not user_id:
        return jsonify({})
    
    notifications = EmailNotification.query.filter_by(user_id=user_id).all()
    prefs = {n.notification_type: n.enabled for n in notifications}
    
    return jsonify({
        'video_ready': prefs.get('video_ready', True),
        'low_tokens': prefs.get('low_tokens', True),
        'weekly_digest': prefs.get('weekly_digest', False)
    })


@app.route('/email-preferences', methods=['POST'])
def save_email_preferences():
    """Save user's email notification preferences."""
    from models import EmailNotification
    
    user_id = get_user_id()
    if not user_id:
        return jsonify({'error': 'Authentication required'}), 401
    
    data = request.get_json() or {}
    
    for notif_type in ['video_ready', 'low_tokens', 'weekly_digest']:
        if notif_type in data:
            notif = EmailNotification.query.filter_by(user_id=user_id, notification_type=notif_type).first()
            if not notif:
                notif = EmailNotification(user_id=user_id, notification_type=notif_type)
                db.session.add(notif)
            notif.enabled = bool(data[notif_type])
    
    db.session.commit()
    
    return jsonify({'success': True})


@app.route('/start-background-render', methods=['POST'])
def start_background_render():
    """Start a video render in the background using database-backed job queue."""
    from flask_login import current_user
    from job_queue import JOB_QUEUE
    import uuid
    
    user_id = None
    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('dev_user_id')
    
    data = request.get_json() or {}
    
    project_id = data.get('project_id')
    quality_tier = data.get('quality_tier', 'good')
    
    job_data = {
        'scenes': data.get('scenes', []),
        'audio_path': data.get('audio_path', ''),
        'format': data.get('format', '9:16'),
        'captions': data.get('captions', {}),
        'script': data.get('script', ''),
        'project_name': data.get('project_name', 'Untitled'),
        'template': data.get('template', 'start_from_scratch')
    }
    
    job = JOB_QUEUE.add_job(
        user_id=user_id,
        project_id=project_id,
        quality_tier=quality_tier,
        job_data=job_data
    )
    
    if job:
        return jsonify({
            'success': True,
            'ok': True,
            'job_id': job['id'],
            'job': job,
            'message': 'Video rendering started. You can continue working while it processes.'
        })
    else:
        return jsonify({
            'success': False,
            'ok': False,
            'error': 'Failed to create job'
        }), 500


@app.route('/render-status/<job_id>', methods=['GET'])
def get_render_status(job_id):
    """Check the status of a background render job."""
    from job_queue import JOB_QUEUE
    
    job = JOB_QUEUE.get_job(job_id)
    
    if not job:
        if job_id in background_render_jobs:
            old_job = background_render_jobs[job_id]
            return jsonify({
                'status': old_job['status'],
                'progress': old_job['progress'],
                'video_url': old_job['video_url'],
                'error': old_job['error']
            })
        return jsonify({'error': 'Job not found'}), 404
    
    return jsonify({
        'status': job['status'],
        'progress': job.get('progress', {}).get('percent', 0),
        'video_url': job.get('result_url'),
        'error': job.get('error_message'),
        'job': job
    })


@app.route('/my-render-jobs', methods=['GET'])
def get_my_render_jobs():
    """Get all render jobs for the current user."""
    from flask_login import current_user
    
    user_id = None
    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('dev_user_id')
    
    if not user_id:
        return jsonify([])
    
    user_jobs = []
    for job_id, job in background_render_jobs.items():
        if job.get('user_id') == user_id:
            user_jobs.append({
                'job_id': job_id,
                'status': job['status'],
                'progress': job['progress'],
                'video_url': job['video_url'],
                'created_at': job.get('created_at')
            })
    
    user_jobs.sort(key=lambda x: x.get('created_at', 0), reverse=True)
    
    return jsonify(user_jobs[:10])


def format_user_error(error_msg):
    """Convert technical error messages to user-friendly versions."""
    error_lower = error_msg.lower()
    
    if 'api key' in error_lower or 'authentication' in error_lower:
        return "We're having trouble connecting to our AI service. Please try again in a moment."
    elif 'rate limit' in error_lower:
        return "Our AI is handling a lot of requests right now. Please wait a minute and try again."
    elif 'timeout' in error_lower or 'timed out' in error_lower:
        return "This is taking longer than expected. Please try again with a shorter script."
    elif 'no visual content' in error_lower or 'no scenes' in error_lower:
        return "Please add some visual content before generating your video."
    elif 'no audio' in error_lower or 'voiceover' in error_lower:
        return "Please generate a voiceover first before creating the video."
    elif 'insufficient tokens' in error_lower or 'not enough tokens' in error_lower:
        return "You don't have enough tokens for this video. Please add more tokens or upgrade your plan."
    elif 'file not found' in error_lower or 'no such file' in error_lower:
        return "Some files are missing. Please try regenerating your content."
    elif 'ffmpeg' in error_lower:
        return "There was an issue assembling your video. Please try again."
    elif 'connection' in error_lower or 'network' in error_lower:
        return "Connection issue. Please check your internet and try again."
    elif 'invalid' in error_lower and 'url' in error_lower:
        return "One of the media links appears to be broken. Try refreshing your visual content."
    else:
        return f"Something went wrong: {error_msg[:100]}. Please try again or contact support."


@app.route('/export-platform-format', methods=['POST'])
def export_platform_format():
    """Export video in platform-specific format with caption styles and post optimization."""
    import subprocess
    import uuid
    from context_engine import call_ai
    from PIL import Image, ImageDraw, ImageFont
    
    data = request.get_json() or {}
    video_url = data.get('video_url', '')
    platform = data.get('platform', 'tiktok')
    caption_style = data.get('caption_style', 'bold_centered')
    is_post_platform = data.get('is_post_platform', False)
    carousel_count = data.get('carousel_count', 5)
    script_text = data.get('script_text', '')
    project_id = data.get('project_id')
    
    if not video_url:
        return jsonify({'success': False, 'error': 'No video URL provided', 'platform': platform}), 400
    
    source_path = video_url.lstrip('/')
    possible_paths = [
        source_path,
        os.path.join('output', os.path.basename(source_path)),
        source_path.replace('/output/', 'output/')
    ]
    
    actual_path = None
    for path in possible_paths:
        if os.path.exists(path):
            actual_path = path
            break
    
    if not actual_path:
        return jsonify({'success': False, 'error': f'Video not found for {platform}', 'platform': platform}), 404
    
    platform_configs = {
        'tiktok': {'width': 1080, 'height': 1920, 'ratio': '9:16', 'caption_y': 0.35},
        'ig_reels': {'width': 1080, 'height': 1920, 'ratio': '9:16', 'caption_y': 0.40},
        'yt_shorts': {'width': 1080, 'height': 1920, 'ratio': '9:16', 'caption_y': 0.45},
        'ig_feed': {'width': 1080, 'height': 1350, 'ratio': '4:5', 'caption_y': 0.50},
        'ig_carousel': {'width': 1080, 'height': 1350, 'ratio': '4:5'},
        'twitter': {'width': 1920, 'height': 1080, 'ratio': '16:9', 'caption_y': 0.80},
        'instagram': {'width': 1080, 'height': 1920, 'ratio': '9:16', 'caption_y': 0.40},
        'youtube': {'width': 1080, 'height': 1920, 'ratio': '9:16', 'caption_y': 0.45}
    }
    
    config = platform_configs.get(platform, platform_configs['tiktok'])
    output_id = str(uuid.uuid4())[:8]
    
    try:
        if platform == 'ig_carousel':
            images = generate_carousel_images(actual_path, carousel_count, script_text, output_id)
            return jsonify({
                'success': True,
                'images': images,
                'platform': platform,
                'format': config['ratio']
            })
        
        output_path = f'output/{platform}_{output_id}.mp4'
        
        vf_filters = [f"scale={config['width']}:{config['height']}:force_original_aspect_ratio=decrease",
                      f"pad={config['width']}:{config['height']}:(ow-iw)/2:(oh-ih)/2"]
        
        cmd = [
            'ffmpeg', '-y', '-i', actual_path,
            '-vf', ','.join(vf_filters),
            '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
            '-c:a', 'aac', '-b:a', '128k',
            output_path
        ]
        
        result = subprocess.run(cmd, capture_output=True, timeout=300)
        
        if result.returncode == 0 and os.path.exists(output_path):
            response_data = {
                'success': True,
                'video_path': '/' + output_path,
                'platform': platform,
                'format': config['ratio']
            }
            
            if is_post_platform and script_text:
                try:
                    platform_name = {'ig_feed': 'Instagram Feed', 'twitter': 'Twitter/X'}.get(platform, platform)
                    caption_prompt = f"""Generate an optimized caption for {platform_name} based on this video script:

{script_text}

Research what works on {platform_name} right now and create:
1. A hook that grabs attention
2. The main message (concise)
3. A call-to-action
4. 3-5 relevant hashtags

Respond with ONLY the caption text ready to post (include hashtags at the end)."""
                    
                    ai_caption = call_ai(caption_prompt, max_tokens=300)
                    response_data['suggested_caption'] = ai_caption.strip()
                except Exception as e:
                    print(f"Caption generation failed: {e}")
            
            return jsonify(response_data)
        else:
            error_msg = result.stderr.decode()[:200] if result.stderr else 'Unknown error'
            print(f"FFmpeg error for {platform}: {error_msg}")
            return jsonify({'success': False, 'error': f'Export failed for {platform}', 'platform': platform}), 500
            
    except Exception as e:
        print(f"Platform export error for {platform}: {e}")
        return jsonify({'success': False, 'error': format_user_error(str(e)), 'platform': platform}), 500


def generate_carousel_images(video_path, count, script_text, output_id):
    """Generate carousel images from video frames with text overlays."""
    import subprocess
    from PIL import Image, ImageDraw, ImageFont
    from context_engine import call_ai
    import json
    
    count = max(2, min(10, int(count or 5)))
    
    os.makedirs('output/carousel', exist_ok=True)
    images = []
    
    try:
        probe_cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                     '-of', 'default=noprint_wrappers=1:nokey=1', video_path]
        duration = float(subprocess.run(probe_cmd, capture_output=True, text=True).stdout.strip() or '10')
    except:
        duration = 10
    
    if not script_text or len(script_text.strip()) < 10:
        slides = [{"text": f"Slide {i+1}", "timestamp": (i + 0.5) / count} for i in range(count)]
    else:
        try:
            slide_prompt = f"""Create {count} Instagram carousel slides from this script:

{script_text}

For each slide, provide:
- "text": Short, impactful text for the slide (max 80 chars)
- "timestamp": Approximate position in video (0.0 to 1.0) for the frame

Return JSON array only:
[{{"text": "...", "timestamp": 0.1}}, ...]"""
            
            ai_response = call_ai(slide_prompt, max_tokens=800)
            ai_response = ai_response.strip()
            if '```' in ai_response:
                ai_response = ai_response.split('```')[1].replace('json', '').strip()
            slides = json.loads(ai_response)
        except Exception as e:
            print(f"AI slide generation failed: {e}")
            slides = [{"text": f"Slide {i+1}", "timestamp": (i + 0.5) / count} for i in range(count)]
    
    for i, slide in enumerate(slides[:count]):
        raw_timestamp = slide.get('timestamp', (i + 0.5) / count)
        clamped_timestamp = max(0.0, min(1.0, float(raw_timestamp)))
        timestamp = clamped_timestamp * duration
        text = slide.get('text', f'Slide {i+1}')[:100]
        frame_path = f'output/carousel/frame_{output_id}_{i}.png'
        output_path = f'output/carousel/slide_{output_id}_{i}.png'
        
        try:
            extract_cmd = ['ffmpeg', '-y', '-ss', str(timestamp), '-i', video_path,
                          '-vframes', '1', '-s', '1080x1350', frame_path]
            subprocess.run(extract_cmd, capture_output=True, timeout=30)
            
            if os.path.exists(frame_path):
                img = Image.open(frame_path)
                draw = ImageDraw.Draw(img)
                
                try:
                    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 48)
                except:
                    font = ImageFont.load_default()
                
                bbox = draw.textbbox((0, 0), text, font=font)
                text_width = bbox[2] - bbox[0]
                text_height = bbox[3] - bbox[1]
                x = (1080 - text_width) // 2
                y = 1350 - text_height - 100
                
                for dx, dy in [(-2, -2), (-2, 2), (2, -2), (2, 2)]:
                    draw.text((x + dx, y + dy), text, font=font, fill='black')
                draw.text((x, y), text, font=font, fill='white')
                
                img.save(output_path)
                images.append('/' + output_path)
                
                if os.path.exists(frame_path):
                    os.remove(frame_path)
        except Exception as e:
            print(f"Carousel slide {i} failed: {e}")
    
    return images


@app.route('/generate-promo-pack', methods=['POST'])
def generate_promo_pack():
    """Generate promotional content from video script."""
    from context_engine import call_ai
    import json
    
    data = request.get_json() or {}
    script = data.get('script', '')
    
    if not script:
        return jsonify({'error': 'No script provided'}), 400
    
    try:
        # Use AI to extract quotes, detect humor, and generate promo content
        prompt = f"""Analyze this video script and generate promotional content:

Script:
{script}

Generate a JSON response with:
1. "quote_cards": Array of 3-4 powerful standalone quotes from the script. Each has:
   - "quote": The exact quote (max 100 chars)
   - "bg_color": A hex color for background
   - "accent_color": A complementary hex color

2. "has_humor": Boolean - is this content funny/memeable?

3. "memes": If has_humor is true, array of 2-3 meme ideas with:
   - "top_text": Top meme text
   - "bottom_text": Bottom meme text
   - "format": Meme format name (e.g., "Drake", "Distracted Boyfriend", "Change My Mind")

4. "infographics": Array of 2-3 key statistics or facts with:
   - "stat": The number or key stat (e.g., "73%", "2.5x")
   - "label": Brief description (max 50 chars)

Only include memes array if the content genuinely has humor potential.
Respond with ONLY valid JSON, no markdown."""

        response = call_ai(prompt, max_tokens=1500)
        
        # Parse AI response
        try:
            # Clean response
            response_text = response.strip()
            if response_text.startswith('```'):
                response_text = response_text.split('```')[1]
                if response_text.startswith('json'):
                    response_text = response_text[4:]
            
            promo_data = json.loads(response_text)
            
            return jsonify({
                'success': True,
                'quote_cards': promo_data.get('quote_cards', []),
                'memes': promo_data.get('memes', []) if promo_data.get('has_humor') else [],
                'infographics': promo_data.get('infographics', []),
                'has_humor': promo_data.get('has_humor', False)
            })
            
        except json.JSONDecodeError:
            # Fallback with basic quote extraction
            lines = [l.strip() for l in script.split('\n') if l.strip() and not l.startswith('[')]
            quotes = lines[:3] if len(lines) >= 3 else lines
            
            return jsonify({
                'success': True,
                'quote_cards': [{'quote': q[:100], 'bg_color': '#1a1a2e', 'accent_color': '#16213e'} for q in quotes],
                'memes': [],
                'infographics': [{'stat': str(len(lines)), 'label': 'Key points covered'}],
                'has_humor': False
            })
            
    except Exception as e:
        print(f"Promo pack error: {e}")
        return jsonify({'error': format_user_error(str(e))}), 500


@app.route('/download-promo-pack', methods=['POST'])
def download_promo_pack():
    """Generate downloadable promo assets."""
    import zipfile
    import uuid
    from PIL import Image, ImageDraw, ImageFont
    
    data = request.get_json() or {}
    approved_items = data.get('approved_items', [])
    promo_data = data.get('promo_data', {})
    
    if not approved_items:
        return jsonify({'error': 'No items selected'}), 400
    
    try:
        # Create output directory
        pack_id = str(uuid.uuid4())[:8]
        pack_dir = f'output/promo_pack_{pack_id}'
        os.makedirs(pack_dir, exist_ok=True)
        
        generated_files = []
        
        def hex_to_rgb(hex_color):
            hex_color = hex_color.lstrip('#')
            return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
        
        def create_gradient(size, color1, color2):
            img = Image.new('RGB', size)
            for y in range(size[1]):
                r = int(color1[0] + (color2[0] - color1[0]) * y / size[1])
                g = int(color1[1] + (color2[1] - color1[1]) * y / size[1])
                b = int(color1[2] + (color2[2] - color1[2]) * y / size[1])
                for x in range(size[0]):
                    img.putpixel((x, y), (r, g, b))
            return img
        
        # Generate each approved item as an image
        for item_key in approved_items:
            item_type, idx = item_key.split('-')
            idx = int(idx)
            
            try:
                font_large = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 56)
                font_med = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 40)
                font_small = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 28)
            except:
                font_large = ImageFont.load_default()
                font_med = font_large
                font_small = font_large
            
            if item_type == 'quote' and idx < len(promo_data.get('quote_cards', [])):
                card = promo_data['quote_cards'][idx]
                bg_color = hex_to_rgb(card.get('bg_color', '#1a1a2e'))
                accent_color = hex_to_rgb(card.get('accent_color', '#16213e'))
                img = create_gradient((1080, 1080), bg_color, accent_color)
                draw = ImageDraw.Draw(img)
                quote_text = f'"{card.get("quote", "")}"'
                # Word wrap for long quotes
                words = quote_text.split()
                lines = []
                current_line = ""
                for word in words:
                    test_line = current_line + " " + word if current_line else word
                    if len(test_line) > 30:
                        lines.append(current_line)
                        current_line = word
                    else:
                        current_line = test_line
                if current_line:
                    lines.append(current_line)
                y_offset = 540 - (len(lines) * 35)
                for line in lines:
                    draw.text((540, y_offset), line, fill='white', font=font_med, anchor='mm')
                    y_offset += 70
                # Add branding
                draw.text((540, 1000), "framd.io", fill=(255, 255, 255, 128), font=font_small, anchor='mm')
                
            elif item_type == 'meme' and idx < len(promo_data.get('memes', [])):
                meme = promo_data['memes'][idx]
                img = Image.new('RGB', (1080, 1080), color='#000000')
                draw = ImageDraw.Draw(img)
                # Meme style text with outline
                top = meme.get('top_text', '').upper()
                bottom = meme.get('bottom_text', '').upper()
                # Draw text with black outline
                for offset in [(-3,-3), (-3,3), (3,-3), (3,3), (-3,0), (3,0), (0,-3), (0,3)]:
                    draw.text((540+offset[0], 80+offset[1]), top, fill='black', font=font_large, anchor='mm')
                    draw.text((540+offset[0], 1000+offset[1]), bottom, fill='black', font=font_large, anchor='mm')
                draw.text((540, 80), top, fill='white', font=font_large, anchor='mm')
                draw.text((540, 1000), bottom, fill='white', font=font_large, anchor='mm')
                # Add format label
                draw.text((540, 540), f"[{meme.get('format', 'Meme')}]", fill='#666666', font=font_small, anchor='mm')
                
            elif item_type == 'info' and idx < len(promo_data.get('infographics', [])):
                info = promo_data['infographics'][idx]
                img = create_gradient((1080, 1080), (10, 31, 20), (26, 61, 42))
                draw = ImageDraw.Draw(img)
                draw.text((540, 400), info.get('stat', ''), fill='#ffd60a', font=font_large, anchor='mm')
                draw.text((540, 520), info.get('label', ''), fill='white', font=font_med, anchor='mm')
                draw.text((540, 1000), "framd.io", fill=(255, 255, 255, 128), font=font_small, anchor='mm')
            else:
                continue
            
            # Save image
            img_path = f'{pack_dir}/{item_type}_{idx}.png'
            img.save(img_path)
            generated_files.append(img_path)
        
        # Create zip file
        zip_path = f'output/promo_pack_{pack_id}.zip'
        with zipfile.ZipFile(zip_path, 'w') as zipf:
            for file_path in generated_files:
                zipf.write(file_path, os.path.basename(file_path))
        
        # Cleanup individual files
        import shutil
        shutil.rmtree(pack_dir, ignore_errors=True)
        
        return jsonify({
            'success': True,
            'download_url': '/' + zip_path
        })
        
    except Exception as e:
        print(f"Promo pack download error: {e}")
        return jsonify({'error': format_user_error(str(e))}), 500


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
        subscription = Subscription.query.filter_by(user_id=current_user.id).first()
        token_balance = subscription.token_balance if subscription else 0
        
        ai_learning = AILearning.query.filter_by(user_id=current_user.id).first()
        export_count = ai_learning.successful_projects if ai_learning else 0
        
        user_initials = ''
        if current_user.first_name:
            user_initials += current_user.first_name[0].upper()
        if current_user.last_name:
            user_initials += current_user.last_name[0].upper()
        if not user_initials:
            user_initials = current_user.email[0].upper() if current_user.email else 'U'
        
        user_name = f"{current_user.first_name or ''} {current_user.last_name or ''}".strip()
        if not user_name:
            user_name = current_user.email or 'User'
        
        return render_template('chat.html',
            user=current_user,
            user_initials=user_initials,
            user_name=user_name,
            token_balance=token_balance,
            export_count=export_count
        )
    return render_template('landing.html')

@app.route('/pricing')
def pricing():
    return render_template('pricing.html')

@app.route('/terms')
def terms():
    return render_template('terms.html')

@app.route('/privacy')
def privacy():
    return render_template('privacy.html')

@app.route('/faq')
def faq():
    return render_template('faq.html')

@app.route('/dev')
def dev_mode():
    session['dev_mode'] = True
    return render_template('chat.html', 
        user=None, 
        dev_mode=True,
        user_initials='D',
        user_name='Dev User',
        token_balance=1000,
        export_count=0
    )

@app.route('/chat')
def chat_interface():
    from flask_login import current_user
    if current_user.is_authenticated:
        subscription = Subscription.query.filter_by(user_id=current_user.id).first()
        token_balance = subscription.token_balance if subscription else 0
        
        ai_learning = AILearning.query.filter_by(user_id=current_user.id).first()
        export_count = ai_learning.successful_projects if ai_learning else 0
        
        user_initials = ''
        if current_user.first_name:
            user_initials += current_user.first_name[0].upper()
        if current_user.last_name:
            user_initials += current_user.last_name[0].upper()
        if not user_initials:
            user_initials = current_user.email[0].upper() if current_user.email else 'U'
        
        user_name = f"{current_user.first_name or ''} {current_user.last_name or ''}".strip()
        if not user_name:
            user_name = current_user.email or 'User'
        
        return render_template('chat.html',
            user=current_user,
            user_initials=user_initials,
            user_name=user_name,
            token_balance=token_balance,
            export_count=export_count
        )
    return render_template('landing.html')


@app.route('/api/projects', methods=['GET'])
def api_get_projects():
    user_id = get_user_id()
    if not user_id:
        return jsonify({'ok': False, 'error': 'Not authenticated'}), 401
    
    projects = Project.query.filter_by(user_id=user_id).order_by(Project.updated_at.desc()).limit(50).all()
    
    return jsonify({
        'ok': True,
        'projects': [{
            'id': p.id,
            'name': p.name,
            'mode': p.template_type,
            'status': p.status,
            'duration': 0,
            'thumbnail': None,
            'created_at': p.created_at.isoformat() if p.created_at else None,
            'updated_at': p.updated_at.isoformat() if p.updated_at else None
        } for p in projects]
    })


@app.route('/api/project/<int:project_id>/chat', methods=['GET'])
def api_get_project_chat(project_id):
    user_id = get_user_id()
    if not user_id:
        return jsonify({'ok': False, 'error': 'Not authenticated'}), 401
    
    project = Project.query.filter_by(id=project_id, user_id=user_id).first()
    if not project:
        return jsonify({'ok': False, 'error': 'Project not found'}), 404
    
    conversations = Conversation.query.filter_by(user_id=user_id).order_by(Conversation.created_at.asc()).all()
    
    project_messages = []
    for conv in conversations:
        try:
            content_data = json.loads(conv.content) if conv.content.startswith('{') else {'text': conv.content}
            if content_data.get('project_id') == project_id or not content_data.get('project_id'):
                project_messages.append({
                    'role': conv.role,
                    'content': content_data.get('text', conv.content)
                })
        except:
            project_messages.append({
                'role': conv.role,
                'content': conv.content
            })
    
    return jsonify({
        'ok': True,
        'name': project.name,
        'mode': project.template_type,
        'messages': project_messages
    })


@app.route('/api/project/<int:project_id>/rename', methods=['POST'])
def api_rename_project(project_id):
    user_id = get_user_id()
    if not user_id:
        return jsonify({'ok': False, 'error': 'Not authenticated'}), 401
    
    project = Project.query.filter_by(id=project_id, user_id=user_id).first()
    if not project:
        return jsonify({'ok': False, 'error': 'Project not found'}), 404
    
    data = request.get_json()
    new_name = data.get('name', '').strip()
    
    if not new_name:
        return jsonify({'ok': False, 'error': 'Name cannot be empty'}), 400
    
    project.name = new_name[:100]
    db.session.commit()
    
    return jsonify({'ok': True, 'name': project.name})


@app.route('/api/jobs', methods=['POST'])
def api_create_job():
    """Create a new video generation job."""
    from job_queue import JOB_QUEUE
    
    user_id = get_user_id()
    if not user_id:
        return jsonify({'ok': False, 'error': 'Not authenticated'}), 401
    
    data = request.get_json()
    project_id = data.get('project_id')
    quality_tier = data.get('quality_tier', 'good')
    job_data = data.get('job_data', {})
    
    if not project_id:
        return jsonify({'ok': False, 'error': 'Project ID required'}), 400
    
    project = Project.query.filter_by(id=project_id, user_id=user_id).first()
    if not project:
        return jsonify({'ok': False, 'error': 'Project not found'}), 404
    
    job_id = JOB_QUEUE.add_job(
        user_id=user_id,
        project_id=project_id,
        quality_tier=quality_tier,
        job_data=job_data
    )
    
    job = JOB_QUEUE.get_job(job_id)
    
    return jsonify({
        'ok': True,
        'job': JOB_QUEUE.to_dict(job)
    })


@app.route('/api/jobs/<int:job_id>', methods=['GET'])
def api_get_job(job_id):
    """Get job status and progress."""
    from job_queue import JOB_QUEUE
    
    user_id = get_user_id()
    if not user_id:
        return jsonify({'ok': False, 'error': 'Not authenticated'}), 401
    
    job = JOB_QUEUE.get_job(job_id)
    if not job:
        return jsonify({'ok': False, 'error': 'Job not found'}), 404
    
    if job.user_id != user_id:
        return jsonify({'ok': False, 'error': 'Not authorized'}), 403
    
    position = JOB_QUEUE.get_queue_position(job_id) if job.status == 'pending' else 0
    
    return jsonify({
        'ok': True,
        'job': JOB_QUEUE.to_dict(job),
        'queue_position': position
    })


@app.route('/api/jobs', methods=['GET'])
def api_get_user_jobs():
    """Get all jobs for the current user."""
    from job_queue import JOB_QUEUE
    
    user_id = get_user_id()
    if not user_id:
        return jsonify({'ok': False, 'error': 'Not authenticated'}), 401
    
    active_only = request.args.get('active', 'false').lower() == 'true'
    
    if active_only:
        jobs = JOB_QUEUE.get_active_jobs(user_id)
    else:
        jobs = JOB_QUEUE.get_user_jobs(user_id, limit=20)
    
    return jsonify({
        'ok': True,
        'jobs': [JOB_QUEUE.to_dict(job) for job in jobs]
    })


@app.route('/api/jobs/<int:job_id>/cancel', methods=['POST'])
def api_cancel_job(job_id):
    """Cancel a pending job."""
    from job_queue import JOB_QUEUE
    
    user_id = get_user_id()
    if not user_id:
        return jsonify({'ok': False, 'error': 'Not authenticated'}), 401
    
    success = JOB_QUEUE.cancel_job(job_id, user_id)
    
    if success:
        return jsonify({'ok': True, 'message': 'Job cancelled'})
    else:
        return jsonify({'ok': False, 'error': 'Cannot cancel job (may already be processing)'}), 400


@app.route('/api/jobs/stats', methods=['GET'])
def api_queue_stats():
    """Get overall queue statistics (admin)."""
    from job_queue import JOB_QUEUE
    
    stats = JOB_QUEUE.get_queue_stats()
    
    return jsonify({
        'ok': True,
        'stats': stats
    })


@app.route('/api/chat', methods=['POST'])
def api_chat():
    user_id = get_user_id()
    if not user_id:
        return jsonify({'ok': False, 'error': 'Not authenticated'}), 401
    
    data = request.get_json()
    message = data.get('message', '').strip()
    project_id = data.get('project_id')
    mode = data.get('mode')
    
    if not message:
        return jsonify({'ok': False, 'error': 'No message provided'}), 400
    
    project = None
    if project_id:
        project = Project.query.filter_by(id=project_id, user_id=user_id).first()
    
    if not project:
        project_name = message[:50] + '...' if len(message) > 50 else message
        project = Project(
            user_id=user_id,
            name=project_name,
            template_type=mode or 'auto',
            status='draft'
        )
        db.session.add(project)
        db.session.commit()
        project_id = project.id
    
    user_conv = Conversation(
        user_id=user_id,
        role='user',
        content=json.dumps({'project_id': project_id, 'text': message})
    )
    db.session.add(user_conv)
    db.session.commit()
    
    ai_role = """You are an AI video editor for Framd. Your purpose is to create videos that match the user's vision.

YOUR JOB:
1. Transform videos while preserving motion and structure (Remix mode)
2. Extract the best moments from long content (Clipper mode)
3. Create original content using stock and AI visuals (Simple Stock mode)
4. Ask questions when critical information is missing
5. Rate your own work honestly - minimum 7.5 to show user

YOU MUST ASK WHEN:
- Brand colors not specified
- Tone/direction unclear (serious? funny? educational?)
- Target audience unknown
- Missing logo, assets, or brand materials
- Vague request that could go multiple directions

Be helpful, concise, and focused on delivering great video content."""

    try:
        response = call_ai(
            prompt=f"User message: {message}\n\nCurrent mode: {mode or 'not selected'}\n\nRespond naturally as a video creation assistant. If you need more information to proceed, ask ONE clear question.",
            system_prompt=ai_role,
            json_output=False,
            max_tokens=500
        )
        
        if isinstance(response, dict):
            ai_response = response.get('response', response.get('text', str(response)))
        else:
            ai_response = str(response)
        
        needs_clarification = any(q in ai_response.lower() for q in ['?', 'what', 'which', 'how', 'could you', 'can you'])
        
    except Exception as e:
        ai_response = "I'm ready to help you create your video. What would you like to make?"
        needs_clarification = True
    
    ai_conv = Conversation(
        user_id=user_id,
        role='assistant',
        content=json.dumps({'project_id': project_id, 'text': ai_response})
    )
    db.session.add(ai_conv)
    db.session.commit()
    
    generation_ready = any(phrase in message.lower() for phrase in [
        'generate', 'create video', 'make video', 'start generation',
        'build video', 'render', "let's go", "looks good", "that's perfect"
    ]) and not needs_clarification
    
    trigger_generation = False
    job_data = None
    
    if generation_ready and mode in ['remix', 'simple', 'clipper']:
        trigger_generation = True
        job_data = {
            'mode': mode,
            'project_name': project.name,
            'project_id': project_id,
            'user_message': message
        }
    
    return jsonify({
        'ok': True,
        'response': ai_response,
        'project_id': project_id,
        'project_name': project.name,
        'needs_clarification': needs_clarification,
        'trigger_generation': trigger_generation,
        'job_data': job_data
    })


@app.route('/api/job/<job_id>/status', methods=['GET'])
def api_job_status(job_id):
    if job_id in background_render_jobs:
        job = background_render_jobs[job_id]
        return jsonify({
            'ok': True,
            'status': job.get('status', 'unknown'),
            'progress': job.get('progress', 0),
            'message': job.get('status', 'Processing...').replace('_', ' ').title(),
            'video_url': job.get('video_url'),
            'error': job.get('error')
        })
    return jsonify({'ok': False, 'error': 'Job not found'}), 404

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


@app.route('/analyze-video', methods=['POST'])
def analyze_video():
    """Analyze an uploaded video by extracting frames and transcribing audio."""
    import base64
    import subprocess
    from openai import OpenAI
    
    data = request.get_json()
    file_path = data.get('file_path')
    
    if not file_path or not os.path.exists(file_path):
        return jsonify({'error': 'Video file not found'}), 404
    
    if not file_path.lower().endswith(('.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v')):
        return jsonify({'error': 'Not a video file'}), 400
    
    try:
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        
        # Get video duration
        dur_cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', file_path]
        result = subprocess.run(dur_cmd, capture_output=True, text=True, timeout=30)
        duration = float(result.stdout.strip()) if result.stdout.strip() else 0
        
        # Extract a frame from the middle of the video
        frames_dir = os.path.join('uploads', 'video_frames')
        os.makedirs(frames_dir, exist_ok=True)
        frame_path = os.path.join(frames_dir, f'frame_{int(time.time())}.jpg')
        
        mid_point = duration / 2 if duration > 0 else 1
        extract_cmd = ['ffmpeg', '-y', '-ss', str(mid_point), '-i', file_path, '-vframes', '1', '-q:v', '2', frame_path]
        subprocess.run(extract_cmd, capture_output=True, timeout=30)
        
        # Extract and transcribe audio
        transcript = ""
        audio_path = file_path.rsplit('.', 1)[0] + '_audio.mp3'
        audio_cmd = ['ffmpeg', '-y', '-i', file_path, '-vn', '-acodec', 'mp3', '-q:a', '4', audio_path]
        subprocess.run(audio_cmd, capture_output=True, timeout=120)
        
        if os.path.exists(audio_path) and os.path.getsize(audio_path) > 1000:
            try:
                with open(audio_path, 'rb') as audio_file:
                    transcription = client.audio.transcriptions.create(
                        model="whisper-1",
                        file=audio_file,
                        response_format="text"
                    )
                    transcript = transcription if isinstance(transcription, str) else str(transcription)
            except Exception as e:
                logging.warning(f"Transcription failed: {e}")
            finally:
                try:
                    os.remove(audio_path)
                except:
                    pass
        
        # Analyze frame with vision
        frame_analysis = None
        if os.path.exists(frame_path):
            with open(frame_path, 'rb') as f:
                frame_b64 = base64.b64encode(f.read()).decode('utf-8')
            
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Describe this video frame briefly. What is shown? What's the visual style and mood?"},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{frame_b64}"}}
                    ]
                }],
                max_tokens=300
            )
            frame_analysis = response.choices[0].message.content
            
            try:
                os.remove(frame_path)
            except:
                pass
        
        # Build description
        description = ""
        if frame_analysis:
            description += f"Visual: {frame_analysis}"
        if transcript:
            description += f"\n\nAudio transcript: {transcript[:1000]}"
        
        return jsonify({
            'success': True,
            'analysis': {
                'description': description or "Video uploaded successfully",
                'duration': duration,
                'transcript': transcript[:1500] if transcript else None,
                'frame_analysis': frame_analysis,
                'mood': 'video content',
                'suggested_use': 'background',
                'content_type': 'video'
            },
            'file_path': file_path
        })
        
    except Exception as e:
        logging.error(f"Video analysis error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': True,
            'analysis': {
                'description': 'Video uploaded (analysis unavailable)',
                'mood': 'video',
                'suggested_use': 'background',
                'content_type': 'video'
            },
            'file_path': file_path
        })


@app.route('/analyze-image', methods=['POST'])
def analyze_image():
    """Analyze an uploaded image using OpenAI GPT-4o vision."""
    import base64
    from openai import OpenAI
    
    data = request.get_json()
    file_path = data.get('file_path')
    
    if not file_path or not os.path.exists(file_path):
        return jsonify({'error': 'File not found'}), 404
    
    # Check if it's an image
    if not file_path.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp')):
        return jsonify({'error': 'Not an image file'}), 400
    
    try:
        # Read and encode image
        with open(file_path, 'rb') as f:
            image_data = base64.b64encode(f.read()).decode('utf-8')
        
        # Determine mime type
        ext = file_path.lower().split('.')[-1]
        mime_types = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png', 'gif': 'image/gif', 'webp': 'image/webp'}
        mime_type = mime_types.get(ext, 'image/jpeg')
        
        # Call OpenAI GPT-4o vision
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": """Analyze this image for use in a short-form video post. Provide:
1. A brief description (1-2 sentences) of what's in the image
2. The mood/tone it conveys
3. Suggested use: 'background' (full-screen behind text) or 'popup' (overlay element)
4. Any text visible in the image

Respond in JSON format:
{
  "description": "...",
  "mood": "...",
  "suggested_use": "background" or "popup",
  "visible_text": "..." or null,
  "content_type": "photo/illustration/screenshot/graphic"
}"""
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime_type};base64,{image_data}"
                            }
                        }
                    ]
                }
            ],
            max_tokens=500
        )
        
        # Parse the response
        reply = response.choices[0].message.content or ""
        
        # Try to extract JSON from response
        import json
        import re
        json_match = re.search(r'\{[^{}]*\}', reply, re.DOTALL)
        if json_match:
            analysis = json.loads(json_match.group())
        else:
            analysis = {
                "description": reply,
                "mood": "neutral",
                "suggested_use": "background",
                "visible_text": None,
                "content_type": "photo"
            }
        
        return jsonify({
            'success': True,
            'analysis': analysis,
            'file_path': file_path
        })
        
    except Exception as e:
        logging.error(f"Image analysis error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/extract-video-template', methods=['POST'])
def extract_video_template():
    """Extract template structure from an uploaded video for personalization."""
    import base64
    import subprocess
    import json
    from openai import OpenAI
    
    data = request.get_json()
    file_path = data.get('file_path')
    user_id = session.get('replit_user_id') or data.get('user_id')
    
    if not file_path or not os.path.exists(file_path):
        return jsonify({'error': 'Video file not found'}), 404
    
    if not file_path.lower().endswith(('.mp4', '.mov', '.avi', '.mkv', '.webm')):
        return jsonify({'error': 'Not a video file'}), 400
    
    try:
        # Get video duration
        dur_cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', file_path]
        result = subprocess.run(dur_cmd, capture_output=True, text=True, timeout=30)
        duration = float(result.stdout.strip()) if result.stdout.strip() else 0
        
        # Extract frames at key points for analysis
        frames_dir = os.path.join('uploads', 'template_frames')
        os.makedirs(frames_dir, exist_ok=True)
        
        frame_timestamps = []
        num_frames = min(8, max(4, int(duration / 5)))
        for i in range(num_frames):
            ts = (duration / num_frames) * i + 0.5
            frame_timestamps.append(ts)
        
        frame_paths = []
        for i, ts in enumerate(frame_timestamps):
            frame_path = os.path.join(frames_dir, f'frame_{int(ts*1000)}.jpg')
            extract_cmd = [
                'ffmpeg', '-y', '-ss', str(ts), '-i', file_path,
                '-vframes', '1', '-q:v', '2', frame_path
            ]
            subprocess.run(extract_cmd, capture_output=True, timeout=30)
            if os.path.exists(frame_path):
                frame_paths.append({'path': frame_path, 'timestamp': ts})
        
        # Extract audio and transcribe
        audio_path = file_path.replace('.mp4', '_audio.mp3').replace('.mov', '_audio.mp3')
        audio_cmd = ['ffmpeg', '-y', '-i', file_path, '-vn', '-acodec', 'mp3', '-q:a', '4', audio_path]
        subprocess.run(audio_cmd, capture_output=True, timeout=120)
        
        transcript = ""
        if os.path.exists(audio_path):
            try:
                client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
                with open(audio_path, 'rb') as audio_file:
                    transcription = client.audio.transcriptions.create(
                        model="whisper-1",
                        file=audio_file,
                        response_format="verbose_json"
                    )
                    transcript = transcription.text if hasattr(transcription, 'text') else str(transcription)
            except Exception as e:
                logging.warning(f"Transcription failed: {e}")
        
        # Encode first frame for AI analysis
        frame_b64 = ""
        if frame_paths:
            with open(frame_paths[0]['path'], 'rb') as f:
                frame_b64 = base64.b64encode(f.read()).decode('utf-8')
        
        # AI analysis of video structure
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        
        analysis_prompt = f"""Analyze this video frame (first of {len(frame_paths)} frames) from a {duration:.1f} second video.

Transcript: {transcript[:1500] if transcript else 'No audio/speech detected'}

Extract the VIDEO TEMPLATE structure:
1. Visual style/aesthetic (colors, mood, energy level)
2. Estimated scene count and pacing
3. Text overlay patterns (position, style, timing)
4. Content structure (hook, body, call-to-action)
5. Transition style (cuts, fades, zooms)

Respond in JSON:
{{
  "aesthetic": {{
    "color_palette": ["primary", "secondary", "accent"],
    "mood": "energetic/calm/dramatic/playful/serious",
    "style": "minimal/bold/cinematic/social-native/professional"
  }},
  "structure": {{
    "hook_duration": 2.5,
    "total_scenes": 5,
    "pacing": "fast/medium/slow",
    "has_text_overlays": true,
    "has_call_to_action": true
  }},
  "text_patterns": {{
    "position": "center/top/bottom",
    "style": "bold/subtle/animated",
    "frequency": "every_scene/sparse/constant"
  }},
  "transitions": {{
    "type": "cut/fade/zoom/slide",
    "speed": "snappy/smooth/dramatic"
  }},
  "content_type": "ad/explainer/testimonial/meme/vlog/tutorial",
  "recommended_length": 30
}}"""
        
        messages = [{"role": "user", "content": [{"type": "text", "text": analysis_prompt}]}]
        if frame_b64:
            messages[0]["content"].append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{frame_b64}"}
            })
        
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            max_tokens=1000
        )
        
        reply = response.choices[0].message.content or ""
        
        import re
        json_match = re.search(r'\{[\s\S]*\}', reply)
        if json_match:
            template_data = json.loads(json_match.group())
        else:
            template_data = {
                "aesthetic": {"mood": "neutral", "style": "social-native"},
                "structure": {"total_scenes": num_frames, "pacing": "medium"},
                "text_patterns": {"position": "center", "style": "bold"},
                "transitions": {"type": "cut", "speed": "snappy"},
                "content_type": "general"
            }
        
        # Generate thumbnail
        thumb_path = os.path.join('uploads', 'template_thumbs', f'template_{int(time.time())}.jpg')
        os.makedirs(os.path.dirname(thumb_path), exist_ok=True)
        if frame_paths:
            import shutil
            shutil.copy(frame_paths[0]['path'], thumb_path)
        
        # Build scene breakdown
        scenes = []
        scene_duration = duration / max(template_data.get('structure', {}).get('total_scenes', num_frames), 1)
        for i, fp in enumerate(frame_paths):
            scenes.append({
                "index": i,
                "start_time": fp['timestamp'],
                "duration": scene_duration,
                "frame_path": fp['path'],
                "placeholder": f"[Scene {i+1} content]"
            })
        
        # Save template to database
        if user_id:
            template = VideoTemplate(
                user_id=user_id,
                name=f"Template {datetime.now().strftime('%m/%d %H:%M')}",
                source_video_path=file_path,
                duration=duration,
                scene_count=len(scenes),
                scenes=scenes,
                aesthetic=template_data.get('aesthetic'),
                transitions=template_data.get('transitions'),
                text_patterns=template_data.get('text_patterns'),
                audio_profile={"transcript": transcript[:2000], "has_speech": bool(transcript)},
                thumbnail_path=thumb_path
            )
            db.session.add(template)
            db.session.commit()
            template_id = template.id
        else:
            template_id = None
        
        # Cleanup temp frames
        for fp in frame_paths:
            try:
                os.remove(fp['path'])
            except:
                pass
        if os.path.exists(audio_path):
            try:
                os.remove(audio_path)
            except:
                pass
        
        return jsonify({
            'success': True,
            'template_id': template_id,
            'duration': duration,
            'scene_count': len(scenes),
            'scenes': scenes,
            'aesthetic': template_data.get('aesthetic'),
            'structure': template_data.get('structure'),
            'text_patterns': template_data.get('text_patterns'),
            'transitions': template_data.get('transitions'),
            'content_type': template_data.get('content_type'),
            'transcript': transcript[:500] if transcript else None,
            'thumbnail': thumb_path,
            'suggested_visuals': []
        })
        
    except Exception as e:
        logging.error(f"Video template extraction error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/extract-template-elements', methods=['POST'])
def extract_template_elements():
    """Extract template with element-level precision for frame-accurate recreation."""
    from template_engine import extract_template, ELEMENT_GROUPS
    
    data = request.get_json()
    file_path = data.get('file_path')
    template_name = data.get('name', f"Template {datetime.now().strftime('%m/%d %H:%M')}")
    user_id = session.get('replit_user_id') or data.get('user_id')
    
    if not file_path or not os.path.exists(file_path):
        return jsonify({'error': 'Video file not found'}), 404
    
    try:
        template_data = extract_template(
            file_path, 
            template_name,
            anthropic_client=anthropic_client,
            openai_client=None
        )
        
        if 'error' in template_data:
            return jsonify(template_data), 400
        
        if user_id:
            template = VideoTemplate(
                user_id=user_id,
                name=template_name,
                source_video_path=file_path,
                duration=template_data.get('duration'),
                scene_count=len(template_data.get('transitions', [])) + 1,
                scenes=template_data.get('transitions'),
                aesthetic={'element_summary': template_data.get('element_summary')},
                transitions=template_data.get('transitions')
            )
            db.session.add(template)
            db.session.commit()
            
            for elem in template_data.get('elements', []):
                template_elem = TemplateElement(
                    template_id=template.id,
                    name=elem.get('name', 'unknown'),
                    display_name=elem.get('display_name'),
                    element_group=elem.get('element_group', 'visuals'),
                    element_type=elem.get('element_type', 'graphic'),
                    position_x=elem.get('position', {}).get('x', 0.5),
                    position_y=elem.get('position', {}).get('y', 0.5),
                    width=elem.get('position', {}).get('width'),
                    height=elem.get('position', {}).get('height'),
                    z_index=elem.get('z_index', 0),
                    start_time=elem.get('start_time', 0),
                    end_time=elem.get('end_time'),
                    duration=elem.get('duration'),
                    animation_in=elem.get('animation_detected'),
                    original_content=elem.get('original_content'),
                    content_description=elem.get('content_description'),
                    style_properties=elem.get('style_properties'),
                    is_swappable=elem.get('is_swappable', True),
                    swap_prompt_hint=elem.get('swap_prompt_hint')
                )
                db.session.add(template_elem)
            
            db.session.commit()
            template_data['template_id'] = template.id
        
        return jsonify({
            'success': True,
            **template_data
        })
        
    except Exception as e:
        logging.error(f"Template element extraction error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/generate-from-template', methods=['POST'])
def generate_from_template():
    """Generate a video by filling template element slots with AI-generated content."""
    from template_engine import generate_element_content
    
    data = request.get_json()
    template_id = data.get('template_id')
    user_request = data.get('request', '')
    user_assets = data.get('assets', {})
    user_id = session.get('replit_user_id') or data.get('user_id')
    
    if not template_id:
        return jsonify({'error': 'Template ID required'}), 400
    
    try:
        template = VideoTemplate.query.get(template_id)
        if not template:
            return jsonify({'error': 'Template not found'}), 404
        
        # Authorization - owner or public template
        if template.user_id != user_id and not template.is_public:
            return jsonify({'error': 'Not authorized'}), 403
        
        # Increment usage count
        template.usage_count = (template.usage_count or 0) + 1
        db.session.commit()
        
        elements = TemplateElement.query.filter_by(template_id=template_id).all()
        
        generated_elements = []
        for elem in elements:
            elem_dict = {
                'id': elem.id,
                'name': elem.name,
                'display_name': elem.display_name,
                'element_type': elem.element_type,
                'element_group': elem.element_group,
                'position': {
                    'x': elem.position_x,
                    'y': elem.position_y,
                    'width': elem.width,
                    'height': elem.height
                },
                'start_time': elem.start_time,
                'end_time': elem.end_time,
                'original_content': elem.original_content,
                'swap_prompt_hint': elem.swap_prompt_hint
            }
            
            if elem.is_swappable:
                new_content = generate_element_content(
                    elem_dict, 
                    user_request,
                    user_assets=user_assets,
                    anthropic_client=anthropic_client
                )
                elem_dict['generated_content'] = new_content
            
            generated_elements.append(elem_dict)
        
        return jsonify({
            'success': True,
            'template_id': template_id,
            'template_name': template.name,
            'duration': template.duration,
            'elements': generated_elements,
            'element_count': len(generated_elements)
        })
        
    except Exception as e:
        logging.error(f"Template generation error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/regenerate-element', methods=['POST'])
def regenerate_element():
    """Regenerate a single element based on user feedback."""
    from template_engine import generate_element_content
    
    data = request.get_json()
    element_id = data.get('element_id')
    user_instruction = data.get('instruction', '')
    user_id = session.get('replit_user_id') or data.get('user_id')
    
    if not element_id:
        return jsonify({'error': 'Element ID required'}), 400
    
    try:
        elem = TemplateElement.query.get(element_id)
        if not elem:
            return jsonify({'error': 'Element not found'}), 404
        
        # Authorization - check template ownership
        template = VideoTemplate.query.get(elem.template_id)
        if not template or (template.user_id != user_id and not template.is_public):
            return jsonify({'error': 'Not authorized'}), 403
        
        elem_dict = {
            'element_type': elem.element_type,
            'element_group': elem.element_group,
            'original_content': elem.original_content,
            'swap_prompt_hint': elem.swap_prompt_hint
        }
        
        new_content = generate_element_content(
            elem_dict,
            user_instruction,
            anthropic_client=anthropic_client
        )
        
        return jsonify({
            'success': True,
            'element_id': element_id,
            'element_name': elem.display_name or elem.name,
            'new_content': new_content
        })
        
    except Exception as e:
        logging.error(f"Element regeneration error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/get-templates', methods=['GET'])
def get_templates():
    """Get available templates for the user."""
    user_id = session.get('replit_user_id') or request.args.get('user_id')
    
    try:
        if user_id:
            templates = VideoTemplate.query.filter_by(user_id=user_id).order_by(VideoTemplate.created_at.desc()).limit(20).all()
        else:
            templates = VideoTemplate.query.filter_by(is_public=True).order_by(VideoTemplate.usage_count.desc()).limit(10).all()
        
        result = []
        for t in templates:
            element_count = TemplateElement.query.filter_by(template_id=t.id).count()
            result.append({
                'id': t.id,
                'name': t.name,
                'duration': t.duration,
                'scene_count': t.scene_count,
                'element_count': element_count,
                'thumbnail': t.thumbnail_path,
                'aesthetic': t.aesthetic,
                'usage_count': t.usage_count,
                'created_at': t.created_at.isoformat() if t.created_at else None
            })
        
        return jsonify({'templates': result})
        
    except Exception as e:
        logging.error(f"Get templates error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/get-template-elements/<int:template_id>', methods=['GET'])
def get_template_elements(template_id):
    """Get all elements for a specific template."""
    user_id = session.get('replit_user_id')
    
    try:
        template = VideoTemplate.query.get(template_id)
        if not template:
            return jsonify({'error': 'Template not found'}), 404
        
        # Authorization check - only owner or public templates
        if template.user_id != user_id and not template.is_public:
            return jsonify({'error': 'Not authorized'}), 403
        
        elements = TemplateElement.query.filter_by(template_id=template_id).order_by(TemplateElement.start_time).all()
        
        result = []
        for e in elements:
            result.append({
                'id': e.id,
                'name': e.name,
                'display_name': e.display_name,
                'element_group': e.element_group,
                'element_type': e.element_type,
                'position': {
                    'x': e.position_x,
                    'y': e.position_y,
                    'width': e.width,
                    'height': e.height
                },
                'z_index': e.z_index,
                'start_time': e.start_time,
                'end_time': e.end_time,
                'duration': e.duration,
                'animation_in': e.animation_in,
                'animation_out': e.animation_out,
                'original_content': e.original_content,
                'content_description': e.content_description,
                'style_properties': e.style_properties,
                'is_swappable': e.is_swappable
            })
        
        return jsonify({'elements': result})
        
    except Exception as e:
        logging.error(f"Get template elements error: {e}")
        return jsonify({'error': str(e)}), 500


def validate_safe_path(file_path):
    """Validate file path is safe and within allowed directories."""
    if not file_path:
        return None
    # Normalize and resolve path
    normalized = os.path.normpath(file_path)
    # Reject absolute paths and path traversal
    if normalized.startswith('/') and not normalized.startswith('/home/runner'):
        if not normalized.startswith('uploads/') and not normalized.startswith('output/'):
            return None
    if '..' in normalized:
        return None
    # Only allow files in uploads/ or output/ directories
    allowed_dirs = ['uploads', 'output', 'tmp']
    path_parts = normalized.replace('\\', '/').split('/')
    if path_parts[0] not in allowed_dirs and not normalized.startswith('/home/runner'):
        # Check if it's a relative path within allowed dirs
        for allowed in allowed_dirs:
            if allowed in path_parts:
                return normalized
        return None
    return normalized


@app.route('/extract-creative-dna', methods=['POST'])
@rate_limit(limit=10, window=60)
def extract_creative_dna():
    """Extract creative DNA from a video for AI Remix: preserves source video structure for reskinning.
    Uses Claude as primary vision model with OpenAI as fallback."""
    import base64
    import subprocess
    from anthropic import Anthropic
    from visual_director import create_visual_plan, COLOR_GRADING_PROFILES
    
    data = request.get_json()
    file_path = data.get('file_path')
    topic = data.get('topic', '')
    
    file_path = validate_safe_path(file_path)
    if not file_path or not os.path.exists(file_path):
        return jsonify({'error': 'Video file not found or invalid path'}), 404
    
    try:
        anthropic_client = Anthropic()
        
        dur_cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', file_path]
        result = subprocess.run(dur_cmd, capture_output=True, text=True, timeout=30)
        duration = float(result.stdout.strip()) if result.stdout.strip() else 30
        
        fps_cmd = ['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=r_frame_rate', '-of', 'csv=p=0', file_path]
        fps_result = subprocess.run(fps_cmd, capture_output=True, text=True, timeout=30)
        fps_str = fps_result.stdout.strip() if fps_result.stdout.strip() else '30/1'
        try:
            if '/' in fps_str:
                num, den = fps_str.split('/')
                fps = float(num) / float(den)
            else:
                fps = float(fps_str)
        except:
            fps = 30.0
        
        res_cmd = ['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height', '-of', 'csv=p=0', file_path]
        res_result = subprocess.run(res_cmd, capture_output=True, text=True, timeout=30)
        try:
            w, h = res_result.stdout.strip().split(',')
            source_width, source_height = int(w), int(h)
        except:
            source_width, source_height = 1080, 1920
        
        frames_dir = os.path.join('uploads', 'dna_frames')
        os.makedirs(frames_dir, exist_ok=True)
        
        num_scenes = max(3, min(8, int(duration / 4)))
        interval = duration / num_scenes
        
        frame_paths = []
        for i in range(num_scenes):
            timestamp = i * interval + (interval / 2)
            frame_path = os.path.join(frames_dir, f'dna_{int(time.time())}_{i}.jpg')
            extract_cmd = ['ffmpeg', '-y', '-ss', str(timestamp), '-i', file_path, '-vframes', '1', '-q:v', '2', frame_path]
            subprocess.run(extract_cmd, capture_output=True, timeout=30)
            if os.path.exists(frame_path):
                frame_paths.append({
                    'path': frame_path, 
                    'timestamp': timestamp, 
                    'start_time': i * interval,
                    'end_time': (i + 1) * interval,
                    'index': i
                })
        
        scenes_dna = []
        
        vision_prompt = """Analyze this video frame for AI Remix. The goal is to transform this video's visuals while keeping its motion and structure.

Output ONLY valid JSON:
{
    "scene_type": "talking_head/product_shot/b_roll/text_overlay/action/transition/establishing",
    "intent": "What is this scene communicating? (1 sentence)",
    "visual_description": "Detailed description of what's visually shown",
    "composition": {
        "layout": "centered/rule_of_thirds/split_screen/fullscreen",
        "subject_position": "center/left/right/top/bottom",
        "framing": "close_up/medium/wide/extreme_wide"
    },
    "colors": {
        "dominant": "#hex",
        "accent": "#hex", 
        "mood": "warm/cool/neutral/vibrant/muted"
    },
    "motion_detected": "static/slow_zoom/pan/tracking/handheld/fast_motion",
    "reskin_approach": "color_grade/overlay_graphics/style_transfer/keep_with_effects",
    "reskin_reasoning": "Why this approach works for this scene",
    "has_text": true/false,
    "has_person": true/false,
    "enhancement_suggestion": "What stock/AI elements could enhance (not replace) this scene"
}"""
        
        def parse_vision_response(raw_text):
            """Parse JSON from vision model response."""
            cleaned = raw_text.strip()
            if cleaned.startswith('```'):
                cleaned = cleaned.split('```')[1]
                if cleaned.startswith('json'):
                    cleaned = cleaned[4:]
            return json.loads(cleaned)
        
        def analyze_frame_with_claude(frame_b64):
            """Analyze a single frame using Claude vision."""
            try:
                response = anthropic_client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=600,
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": frame_b64}},
                            {"type": "text", "text": vision_prompt}
                        ]
                    }]
                )
                return parse_vision_response(response.content[0].text)
            except Exception as e:
                logging.warning(f"Claude vision analysis failed: {e}")
                return None
        
        def analyze_frame_with_openai(frame_b64):
            """Fallback: Analyze frame using OpenAI GPT-4o vision."""
            try:
                from openai import OpenAI
                openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
                response = openai_client.chat.completions.create(
                    model="gpt-4o",
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": vision_prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{frame_b64}"}}
                        ]
                    }],
                    max_tokens=600,
                    timeout=180
                )
                return parse_vision_response(response.choices[0].message.content)
            except Exception as e:
                logging.warning(f"OpenAI vision fallback failed: {e}")
                return None
        
        for frame_info in frame_paths:
            with open(frame_info['path'], 'rb') as f:
                frame_b64 = base64.b64encode(f.read()).decode('utf-8')
            
            scene_data = analyze_frame_with_claude(frame_b64)
            
            if not scene_data:
                logging.info("Claude failed, trying OpenAI fallback...")
                scene_data = analyze_frame_with_openai(frame_b64)
            
            if not scene_data:
                logging.warning("Both vision models failed, using default scene data")
                scene_data = {
                    "scene_type": "b_roll",
                    "intent": "Visual content",
                    "visual_description": "Video content",
                    "composition": {"layout": "centered", "framing": "medium"},
                    "colors": {"dominant": "#333333", "mood": "neutral"},
                    "motion_detected": "static",
                    "reskin_approach": "color_grade",
                    "reskin_reasoning": "Default approach - apply color grading to preserve original footage",
                    "has_text": False,
                    "has_person": False,
                    "enhancement_suggestion": "Apply subtle color grading to match new topic"
                }
            
            scene_data['timestamp'] = frame_info['timestamp']
            scene_data['start_time'] = frame_info['start_time']
            scene_data['end_time'] = frame_info['end_time']
            scene_data['duration'] = interval
            scene_data['index'] = frame_info['index']
            scenes_dna.append(scene_data)
            
            try:
                os.remove(frame_info['path'])
            except:
                pass
        
        transcript = ""
        audio_path = file_path.rsplit('.', 1)[0] + '_dna_audio.mp3'
        audio_cmd = ['ffmpeg', '-y', '-i', file_path, '-vn', '-acodec', 'mp3', '-q:a', '4', audio_path]
        subprocess.run(audio_cmd, capture_output=True, timeout=120)
        
        if os.path.exists(audio_path) and os.path.getsize(audio_path) > 1000:
            try:
                from openai import OpenAI
                openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
                with open(audio_path, 'rb') as audio_file:
                    transcription = openai_client.audio.transcriptions.create(
                        model="whisper-1",
                        file=audio_file,
                        response_format="text"
                    )
                    transcript = transcription if isinstance(transcription, str) else str(transcription)
            except Exception as e:
                logging.warning(f"Transcription failed: {e}")
            finally:
                try:
                    os.remove(audio_path)
                except:
                    pass
        
        visual_plan = None
        if topic or transcript:
            try:
                visual_plan = create_visual_plan(
                    script=transcript or topic,
                    user_intent=topic,
                    template_type=None
                )
                logging.info(f"Visual Director plan created: content_type={visual_plan.get('content_type')}")
            except Exception as e:
                logging.warning(f"Visual Director planning failed: {e}")
        
        recommended_grade = 'cinematic'
        if visual_plan:
            color_mood = visual_plan.get('color_mood', 'clean_modern')
            mood_to_grade = {
                'warm_professional': 'warm',
                'clean_modern': 'cool',
                'bold_contrast': 'vibrant',
                'atmospheric': 'cinematic',
                'professional_serious': 'cool',
                'vibrant_saturated': 'vibrant',
                'brand_aligned': 'cinematic',
            }
            recommended_grade = mood_to_grade.get(color_mood, 'cinematic')
        
        creative_dna = {
            "total_duration": duration,
            "fps": fps,
            "source_width": source_width,
            "source_height": source_height,
            "scene_count": len(scenes_dna),
            "scenes": scenes_dna,
            "overall_style": {
                "pacing": "fast" if duration / len(scenes_dna) < 3 else "medium" if duration / len(scenes_dna) < 5 else "slow",
                "color_palette": list(set([s.get('colors', {}).get('dominant', '#333') for s in scenes_dna])),
                "dominant_motion": max(set([s.get('motion_detected', 'static') for s in scenes_dna]), key=[s.get('motion_detected', 'static') for s in scenes_dna].count) if scenes_dna else 'static'
            },
            "transcript": transcript[:2000] if transcript else None,
            "source_path": file_path,
            "visual_director_plan": visual_plan,
            "recommended_color_grade": recommended_grade,
            "remix_strategy": {
                "use_source_video": True,
                "apply_style_transfer": True,
                "overlay_graphics": True,
                "replace_scenes": False,
                "preserve_motion": True,
                "preserve_duration": True
            }
        }
        
        return jsonify({
            'success': True,
            'creative_dna': creative_dna
        })
        
    except Exception as e:
        logging.error(f"Creative DNA extraction error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/reskin-video', methods=['POST'])
@rate_limit(limit=3, window=60)
def reskin_video():
    """AI Remix: Transform source video with new visual style while preserving motion and structure.
    
    NEW APPROACH:
    1. Keep source video as the foundation (not replace with static images)
    2. Apply visual transformations (color grading, overlays, effects) to original footage
    3. Mix in stock images/DALL-E only as enhancements, not replacements
    4. Preserve original duration, motion, and timing exactly
    """
    import subprocess
    import uuid
    import shutil
    from openai import OpenAI
    
    data = request.get_json()
    creative_dna = data.get('creative_dna', {})
    new_topic = data.get('topic', '')
    new_script = data.get('script', '')
    brand_colors = data.get('brand_colors', {})
    custom_images_raw = data.get('custom_images', [])
    voiceover_path = data.get('voiceover_path')
    caption_position = data.get('caption_position', 'bottom')
    caption_style = data.get('caption_style', 'modern')
    color_grade = data.get('color_grade', 'cinematic')
    
    custom_images = [validate_safe_path(p) for p in custom_images_raw if validate_safe_path(p)]
    if voiceover_path:
        voiceover_path = validate_safe_path(voiceover_path)
    
    if not creative_dna:
        return jsonify({'error': 'Creative DNA required'}), 400
    
    source_path = creative_dna.get('source_path')
    if not source_path or not os.path.exists(source_path):
        return jsonify({'error': 'Source video not found. Please re-upload the video.'}), 400
    
    if not new_topic and not new_script:
        return jsonify({'error': 'Topic or script required'}), 400
    
    try:
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        output_id = str(uuid.uuid4())[:8]
        os.makedirs('output', exist_ok=True)
        os.makedirs('uploads/remix_overlays', exist_ok=True)
        
        source_duration = creative_dna.get('total_duration', 30)
        source_width = creative_dna.get('source_width', 1080)
        source_height = creative_dna.get('source_height', 1920)
        scenes = creative_dna.get('scenes', [])
        
        logging.info(f"AI Remix starting: {len(scenes)} scenes, {source_duration:.1f}s duration")
        
        format_dims = {'9:16': (1080, 1920), '16:9': (1920, 1080), '1:1': (1080, 1080)}
        target_width, target_height = format_dims.get(data.get('format', '9:16'), (1080, 1920))
        
        color_grades = {
            'cinematic': 'eq=contrast=1.1:brightness=0.02:saturation=1.2,colorbalance=rs=0.05:gs=-0.02:bs=0.08',
            'warm': 'eq=contrast=1.05:brightness=0.03:saturation=1.1,colorbalance=rs=0.12:gs=0.05:bs=-0.05',
            'cool': 'eq=contrast=1.1:brightness=0:saturation=0.95,colorbalance=rs=-0.05:gs=0:bs=0.1',
            'vibrant': 'eq=contrast=1.15:brightness=0.02:saturation=1.4',
            'muted': 'eq=contrast=0.95:brightness=0:saturation=0.7',
            'vintage': 'eq=contrast=1.1:brightness=-0.02:saturation=0.85,colorbalance=rs=0.1:gs=0.05:bs=-0.1',
            'none': ''
        }
        grade_filter = color_grades.get(color_grade, color_grades['cinematic'])
        
        base_reskinned = f'output/remix_base_{output_id}.mp4'
        
        base_filter = f'scale={target_width}:{target_height}:force_original_aspect_ratio=increase,crop={target_width}:{target_height},setsar=1'
        if grade_filter:
            base_filter += f',{grade_filter}'
        
        logging.info("Step 1: Applying visual transformation to source video...")
        base_cmd = [
            'ffmpeg', '-y', '-i', source_path,
            '-vf', base_filter,
            '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
            '-c:a', 'aac', '-b:a', '192k',
            base_reskinned
        ]
        result = subprocess.run(base_cmd, capture_output=True, timeout=300)
        
        if result.returncode != 0:
            logging.error(f"Base transformation failed: {result.stderr.decode()}")
            return jsonify({'error': 'Failed to process source video'}), 500
        
        current_video = base_reskinned
        
        overlay_decisions = []
        pexels_key = os.environ.get("PEXELS_API_KEY")
        
        def generate_overlay_for_scene(scene, scene_idx):
            approach = scene.get('reskin_approach', 'color_grade')
            enhancement = scene.get('enhancement_suggestion', '')
            intent = scene.get('intent', '')
            has_person = scene.get('has_person', False)
            
            overlay_info = {
                'scene_index': scene_idx,
                'approach': approach,
                'overlay_path': None,
                'overlay_type': None,
                'start_time': scene.get('start_time', 0),
                'end_time': scene.get('end_time', 0),
                'duration': scene.get('duration', 3)
            }
            
            if approach in ['color_grade', 'keep_with_effects']:
                overlay_info['action'] = 'keep_original'
                return overlay_info
            
            if approach == 'overlay_graphics' and not has_person:
                try:
                    overlay_prompt = f"""Create a subtle, semi-transparent graphic overlay for: {new_topic}.
Scene context: {intent}.
Style: Modern, minimalist, suitable as video overlay.
Must be: Abstract shapes, light patterns, or decorative elements only.
No text, no faces, no solid backgrounds."""
                    
                    dalle_response = client.images.generate(
                        model="dall-e-3",
                        prompt=overlay_prompt,
                        size="1024x1792",
                        quality="standard",
                        n=1
                    )
                    
                    overlay_url = dalle_response.data[0].url
                    overlay_path = f'uploads/remix_overlays/overlay_{output_id}_{scene_idx}.png'
                    img_response = requests.get(overlay_url, timeout=30)
                    with open(overlay_path, 'wb') as f:
                        f.write(img_response.content)
                    
                    overlay_info['overlay_path'] = overlay_path
                    overlay_info['overlay_type'] = 'ai_graphic'
                    overlay_info['action'] = 'blend_overlay'
                    return overlay_info
                    
                except Exception as e:
                    logging.warning(f"Overlay generation failed for scene {scene_idx}: {e}")
            
            if approach == 'style_transfer' and pexels_key:
                try:
                    search_query = f"{new_topic} {enhancement or 'background'}"
                    headers = {"Authorization": pexels_key}
                    
                    video_url = f"https://api.pexels.com/videos/search?query={search_query}&per_page=1&orientation=portrait"
                    response = requests.get(video_url, headers=headers, timeout=10)
                    
                    if response.status_code == 200:
                        videos = response.json().get('videos', [])
                        if videos:
                            video_files = videos[0].get('video_files', [])
                            hd_files = [f for f in video_files if f.get('height', 0) >= 720]
                            if hd_files:
                                stock_url = hd_files[0].get('link')
                                stock_path = f'uploads/remix_overlays/stock_{output_id}_{scene_idx}.mp4'
                                stock_response = requests.get(stock_url, timeout=30)
                                with open(stock_path, 'wb') as f:
                                    f.write(stock_response.content)
                                
                                overlay_info['overlay_path'] = stock_path
                                overlay_info['overlay_type'] = 'stock_video'
                                overlay_info['action'] = 'blend_video'
                                return overlay_info
                except Exception as e:
                    logging.warning(f"Stock video fetch failed: {e}")
            
            overlay_info['action'] = 'keep_original'
            return overlay_info
        
        logging.info("Step 2: Analyzing scenes for enhancements...")
        
        enhancement_scenes = [s for s in scenes if s.get('reskin_approach') in ['overlay_graphics', 'style_transfer']]
        
        if enhancement_scenes and len(enhancement_scenes) <= 3:
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = {executor.submit(generate_overlay_for_scene, scene, scene.get('index', i)): i 
                          for i, scene in enumerate(enhancement_scenes)}
                for future in as_completed(futures):
                    try:
                        overlay_info = future.result()
                        if overlay_info.get('overlay_path'):
                            overlay_decisions.append(overlay_info)
                    except Exception as e:
                        logging.warning(f"Overlay generation error: {e}")
        
        if overlay_decisions:
            logging.info(f"Step 3: Applying {len(overlay_decisions)} enhancement overlays...")
            
            for overlay in overlay_decisions:
                if not overlay.get('overlay_path') or not os.path.exists(overlay['overlay_path']):
                    continue
                
                overlay_output = f'output/remix_overlay_{output_id}_{overlay["scene_index"]}.mp4'
                start_time = overlay.get('start_time', 0)
                end_time = overlay.get('end_time', start_time + 3)
                
                if overlay['overlay_type'] == 'ai_graphic':
                    overlay_filter = f"[1:v]scale={target_width}:{target_height},format=rgba,colorchannelmixer=aa=0.3[ovr];[0:v][ovr]overlay=0:0:enable='between(t,{start_time},{end_time})'"
                else:
                    overlay_filter = f"[1:v]scale={target_width}:{target_height},format=rgba,colorchannelmixer=aa=0.25[ovr];[0:v][ovr]blend=all_mode=overlay:all_opacity=0.3:enable='between(t,{start_time},{end_time})'"
                
                try:
                    overlay_cmd = [
                        'ffmpeg', '-y',
                        '-i', current_video,
                        '-i', overlay['overlay_path'],
                        '-filter_complex', overlay_filter,
                        '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
                        '-c:a', 'copy',
                        overlay_output
                    ]
                    result = subprocess.run(overlay_cmd, capture_output=True, timeout=120)
                    
                    if result.returncode == 0 and os.path.exists(overlay_output):
                        if current_video != base_reskinned:
                            try:
                                os.remove(current_video)
                            except:
                                pass
                        current_video = overlay_output
                except Exception as e:
                    logging.warning(f"Overlay application failed: {e}")
        
        final_output = f'output/reskinned_{output_id}.mp4'
        
        if voiceover_path and os.path.exists(voiceover_path):
            logging.info("Step 4: Adding voiceover...")
            audio_cmd = [
                'ffmpeg', '-y',
                '-i', current_video,
                '-i', voiceover_path,
                '-c:v', 'copy',
                '-c:a', 'aac', '-b:a', '192k',
                '-map', '0:v:0', '-map', '1:a:0',
                final_output
            ]
            subprocess.run(audio_cmd, capture_output=True, timeout=300)
        else:
            shutil.copy(current_video, final_output)
        
        if new_script and data.get('captions_enabled', True):
            logging.info("Step 5: Adding dynamic animated captions...")
            captioned_output = f'output/reskinned_captioned_{output_id}.mp4'
            
            script_text = new_script
            if isinstance(new_script, dict):
                script_text = new_script.get('text', new_script.get('script', new_script.get('content', '')))
            if not isinstance(script_text, str):
                script_text = str(script_text) if script_text else ''
            
            if voiceover_path and os.path.exists(voiceover_path):
                dur_cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', voiceover_path]
                dur_result = subprocess.run(dur_cmd, capture_output=True, text=True, timeout=30)
                audio_duration = float(dur_result.stdout.strip()) if dur_result.stdout.strip() else source_duration
            else:
                audio_duration = source_duration
            
            caption_template = caption_style if caption_style in CAPTION_TEMPLATES else 'bold_pop'
            
            ass_path = f'output/captions_{output_id}.ass'
            
            # Use Whisper-synced captions if voiceover exists, otherwise fall back to estimated timing
            whisper_success = False
            if voiceover_path and os.path.exists(voiceover_path):
                _, whisper_success = create_whisper_synced_captions(
                    voiceover_path,
                    ass_path,
                    template=caption_template,
                    position=caption_position,
                    video_width=target_width,
                    video_height=target_height
                )
            
            if not whisper_success:
                # Fall back to estimated timing
                logging.info("Using estimated caption timing (no voiceover for Whisper sync)")
                create_dynamic_captions_ass(
                    script_text, 
                    audio_duration, 
                    ass_path, 
                    template=caption_template,
                    position=caption_position,
                    video_width=target_width,
                    video_height=target_height
                )
            
            caption_cmd = [
                'ffmpeg', '-y',
                '-i', final_output,
                '-vf', f"ass={ass_path}",
                '-c:a', 'copy',
                captioned_output
            ]
            result = subprocess.run(caption_cmd, capture_output=True, timeout=600)
            
            if result.returncode == 0 and os.path.exists(captioned_output):
                shutil.move(captioned_output, final_output)
                logging.info(f"Captions applied with template: {caption_template} (Whisper-synced: {whisper_success})")
            else:
                logging.warning(f"ASS caption failed, falling back to SRT: {result.stderr.decode() if result.stderr else 'unknown error'}")
                srt_path = f'output/captions_{output_id}.srt'
                create_word_synced_subtitles(script_text, audio_duration, srt_path)
                
                fallback_cmd = [
                    'ffmpeg', '-y',
                    '-i', final_output,
                    '-vf', f"subtitles={srt_path}:force_style='FontName=Arial,FontSize=48,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=3,Shadow=2,Bold=1,MarginV=100,Alignment=2'",
                    '-c:a', 'copy',
                    captioned_output
                ]
                fallback_result = subprocess.run(fallback_cmd, capture_output=True, timeout=600)
                if fallback_result.returncode == 0 and os.path.exists(captioned_output):
                    shutil.move(captioned_output, final_output)
                if os.path.exists(srt_path):
                    os.remove(srt_path)
            
            if os.path.exists(ass_path):
                os.remove(ass_path)
        
        logging.info("Cleaning up temporary files...")
        cleanup_files = [base_reskinned]
        if current_video != base_reskinned and current_video != final_output:
            cleanup_files.append(current_video)
        
        for overlay in overlay_decisions:
            if overlay.get('overlay_path') and os.path.exists(overlay['overlay_path']):
                cleanup_files.append(overlay['overlay_path'])
        
        for f in cleanup_files:
            if f and os.path.exists(f) and f != final_output:
                try:
                    os.remove(f)
                except:
                    pass
        
        dur_cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', final_output]
        dur_result = subprocess.run(dur_cmd, capture_output=True, text=True, timeout=30)
        final_duration = float(dur_result.stdout.strip()) if dur_result.stdout.strip() else source_duration
        
        logging.info(f"AI Remix complete: {final_duration:.1f}s video created")
        
        return jsonify({
            'success': True,
            'video_path': '/' + final_output,
            'video_url': '/' + final_output,
            'duration': final_duration,
            'source_duration': source_duration,
            'scene_count': len(scenes),
            'color_grade_applied': color_grade,
            'overlays_applied': len([o for o in overlay_decisions if o.get('overlay_path')]),
            'approach': 'source_video_transformation',
            'creative_decisions': overlay_decisions
        })
        
    except Exception as e:
        logging.error(f"AI Remix error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/ai-quality-review', methods=['POST'])
def ai_quality_review():
    """AI self-reviews a generated video before showing to user."""
    import base64
    import subprocess
    from openai import OpenAI
    
    data = request.get_json()
    video_path = data.get('video_path')
    topic = data.get('topic', '')
    script = data.get('script', '')
    creative_dna = data.get('creative_dna', {})
    
    if not video_path or not os.path.exists(video_path.lstrip('/')):
        return jsonify({'error': 'Video not found'}), 404
    
    actual_path = video_path.lstrip('/')
    
    try:
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        
        # Extract frames from the generated video for review
        frames_dir = os.path.join('uploads', 'review_frames')
        os.makedirs(frames_dir, exist_ok=True)
        
        # Get duration
        dur_cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', actual_path]
        result = subprocess.run(dur_cmd, capture_output=True, text=True, timeout=30)
        duration = float(result.stdout.strip()) if result.stdout.strip() else 30
        
        # Extract 3 frames: start, middle, end
        frame_paths = []
        for i, timestamp in enumerate([2, duration/2, max(duration-2, 3)]):
            frame_path = os.path.join(frames_dir, f'review_{int(time.time())}_{i}.jpg')
            extract_cmd = ['ffmpeg', '-y', '-ss', str(timestamp), '-i', actual_path, '-vframes', '1', '-q:v', '2', frame_path]
            subprocess.run(extract_cmd, capture_output=True, timeout=30)
            if os.path.exists(frame_path):
                frame_paths.append(frame_path)
        
        if not frame_paths:
            return jsonify({'quality_score': 0.5, 'pass': True, 'issues': ['Could not extract frames for review']})
        
        # Encode frames
        frame_contents = []
        for fp in frame_paths:
            with open(fp, 'rb') as f:
                frame_b64 = base64.b64encode(f.read()).decode('utf-8')
                frame_contents.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{frame_b64}"}})
        
        # AI reviews the video quality
        review_prompt = f"""Review this generated video for quality. The video was created for topic: "{topic}"

Script being used: {script[:500] if script else 'N/A'}

Score each aspect from 0.0 to 1.0:
1. Visual coherence - Do the scenes flow together naturally?
2. Topic alignment - Do the visuals match the topic/script?
3. Professional quality - Does it look like professional content, not stock footage slideshow?
4. Brand consistency - Is there visual consistency throughout?

Output JSON only:
{{
    "visual_coherence": 0.0-1.0,
    "topic_alignment": 0.0-1.0,
    "professional_quality": 0.0-1.0,
    "brand_consistency": 0.0-1.0,
    "overall_score": 0.0-1.0,
    "pass": true/false (true if overall >= 0.6),
    "issues": ["list of specific issues found"],
    "weak_scenes": [0, 1, 2] (indexes of scenes that need regeneration),
    "suggestions": ["how to improve"]
}}"""

        content = [{"type": "text", "text": review_prompt}] + frame_contents
        
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": content}],
            max_tokens=500
        )
        
        review_text = response.choices[0].message.content
        
        # Parse review
        try:
            import json
            cleaned = review_text.strip()
            if cleaned.startswith('```'):
                cleaned = cleaned.split('```')[1]
                if cleaned.startswith('json'):
                    cleaned = cleaned[4:]
            review = json.loads(cleaned)
        except:
            review = {
                "overall_score": 0.7,
                "pass": True,
                "issues": [],
                "weak_scenes": [],
                "suggestions": []
            }
        
        # Cleanup frames
        for fp in frame_paths:
            try:
                os.remove(fp)
            except:
                pass
        
        return jsonify({
            'success': True,
            'review': review,
            'quality_score': review.get('overall_score', 0.7),
            'pass': review.get('pass', True),
            'issues': review.get('issues', []),
            'weak_scenes': review.get('weak_scenes', [])
        })
        
    except Exception as e:
        logging.error(f"AI quality review error: {e}")
        return jsonify({'quality_score': 0.6, 'pass': True, 'issues': [str(e)]})


@app.route('/reskin-feedback', methods=['POST'])
def reskin_feedback():
    """Store feedback on reskinned video for global learning."""
    from models import ReskinFeedback, VisualMatch
    from flask_login import current_user
    
    data = request.get_json()
    liked = data.get('liked')
    comment = data.get('comment', '')
    video_path = data.get('video_path')
    topic = data.get('topic', '')
    visual_sources = data.get('visual_sources', [])
    search_queries = data.get('search_queries', [])
    creative_dna = data.get('creative_dna', {})
    quality_scores = data.get('quality_scores', {})
    
    user_id = None
    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('dev_user_id')
    
    try:
        # Store feedback
        feedback = ReskinFeedback(
            user_id=user_id,
            source_dna=creative_dna,
            topic=topic,
            visual_sources=visual_sources,
            ai_quality_score=quality_scores.get('overall_score'),
            visual_match_score=quality_scores.get('topic_alignment'),
            brand_alignment_score=quality_scores.get('brand_consistency'),
            coherence_score=quality_scores.get('visual_coherence'),
            user_liked=liked,
            user_comment=comment,
            search_queries_used=search_queries,
            successful_visuals=[v for i, v in enumerate(visual_sources) if liked] if liked else [],
            failed_visuals=[v for i, v in enumerate(visual_sources) if not liked] if not liked else []
        )
        db.session.add(feedback)
        
        # Update global visual match patterns
        for i, scene in enumerate(creative_dna.get('scenes', [])):
            intent = scene.get('intent', '')
            scene_type = scene.get('scene_type', '')
            query = search_queries[i] if i < len(search_queries) else ''
            source = visual_sources[i] if i < len(visual_sources) else ''
            
            if intent and query:
                # Find or create visual match record
                match = VisualMatch.query.filter_by(
                    scene_intent=intent[:500],
                    search_query=query[:500]
                ).first()
                
                if not match:
                    match = VisualMatch(
                        scene_intent=intent[:500],
                        scene_type=scene_type,
                        search_query=query[:500],
                        source=source
                    )
                    db.session.add(match)
                
                # Update success/fail counts
                if liked:
                    match.success_count += 1
                else:
                    match.fail_count += 1
                
                total = match.success_count + match.fail_count
                match.success_rate = match.success_count / total if total > 0 else 0
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Feedback recorded for global learning',
            'feedback_id': feedback.id
        })
        
    except Exception as e:
        logging.error(f"Reskin feedback error: {e}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/get-best-visual-match', methods=['POST'])
def get_best_visual_match():
    """Get the best visual search query for a scene intent based on global learning."""
    from models import VisualMatch
    
    data = request.get_json()
    intent = data.get('intent', '')
    scene_type = data.get('scene_type', '')
    topic = data.get('topic', '')
    
    if not intent:
        return jsonify({'query': topic or 'professional background'})
    
    try:
        # Find best matching visual queries from global learning
        matches = VisualMatch.query.filter(
            VisualMatch.scene_intent.ilike(f'%{intent[:100]}%')
        ).order_by(
            VisualMatch.success_rate.desc(),
            VisualMatch.success_count.desc()
        ).limit(5).all()
        
        if matches and matches[0].success_rate > 0.5:
            # Use proven query
            best_query = matches[0].search_query
            # Adapt to new topic
            if topic:
                best_query = f"{topic} {best_query}"
            return jsonify({
                'query': best_query,
                'source': 'learned',
                'confidence': matches[0].success_rate
            })
        
        # No good match, return generic
        return jsonify({
            'query': f"{topic} {scene_type}" if topic else scene_type or 'professional background',
            'source': 'default',
            'confidence': 0.0
        })
        
    except Exception as e:
        logging.error(f"Visual match lookup error: {e}")
        return jsonify({'query': topic or 'professional background', 'source': 'fallback'})


@app.route('/render-personalized-video', methods=['POST'])
@rate_limit(limit=5, window=60)
def render_personalized_video():
    """Render a personalized video: mute original audio, dub with new voiceover, add captions."""
    import subprocess
    import uuid
    from models import Subscription, User
    from flask_login import current_user
    
    user_id = None
    is_dev_mode = os.environ.get('FLASK_ENV') == 'development' or os.environ.get('DEV_MODE') == 'true'
    
    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('dev_user_id')
    
    # Check subscription (same as render-video)
    if not is_dev_mode:
        sub = Subscription.query.filter_by(user_id=user_id).first() if user_id else None
        user = User.query.get(user_id) if user_id else None
        
        has_active_sub = sub and sub.is_active()
        has_free_generation = user and hasattr(user, 'free_video_generations') and (user.free_video_generations or 0) > 0
        
        if not has_active_sub and not has_free_generation:
            return jsonify({
                'error': 'Pro subscription required',
                'requires_subscription': True,
                'message': 'Video rendering requires a Pro subscription.'
            }), 403
        
        if not has_active_sub and has_free_generation:
            user.free_video_generations = max(0, (user.free_video_generations or 1) - 1)
            db.session.commit()
    
    data = request.get_json()
    template_path = data.get('template_path')
    template_id = data.get('template_id')
    audio_path = data.get('audio_path')
    script_text = data.get('script', '')
    captions_data = data.get('captions', {})
    video_format = data.get('format', '9:16')
    
    if not template_path or not os.path.exists(template_path):
        return jsonify({'error': 'Template video not found'}), 404
    
    if not audio_path or not os.path.exists(audio_path):
        return jsonify({'error': 'Audio file not found'}), 404
    
    try:
        output_id = str(uuid.uuid4())[:8]
        output_path = f'output/personalized_{output_id}.mp4'
        os.makedirs('output', exist_ok=True)
        
        # Get format dimensions
        format_dims = {
            '9:16': (1080, 1920),
            '16:9': (1920, 1080),
            '1:1': (1080, 1080),
            '4:5': (1080, 1350)
        }
        width, height = format_dims.get(video_format, (1080, 1920))
        
        # Get template video duration
        dur_cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', template_path]
        dur_result = subprocess.run(dur_cmd, capture_output=True, text=True, timeout=30)
        video_duration = float(dur_result.stdout.strip()) if dur_result.stdout.strip() else 30
        
        # Get audio duration
        audio_dur_cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', audio_path]
        audio_result = subprocess.run(audio_dur_cmd, capture_output=True, text=True, timeout=30)
        audio_duration = float(audio_result.stdout.strip()) if audio_result.stdout.strip() else 30
        
        # Determine output duration (use whichever is shorter, or loop video if audio is longer)
        target_duration = max(audio_duration, video_duration)
        
        # Step 1: Mute original video and scale to format
        muted_path = f'output/muted_{output_id}.mp4'
        mute_cmd = [
            'ffmpeg', '-y', '-i', template_path,
            '-an',  # Remove audio
            '-vf', f'scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1',
            '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
            '-t', str(target_duration),
            muted_path
        ]
        subprocess.run(mute_cmd, capture_output=True, timeout=300)
        
        # Step 2: Add new voiceover to muted video
        dubbed_path = f'output/dubbed_{output_id}.mp4'
        dub_cmd = [
            'ffmpeg', '-y',
            '-i', muted_path,
            '-i', audio_path,
            '-c:v', 'copy',
            '-c:a', 'aac', '-b:a', '192k',
            '-map', '0:v:0', '-map', '1:a:0',
            '-shortest',
            dubbed_path
        ]
        subprocess.run(dub_cmd, capture_output=True, timeout=300)
        
        # Step 3: Add captions if enabled
        captions_enabled = captions_data.get('enabled', False) if isinstance(captions_data, dict) else bool(captions_data)
        
        if captions_enabled and script_text:
            final_path = output_path
            
            # Get script as full text
            full_script = script_text.get('full_script', '') if isinstance(script_text, dict) else str(script_text)
            
            # Try Whisper-synced captions first, fallback to estimated timing
            ass_path = f'output/captions_{output_id}.ass'
            srt_path = f'output/captions_{output_id}.srt'
            whisper_success = False
            
            if audio_path and os.path.exists(audio_path):
                _, whisper_success = create_whisper_synced_captions(
                    audio_path,
                    ass_path,
                    template='bold_pop',
                    position='bottom',
                    video_width=width,
                    video_height=height
                )
            
            if not whisper_success:
                create_word_synced_subtitles(full_script, audio_duration, srt_path)
            
            # Get caption styling
            caption_style = captions_data.get('style', 'modern') if isinstance(captions_data, dict) else 'modern'
            
            # Define caption styles
            styles = {
                'modern': {'font': 'Inter-Bold', 'size': 52, 'color': 'white', 'outline': 3, 'shadow': 2},
                'minimal': {'font': 'Inter-Regular', 'size': 44, 'color': 'white', 'outline': 2, 'shadow': 0},
                'bold': {'font': 'Inter-ExtraBold', 'size': 60, 'color': 'yellow', 'outline': 4, 'shadow': 2},
                'neon': {'font': 'Inter-Bold', 'size': 54, 'color': '#00ffff', 'outline': 3, 'shadow': 4}
            }
            style = styles.get(caption_style, styles['modern'])
            
            # Use ASS if Whisper succeeded, otherwise SRT
            if whisper_success and os.path.exists(ass_path):
                caption_cmd = [
                    'ffmpeg', '-y',
                    '-i', dubbed_path,
                    '-vf', f"ass={ass_path}",
                    '-c:a', 'copy',
                    final_path
                ]
                logging.info("Using Whisper-synced ASS captions")
            else:
                caption_cmd = [
                    'ffmpeg', '-y',
                    '-i', dubbed_path,
                    '-vf', f"subtitles={srt_path}:force_style='FontName={style['font']},FontSize={style['size']},PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline={style['outline']},Shadow={style['shadow']},MarginV=60'",
                    '-c:a', 'copy',
                    final_path
                ]
                logging.info("Using estimated timing SRT captions")
            
            result = subprocess.run(caption_cmd, capture_output=True, timeout=600)
            
            if result.returncode != 0:
                # Fallback without fancy subtitles
                import shutil
                shutil.copy(dubbed_path, final_path)
            
            # Cleanup temp files
            for f in [srt_path, ass_path]:
                if os.path.exists(f):
                    os.remove(f)
        else:
            # No captions, just use dubbed video
            import shutil
            shutil.copy(dubbed_path, output_path)
        
        # Cleanup intermediate files
        for f in [muted_path, dubbed_path]:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except:
                    pass
        
        # Generate video description
        description = ""
        if script_text:
            full_script = script_text.get('full_script', '') if isinstance(script_text, dict) else str(script_text)
            description = generate_video_description(full_script[:500])
        
        return jsonify({
            'success': True,
            'video_path': '/' + output_path,
            'video_url': '/' + output_path,
            'url': '/' + output_path,
            'duration': target_duration,
            'format': video_format,
            'description': description,
            'template_id': template_id
        })
        
    except Exception as e:
        logging.error(f"Personalized video render error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/apply-template', methods=['POST'])
def apply_template():
    """Apply a video template to user's content and suggest curated visuals."""
    data = request.get_json()
    template_id = data.get('template_id')
    user_content = data.get('content', '')
    user_topic = data.get('topic', '')
    
    if not template_id:
        return jsonify({'error': 'Template ID required'}), 400
    
    template = VideoTemplate.query.get(template_id)
    if not template:
        return jsonify({'error': 'Template not found'}), 404
    
    try:
        from context_engine import get_ai_client
        
        client = get_ai_client()
        
        prompt = f"""You are adapting a video template to new content.

TEMPLATE INFO:
- Duration: {template.duration:.1f} seconds
- Scenes: {template.scene_count}
- Style: {template.aesthetic}
- Text patterns: {template.text_patterns}
- Original structure: {template.scenes}

USER'S NEW CONTENT:
Topic: {user_topic}
Content: {user_content[:2000]}

Create a personalized script that follows the SAME STRUCTURE and PACING as the template, but with the user's content.

For each scene, provide:
1. The adapted text/narration
2. A suggested visual concept (for AI to find a matching image)
3. Any text overlay content

Respond in JSON:
{{
  "title": "Video title",
  "scenes": [
    {{
      "index": 0,
      "narration": "Adapted narration for this scene",
      "visual_concept": "Description for image search",
      "text_overlay": "Text to show on screen",
      "duration": 3.0
    }}
  ],
  "estimated_duration": 30,
  "style_notes": "How to maintain the template's aesthetic"
}}"""
        
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )
        
        reply = response.content[0].text if response.content else ""
        
        import json
        import re
        json_match = re.search(r'\{[\s\S]*\}', reply)
        if json_match:
            adapted = json.loads(json_match.group())
        else:
            adapted = {"scenes": [], "error": "Could not parse response"}
        
        # Update template usage count
        template.usage_count += 1
        db.session.commit()
        
        return jsonify({
            'success': True,
            'adapted_content': adapted,
            'template_aesthetic': template.aesthetic,
            'template_transitions': template.transitions
        })
        
    except Exception as e:
        logging.error(f"Template application error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/my-templates', methods=['GET'])
def get_my_templates():
    """Get user's saved video templates."""
    user_id = session.get('replit_user_id')
    if not user_id:
        return jsonify({'templates': []})
    
    templates = VideoTemplate.query.filter_by(user_id=user_id).order_by(VideoTemplate.created_at.desc()).limit(20).all()
    
    return jsonify({
        'templates': [{
            'id': t.id,
            'name': t.name,
            'duration': t.duration,
            'scene_count': t.scene_count,
            'aesthetic': t.aesthetic,
            'thumbnail': t.thumbnail_path,
            'usage_count': t.usage_count,
            'created_at': t.created_at.isoformat() if t.created_at else None
        } for t in templates]
    })


@app.route('/transcribe', methods=['POST'])
def transcribe():
    data = request.get_json()
    file_path = data.get('file_path') or data.get('filename')
    
    if not file_path:
        return jsonify({'error': 'No file specified'}), 400
    
    # Try multiple path resolutions
    possible_paths = [
        file_path,
        f'uploads/{file_path}',
        f'uploads/{os.path.basename(file_path)}',
        os.path.basename(file_path)
    ]
    
    resolved_path = None
    for path in possible_paths:
        if os.path.exists(path):
            resolved_path = path
            break
    
    if not resolved_path:
        return jsonify({'error': f'File not found: {file_path}'}), 404
    
    file_path = resolved_path
    
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


@app.route('/research-trends', methods=['POST'])
def research_trends_endpoint():
    """Research how a topic is being discussed across platforms - Trend Intelligence feature."""
    from context_engine import research_topic_trends
    
    data = request.get_json()
    topic = data.get('topic')
    platform = data.get('platform', 'all')
    
    if not topic:
        return jsonify({'error': 'Missing topic'}), 400
    
    try:
        trends = research_topic_trends(topic, platform)
        return jsonify({
            'success': True,
            'trends': trends
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/generate-script', methods=['POST'])
def generate_script_endpoint():
    data = request.get_json()
    idea = data.get('idea')
    transcript = data.get('transcript')
    duration = data.get('duration', 30)
    template_type = data.get('template_type', 'start_from_scratch')
    
    if not idea or not transcript:
        return jsonify({'error': 'Missing idea or transcript'}), 400
    
    try:
        script = generate_script(idea, transcript, duration, use_trends=True, template_type=template_type)
        
        # Store trend sources in session for later use in render
        if script and script.get('trend_intel', {}).get('sources'):
            session['last_trend_sources'] = script['trend_intel']['sources']
        
        return jsonify({
            'success': True,
            'script': script
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/validate-loop', methods=['POST'])
def validate_loop_endpoint():
    """Validate how well a script closes back to its thesis."""
    from context_engine import validate_loop_score
    
    data = request.get_json()
    thesis = data.get('thesis')
    script = data.get('script')
    
    if not thesis or not script:
        return jsonify({'error': 'Missing thesis or script'}), 400
    
    try:
        result = validate_loop_score(thesis, script)
        return jsonify({
            'success': True,
            **result
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/scene-visuals', methods=['POST'])
def get_scene_visuals_endpoint():
    """Get AI-curated visual suggestions for a specific scene with 3 categories."""
    from context_engine import get_scene_visuals, search_visuals_unified, detect_characters_in_scene
    
    data = request.get_json()
    scene_text = data.get('scene_text')
    scene_type = data.get('scene_type', 'CLAIM')
    keywords = data.get('keywords', [])
    
    if not scene_text:
        return jsonify({'error': 'Missing scene_text'}), 400
    
    try:
        visual_suggestions = get_scene_visuals(scene_text, scene_type, keywords)
        
        # 1. Characters - detect people/figures in the scene
        characters = []
        try:
            char_data = detect_characters_in_scene(scene_text)
            for char in char_data.get('characters', [])[:3]:
                char_name = char.get('name', '')
                char_type = char.get('type', 'generic')
                search_query = char.get('search_query', char_name)
                
                if char_type == 'historical' and search_query:
                    results = search_visuals_unified(search_query, per_page=2)
                    for r in results:
                        r['character_name'] = char_name
                        r['category'] = 'character'
                    characters.extend(results)
                elif char_type == 'generic':
                    results = search_visuals_unified(search_query or 'person silhouette', per_page=2)
                    for r in results:
                        r['character_name'] = char_name or 'Character'
                        r['category'] = 'character'
                    characters.extend(results)
        except:
            pass
        
        # 2. Curated visuals - scene-specific imagery
        curated = []
        for query in visual_suggestions.get('search_queries', [])[:2]:
            try:
                results = search_visuals_unified(query, per_page=3)
                for r in results:
                    r['category'] = 'curated'
                curated.extend(results)
            except:
                pass
        
        # 3. Backgrounds - atmospheric/setting imagery
        backgrounds = []
        bg_queries = visual_suggestions.get('background_queries', [])
        if not bg_queries:
            bg_queries = ['cinematic background', 'dramatic atmosphere']
        for query in bg_queries[:2]:
            try:
                results = search_visuals_unified(query, per_page=2)
                for r in results:
                    r['category'] = 'background'
                backgrounds.extend(results)
            except:
                pass
        
        return jsonify({
            'success': True,
            'suggestions': visual_suggestions,
            'characters': characters[:4],
            'curated': curated[:4],
            'backgrounds': backgrounds[:4],
            'images': characters[:2] + curated[:2] + backgrounds[:2]
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/generate-scene-direction', methods=['POST'])
def generate_scene_direction():
    """Generate AI suggestion for scene camera direction based on content using Claude."""
    from context_engine import call_ai
    
    data = request.get_json()
    scene_text = data.get('scene_text', '')
    scene_type = data.get('scene_type', 'SCENE')
    visual_description = data.get('visual_description', '')
    
    if not scene_text:
        return jsonify({'direction': 'static'})
    
    prompt = f"""Based on this scene content, suggest ONE camera direction that best matches the emotional and narrative tone.

Scene type: {scene_type}
Scene text: "{scene_text}"
{f'Visual description: {visual_description}' if visual_description else ''}

Available directions:
- "zoom in slowly" - for reveals, emphasis, drawing viewer in, intimate moments
- "zoom out" - for big picture moments, conclusions, pulling back to show context
- "pan left" - for transitions, showing progression, scanning across a scene
- "pan right" - for returning to something, contrast, counter-movement
- "static" - for direct statements, stable moments, letting content speak

Consider:
1. The emotional arc of the text
2. Whether this is building tension or releasing it
3. What movement would enhance rather than distract from the message

Respond with ONLY the direction (e.g. "zoom in slowly" or "static"). No explanation."""

    try:
        response = call_ai(prompt, max_tokens=20)
        direction = response.strip().lower().strip('"\'')
        
        valid_directions = ['zoom in slowly', 'zoom out', 'pan left', 'pan right', 'static', 'zoom in', 'slow zoom']
        if not any(d in direction for d in valid_directions):
            direction = 'static'
        
        if 'zoom in' in direction:
            direction = 'zoom in slowly'
        elif 'zoom out' in direction:
            direction = 'zoom out'
        elif 'pan left' in direction:
            direction = 'pan left'
        elif 'pan right' in direction:
            direction = 'pan right'
        else:
            direction = 'static'
            
        return jsonify({'direction': direction})
        
    except Exception as e:
        print(f"[Scene Direction AI] Error: {e}")
        type_defaults = {
            'HOOK': 'zoom in slowly',
            'CLAIM': 'static',
            'EVIDENCE': 'pan left',
            'PIVOT': 'zoom out',
            'COUNTER': 'pan right',
            'CLOSER': 'zoom in slowly'
        }
        return jsonify({'direction': type_defaults.get(scene_type.upper(), 'static')})


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


@app.route('/favicon.ico')
def favicon():
    return send_from_directory('static', 'favicon.svg', mimetype='image/svg+xml')


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


TOKEN_COSTS = {
    'base_video': 25,
    'per_character': 3,
    'per_sfx': 1
}

@app.route('/generate-video', methods=['POST'])
@rate_limit(limit=10, window=60)
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
    
    # Calculate token cost
    data = request.get_json()
    extra_characters = max(0, len(data.get('character_layers', [])) - 1)
    sfx_count = len(data.get('sound_effects', []))
    token_cost = TOKEN_COSTS['base_video'] + (extra_characters * TOKEN_COSTS['per_character']) + (sfx_count * TOKEN_COSTS['per_sfx'])
    
    # Dev mode (server-side flag): fully free
    if is_dev_mode:
        print(f"[generate-video] Dev mode - free access (would cost {token_cost} tokens)")
    else:
        sub = Subscription.query.filter_by(user_id=user_id).first() if user_id else None
        user = User.query.get(user_id) if user_id else None
        
        # Check if user can generate (has tokens or free tier with video export)
        if sub:
            # Initialize token balance if needed
            if sub.token_balance is None:
                tier_tokens = {'free': 50, 'creator': 300, 'pro': 1000}
                sub.token_balance = tier_tokens.get(sub.tier, 50)
                db.session.commit()
            
            # Free tier cannot export videos
            if sub.tier == 'free':
                return jsonify({
                    'error': 'Video export requires Creator or Pro subscription',
                    'requires_subscription': True,
                    'message': 'Upgrade to Creator ($10/mo) or Pro ($25/mo) to export videos.'
                }), 403
            
            # Check token balance
            if sub.token_balance < token_cost:
                return jsonify({
                    'error': 'Not enough tokens',
                    'token_balance': sub.token_balance,
                    'token_cost': token_cost,
                    'message': f'You need {token_cost} tokens but only have {sub.token_balance}. Tokens refresh monthly or upgrade your plan.'
                }), 403
            
            # Deduct tokens
            sub.token_balance -= token_cost
            db.session.commit()
            print(f"[generate-video] Deducted {token_cost} tokens. New balance: {sub.token_balance}")
        else:
            # No subscription at all
            return jsonify({
                'error': 'Subscription required',
                'requires_subscription': True,
                'message': 'Video generation requires a Creator or Pro subscription.'
            }), 403
    
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
            
            # Apply caption template if specified (centralized helper)
            from visual_director import apply_caption_template
            captions = apply_caption_template(captions)
            
            # Get caption settings with template overrides applied
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
    from io import BytesIO
    
    data = request.get_json()
    text = data.get('text', '')
    voice = data.get('voice', data.get('voice_name', 'alloy'))
    
    if not text:
        return jsonify({'error': 'No text provided'}), 400
    
    # Direct ElevenLabs voice ID mapping for common voices
    elevenlabs_voice_map = {
        'Adam': 'pNInz6obpgDQGcFmaJgB',
        'Antoni': 'ErXwobaYiN019PkySvjV',
        'Arnold': 'VR6AewLTigWG4xSOukaG',
        'Bella': 'EXAVITQu4vr4xnSDxMaL',
        'Domi': 'AZnzlk1XvdvUeBnXmlld',
        'Elli': 'MF3mGyEYCl7XYWbV9V6O',
        'Josh': 'TxGEqnHWrfWFTfGW9XjX',
        'Rachel': '21m00Tcm4TlvDq8ikWAM',
        'Sam': 'yoZ06aMxZJJ28mfd3POQ'
    }
    
    # OpenAI voice mapping - each voice gets a distinct sound
    # OpenAI voices: alloy, echo, fable, onyx, nova, shimmer
    openai_voice_map = {
        # Male voices
        'Adam': 'onyx',       # Deep, authoritative
        'Antoni': 'echo',     # Clear, neutral
        'Arnold': 'onyx',     # Strong, deep
        'Josh': 'fable',      # Warm, expressive
        'Sam': 'echo',        # Neutral, professional
        # Female voices
        'Bella': 'nova',      # Warm, friendly
        'Domi': 'shimmer',    # Expressive, dynamic
        'Elli': 'shimmer',    # Soft, gentle
        'Rachel': 'nova',     # Clear, professional
        # Persona-based voices
        'The Analyst': 'echo',
        'The Narrator': 'onyx',
        'The Storyteller': 'fable',
        'The Teacher': 'nova',
        'The Critic': 'echo',
        'The Advocate': 'fable',
        'The Philosopher': 'onyx',
        'The Journalist': 'alloy',
    }
    
    # Use direct mapping if voice name matches, otherwise use get_voice_config
    if voice in elevenlabs_voice_map:
        elevenlabs_voice_id = elevenlabs_voice_map[voice]
        base_voice = openai_voice_map.get(voice, 'alloy')
    else:
        # Get voice config for persona-based voices
        base_voice, elevenlabs_voice_id, system_prompt = get_voice_config(voice)
        # Override with OpenAI mapping if available
        if voice in openai_voice_map:
            base_voice = openai_voice_map[voice]
    
    elevenlabs_key = os.environ.get("ELEVENLABS_API_KEY")
    
    # Try ElevenLabs first - return audio directly as stream
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
            
            # Collect audio bytes for direct streaming
            audio_buffer = BytesIO()
            for chunk in audio:
                if isinstance(chunk, bytes):
                    audio_buffer.write(chunk)
            
            audio_buffer.seek(0)
            
            if audio_buffer.getbuffer().nbytes > 0:
                return Response(
                    audio_buffer.getvalue(),
                    mimetype='audio/mpeg',
                    headers={'Content-Type': 'audio/mpeg'}
                )
            else:
                print("ElevenLabs preview produced empty audio, falling back to OpenAI")
                
        except Exception as e:
            print(f"ElevenLabs preview error, falling back to OpenAI: {e}")
    
    # Fallback to OpenAI - also return audio directly
    try:
        from openai import OpenAI
        
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        
        response = client.audio.speech.create(
            model="tts-1",
            voice=base_voice,
            input=text,
            speed=1.25
        )
        
        # Stream response directly
        audio_buffer = BytesIO()
        for chunk in response.iter_bytes():
            audio_buffer.write(chunk)
        
        audio_buffer.seek(0)
        return Response(
            audio_buffer.getvalue(),
            mimetype='audio/mpeg',
            headers={'Content-Type': 'audio/mpeg'}
        )
            
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
    data = request.get_json()
    script = data.get('script', '')
    
    if not script:
        return jsonify({'success': False, 'error': 'No script provided'}), 400
    
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
        prompt = f"Detect characters in this script:\n\n{script}"
        result = call_ai(prompt, system_prompt, json_output=True, max_tokens=1024)
        
        if not result:
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
    data = request.get_json()
    script = data.get('script', '')
    
    if not script:
        return jsonify({'success': False, 'error': 'No script provided'}), 400
    
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
        system = "You are an audio director for short-form video content."
        result = call_ai(prompt, system, json_output=False, max_tokens=1024)
        directions = result.get('text', '') if result else ""
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
    
    # Handle case where script is passed as a dict instead of string
    if isinstance(script, dict):
        # Extract text from various possible dict formats
        script = script.get('text', '') or script.get('content', '') or script.get('script', '') or str(script)
    
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
        
        # If no structured lines found, treat entire script as narration
        if not lines:
            # Clean up script and treat as single narrator voice
            clean_script = script.strip()
            # Remove any remaining headers/markers and AI meta-commentary
            clean_lines = []
            
            # Patterns that indicate AI meta-commentary (not actual script)
            meta_patterns = [
                r'^Understood', r'^I\'ll create', r'^Here\'s', r'^Let me create',
                r'^This script', r'^The script', r'^I\'ve', r'^I can create',
                r'^Let me know', r'^Would you like', r'^The message',
                r'^exaggerated personas', r'^With voices', r'^I hope this',
                r'^This uses a', r'^The humor comes', r'^Here is',
                r'^I\'ve crafted', r'^This ad', r'^The ad', r'^Below is',
                r'^Note:', r'^---', r'^\*\*', r'^Script:', r'^Title:',
            ]
            
            for line in clean_script.split('\n'):
                line = line.strip()
                if not line:
                    continue
                # Skip metadata lines
                if line.startswith('HOOK:') or line.startswith('BODY:') or line.startswith('CLOSER:'):
                    continue
                if line.startswith('[') and line.endswith(']') and ':' not in line:
                    continue
                # Skip AI meta-commentary
                if any(re.match(p, line, re.IGNORECASE) for p in meta_patterns):
                    continue
                # Skip lines that look like headers or explanations (all caps, short)
                if re.match(r'^[A-Z\s]+$', line) and len(line) < 30:
                    continue
                clean_lines.append(line)
            
            if clean_lines:
                narration_text = ' '.join(clean_lines)
                lines.append({'character': 'NARRATOR', 'text': narration_text})
        
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
@rate_limit(limit=60, window=60)
def chat():
    """Direct chat with Krakd AI with conversation memory and unified content engine."""
    from openai import OpenAI
    from context_engine import save_conversation, build_personalized_prompt, get_source_learning_context, extract_audio, transcribe_audio
    from flask_login import current_user
    import os
    import re
    
    data = request.get_json()
    message = data.get('message')
    conversation = data.get('conversation', [])
    use_unified_engine = data.get('use_unified_engine', False)
    mode = data.get('mode', 'auto')
    
    user_id = current_user.id if current_user.is_authenticated else 'dev_user'
    
    if not message:
        return jsonify({'error': 'No message provided'}), 400
    
    video_context = ""
    video_patterns = [
        r'📎\s*([^\s]+\.mp4)',
        r'([^\s]+\.mp4)',
        r'uploads/[^\s]+\.mp4',
        r'output/[^\s]+\.mp4'
    ]
    
    video_file = None
    for pattern in video_patterns:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            potential_file = match.group(1) if '(' in pattern else match.group(0)
            potential_file = potential_file.strip()
            if os.path.exists(potential_file):
                video_file = potential_file
                break
            clean_path = potential_file.lstrip('📎').strip()
            if os.path.exists(clean_path):
                video_file = clean_path
                break
            for prefix in ['uploads/', 'output/', '']:
                test_path = prefix + os.path.basename(clean_path)
                if os.path.exists(test_path):
                    video_file = test_path
                    break
    
    if video_file and os.path.exists(video_file):
        try:
            audio_path = video_file.rsplit('.', 1)[0] + '_audio.mp3'
            if extract_audio(video_file, audio_path):
                transcript_data = transcribe_audio(audio_path)
                if transcript_data and transcript_data.get('text'):
                    video_context = f"\n\n[VIDEO TRANSCRIPTION from {os.path.basename(video_file)}]:\n{transcript_data['text']}\n\n[Use this transcription to understand the video's content, style, and message.]"
                try:
                    os.remove(audio_path)
                except:
                    pass
        except Exception as e:
            logging.warning(f"Could not transcribe video {video_file}: {e}")
    
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
    
    user_message_with_context = message + video_context if video_context else message
    messages.append({"role": "user", "content": user_message_with_context})
    
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
    
    When has_media=True, shows options: "Inspire my visuals" or "Clip this video"
    """
    from flask_login import current_user
    from models import SourceContent, ProjectThesis, ScriptAnchor, ThoughtChange, Project
    
    data = request.get_json()
    user_input = data.get('input', '')
    mode = data.get('mode', 'auto')
    project_id = data.get('project_id')
    has_media = data.get('has_media', False)  # True when video/audio is uploaded
    clarification_count = data.get('clarification_count', 0)
    force_generate = data.get('force_generate', False)
    
    user_id = current_user.id if current_user.is_authenticated else 'dev_user'
    
    if not user_input:
        return jsonify({'error': 'No input provided'}), 400
    
    try:
        result = unified_content_engine(user_input, user_id, mode, has_media, clarification_count, force_generate)
        
        if result.get('mode') == 'greeting':
            return jsonify({
                'mode': 'greeting',
                'status': 'conversational',
                'reply': result.get('reply', "What's on your mind the world should get to know?"),
                'needs_content': True
            })
        
        # Handle media options - user needs to choose what to do with their media
        if result.get('mode') == 'media_options':
            return jsonify({
                'mode': 'media_options',
                'status': 'needs_choice',
                'options': result.get('options', []),
                'question': result.get('question', 'What would you like to do with this video?')
            })
        
        if result.get('status') == 'ready':
            if result.get('mode') == 'clip_video':
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
            
            # Global AI learning - analyze and store patterns for all users
            try:
                global_analysis = analyze_editing_patterns_global(
                    {'transcript': transcript},
                    result.get('recommended_clips', [])
                )
                if global_analysis.get('success') and global_analysis.get('patterns'):
                    store_global_patterns(global_analysis['patterns'], db.session)
                    print(f"[Global Learning] Stored {len(global_analysis['patterns'])} patterns from clip source")
            except Exception as ge:
                print(f"[Global Learning] Error: {ge}")
            
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


@app.route('/auto-assign-voices', methods=['POST'])
def auto_assign_voices():
    """Auto-assign voices to characters based on script context."""
    data = request.get_json()
    script = data.get('script', {})
    
    # Get characters from script anchors
    characters = set()
    anchors = script.get('anchors', [])
    for anchor in anchors:
        char = anchor.get('character', 'Narrator')
        characters.add(char)
    
    if not characters:
        characters.add('Narrator')
    
    # Default voice mappings based on character type
    voice_pool = {
        'male': ['Adam', 'Antoni', 'Arnold', 'Josh', 'Sam'],
        'female': ['Bella', 'Domi', 'Elli', 'Rachel'],
        'neutral': ['Adam', 'Rachel']
    }
    
    # Simple heuristic for voice assignment
    voice_assignments = {}
    male_idx = 0
    female_idx = 0
    
    for char in sorted(characters):
        char_lower = char.lower()
        
        # Guess gender from common names/patterns
        if any(name in char_lower for name in ['narrator', 'host', 'adam', 'john', 'mike', 'david', 'james']):
            voice_assignments[char] = voice_pool['male'][male_idx % len(voice_pool['male'])]
            male_idx += 1
        elif any(name in char_lower for name in ['sarah', 'rachel', 'bella', 'emma', 'lisa', 'amy']):
            voice_assignments[char] = voice_pool['female'][female_idx % len(voice_pool['female'])]
            female_idx += 1
        else:
            # Default to alternating
            if male_idx <= female_idx:
                voice_assignments[char] = voice_pool['male'][male_idx % len(voice_pool['male'])]
                male_idx += 1
            else:
                voice_assignments[char] = voice_pool['female'][female_idx % len(voice_pool['female'])]
                female_idx += 1
    
    return jsonify({
        'success': True,
        'voice_assignments': voice_assignments
    })


@app.route('/render-video', methods=['POST'])
@rate_limit(limit=10, window=60)
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
    template_type = data.get('template', 'start_from_scratch')  # Template for visual FX styling
    
    from context_engine import get_template_visual_fx
    visual_fx = get_template_visual_fx(template_type)
    
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
        # Count scenes with video OR image URLs
        num_scenes = len([s for s in scenes if s.get('video_url') or s.get('image_url') or s.get('visual') or s.get('thumbnail')])
        if audio_duration and num_scenes > 0:
            # Distribute clips evenly across audio duration
            base_clip_duration = audio_duration / num_scenes
            print(f"Audio-driven clips: {base_clip_duration:.2f}s each for {num_scenes} scenes")
        else:
            base_clip_duration = None  # Fall back to scene-specified durations
        
        # Download video clips and trim to match audio - PARALLELIZED for speed
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        def download_and_trim_clip(args):
            """Download and trim a single clip - runs in parallel. Supports both video and image URLs."""
            i, scene, duration, output_id = args
            video_url = scene.get('video_url', '')
            image_url = scene.get('image_url', '') or scene.get('visual', '') or scene.get('thumbnail', '')
            
            if not video_url and not image_url:
                print(f"Clip {i}: No video_url or image_url found")
                return None, i, duration
            
            raw_path = f'output/raw_{output_id}_{i}.mp4'
            clip_path = f'output/clip_{output_id}_{i}.mp4'
            
            try:
                if video_url:
                    # Download video clip
                    req = urllib.request.Request(video_url, headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req, timeout=20) as response:
                        with open(raw_path, 'wb') as f:
                            f.write(response.read())
                    
                    # Trim clip
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
                else:
                    # Image URL - convert static image to video clip
                    img_path = f'output/img_{output_id}_{i}.jpg'
                    direction = scene.get('direction', 'static')
                    print(f"Clip {i}: Converting image to video - direction: {direction}")
                    
                    # Download image
                    req = urllib.request.Request(image_url, headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req, timeout=20) as response:
                        with open(img_path, 'wb') as f:
                            f.write(response.read())
                    
                    # Get target dimensions based on format
                    format_sizes = {
                        '9:16': (1080, 1920),
                        '1:1': (1080, 1080),
                        '4:5': (1080, 1350),
                        '16:9': (1920, 1080)
                    }
                    target_w, target_h = format_sizes.get(video_format, (1080, 1920))
                    
                    # Build video filter based on direction
                    # Default: static with center crop
                    base_filter = f'scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black'
                    
                    # Apply camera movement only if explicitly specified (not 'static')
                    if direction and direction.lower() not in ['static', '']:
                        # Scale image larger for movement headroom
                        if 'zoom in' in direction.lower():
                            # Zoom in: start at 100%, end at 110%
                            motion_filter = f'scale={int(target_w*1.2)}:{int(target_h*1.2)}:force_original_aspect_ratio=decrease,zoompan=z=\'min(zoom+0.0015,1.1)\':x=\'iw/2-(iw/zoom/2)\':y=\'ih/2-(ih/zoom/2)\':d={int(duration*30)}:s={target_w}x{target_h}'
                        elif 'zoom out' in direction.lower():
                            # Zoom out: start at 110%, end at 100%
                            motion_filter = f'scale={int(target_w*1.2)}:{int(target_h*1.2)}:force_original_aspect_ratio=decrease,zoompan=z=\'if(lte(zoom,1.0),1.1,max(zoom-0.0015,1.0))\':x=\'iw/2-(iw/zoom/2)\':y=\'ih/2-(ih/zoom/2)\':d={int(duration*30)}:s={target_w}x{target_h}'
                        elif 'pan left' in direction.lower():
                            # Pan left: move from right to left
                            motion_filter = f'scale={int(target_w*1.3)}:-1,crop={target_w}:{target_h}:x=\'(iw-{target_w})*t/{duration}\':y=0'
                        elif 'pan right' in direction.lower():
                            # Pan right: move from left to right
                            motion_filter = f'scale={int(target_w*1.3)}:-1,crop={target_w}:{target_h}:x=\'(iw-{target_w})*(1-t/{duration})\':y=0'
                        else:
                            motion_filter = base_filter
                        vf = motion_filter
                    else:
                        vf = base_filter
                    
                    # Convert image to video
                    img_to_vid_cmd = [
                        'ffmpeg', '-y',
                        '-loop', '1',
                        '-i', os.path.abspath(img_path),
                        '-t', str(duration),
                        '-vf', vf,
                        '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '23',
                        '-pix_fmt', 'yuv420p',
                        '-an',
                        os.path.abspath(clip_path)
                    ]
                    result = subprocess.run(img_to_vid_cmd, capture_output=True, timeout=60)
                    
                    if result.returncode != 0:
                        print(f"Clip {i}: FFmpeg error - {result.stderr.decode()[:200]}")
                        # Fallback to simple static conversion
                        fallback_cmd = [
                            'ffmpeg', '-y', '-loop', '1',
                            '-i', os.path.abspath(img_path),
                            '-t', str(duration),
                            '-vf', base_filter,
                            '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '23',
                            '-pix_fmt', 'yuv420p', '-an',
                            os.path.abspath(clip_path)
                        ]
                        subprocess.run(fallback_cmd, capture_output=True, timeout=60)
                    
                    # Cleanup temp image
                    if os.path.exists(img_path):
                        os.remove(img_path)
                
                if os.path.exists(clip_path):
                    print(f"Clip {i}: Success - {duration:.1f}s")
                    return clip_path, i, duration
                return None, i, duration
            except Exception as e:
                print(f"Clip {i} error: {e}")
                for f in [raw_path, clip_path, f'output/img_{output_id}_{i}.jpg']:
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
        caption_srt_path = None
        caption_style_settings = None
        
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
                    # Apply caption template if specified (centralized helper)
                    from visual_director import apply_caption_template
                    caption_settings = apply_caption_template(caption_settings)
                    
                    # Get caption settings with template overrides applied
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
                    
                    # Font size based on resolution (smaller for better readability)
                    fontsize = 24 if not preview_mode else 14
                    
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
                    
                    # Generate SRT file from phrases (more robust than drawtext chains)
                    def format_srt_time(seconds):
                        """Convert seconds to SRT timestamp format (HH:MM:SS,mmm)"""
                        hours = int(seconds // 3600)
                        minutes = int((seconds % 3600) // 60)
                        secs = int(seconds % 60)
                        millis = int((seconds % 1) * 1000)
                        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
                    
                    srt_content = []
                    for i, phrase in enumerate(phrases, 1):
                        text = phrase['text']
                        if caption_uppercase:
                            text = text.upper()
                        start_srt = format_srt_time(phrase['start'])
                        end_srt = format_srt_time(phrase['end'])
                        srt_content.append(f"{i}\n{start_srt} --> {end_srt}\n{text}\n")
                    
                    # Write SRT file
                    srt_path = f"output/captions_{uuid.uuid4().hex[:8]}.srt"
                    with open(srt_path, 'w', encoding='utf-8') as f:
                        f.write('\n'.join(srt_content))
                    
                    # Store SRT path and style settings for Pass 2
                    caption_srt_path = srt_path
                    caption_style_settings = {
                        'fontsize': fontsize,
                        'color': caption_color,
                        'outline': caption_outline,
                        'shadow': caption_shadow,
                        'y_pos': y_pos
                    }
                    
                    print(f"Generated SRT with {len(phrases)} caption phrases: {srt_path}")
                else:
                    print("No word timestamps returned from Whisper")
                    
            except Exception as e:
                print(f"Whisper transcription failed, skipping captions: {e}")
        
        # Pass 2: Add captions using SRT file (more robust than drawtext chains)
        if caption_srt_path and os.path.exists(caption_srt_path) and os.path.exists(temp_combined):
            print(f"Pass 2: Adding captions from SRT file...")
            
            # Build subtitle style for FFmpeg ASS format
            # FontSize, PrimaryColour (BGR format), OutlineColour, BorderStyle, Outline
            style = caption_style_settings
            font_size = style['fontsize']
            # Convert hex color to BGR (FFmpeg ASS uses &HBBGGRR& format)
            hex_color = style['color'].lstrip('#')
            if len(hex_color) == 6:
                r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
                bgr_color = f"&H{b:02X}{g:02X}{r:02X}&"
            else:
                bgr_color = "&HFFFFFF&"
            
            # Build force_style string
            outline_width = 3 if style['outline'] else 0
            shadow_depth = 2 if style['shadow'] else 0
            
            # Escape the SRT path for FFmpeg (colons and backslashes)
            escaped_srt = caption_srt_path.replace('\\', '/').replace(':', r'\:')
            
            # Calculate MarginV (vertical margin from bottom) based on y_pos
            # y_pos is from top, we need to convert to margin from bottom
            # Assume 1080p height, MarginV is pixels from bottom
            margin_v = 100  # Default bottom margin
            
            subtitle_filter = (
                f"subtitles={escaped_srt}:force_style='"
                f"FontName=DejaVu Sans,FontSize={font_size},"
                f"PrimaryColour={bgr_color},OutlineColour=&H000000&,"
                f"BorderStyle=1,Outline={outline_width},Shadow={shadow_depth},"
                f"Alignment=2,MarginV={margin_v}'"
            )
            
            pass2_cmd = [
                'ffmpeg', '-y',
                '-i', temp_combined,
                '-vf', subtitle_filter,
                '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '26', '-threads', '0',
                '-c:a', 'copy',
                output_path
            ]
            
            print(f"Caption filter: {subtitle_filter}")
            pass2_result = subprocess.run(pass2_cmd, capture_output=True, timeout=300)
            
            if pass2_result.returncode != 0:
                error_msg = pass2_result.stderr.decode()[:2000]
                print(f"Pass 2 (captions) failed: {error_msg}")
                # Fallback: use pass 1 output without captions
                import shutil
                shutil.copy(temp_combined, output_path)
                print("Using video without captions as fallback")
            else:
                print("Pass 2 succeeded - captions added via SRT")
            
            # Cleanup SRT file
            try:
                os.remove(caption_srt_path)
            except:
                pass
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
            response_data = {
                'success': True,
                'video_url': '/' + output_path,
                'video_path': '/' + output_path,  # Keep for backwards compatibility
                'format': video_format
            }
            
            # Generate video description using Trend Intelligence
            try:
                from context_engine import generate_video_description
                
                # Get trend sources from session
                trend_sources = session.get('last_trend_sources', [])
                
                # Generate description with trend sources for context
                desc_result = generate_video_description(script_text or '', trend_sources=trend_sources)
                response_data['description'] = desc_result.get('description', '')
                response_data['hashtags'] = desc_result.get('hashtags', [])
                response_data['trend_sources'] = trend_sources
            except Exception as desc_err:
                print(f"Description generation error: {desc_err}")
                response_data['description'] = ''
                response_data['trend_sources'] = []
            
            # AI Self-Critique: Run AFTER successful export (user accepted by downloading)
            # This lets the AI analyze what it did well and didn't do well
            try:
                from context_engine import ai_self_critique, store_ai_learnings
                project_data = {
                    'project_id': session.get('current_project_id'),
                    'script': script_text or '',
                    'visual_plan': scenes,
                    'template': session.get('current_template', 'start_from_scratch'),
                    'original_request': session.get('original_user_request', ''),
                    'user_id': user_id
                }
                critique_result = ai_self_critique(project_data, user_accepted=True)
                if critique_result:
                    critique_result['user_id'] = user_id
                    store_ai_learnings(critique_result, db.session)
                    response_data['ai_self_score'] = critique_result.get('overall_self_score', 0)
                    print(f"[AI Self-Critique] Completed: {critique_result.get('honest_assessment', 'N/A')}")
            except Exception as critique_err:
                print(f"[AI Self-Critique] Error (non-blocking): {critique_err}")
            
            return jsonify(response_data)
        else:
            return jsonify({'error': format_user_error('Video render failed')}), 500
            
    except Exception as e:
        print(f"Render error: {e}")
        return jsonify({'error': format_user_error(str(e))}), 500


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


@app.route('/video-feedback', methods=['POST'])
def video_feedback():
    """Save video like/dislike feedback."""
    from models import VideoFeedback, Project, AILearning, GlobalPattern
    from flask_login import current_user
    
    data = request.json
    project_id = data.get('project_id')
    liked = data.get('liked')
    comment = data.get('comment')
    script = data.get('script', '')
    revision_number = data.get('revision_number', 0)
    
    user_id = None
    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('anonymous_user_id', 'anonymous')
    
    try:
        # Create feedback record
        feedback = VideoFeedback(
            project_id=project_id if project_id else None,
            user_id=user_id,
            liked=liked,
            comment=comment,
            script_version=script[:2000] if script else None,
            revision_number=revision_number
        )
        db.session.add(feedback)
        
        # Update project liked status
        if project_id:
            project = Project.query.get(project_id)
            if project:
                project.liked = liked
                project.revision_count = revision_number
                if liked:
                    project.is_successful = True
                    project.success_score = max(project.success_score, 80)
        
        # Update AI learning based on feedback
        ai_learning = AILearning.query.filter_by(user_id=user_id).first()
        if ai_learning:
            if liked:
                ai_learning.successful_projects += 1
                ai_learning.learning_progress = min(ai_learning.learning_progress + 3, 100)
            else:
                ai_learning.learning_progress = min(ai_learning.learning_progress + 5, 100)
        
        # Update global patterns for AI improvement
        if liked:
            pattern = GlobalPattern.query.filter_by(pattern_type='like_rate').first()
            if pattern:
                pattern.success_count += 1
                pattern.usage_count += 1
                pattern.success_rate = pattern.success_count / max(pattern.usage_count, 1)
            else:
                pattern = GlobalPattern(
                    pattern_type='like_rate',
                    pattern_data={'description': 'Video like/dislike ratio'},
                    success_count=1,
                    usage_count=1,
                    success_rate=1.0
                )
                db.session.add(pattern)
            
            # If this is a revision (revision_number > 0), mark feedback patterns as successful
            if revision_number > 0:
                # Find the previous dislike feedback to get what issue was fixed
                prev_feedback = VideoFeedback.query.filter_by(
                    project_id=project_id,
                    user_id=user_id,
                    liked=False
                ).order_by(VideoFeedback.created_at.desc()).first()
                
                if prev_feedback and prev_feedback.ai_analysis:
                    pattern_type = prev_feedback.ai_analysis.get('pattern')
                    if pattern_type:
                        feedback_pattern = GlobalPattern.query.filter_by(
                            pattern_type=f"feedback_{pattern_type}"
                        ).first()
                        if feedback_pattern:
                            feedback_pattern.success_count += 1
                            feedback_pattern.success_rate = feedback_pattern.success_count / max(feedback_pattern.usage_count, 1)
        else:
            pattern = GlobalPattern.query.filter_by(pattern_type='like_rate').first()
            if pattern:
                pattern.usage_count += 1
                pattern.success_rate = pattern.success_count / max(pattern.usage_count, 1)
        
        db.session.commit()
        
        return jsonify({'success': True})
        
    except Exception as e:
        print(f"Video feedback error: {e}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/refine-from-feedback', methods=['POST'])
def refine_from_feedback():
    """Refine script based on user feedback using AI."""
    from models import VideoFeedback, Project, Subscription, GlobalPattern
    from flask_login import current_user
    import os
    from openai import OpenAI
    
    data = request.json
    project_id = data.get('project_id')
    script = data.get('script', '')
    feedback = data.get('feedback', '')
    
    user_id = None
    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('anonymous_user_id', 'anonymous')
    
    # Get actual revision count from project (server-side enforcement)
    MAX_FREE_REVISIONS = 3
    revision_number = 1
    
    if project_id:
        project = Project.query.get(project_id)
        if project:
            revision_number = (project.revision_count or 0) + 1
    
    # Check subscription status
    is_pro = False
    if user_id:
        sub = Subscription.query.filter_by(user_id=user_id).first()
        is_pro = sub and sub.is_active()
    
    # Server-side revision limit enforcement
    if not is_pro and revision_number > MAX_FREE_REVISIONS:
        return jsonify({
            'success': False,
            'error': 'Revision limit reached. Upgrade to Pro for unlimited revisions.',
            'requires_subscription': True,
            'revisions_used': revision_number - 1,
            'max_revisions': MAX_FREE_REVISIONS
        }), 403
    
    if not script:
        return jsonify({'error': 'No script to refine'}), 400
    
    if not feedback:
        return jsonify({'error': 'No feedback provided'}), 400
    
    try:
        client = OpenAI(
            api_key=os.environ.get("XAI_API_KEY"),
            base_url="https://api.x.ai/v1"
        )
        
        # Get past feedback patterns for this user to learn from
        past_feedbacks = VideoFeedback.query.filter_by(user_id=user_id, liked=False).order_by(VideoFeedback.created_at.desc()).limit(5).all()
        past_feedback_summary = ""
        if past_feedbacks:
            past_feedback_summary = "\n".join([f"- {fb.comment}" for fb in past_feedbacks if fb.comment])
        
        # Get global patterns that led to successful revisions (pattern injection for AI improvement)
        successful_patterns = GlobalPattern.query.filter(
            GlobalPattern.pattern_type.like('feedback_%'),
            GlobalPattern.success_rate > 0.5
        ).order_by(GlobalPattern.success_rate.desc()).limit(3).all()
        
        pattern_insights = ""
        if successful_patterns:
            pattern_insights = "LEARNED PATTERNS THAT WORK:\n" + "\n".join([
                f"- When users complain about '{p.pattern_type.replace('feedback_', '')}', fixes that address it directly have {int(p.success_rate * 100)}% success rate"
                for p in successful_patterns
            ])
        
        refine_prompt = f"""You are Krakd — a script refinement engine. The user disliked their video and provided specific feedback.

ORIGINAL SCRIPT:
{script}

USER'S FEEDBACK (what they want fixed):
{feedback}

PREVIOUS FEEDBACK FROM THIS USER (patterns to learn from):
{past_feedback_summary if past_feedback_summary else 'No previous feedback'}

{pattern_insights}

REVISION NUMBER: {revision_number}

YOUR TASK:
1. Analyze the user's feedback carefully
2. Identify the specific problems they mentioned
3. Revise the script to address EXACTLY what they asked for
4. Keep the core thesis and structure intact unless they asked to change it
5. Make targeted improvements, not complete rewrites

RULES:
- If they say "too slow" → tighten dialogue, cut filler
- If they say "too robotic" → make dialogue more conversational and natural
- If they say "wrong tone" → adjust the voice/style
- If they say "visuals don't match" → update VISUAL tags
- Be specific with your changes

Output the refined script in the same format as the original (plain text screenplay format).
Do NOT explain what you changed — just output the refined script."""

        response = client.chat.completions.create(
            model="grok-3",
            messages=[{"role": "user", "content": refine_prompt}],
            max_tokens=2048
        )
        
        refined_script = response.choices[0].message.content.strip()
        
        # Store the AI's analysis of what went wrong
        analysis_prompt = f"""Based on this feedback: "{feedback}"
And this script revision, briefly summarize in JSON:
{{"issue": "one line describing the main problem", "fix_applied": "one line describing the fix", "pattern": "one word category like 'pacing', 'tone', 'visuals', 'dialogue'"}}"""
        
        analysis_response = client.chat.completions.create(
            model="grok-3-fast",
            messages=[{"role": "user", "content": analysis_prompt}],
            max_tokens=200
        )
        
        import json
        import re
        analysis_text = analysis_response.choices[0].message.content.strip()
        json_match = re.search(r'\{[\s\S]*\}', analysis_text)
        ai_analysis = {}
        if json_match:
            try:
                ai_analysis = json.loads(json_match.group())
            except:
                ai_analysis = {'issue': feedback, 'fix_applied': 'Script refined', 'pattern': 'general'}
        
        # Update the last feedback record with AI analysis
        last_feedback = VideoFeedback.query.filter_by(
            project_id=project_id,
            user_id=user_id
        ).order_by(VideoFeedback.created_at.desc()).first()
        
        if last_feedback:
            last_feedback.ai_analysis = ai_analysis
        
        # Update project with new script
        if project_id:
            project = Project.query.get(project_id)
            if project:
                project.script = refined_script
                project.revision_count = revision_number
        
        # Track pattern for AI improvement
        if ai_analysis.get('pattern'):
            pattern = GlobalPattern.query.filter_by(
                pattern_type=f"feedback_{ai_analysis['pattern']}"
            ).first()
            if pattern:
                pattern.usage_count += 1
            else:
                pattern = GlobalPattern(
                    pattern_type=f"feedback_{ai_analysis['pattern']}",
                    pattern_data={'description': f"Common feedback: {ai_analysis['pattern']}"},
                    success_count=0,
                    usage_count=1,
                    success_rate=0.0
                )
                db.session.add(pattern)
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'refined_script': refined_script,
            'ai_message': f"I adjusted the script based on your feedback about {ai_analysis.get('pattern', 'the content')}. Review it and regenerate when ready.",
            'analysis': ai_analysis,
            'revision_number': revision_number
        })
        
    except Exception as e:
        print(f"Refinement error: {e}")
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/ai-improvement-stats', methods=['GET'])
def ai_improvement_stats():
    """Get AI improvement statistics."""
    from models import GlobalPattern, VideoFeedback
    
    try:
        # Get like/dislike ratio
        like_pattern = GlobalPattern.query.filter_by(pattern_type='like_rate').first()
        like_rate = like_pattern.success_rate if like_pattern else 0.0
        total_feedbacks = like_pattern.usage_count if like_pattern else 0
        
        # Get common feedback patterns
        feedback_patterns = GlobalPattern.query.filter(
            GlobalPattern.pattern_type.like('feedback_%')
        ).order_by(GlobalPattern.usage_count.desc()).limit(5).all()
        
        patterns = [{
            'type': p.pattern_type.replace('feedback_', ''),
            'count': p.usage_count,
            'description': p.pattern_data.get('description', '')
        } for p in feedback_patterns]
        
        return jsonify({
            'success': True,
            'like_rate': round(like_rate * 100, 1),
            'total_feedbacks': total_feedbacks,
            'common_issues': patterns
        })
        
    except Exception as e:
        print(f"Stats error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/generator-settings', methods=['GET', 'POST'])
def generator_settings():
    """Get or update user generator settings for auto-generation."""
    from models import GeneratorSettings
    from flask_login import current_user
    
    user_id = None
    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('dev_user_id')
    
    if not user_id:
        return jsonify({'error': 'Authentication required'}), 401
    
    if request.method == 'GET':
        settings = GeneratorSettings.query.filter_by(user_id=user_id).first()
        if not settings:
            # Return defaults
            return jsonify({
                'success': True,
                'settings': {
                    'tone': 'neutral',
                    'format_type': 'explainer',
                    'target_length': 45,
                    'voice_style': 'news_anchor',
                    'enabled_topics': [],
                    'auto_enabled': False
                }
            })
        return jsonify({
            'success': True,
            'settings': {
                'tone': settings.tone,
                'format_type': settings.format_type,
                'target_length': settings.target_length,
                'voice_style': settings.voice_style,
                'enabled_topics': settings.enabled_topics or [],
                'auto_enabled': settings.auto_enabled
            }
        })
    
    # POST - update settings
    data = request.get_json()
    settings = GeneratorSettings.query.filter_by(user_id=user_id).first()
    if not settings:
        settings = GeneratorSettings(user_id=user_id)
        db.session.add(settings)
    
    if 'tone' in data:
        settings.tone = data['tone']
    if 'format_type' in data:
        settings.format_type = data['format_type']
    if 'target_length' in data:
        settings.target_length = max(35, min(75, int(data['target_length'])))
    if 'voice_style' in data:
        settings.voice_style = data['voice_style']
    if 'enabled_topics' in data:
        settings.enabled_topics = data['enabled_topics']
    if 'auto_enabled' in data:
        settings.auto_enabled = data['auto_enabled']
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': 'Settings updated',
        'settings': {
            'tone': settings.tone,
            'format_type': settings.format_type,
            'target_length': settings.target_length,
            'voice_style': settings.voice_style,
            'enabled_topics': settings.enabled_topics or [],
            'auto_enabled': settings.auto_enabled
        }
    })


@app.route('/generator-confidence', methods=['GET'])
def generator_confidence():
    """Calculate AI confidence for auto-generation based on liked videos."""
    from models import Project, AILearning, GlobalPattern, VideoFeedback
    
    UNLOCK_THRESHOLD = 5  # Need 5 liked videos to unlock auto-generation
    
    user_id = get_user_id()
    if not user_id:
        return jsonify({'error': 'Authentication required'}), 401
    
    try:
        # Count liked videos (not projects) for this user
        liked_count = VideoFeedback.query.filter_by(user_id=user_id, liked=True).count()
        total_with_feedback = VideoFeedback.query.filter_by(user_id=user_id).count()
        
        # Calculate success rate
        success_rate = (liked_count / total_with_feedback * 100) if total_with_feedback > 0 else 0
        
        # Check if unlocked
        is_unlocked = liked_count >= UNLOCK_THRESHOLD
        
        # Progress message
        if is_unlocked:
            progress_message = "Auto-Generate unlocked!"
        else:
            remaining = UNLOCK_THRESHOLD - liked_count
            progress_message = f"{liked_count}/{UNLOCK_THRESHOLD} videos liked to unlock"
        
        # Get learned patterns for confidence
        learned_patterns = GlobalPattern.query.filter(
            GlobalPattern.success_count > 0
        ).count()
        
        return jsonify({
            'success': True,
            'liked_count': liked_count,
            'total_projects': total_with_feedback,
            'success_rate': round(success_rate, 1),
            'unlock_threshold': UNLOCK_THRESHOLD,
            'is_unlocked': is_unlocked,
            'progress_message': progress_message,
            'learned_patterns': learned_patterns,
            'confidence_score': min(100, (liked_count / UNLOCK_THRESHOLD) * 100)
        })
        
    except Exception as e:
        print(f"Confidence calculation error: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/auto-generate', methods=['POST'])
def auto_generate():
    """Auto-generate content using learned patterns, user settings, and template-specific styling."""
    from models import Project, GeneratorSettings, GlobalPattern, AILearning, VideoFeedback
    from flask_login import current_user
    from context_engine import get_template_guidelines, research_trends
    import os
    import json
    
    UNLOCK_THRESHOLD = 5
    
    user_id = None
    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('dev_user_id')
    
    if not user_id:
        return jsonify({'error': 'Authentication required'}), 401
    
    # Check if unlocked (count liked videos, not projects)
    liked_count = VideoFeedback.query.filter_by(user_id=user_id, liked=True).count()
    if liked_count < UNLOCK_THRESHOLD:
        return jsonify({
            'error': 'Auto-generation not unlocked',
            'message': f'Need {UNLOCK_THRESHOLD - liked_count} more liked videos to unlock',
            'requires_unlock': True
        }), 403
    
    # Get user settings
    settings = GeneratorSettings.query.filter_by(user_id=user_id).first()
    if not settings:
        settings = GeneratorSettings(user_id=user_id)
    
    # Get learned patterns
    successful_patterns = GlobalPattern.query.filter(
        GlobalPattern.success_rate > 0.5
    ).order_by(GlobalPattern.success_rate.desc()).limit(5).all()
    
    pattern_hints = []
    for p in successful_patterns:
        if p.pattern_data.get('description'):
            pattern_hints.append(p.pattern_data['description'])
    
    # Get AI learning data
    ai_learning = AILearning.query.filter_by(user_id=user_id).first()
    learned_hooks = ai_learning.learned_hooks if ai_learning else []
    learned_styles = ai_learning.learned_styles if ai_learning else []
    
    # Get topic and template for generation
    data = request.get_json() or {}
    topic = data.get('topic', '')
    template_type = data.get('template', 'start_from_scratch')
    
    # Get template-specific guidelines
    template_dna = get_template_guidelines(template_type)
    
    # Fetch trend research for the topic
    trend_data = None
    try:
        trend_data = research_trends(topic or 'general content creation')
    except Exception as e:
        logging.warning(f"Trend research failed in auto-generate: {e}")
        trend_data = {'hooks': [], 'formats': [], 'visuals': [], 'sounds': []}
    
    # Build auto-generation prompt with template awareness
    prompt = f"""Generate a complete short-form video script based on user preferences, learned patterns, and current trends.

TEMPLATE: {template_type.upper().replace('_', ' ')}
TEMPLATE TONE: {template_dna['tone']}
TEMPLATE VOICE: {template_dna['voice']}
TEMPLATE HOOK STYLE: {template_dna['hook_style']}
TEMPLATE PACING: {template_dna['pacing']}
HOW TO APPLY TRENDS: {template_dna['trend_application']}

USER SETTINGS:
- Tone: {settings.tone}
- Format: {settings.format_type}
- Target Length: {settings.target_length} seconds
- Voice Style: {settings.voice_style}
- Preferred Topics: {', '.join(settings.enabled_topics) if settings.enabled_topics else 'General'}

CURRENT TRENDS (apply within template tone):
{json.dumps(trend_data, indent=2) if trend_data else 'No trend data available'}

LEARNED PATTERNS (from previous successful content):
{chr(10).join(f'- {hint}' for hint in pattern_hints[:3]) if pattern_hints else '- No specific patterns learned yet'}

LEARNED HOOKS: {', '.join(learned_hooks[:3]) if learned_hooks else 'None'}
LEARNED STYLES: {', '.join(learned_styles[:3]) if learned_styles else 'None'}

TOPIC/IDEA: {topic if topic else 'Generate based on user preferences and trending topics'}

Generate a complete {settings.target_length}-second video script following the thesis-driven anchor structure:
1. HOOK - {template_dna['hook_style']} opener that grabs attention
2. CLAIM - Core thesis statement
3. EVIDENCE - Supporting points (2-3 max)
4. PIVOT - Unexpected angle or reframe
5. CLOSER - Return to thesis with impact

CRITICAL: Stay in the {template_type.replace('_', ' ')} template voice. Trends inform HOW you execute, not WHAT tone you use.

Output the script with clear character lines formatted as:
[CHARACTER]: dialogue

Include [PAUSE] and [BEAT] markers for pacing.

Also output a visual plan and sound plan as JSON:
{{
  "script": "The full script with speaker labels",
  "visual_plan": [{{"scene": 1, "description": "...", "timing": "0-5s"}}],
  "sound_plan": {{"music_vibe": "...", "sfx_suggestions": ["..."]}}
}}
"""

    try:
        # Call AI to generate
        xai_api_key = os.environ.get('XAI_API_KEY')
        if not xai_api_key:
            return jsonify({'error': 'AI configuration error'}), 500
        
        from openai import OpenAI
        client = OpenAI(
            api_key=xai_api_key,
            base_url="https://api.x.ai/v1"
        )
        
        response = client.chat.completions.create(
            model="grok-3",
            messages=[
                {"role": "system", "content": "You are Krakd, a thesis-driven content engine. Generate clear, honest, human-feeling scripts that respect complexity. Prioritize clarity, integrity, and resonance over virality."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=2000
        )
        
        generated_response = response.choices[0].message.content
        
        # Parse the JSON response
        script_text = generated_response
        visual_plan = []
        sound_plan = {}
        
        try:
            if '```json' in generated_response:
                json_str = generated_response.split('```json')[1].split('```')[0]
            elif '```' in generated_response:
                json_str = generated_response.split('```')[1].split('```')[0]
            else:
                json_str = generated_response
            
            parsed = json.loads(json_str.strip())
            script_text = parsed.get('script', generated_response)
            visual_plan = parsed.get('visual_plan', [])
            sound_plan = parsed.get('sound_plan', {})
        except (json.JSONDecodeError, IndexError):
            script_text = generated_response
        
        # Get template visual FX info
        from context_engine import TEMPLATE_VISUAL_FX
        template_fx = TEMPLATE_VISUAL_FX.get(template_type, TEMPLATE_VISUAL_FX['start_from_scratch'])
        
        # Create a new project with the generated content
        project = Project(
            user_id=user_id,
            name=f"Auto-Generated: {topic[:50]}" if topic else "Auto-Generated Content",
            description="Generated using AI learning, trends, and user preferences",
            script=script_text,
            template_type=template_type,
            visual_plan=visual_plan,
            sound_plan=sound_plan,
            status='draft',
            workflow_step=3  # Start at script stage
        )
        db.session.add(project)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'project_id': project.id,
            'script': script_text,
            'visual_plan': visual_plan,
            'sound_plan': sound_plan,
            'template': {
                'type': template_type,
                'dna': template_dna,
                'visual_fx': template_fx
            },
            'settings_used': {
                'tone': settings.tone,
                'format_type': settings.format_type,
                'target_length': settings.target_length,
                'voice_style': settings.voice_style
            },
            'trends_applied': bool(trend_data and (trend_data.get('hooks') or trend_data.get('formats'))),
            'patterns_applied': len(pattern_hints)
        })
        
    except Exception as e:
        print(f"Auto-generate error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


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


@app.route('/finalize-video', methods=['POST'])
def finalize_video():
    """Remove watermark and create final video - uses tokens."""
    from models import User, Subscription, PreviewVideo
    from flask_login import current_user
    from datetime import datetime
    
    user_id = None
    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('dev_user_id')
    
    data = request.get_json()
    preview_url = data.get('preview_url', '')
    caption_position = data.get('caption_position', 'bottom')
    
    if not preview_url:
        return jsonify({'error': 'No preview URL provided'}), 400
    
    # Check subscription/tokens
    is_dev_mode = os.environ.get('FLASK_ENV') == 'development' or os.environ.get('DEV_MODE') == 'true'
    
    if not is_dev_mode and user_id:
        sub = Subscription.query.filter_by(user_id=user_id).first()
        user = User.query.get(user_id)
        
        if not (sub and sub.is_active()):
            # Deduct tokens for non-subscribers
            if user and user.tokens and user.tokens >= 10:
                user.tokens -= 10
                db.session.commit()
            else:
                return jsonify({
                    'error': 'Insufficient tokens',
                    'requires_subscription': True
                }), 403
    
    # For now, the preview is the final (watermark is CSS overlay, not burned in)
    # In production, you'd re-render without watermark flag
    final_url = preview_url
    
    # Record finalization
    try:
        preview_record = PreviewVideo(
            user_id=user_id,
            preview_path=preview_url,
            final_path=final_url,
            is_finalized=True,
            finalized_at=datetime.utcnow()
        )
        db.session.add(preview_record)
        db.session.commit()
    except Exception as e:
        print(f"[finalize-video] Failed to record: {e}")
    
    return jsonify({
        'success': True,
        'video_url': final_url,
        'caption_position': caption_position
    })


@app.route('/record-video-feedback', methods=['POST'])
def record_video_feedback():
    """Record video feedback for AI learning."""
    from models import VideoFeedback, VisualLearning
    from flask_login import current_user
    
    user_id = None
    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('dev_user_id')
    
    if not user_id:
        return jsonify({'success': False, 'error': 'User not authenticated'})
    
    data = request.get_json()
    feedback_type = data.get('feedback_type', 'unknown')
    details = data.get('details', '')
    video_data = data.get('video_data', {})
    
    try:
        feedback = VideoFeedback(
            user_id=user_id,
            liked=(feedback_type == 'positive'),
            comment=f"{feedback_type}: {details}",
            revision_number=video_data.get('revision_count', 0)
        )
        db.session.add(feedback)
        
        # Also record for visual learning if content type is known
        content_type = video_data.get('content_type', 'general')
        if content_type and feedback_type == 'positive':
            learning = VisualLearning(
                content_type=content_type,
                scene_position='general',
                source_type=video_data.get('source_type', 'mixed'),
                feedback='positive',
                scene_text_sample=details[:200] if details else ''
            )
            db.session.add(learning)
        
        db.session.commit()
        
        return jsonify({'success': True})
    except Exception as e:
        print(f"[record-video-feedback] Error: {e}")
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)})


@app.route('/create-visual-plan', methods=['POST'])
def create_visual_plan():
    """Create a visual plan using the Visual Director AI."""
    from visual_director import create_visual_plan as vd_create_plan
    from models import VisualPlan
    from flask_login import current_user
    
    user_id = None
    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('dev_user_id')
    
    data = request.get_json()
    script = data.get('script', '')
    user_intent = data.get('intent', '')
    template_type = data.get('template', None)
    user_content = data.get('user_content', [])
    
    if not script:
        return jsonify({'error': 'Script is required'}), 400
    
    try:
        # Create the visual plan
        plan = vd_create_plan(
            script=script,
            user_intent=user_intent,
            user_content=user_content,
            template_type=template_type
        )
        
        # Store in database for reuse
        try:
            plan_record = VisualPlan(
                user_id=user_id,
                plan_id=plan['plan_id'],
                content_type=plan['content_type'],
                color_palette=plan['color_palette'],
                editing_dna=plan['editing_dna'],
                scenes=plan['scenes']
            )
            db.session.add(plan_record)
            db.session.commit()
        except Exception as e:
            print(f"[create-visual-plan] Failed to store plan: {e}")
        
        return jsonify({
            'success': True,
            'plan': plan
        })
    except Exception as e:
        print(f"[create-visual-plan] Error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/get-visual-plan/<plan_id>', methods=['GET'])
def get_visual_plan(plan_id):
    """Retrieve a stored visual plan."""
    from models import VisualPlan
    
    plan = VisualPlan.query.filter_by(plan_id=plan_id).first()
    
    if not plan:
        return jsonify({'error': 'Plan not found'}), 404
    
    return jsonify({
        'success': True,
        'plan': {
            'plan_id': plan.plan_id,
            'content_type': plan.content_type,
            'color_palette': plan.color_palette,
            'editing_dna': plan.editing_dna,
            'scenes': plan.scenes
        }
    })


@app.route('/execute-visual-plan', methods=['POST'])
def execute_visual_plan_endpoint():
    """Execute a visual plan - fetch stock photos and prepare DALL-E prompts."""
    from visual_director import execute_visual_plan
    from models import VisualPlan
    
    data = request.get_json()
    plan_id = data.get('plan_id')
    
    if not plan_id:
        return jsonify({'error': 'Plan ID is required'}), 400
    
    plan = VisualPlan.query.filter_by(plan_id=plan_id).first()
    
    if not plan:
        return jsonify({'error': 'Plan not found'}), 404
    
    try:
        visual_plan = {
            'plan_id': plan.plan_id,
            'content_type': plan.content_type,
            'color_palette': plan.color_palette,
            'editing_dna': plan.editing_dna,
            'scenes': plan.scenes
        }
        
        executed_scenes = execute_visual_plan(visual_plan)
        
        return jsonify({
            'success': True,
            'scenes': executed_scenes,
            'content_type': plan.content_type,
            'color_palette': plan.color_palette
        })
    except Exception as e:
        print(f"[execute-visual-plan] Error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/render-with-plan', methods=['POST'])
def render_with_visual_plan():
    """Render a video using a visual plan - unified pipeline with Source Merging Engine."""
    from visual_director import execute_visual_plan, get_merging_config
    from models import VisualPlan
    from flask_login import current_user
    import subprocess
    import uuid
    
    user_id = None
    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('dev_user_id')
    
    data = request.get_json()
    plan_id = data.get('plan_id')
    script = data.get('script', '')
    audio_path = data.get('audio_path', '')
    video_format = data.get('format', '9:16')
    caption_position = data.get('caption_position', 'bottom')
    color_style = data.get('color_style')
    film_grain = data.get('film_grain', True)
    caption_template = data.get('caption_template', 'bold_pop')
    
    plan = VisualPlan.query.filter_by(plan_id=plan_id).first() if plan_id else None
    
    try:
        scenes_to_render = []
        content_type = plan.content_type if plan else 'general'
        
        # Get merging config (color grading + transitions)
        merging_config = get_merging_config(
            content_type,
            {'color_style': color_style, 'film_grain': film_grain}
        )
        
        if plan:
            visual_plan = {
                'plan_id': plan.plan_id,
                'content_type': plan.content_type,
                'color_palette': plan.color_palette,
                'editing_dna': plan.editing_dna,
                'scenes': plan.scenes
            }
            executed_scenes = execute_visual_plan(visual_plan)
            
            for scene in executed_scenes:
                if scene.get('visual'):
                    scenes_to_render.append({
                        'text': scene.get('text', ''),
                        'image_url': scene.get('visual'),
                        'source_type': scene.get('source_type', 'stock'),
                        'duration': 4
                    })
                elif scene.get('dalle_prompt'):
                    scenes_to_render.append({
                        'text': scene.get('text', ''),
                        'dalle_prompt': scene.get('dalle_prompt'),
                        'source_type': 'dalle',
                        'duration': 4
                    })
        
        if not scenes_to_render:
            return jsonify({'error': 'No scenes to render'}), 400
        
        output_id = str(uuid.uuid4())[:8]
        output_path = f'output/plan_{output_id}.mp4'
        os.makedirs('output', exist_ok=True)
        
        # Get video dimensions from format
        format_dims = {
            '9:16': (1080, 1920), '16:9': (1920, 1080),
            '1:1': (1080, 1080), '4:5': (1080, 1350)
        }
        width, height = format_dims.get(video_format, (1080, 1920))
        
        # Calculate audio duration for scene timing
        audio_duration = 0
        if audio_path and os.path.exists(audio_path):
            dur_cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', audio_path]
            dur_result = subprocess.run(dur_cmd, capture_output=True, text=True, timeout=30)
            audio_duration = float(dur_result.stdout.strip()) if dur_result.stdout.strip() else 30
        else:
            audio_duration = len(scenes_to_render) * 4  # Default 4s per scene
        
        scene_duration = audio_duration / max(len(scenes_to_render), 1)
        
        # Download/generate images for each scene and compose into video
        scene_clips = []
        temp_files = []
        
        for i, scene in enumerate(scenes_to_render):
            scene_img_path = f'output/scene_{output_id}_{i}.jpg'
            
            if scene.get('image_url'):
                # Download stock image
                try:
                    import requests
                    resp = requests.get(scene['image_url'], timeout=30)
                    if resp.status_code == 200:
                        with open(scene_img_path, 'wb') as f:
                            f.write(resp.content)
                        temp_files.append(scene_img_path)
                except Exception as e:
                    print(f"Failed to download scene {i} image: {e}")
                    continue
            elif scene.get('dalle_prompt'):
                # Generate DALL-E image
                try:
                    from openai import OpenAI
                    dalle_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
                    response = dalle_client.images.generate(
                        model="dall-e-3",
                        prompt=scene['dalle_prompt'],
                        size="1024x1792" if video_format == '9:16' else "1792x1024",
                        quality="standard",
                        n=1
                    )
                    img_url = response.data[0].url
                    import requests
                    resp = requests.get(img_url, timeout=60)
                    if resp.status_code == 200:
                        with open(scene_img_path, 'wb') as f:
                            f.write(resp.content)
                        temp_files.append(scene_img_path)
                except Exception as e:
                    print(f"Failed to generate DALL-E image for scene {i}: {e}")
                    continue
            else:
                continue
            
            if os.path.exists(scene_img_path):
                scene_clips.append({
                    'path': scene_img_path,
                    'duration': scene_duration
                })
        
        if not scene_clips:
            return jsonify({'error': 'Failed to create any scene clips'}), 400
        
        # Create video from scene images using FFmpeg concat
        concat_file = f'output/concat_{output_id}.txt'
        temp_scene_videos = []
        
        # Build color grading filter from merging config
        color_filter = merging_config.get('filter_chain', '')
        
        for i, clip in enumerate(scene_clips):
            scene_video = f'output/scene_vid_{output_id}_{i}.mp4'
            
            # Calculate zoompan duration (frames = fps * duration)
            fps = 30
            zoom_frames = int(fps * clip['duration'])
            
            # Create video from image with zoom/pan effect and color grading
            # Use dynamic size matching the target format
            vf_filters = [
                f'scale={width*2}:{height*2}:force_original_aspect_ratio=increase',
                f'crop={width*2}:{height*2}',
                f'zoompan=z=1.04:d={zoom_frames}:x=iw/2-(iw/zoom/2):y=ih/2-(ih/zoom/2):s={width}x{height}:fps={fps}'
            ]
            
            # Safely add color filter if valid
            if color_filter and isinstance(color_filter, str) and color_filter.strip():
                # Validate basic filter structure (no empty or malformed filters)
                try:
                    vf_filters.append(color_filter.strip())
                except:
                    pass
            
            img_cmd = [
                'ffmpeg', '-y',
                '-loop', '1', '-i', clip['path'],
                '-t', str(clip['duration']),
                '-vf', ','.join(vf_filters),
                '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
                '-pix_fmt', 'yuv420p',
                scene_video
            ]
            result = subprocess.run(img_cmd, capture_output=True, timeout=180)
            
            if result.returncode == 0 and os.path.exists(scene_video):
                temp_scene_videos.append(scene_video)
            else:
                # Fallback: simpler approach without zoompan if it fails
                stderr_msg = result.stderr.decode()[:500] if result.stderr else 'unknown error'
                print(f"[render-with-plan] Zoompan failed for scene {i}: {stderr_msg}")
                simple_filters = [
                    f'scale={width}:{height}:force_original_aspect_ratio=increase',
                    f'crop={width}:{height}',
                    'setsar=1'
                ]
                simple_cmd = [
                    'ffmpeg', '-y',
                    '-loop', '1', '-i', clip['path'],
                    '-t', str(clip['duration']),
                    '-vf', ','.join(simple_filters),
                    '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
                    '-pix_fmt', 'yuv420p',
                    '-r', str(fps),
                    scene_video
                ]
                fallback_result = subprocess.run(simple_cmd, capture_output=True, timeout=180)
                if fallback_result.returncode == 0 and os.path.exists(scene_video):
                    temp_scene_videos.append(scene_video)
        
        if not temp_scene_videos:
            return jsonify({'error': 'Failed to create scene videos'}), 400
        
        # Write concat file
        with open(concat_file, 'w') as f:
            for vid in temp_scene_videos:
                f.write(f"file '{os.path.abspath(vid)}'\n")
        temp_files.append(concat_file)
        
        # Concat all scenes
        concat_output = f'output/concat_out_{output_id}.mp4'
        concat_cmd = [
            'ffmpeg', '-y', '-f', 'concat', '-safe', '0',
            '-i', concat_file,
            '-c', 'copy',
            concat_output
        ]
        subprocess.run(concat_cmd, capture_output=True, timeout=300)
        temp_files.append(concat_output)
        
        # Add audio if available
        if audio_path and os.path.exists(audio_path):
            audio_output = f'output/audio_{output_id}.mp4'
            audio_cmd = [
                'ffmpeg', '-y',
                '-i', concat_output,
                '-i', audio_path,
                '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
                '-map', '0:v:0', '-map', '1:a:0',
                '-shortest',
                audio_output
            ]
            subprocess.run(audio_cmd, capture_output=True, timeout=300)
            temp_files.append(audio_output)
            current_video = audio_output
        else:
            current_video = concat_output
        
        # Add Whisper-synced captions if audio exists
        if audio_path and os.path.exists(audio_path):
            ass_path = f'output/plan_captions_{output_id}.ass'
            _, whisper_success = create_whisper_synced_captions(
                audio_path, ass_path,
                template=caption_template,
                position=caption_position,
                video_width=width, video_height=height
            )
            
            if whisper_success:
                caption_output = f'output/captioned_{output_id}.mp4'
                caption_cmd = [
                    'ffmpeg', '-y',
                    '-i', current_video,
                    '-vf', f"ass={ass_path}",
                    '-c:a', 'copy',
                    caption_output
                ]
                result = subprocess.run(caption_cmd, capture_output=True, timeout=300)
                if result.returncode == 0 and os.path.exists(caption_output):
                    current_video = caption_output
                    temp_files.append(caption_output)
                temp_files.append(ass_path)
        
        # Move final video to output path
        shutil.move(current_video, output_path)
        
        # Cleanup temp files
        for f in temp_files + temp_scene_videos:
            if f and os.path.exists(f) and f != output_path:
                try:
                    os.remove(f)
                except:
                    pass
        
        print(f"[render-with-plan] Created video with {len(scene_clips)} scenes: {output_path}")
        
        return jsonify({
            'success': True,
            'video_url': f'/{output_path}',
            'video_path': output_path,
            'scenes_count': len(scenes_to_render),
            'content_type': content_type,
            'merging_config': merging_config,
            'caption_template': caption_template,
            'is_preview': True
        })
        
    except Exception as e:
        print(f"[render-with-plan] Error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/get-merging-options', methods=['POST'])
def get_merging_options():
    """Get AI-recommended color styles and caption templates for a project."""
    from visual_director import recommend_color_style, recommend_caption_style, CAPTION_TEMPLATES
    
    data = request.get_json()
    content_type = data.get('content_type', 'general')
    
    try:
        color_rec = recommend_color_style(content_type)
        caption_rec = recommend_caption_style(content_type)
        
        return jsonify({
            'success': True,
            'color_recommendation': color_rec,
            'caption_recommendation': caption_rec,
            'caption_templates': {k: v for k, v in CAPTION_TEMPLATES.items()}
        })
    except Exception as e:
        print(f"[get-merging-options] Error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/save-merging-preferences', methods=['POST'])
def save_merging_preferences():
    """Save user's Source Merging preferences."""
    from models import UserMergingPreferences
    from flask_login import current_user
    
    user_id = None
    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('dev_user_id')
    
    if not user_id:
        return jsonify({'error': 'Not authenticated'}), 401
    
    data = request.get_json()
    
    try:
        prefs = UserMergingPreferences.query.filter_by(user_id=user_id).first()
        
        if not prefs:
            prefs = UserMergingPreferences(user_id=user_id)
            db.session.add(prefs)
        
        if 'color_style' in data:
            prefs.preferred_color_style = data['color_style']
        if 'film_grain' in data:
            prefs.film_grain_enabled = data['film_grain']
        if 'caption_template' in data:
            prefs.preferred_caption_template = data['caption_template']
        
        db.session.commit()
        
        return jsonify({'success': True})
    except Exception as e:
        print(f"[save-merging-preferences] Error: {e}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/refresh-caption-style', methods=['POST'])
def refresh_caption_style():
    """Refresh to get a new AI-curated caption style, save current to history."""
    from visual_director import recommend_caption_style, save_caption_style_choice, CAPTION_TEMPLATES
    from models import CaptionStyleHistory
    from flask_login import current_user
    import random
    
    user_id = None
    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('dev_user_id')
    
    data = request.get_json()
    current_style = data.get('current_style')
    content_type = data.get('content_type', 'general')
    
    try:
        # Save current style to history
        if user_id and current_style:
            save_caption_style_choice(user_id, current_style, was_refresh=True)
        
        # Get new style (different from current)
        available = list(CAPTION_TEMPLATES.keys())
        if current_style in available:
            available.remove(current_style)
        
        new_key = random.choice(available)
        new_template = CAPTION_TEMPLATES[new_key].copy()
        new_template['key'] = new_key
        
        return jsonify({
            'success': True,
            'new_style': new_template
        })
    except Exception as e:
        print(f"[refresh-caption-style] Error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/get-caption-history', methods=['GET'])
def get_caption_history():
    """Get user's caption style history for back/forward navigation."""
    from visual_director import get_caption_style_history, CAPTION_TEMPLATES
    from flask_login import current_user
    
    user_id = None
    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('dev_user_id')
    
    if not user_id:
        return jsonify({'history': []})
    
    try:
        history = get_caption_style_history(user_id)
        
        # Enrich with full template data
        for item in history:
            if item['template_key'] in CAPTION_TEMPLATES:
                item['template'] = CAPTION_TEMPLATES[item['template_key']]
        
        return jsonify({'success': True, 'history': history})
    except Exception as e:
        print(f"[get-caption-history] Error: {e}")
        return jsonify({'history': []})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
