"""
Authentication routes blueprint.
Handles login, logout, and session management.
"""
from flask import Blueprint, redirect, session, jsonify
from flask_login import logout_user

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/logout')
def logout():
    """Log out user and clear session."""
    logout_user()
    session.clear()
    return redirect('/')


@auth_bp.route('/health', methods=['GET'])
def health():
    """Health check endpoint."""
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
