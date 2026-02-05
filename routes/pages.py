"""
Pages routes blueprint.
Handles template rendering for static pages.
"""
from flask import Blueprint, render_template, session
from flask_login import current_user

from models import Subscription, AILearning

pages_bp = Blueprint('pages', __name__)


def get_user_context():
    """Get common user context for authenticated pages."""
    if not current_user.is_authenticated:
        return None, None, None, None, 0
    
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
    
    return user_initials, user_name, token_balance, export_count, True


@pages_bp.route('/')
def index():
    """Landing page or dashboard based on auth state."""
    if current_user.is_authenticated:
        user_initials, user_name, token_balance, export_count, _ = get_user_context()
        return render_template('chat.html',
            user=current_user,
            user_initials=user_initials,
            user_name=user_name,
            token_balance=token_balance,
            export_count=export_count
        )
    return render_template('landing.html')


@pages_bp.route('/pricing')
def pricing():
    """Pricing page."""
    return render_template('pricing.html')


@pages_bp.route('/terms')
def terms():
    """Terms of service page."""
    return render_template('terms.html')


@pages_bp.route('/privacy')
def privacy():
    """Privacy policy page."""
    return render_template('privacy.html')


@pages_bp.route('/faq')
def faq():
    """FAQ page."""
    return render_template('faq.html')


@pages_bp.route('/dev')
def dev_mode():
    """Developer mode - bypasses auth for testing."""
    session['dev_mode'] = True
    return render_template('chat.html', 
        user=None, 
        dev_mode=True,
        user_initials='D',
        user_name='Dev User',
        token_balance=1000,
        export_count=0
    )


@pages_bp.route('/chat')
def chat_interface():
    """Chat interface page."""
    if current_user.is_authenticated:
        user_initials, user_name, token_balance, export_count, _ = get_user_context()
        return render_template('chat.html',
            user=current_user,
            user_initials=user_initials,
            user_name=user_name,
            token_balance=token_balance,
            export_count=export_count
        )
    return render_template('landing.html')


@pages_bp.route('/health', methods=['GET'])
def health():
    """Health check endpoint."""
    return {'status': 'healthy'}
