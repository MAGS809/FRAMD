"""
Project management routes blueprint.
Handles project CRUD, workflow steps, drafts, and AI learning.
"""
import json
import re
from datetime import date
from flask import Blueprint, request, jsonify
from extensions import db
from models import Project, AILearning, GeneratedDraft, GlobalPattern, Subscription, User
from routes.utils import get_user_id

projects_bp = Blueprint('projects', __name__)


@projects_bp.route('/projects', methods=['GET'])
def get_projects():
    """Get all projects for the current user."""
    user_id = get_user_id()
    if not user_id:
        return jsonify({'projects': [], 'ai_learning': {'learning_progress': 0, 'total_projects': 0, 'successful_projects': 0, 'can_auto_generate': False}})
    
    if user_id == 'dev_user':
        dev_user = User.query.filter_by(id='dev_user').first()
        if not dev_user:
            dev_user = User(id='dev_user', first_name='Developer', tokens=1000)
            db.session.add(dev_user)
            db.session.commit()
    
    projects = Project.query.filter_by(user_id=user_id).order_by(Project.updated_at.desc()).all()
    
    ai_learning = AILearning.query.filter_by(user_id=user_id).first()
    if not ai_learning:
        ai_learning = AILearning(user_id=user_id)
        db.session.add(ai_learning)
        db.session.commit()
    
    return jsonify({
        'projects': [{
            'id': p.id,
            'name': p.name,
            'description': p.description,
            'status': p.status,
            'workflow_step': getattr(p, 'workflow_step', 1) or 1,
            'is_successful': p.is_successful,
            'success_score': p.success_score,
            'auto_generate_enabled': getattr(p, 'auto_generate_enabled', False) or False,
            'liked': getattr(p, 'liked', None),
            'template_type': getattr(p, 'template_type', 'start_from_scratch') or 'start_from_scratch',
            'script': getattr(p, 'script', None),
            'created_at': p.created_at.isoformat() if p.created_at else None,
            'updated_at': p.updated_at.isoformat() if p.updated_at else None
        } for p in projects],
        'ai_learning': {
            'total_projects': ai_learning.total_projects,
            'successful_projects': ai_learning.successful_projects,
            'learning_progress': ai_learning.learning_progress,
            'can_auto_generate': ai_learning.can_auto_generate
        }
    })


@projects_bp.route('/projects', methods=['POST'])
def create_project():
    """Create a new project."""
    user_id = get_user_id()
    if not user_id:
        return jsonify({'error': 'Not authenticated'}), 401
    
    if user_id == 'dev_user':
        dev_user = User.query.filter_by(id='dev_user').first()
        if not dev_user:
            dev_user = User(id='dev_user', first_name='Developer', tokens=1000)
            db.session.add(dev_user)
            db.session.commit()
    
    data = request.get_json() or {}
    name = data.get('name', 'Untitled Project')
    description = data.get('description', '')
    template_type = data.get('template_type', 'start_from_scratch')
    
    project = Project(
        user_id=user_id,
        name=name,
        description=description,
        template_type=template_type,
        status='draft'
    )
    db.session.add(project)
    
    ai_learning = AILearning.query.filter_by(user_id=user_id).first()
    if ai_learning:
        ai_learning.total_projects += 1
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'project': {
            'id': project.id,
            'name': project.name,
            'status': project.status
        }
    })


@projects_bp.route('/projects/<int:project_id>', methods=['GET'])
def get_project(project_id):
    """Get a specific project."""
    user_id = get_user_id()
    if not user_id:
        return jsonify({'error': 'Not authenticated'}), 401
    
    project = Project.query.filter_by(id=project_id, user_id=user_id).first()
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    
    return jsonify({
        'id': project.id,
        'name': project.name,
        'description': project.description,
        'status': project.status,
        'script': project.script,
        'visual_plan': project.visual_plan,
        'voice_assignments': project.voice_assignments,
        'caption_settings': project.caption_settings,
        'video_path': project.video_path,
        'is_successful': project.is_successful,
        'success_score': project.success_score,
        'created_at': project.created_at.isoformat() if project.created_at else None,
        'updated_at': project.updated_at.isoformat() if project.updated_at else None
    })


@projects_bp.route('/projects/<int:project_id>', methods=['PUT'])
def update_project(project_id):
    """Update a project."""
    user_id = get_user_id()
    if not user_id:
        return jsonify({'error': 'Not authenticated'}), 401
    
    project = Project.query.filter_by(id=project_id, user_id=user_id).first()
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    
    data = request.get_json() or {}
    
    if 'name' in data:
        project.name = data['name']
    if 'description' in data:
        project.description = data['description']
    if 'status' in data:
        project.status = data['status']
    if 'script' in data:
        project.script = data['script']
        if project.name in ['Untitled', 'Untitled Project', 'New Project', '']:
            script_text = data['script']
            lines = [l.strip() for l in script_text.split('\n') if l.strip() and not l.strip().startswith('[')]
            if lines:
                first_line = lines[0]
                first_line = re.sub(r'^[A-Z]+:\s*', '', first_line)
                if len(first_line) > 50:
                    first_line = first_line[:47] + '...'
                project.name = first_line
    if 'visual_plan' in data:
        project.visual_plan = data['visual_plan']
    if 'voice_assignments' in data:
        project.voice_assignments = data['voice_assignments']
    if 'caption_settings' in data:
        project.caption_settings = data['caption_settings']
    if 'video_path' in data:
        project.video_path = data['video_path']
    
    db.session.commit()
    
    return jsonify({'success': True, 'project_id': project.id, 'name': project.name})


@projects_bp.route('/project/<int:project_id>', methods=['DELETE'])
def delete_project(project_id):
    """Delete a project."""
    user_id = get_user_id()
    if not user_id:
        return jsonify({'error': 'Not authenticated'}), 401
    
    project = Project.query.filter_by(id=project_id, user_id=user_id).first()
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    
    db.session.delete(project)
    db.session.commit()
    
    return jsonify({'success': True, 'message': 'Project deleted'})


@projects_bp.route('/projects/<int:project_id>/workflow-step', methods=['POST'])
def update_project_workflow_step(project_id):
    """Update the workflow step for a project."""
    user_id = get_user_id()
    if not user_id:
        return jsonify({'error': 'Not authenticated'}), 401
    
    project = Project.query.filter_by(id=project_id, user_id=user_id).first()
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    
    data = request.get_json() or {}
    step = data.get('step', 1)
    
    project.workflow_step = min(max(step, 1), 8)
    db.session.commit()
    
    return jsonify({'success': True, 'workflow_step': project.workflow_step})


@projects_bp.route('/projects/<int:project_id>/mark-successful', methods=['POST'])
def mark_project_successful(project_id):
    """Mark a project as successful - rewards the AI for learning."""
    user_id = get_user_id()
    if not user_id:
        return jsonify({'error': 'Not authenticated'}), 401
    
    project = Project.query.filter_by(id=project_id, user_id=user_id).first()
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    
    data = request.get_json() or {}
    success_score = data.get('score', 1)
    
    project.is_successful = True
    project.success_score = success_score
    project.status = 'completed'
    
    ai_learning = AILearning.query.filter_by(user_id=user_id).first()
    if ai_learning:
        ai_learning.successful_projects += 1
        
        new_progress = min(100, int((ai_learning.successful_projects / max(ai_learning.total_projects, 1)) * 100) + (ai_learning.successful_projects * 5))
        ai_learning.learning_progress = new_progress
        
        if ai_learning.successful_projects >= 5 and ai_learning.learning_progress >= 50:
            ai_learning.can_auto_generate = True
        
        if project.script:
            hooks = ai_learning.learned_hooks or []
            first_line = project.script.split('\n')[0][:100] if project.script else ''
            if first_line and first_line not in hooks:
                hooks.append(first_line)
                ai_learning.learned_hooks = hooks[:20]
        
        if project.voice_assignments:
            voices = ai_learning.learned_voices or []
            for voice in (project.voice_assignments.values() if isinstance(project.voice_assignments, dict) else []):
                if voice and voice not in voices:
                    voices.append(voice)
            ai_learning.learned_voices = voices[:10]
    
    if project.script:
        hook_pattern = GlobalPattern.query.filter_by(pattern_type='hook').first()
        if not hook_pattern:
            hook_pattern = GlobalPattern(pattern_type='hook', pattern_data={'hooks': []})
            db.session.add(hook_pattern)
        hook_pattern.success_count += 1
        hook_pattern.usage_count += 1
        hook_pattern.success_rate = hook_pattern.success_count / max(hook_pattern.usage_count, 1)
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': 'Project marked as successful! AI learning updated.',
        'learning_progress': ai_learning.learning_progress if ai_learning else 0,
        'can_auto_generate': ai_learning.can_auto_generate if ai_learning else False
    })


@projects_bp.route('/ai-learning', methods=['GET'])
def get_ai_learning():
    """Get the AI learning progress for the current user."""
    user_id = get_user_id()
    if not user_id:
        return jsonify({'learning_progress': 0, 'total_projects': 0, 'successful_projects': 0, 'can_auto_generate': False})
    
    ai_learning = AILearning.query.filter_by(user_id=user_id).first()
    if not ai_learning:
        ai_learning = AILearning(user_id=user_id)
        db.session.add(ai_learning)
        db.session.commit()
    
    return jsonify({
        'total_projects': ai_learning.total_projects,
        'successful_projects': ai_learning.successful_projects,
        'learning_progress': ai_learning.learning_progress,
        'learned_hooks': ai_learning.learned_hooks or [],
        'learned_voices': ai_learning.learned_voices or [],
        'learned_styles': ai_learning.learned_styles or [],
        'learned_topics': ai_learning.learned_topics or [],
        'can_auto_generate': ai_learning.can_auto_generate
    })


@projects_bp.route('/projects/<int:project_id>/toggle-auto-generate', methods=['POST'])
def toggle_auto_generate(project_id):
    """Toggle auto-generate for a project. Requires Pro subscription and 5+ liked videos."""
    user_id = get_user_id()
    if not user_id:
        return jsonify({'error': 'Not authenticated'}), 401
    
    project = Project.query.filter_by(id=project_id, user_id=user_id).first()
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    
    sub = Subscription.query.filter_by(user_id=user_id).first()
    if not sub or sub.tier != 'pro':
        return jsonify({'error': 'Pro subscription required for auto-generation'}), 403
    
    liked_count = Project.query.filter_by(user_id=user_id, liked=True).count()
    if liked_count < 5:
        return jsonify({'error': f'Need 5 liked videos to unlock auto-generation ({liked_count}/5)'}), 403
    
    data = request.get_json() or {}
    if 'enable' in data:
        project.auto_generate_enabled = bool(data['enable'])
    else:
        project.auto_generate_enabled = not project.auto_generate_enabled
    db.session.commit()
    
    return jsonify({
        'success': True,
        'auto_generate_enabled': project.auto_generate_enabled
    })


@projects_bp.route('/projects/<int:project_id>/generated-drafts', methods=['GET'])
def get_generated_drafts(project_id):
    """Get all generated drafts for a project."""
    user_id = get_user_id()
    if not user_id:
        return jsonify({'error': 'Not authenticated'}), 401
    
    project = Project.query.filter_by(id=project_id, user_id=user_id).first()
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    
    drafts = GeneratedDraft.query.filter_by(project_id=project_id, status='pending').order_by(GeneratedDraft.created_at.desc()).limit(3).all()
    
    return jsonify({
        'drafts': [{
            'id': d.id,
            'script': d.script,
            'visual_plan': d.visual_plan,
            'sound_plan': d.sound_plan,
            'angle_used': d.angle_used,
            'vibe_used': d.vibe_used,
            'hook_type': d.hook_type,
            'clips_used': d.clips_used,
            'trend_data': d.trend_data,
            'created_at': d.created_at.isoformat() if d.created_at else None
        } for d in drafts],
        'can_generate_more': len(drafts) < 3
    })


@projects_bp.route('/draft-settings', methods=['GET'])
def get_draft_settings():
    """Get draft generation settings for the current user."""
    user_id = get_user_id()
    if not user_id:
        return jsonify({'daily_limit': 3, 'generated_today': 0})
    
    ai_learning = AILearning.query.filter_by(user_id=user_id).first()
    if not ai_learning:
        return jsonify({'daily_limit': 3, 'generated_today': 0})
    
    if ai_learning.last_draft_reset != date.today():
        ai_learning.drafts_generated_today = 0
        ai_learning.last_draft_reset = date.today()
        db.session.commit()
    
    return jsonify({
        'daily_limit': ai_learning.daily_draft_limit or 3,
        'generated_today': ai_learning.drafts_generated_today or 0
    })


@projects_bp.route('/draft-settings', methods=['POST'])
def update_draft_settings():
    """Update draft generation settings for the current user."""
    user_id = get_user_id()
    if not user_id:
        return jsonify({'error': 'Not authenticated'}), 401
    
    ai_learning = AILearning.query.filter_by(user_id=user_id).first()
    if not ai_learning:
        ai_learning = AILearning(user_id=user_id)
        db.session.add(ai_learning)
    
    data = request.get_json() or {}
    if 'daily_limit' in data:
        ai_learning.daily_draft_limit = max(1, min(10, int(data['daily_limit'])))
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'daily_limit': ai_learning.daily_draft_limit
    })


@projects_bp.route('/auto-generate-status', methods=['GET'])
def auto_generate_status():
    """Get auto-generation eligibility status for current user."""
    user_id = get_user_id()
    if not user_id:
        return jsonify({'eligible': False, 'reason': 'Not authenticated'})
    
    sub = Subscription.query.filter_by(user_id=user_id).first()
    if not sub or sub.tier != 'pro':
        return jsonify({'eligible': False, 'reason': 'Pro subscription required', 'has_pro': False})
    
    liked_count = Project.query.filter_by(user_id=user_id, liked=True).count()
    if liked_count < 5:
        return jsonify({
            'eligible': False,
            'reason': f'Need 5 liked videos ({liked_count}/5)',
            'has_pro': True,
            'liked_count': liked_count
        })
    
    return jsonify({
        'eligible': True,
        'has_pro': True,
        'liked_count': liked_count
    })
