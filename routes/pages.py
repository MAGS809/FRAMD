"""
Pages routes blueprint.
Handles template rendering for static pages.
"""
from flask import Blueprint, render_template, session, send_from_directory, redirect, url_for
from flask_login import current_user, login_required
import os

from models import Subscription, AILearning, Project, ScenePlan

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


@pages_bp.route('/profile')
@login_required
def profile():
    """User profile page."""
    user_initials, user_name, token_balance, export_count, _ = get_user_context()
    subscription = Subscription.query.filter_by(user_id=current_user.id).first()
    tier = subscription.tier if subscription else 'free'
    project_count = Project.query.filter_by(user_id=current_user.id).count()
    videos_completed = Project.query.filter_by(user_id=current_user.id, status='completed').count()
    member_since = current_user.created_at.strftime('%B %Y') if hasattr(current_user, 'created_at') and current_user.created_at else 'Unknown'
    return render_template('profile.html',
        user_initials=user_initials,
        user_name=user_name,
        user_email=current_user.email or '',
        tier=tier,
        member_since=member_since,
        project_count=project_count,
        token_balance=token_balance,
        videos_completed=videos_completed
    )


@pages_bp.route('/history')
@login_required
def history():
    """User video history page."""
    projects = Project.query.filter_by(user_id=current_user.id).order_by(Project.created_at.desc()).all()
    total_spent = sum(p.total_estimated_cost or 0 for p in projects)

    for p in projects:
        scenes = ScenePlan.query.filter_by(project_id=p.id).all()
        if scenes:
            p.scene_costs = {}
            for s in scenes:
                label = f"Scene {s.scene_index}: {s.source_type or 'unknown'}"
                p.scene_costs[label] = s.estimated_cost or 0
        else:
            p.scene_costs = None

    return render_template('history.html',
        projects=projects,
        total_spent=total_spent
    )


@pages_bp.route('/billing')
@login_required
def billing():
    """User billing page."""
    user_initials, user_name, token_balance, export_count, _ = get_user_context()
    subscription = Subscription.query.filter_by(user_id=current_user.id).first()
    tier = subscription.tier if subscription else 'free'

    monthly_tokens_map = {'free': 50, 'creator': 300, 'pro': 1000}
    monthly_tokens = monthly_tokens_map.get(tier, 50)
    token_pct = min(100, (token_balance / monthly_tokens * 100)) if monthly_tokens > 0 else 0
    period_end = subscription.current_period_end.strftime('%b %d, %Y') if subscription and subscription.current_period_end else None

    return render_template('billing.html',
        tier=tier,
        token_balance=token_balance,
        monthly_tokens=monthly_tokens,
        token_pct=token_pct,
        period_end=period_end
    )


@pages_bp.route('/robots.txt')
def robots_txt():
    return send_from_directory(os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static'), 'robots.txt')
