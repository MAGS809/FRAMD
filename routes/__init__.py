"""
Flask Blueprints for route organization.
"""
from routes.auth import auth_bp
from routes.payments import payments_bp
from routes.projects import projects_bp
from routes.video import video_bp
from routes.chat import chat_bp
from routes.api import api_bp
from routes.pages import pages_bp
from routes.visual import visual_bp
from routes.feed import feed_bp
from routes.feedback import feedback_bp
from routes.generator import generator_bp
from routes.content import content_bp
from routes.render import render_bp
from routes.stripe import stripe_bp
from routes.files import files_bp
from routes.templates import template_bp
from routes.pipeline import pipeline_bp
from routes.voice import voice_bp
from routes.overlays import overlays_bp
from routes.community import community_bp

__all__ = ['auth_bp', 'payments_bp', 'projects_bp', 'video_bp', 'chat_bp', 'api_bp', 'pages_bp', 'visual_bp', 'feed_bp', 'feedback_bp', 'generator_bp', 'content_bp', 'render_bp', 'stripe_bp', 'files_bp', 'template_bp', 'pipeline_bp', 'voice_bp', 'overlays_bp', 'community_bp']
