"""
Flask Blueprints for route organization.
"""
from routes.auth import auth_bp
from routes.payments import payments_bp
from routes.projects import projects_bp
from routes.video import video_bp

__all__ = ['auth_bp', 'payments_bp', 'projects_bp', 'video_bp']
