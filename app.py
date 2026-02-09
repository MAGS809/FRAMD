from flask import Flask, request, jsonify, send_from_directory, session
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix
import os
import logging
from extensions import db, login_manager
import threading
from concurrent.futures import ThreadPoolExecutor

logging.basicConfig(level=logging.DEBUG)

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

@app.after_request
def add_no_cache_headers(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response
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
    
    try:
        if 'postgresql' in str(db.engine.url):
            from sqlalchemy import text
            with db.engine.connect() as conn:
                result = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='projects' AND column_name='revision_count'"))
                if not result.fetchone():
                    conn.execute(text("ALTER TABLE projects ADD COLUMN revision_count INTEGER DEFAULT 0"))
                    conn.commit()
                
                result = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='projects' AND column_name='liked'"))
                if not result.fetchone():
                    conn.execute(text("ALTER TABLE projects ADD COLUMN liked BOOLEAN DEFAULT NULL"))
                    conn.commit()
                
                result = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='projects' AND column_name='sound_plan'"))
                if not result.fetchone():
                    conn.execute(text("ALTER TABLE projects ADD COLUMN sound_plan JSONB"))
                    conn.commit()
                
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
from routes.overlays import overlays_bp
from routes.community import community_bp
app.register_blueprint(auth_bp)
app.register_blueprint(payments_bp)
app.register_blueprint(projects_bp)
app.register_blueprint(video_bp)
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
app.register_blueprint(overlays_bp)
app.register_blueprint(community_bp)


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


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
