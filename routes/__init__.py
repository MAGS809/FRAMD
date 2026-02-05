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

__all__ = ['auth_bp', 'payments_bp', 'projects_bp', 'video_bp', 'chat_bp', 'api_bp', 'pages_bp']
