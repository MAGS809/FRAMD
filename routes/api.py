"""
API routes blueprint.
Handles /api/jobs endpoints for video generation job queue.
"""
from flask import Blueprint, request, jsonify
from flask_login import current_user

from models import Project
from job_queue import JOB_QUEUE
from routes.utils import get_user_id

api_bp = Blueprint('api', __name__)


@api_bp.route('/api/jobs', methods=['POST'])
def api_create_job():
    """Create a new video generation job."""
    user_id = get_user_id()
    if not user_id:
        return jsonify({'ok': False, 'error': 'Not authenticated'}), 401
    
    data = request.get_json()
    project_id = data.get('project_id')
    quality_tier = data.get('quality_tier', 'good')
    job_data = data.get('job_data', {})
    
    if not project_id:
        return jsonify({'ok': False, 'error': 'Project ID required'}), 400
    
    project = Project.query.filter_by(id=project_id, user_id=user_id).first()
    if not project:
        return jsonify({'ok': False, 'error': 'Project not found'}), 404
    
    job_id = JOB_QUEUE.add_job(
        user_id=user_id,
        project_id=project_id,
        quality_tier=quality_tier,
        job_data=job_data
    )
    
    job = JOB_QUEUE.get_job(job_id)
    
    return jsonify({
        'ok': True,
        'job': JOB_QUEUE.to_dict(job)
    })


@api_bp.route('/api/jobs/<int:job_id>', methods=['GET'])
def api_get_job(job_id):
    """Get job status and progress."""
    user_id = get_user_id()
    if not user_id:
        return jsonify({'ok': False, 'error': 'Not authenticated'}), 401
    
    job = JOB_QUEUE.get_job(job_id)
    if not job:
        return jsonify({'ok': False, 'error': 'Job not found'}), 404
    
    if job.user_id != user_id:
        return jsonify({'ok': False, 'error': 'Not authorized'}), 403
    
    position = JOB_QUEUE.get_queue_position(job_id) if job.status == 'pending' else 0
    
    return jsonify({
        'ok': True,
        'job': JOB_QUEUE.to_dict(job),
        'queue_position': position
    })


@api_bp.route('/api/jobs', methods=['GET'])
def api_get_user_jobs():
    """Get all jobs for the current user."""
    user_id = get_user_id()
    if not user_id:
        return jsonify({'ok': False, 'error': 'Not authenticated'}), 401
    
    active_only = request.args.get('active', 'false').lower() == 'true'
    
    if active_only:
        jobs = JOB_QUEUE.get_active_jobs(user_id)
    else:
        jobs = JOB_QUEUE.get_user_jobs(user_id, limit=20)
    
    return jsonify({
        'ok': True,
        'jobs': [JOB_QUEUE.to_dict(job) for job in jobs]
    })


@api_bp.route('/api/jobs/<int:job_id>/cancel', methods=['POST'])
def api_cancel_job(job_id):
    """Cancel a pending job."""
    user_id = get_user_id()
    if not user_id:
        return jsonify({'ok': False, 'error': 'Not authenticated'}), 401
    
    success = JOB_QUEUE.cancel_job(job_id, user_id)
    
    if success:
        return jsonify({'ok': True, 'message': 'Job cancelled'})
    else:
        return jsonify({'ok': False, 'error': 'Cannot cancel job (may already be processing)'}), 400


@api_bp.route('/api/jobs/stats', methods=['GET'])
def api_queue_stats():
    """Get overall queue statistics (admin)."""
    stats = JOB_QUEUE.get_queue_stats()
    
    return jsonify({
        'ok': True,
        'stats': stats
    })


@api_bp.route('/api/projects', methods=['GET'])
def api_get_projects():
    user_id = get_user_id()
    if not user_id:
        return jsonify({'ok': False, 'error': 'Not authenticated'}), 401
    
    projects = Project.query.filter_by(user_id=user_id).order_by(Project.updated_at.desc()).limit(50).all()
    
    return jsonify({
        'ok': True,
        'projects': [{
            'id': p.id,
            'name': p.name,
            'mode': p.template_type,
            'status': p.status,
            'duration': 0,
            'thumbnail': None,
            'created_at': p.created_at.isoformat() if p.created_at else None,
            'updated_at': p.updated_at.isoformat() if p.updated_at else None
        } for p in projects]
    })
