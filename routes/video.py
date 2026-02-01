"""
Video management routes blueprint.
Handles caption preferences, video history, feedback, and hosting.
"""
import os
from flask import Blueprint, request, jsonify, session
from extensions import db
from models import Project, User, VideoFeedback
from routes.utils import get_user_id

video_bp = Blueprint('video', __name__)


@video_bp.route('/save-caption-preferences', methods=['POST'])
def save_caption_preferences():
    """Save user's caption style preferences."""
    user_id = get_user_id()
    if not user_id:
        return jsonify({'error': 'Not authenticated'}), 401
    
    data = request.get_json() or {}
    
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    
    user.caption_preferences = data
    db.session.commit()
    
    return jsonify({'success': True})


@video_bp.route('/get-caption-preferences', methods=['GET'])
def get_caption_preferences():
    """Get user's saved caption preferences."""
    user_id = get_user_id()
    if not user_id:
        return jsonify({
            'font': 'bold-center',
            'color': '#FFFFFF',
            'position': 'center',
            'style': 'bold-center'
        })
    
    user = User.query.get(user_id)
    if not user or not user.caption_preferences:
        return jsonify({
            'font': 'bold-center',
            'color': '#FFFFFF',
            'position': 'center',
            'style': 'bold-center'
        })
    
    return jsonify(user.caption_preferences)


@video_bp.route('/video-history', methods=['GET'])
def get_video_history():
    """Get user's video generation history."""
    user_id = get_user_id()
    if not user_id:
        return jsonify({'videos': []})
    
    projects = Project.query.filter_by(user_id=user_id).filter(
        Project.video_path.isnot(None)
    ).order_by(Project.updated_at.desc()).limit(20).all()
    
    videos = [{
        'id': p.id,
        'name': p.name,
        'video_path': p.video_path,
        'created_at': p.updated_at.isoformat() if p.updated_at else None
    } for p in projects]
    
    return jsonify({'videos': videos})


@video_bp.route('/save-video-history', methods=['POST'])
def save_video_history():
    """Save a video to user's history."""
    user_id = get_user_id()
    if not user_id:
        return jsonify({'error': 'Not authenticated'}), 401
    
    data = request.get_json() or {}
    project_id = data.get('project_id')
    video_path = data.get('video_path')
    
    if project_id:
        project = Project.query.filter_by(id=project_id, user_id=user_id).first()
        if project and video_path:
            project.video_path = video_path
            db.session.commit()
            return jsonify({'success': True})
    
    return jsonify({'error': 'Could not save video'}), 400


@video_bp.route('/video-feedback', methods=['POST'])
def video_feedback():
    """Record feedback (like/dislike) on a generated video."""
    user_id = get_user_id()
    if not user_id:
        return jsonify({'error': 'Not authenticated'}), 401
    
    data = request.get_json() or {}
    project_id = data.get('project_id')
    liked = data.get('liked')
    dislike_reason = data.get('dislike_reason', '')
    
    if project_id is None or liked is None:
        return jsonify({'error': 'Missing project_id or liked value'}), 400
    
    project = Project.query.filter_by(id=project_id, user_id=user_id).first()
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    
    project.liked = bool(liked)
    
    feedback = VideoFeedback.query.filter_by(project_id=project_id, user_id=user_id).first()
    if not feedback:
        feedback = VideoFeedback(project_id=project_id, user_id=user_id)
        db.session.add(feedback)
    
    feedback.liked = bool(liked)
    feedback.dislike_reason = dislike_reason if not liked else None
    
    db.session.commit()
    
    response = {
        'success': True,
        'liked': liked,
        'message': 'Thanks for your feedback!'
    }
    
    if liked:
        from models import AILearning
        ai_learning = AILearning.query.filter_by(user_id=user_id).first()
        if ai_learning:
            liked_count = Project.query.filter_by(user_id=user_id, liked=True).count()
            response['liked_count'] = liked_count
            response['can_unlock_generator'] = liked_count >= 5
    
    return jsonify(response)


@video_bp.route('/host-video', methods=['POST'])
def host_video():
    """Host a generated video for sharing."""
    user_id = get_user_id()
    if not user_id:
        return jsonify({'error': 'Not authenticated'}), 401
    
    data = request.get_json() or {}
    video_path = data.get('video_path')
    project_id = data.get('project_id')
    
    if not video_path:
        return jsonify({'error': 'No video path provided'}), 400
    
    if not os.path.exists(video_path):
        return jsonify({'error': 'Video file not found'}), 404
    
    if project_id:
        project = Project.query.filter_by(id=project_id, user_id=user_id).first()
        if project:
            project.hosted = True
            project.video_path = video_path
            db.session.commit()
    
    base_url = os.environ.get('REPLIT_DOMAINS', 'localhost:5000').split(',')[0]
    protocol = 'https' if 'replit' in base_url else 'http'
    hosted_url = f"{protocol}://{base_url}/{video_path}"
    
    return jsonify({
        'success': True,
        'hosted_url': hosted_url
    })


@video_bp.route('/my-hosted-videos', methods=['GET'])
def my_hosted_videos():
    """Get all hosted videos for the current user."""
    user_id = get_user_id()
    if not user_id:
        return jsonify({'videos': []})
    
    projects = Project.query.filter_by(user_id=user_id, hosted=True).order_by(
        Project.updated_at.desc()
    ).limit(50).all()
    
    base_url = os.environ.get('REPLIT_DOMAINS', 'localhost:5000').split(',')[0]
    protocol = 'https' if 'replit' in base_url else 'http'
    
    videos = [{
        'id': p.id,
        'name': p.name,
        'video_path': p.video_path,
        'hosted_url': f"{protocol}://{base_url}/{p.video_path}" if p.video_path else None,
        'created_at': p.updated_at.isoformat() if p.updated_at else None
    } for p in projects]
    
    return jsonify({'videos': videos})


@video_bp.route('/video-feedback-stats', methods=['GET'])
def video_feedback_stats():
    """Get aggregate feedback stats for the current user."""
    user_id = get_user_id()
    if not user_id:
        return jsonify({'total': 0, 'liked': 0, 'disliked': 0})
    
    liked_count = VideoFeedback.query.filter_by(user_id=user_id, liked=True).count()
    disliked_count = VideoFeedback.query.filter_by(user_id=user_id, liked=False).count()
    
    return jsonify({
        'total': liked_count + disliked_count,
        'liked': liked_count,
        'disliked': disliked_count
    })
