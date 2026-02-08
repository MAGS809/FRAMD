"""
Authentication routes blueprint.
Handles login, logout, and session management.
"""
from flask import Blueprint, render_template, redirect, session
from flask_login import current_user, logout_user, login_required

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/')
def index():
    """Landing page or dashboard based on auth state."""
    if current_user.is_authenticated:
        return render_template('index.html', user=current_user)
    return render_template('landing.html')


@auth_bp.route('/pricing')
def pricing():
    """Pricing page."""
    return render_template('pricing.html')


@auth_bp.route('/dev')
def dev_mode():
    """Developer mode - bypasses auth for testing."""
    session['dev_mode'] = True
    return render_template('index.html', user=None, dev_mode=True)


@auth_bp.route('/logout')
def logout():
    """Log out user and clear session."""
    logout_user()
    session.clear()
    return redirect('/')


@auth_bp.route('/health', methods=['GET'])
def health():
    """Health check endpoint."""
    from flask import jsonify
    from models import MediaAsset, KeywordAssetCache
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
