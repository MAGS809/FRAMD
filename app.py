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
from audio_engine import (
    extract_dialogue_only, generate_sound_effect_elevenlabs,
    generate_sound_effect, parse_sfx_from_directions,
    mix_sfx_into_audio, extract_voice_actor_script,
    parse_character_lines, get_character_voice_map, assemble_audio_clips
)
from extensions import db, login_manager
from functools import wraps
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

logging.basicConfig(level=logging.DEBUG)

_rate_limit_table_created = False

def _ensure_rate_limit_table():
    """Create rate_limits table at startup (called once)."""
    global _rate_limit_table_created
    if _rate_limit_table_created:
        return
    import psycopg2
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        return
    try:
        conn = psycopg2.connect(db_url)
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS rate_limits (
                    id SERIAL PRIMARY KEY,
                    client_key VARCHAR(255) NOT NULL,
                    request_time TIMESTAMP NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_rate_limits_key_time
                ON rate_limits (client_key, request_time)
            """)
        conn.commit()
        conn.close()
        _rate_limit_table_created = True
    except Exception as e:
        logging.warning(f"Rate limit table creation failed: {e}")

def rate_limit(limit=30, window=60):
    """Database-backed rate limiting decorator. Default: 30 requests per 60 seconds."""
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            import psycopg2
            from flask_login import current_user
            from datetime import datetime, timedelta
            
            if current_user.is_authenticated:
                key = f"user:{current_user.id}"
            else:
                key = f"ip:{request.remote_addr}"
            
            db_url = os.environ.get("DATABASE_URL")
            if not db_url:
                return f(*args, **kwargs)
            
            _ensure_rate_limit_table()
            cutoff = datetime.utcnow() - timedelta(seconds=window)
            
            try:
                conn = psycopg2.connect(db_url)
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM rate_limits WHERE request_time < %s",
                        (cutoff,)
                    )
                    cur.execute(
                        "SELECT COUNT(*) FROM rate_limits WHERE client_key = %s AND request_time > %s",
                        (key, cutoff)
                    )
                    count = cur.fetchone()[0]
                    
                    if count >= limit:
                        conn.commit()
                        conn.close()
                        return jsonify({'error': 'Rate limit exceeded. Please slow down.'}), 429
                    
                    cur.execute(
                        "INSERT INTO rate_limits (client_key, request_time) VALUES (%s, NOW())",
                        (key,)
                    )
                    conn.commit()
                conn.close()
            except Exception as e:
                logging.warning(f"Rate limit check failed: {e}")
            
            return f(*args, **kwargs)
        return wrapped
    return decorator

import threading
from concurrent.futures import ThreadPoolExecutor

background_render_jobs = {}
render_executor = ThreadPoolExecutor(max_workers=3)

from video_renderer import (
    build_visual_fx_filter,
    create_whisper_synced_captions,
    create_dynamic_captions_ass,
    create_word_synced_subtitles,
    generate_video_description,
    send_render_complete_email,
    background_render_task,
    CAPTION_TEMPLATES,
)



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

from routes import auth_bp, payments_bp, projects_bp, video_bp, chat_bp, api_bp, pages_bp, visual_bp, feed_bp, feedback_bp, generator_bp, content_bp, render_bp, stripe_bp, files_bp
from routes.templates import template_bp
from routes.pipeline import pipeline_bp
from routes.voice import voice_bp
app.register_blueprint(auth_bp, url_prefix='/v2')
app.register_blueprint(payments_bp, url_prefix='/v2')
app.register_blueprint(projects_bp, url_prefix='/v2')
app.register_blueprint(video_bp, url_prefix='/v2')
app.register_blueprint(chat_bp)
app.register_blueprint(api_bp)
app.register_blueprint(pages_bp)
app.register_blueprint(visual_bp)
app.register_blueprint(feed_bp)
app.register_blueprint(feedback_bp)
app.register_blueprint(generator_bp)
app.register_blueprint(content_bp)
app.register_blueprint(render_bp)
app.register_blueprint(stripe_bp)
app.register_blueprint(files_bp)
app.register_blueprint(template_bp)
app.register_blueprint(pipeline_bp)
app.register_blueprint(voice_bp)









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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
