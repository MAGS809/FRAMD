"""
Video management routes blueprint.
Handles caption preferences, video history, email preferences, feedback, and hosting.
"""
import os
from flask import Blueprint, request, jsonify, session
from extensions import db
from models import Project, User, VideoFeedback
from routes.utils import get_user_id

video_bp = Blueprint('video', __name__)


@video_bp.route('/save-caption-preferences', methods=['POST'])
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
    
    caption_prefs = {
        'caption_position': data.get('caption_position', 'bottom'),
        'caption_offset': data.get('caption_offset', 10),
        'caption_size': data.get('caption_size', 22),
        'caption_opacity': data.get('caption_opacity', 80),
        'caption_color': data.get('caption_color', '#ffffff')
    }
    
    current_styles = ai_learning.learned_styles or []
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


@video_bp.route('/get-caption-preferences', methods=['GET'])
def get_caption_preferences():
    """Get user's saved caption style preferences."""
    from models import AILearning
    
    user_id = get_user_id()
    if not user_id:
        return jsonify({})
    
    ai_learning = AILearning.query.filter_by(user_id=user_id).first()
    if not ai_learning:
        return jsonify({})
    
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


@video_bp.route('/video-history', methods=['GET'])
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


@video_bp.route('/save-video-history', methods=['POST'])
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


@video_bp.route('/email-preferences', methods=['GET'])
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


@video_bp.route('/email-preferences', methods=['POST'])
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
