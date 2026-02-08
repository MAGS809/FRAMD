"""
Project management routes blueprint.
Handles project CRUD, workflow steps, drafts, and AI learning.
"""
import json
import re
import logging
import random
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


@projects_bp.route('/projects/<int:project_id>/generate-drafts', methods=['POST'])
def generate_drafts(project_id):
    """Generate new AI drafts for a project using trend research and learned patterns."""
    user_id = get_user_id()
    if not user_id:
        return jsonify({'error': 'Not authenticated'}), 401
    
    project = Project.query.filter_by(id=project_id, user_id=user_id).first()
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    
    sub = Subscription.query.filter_by(user_id=user_id).first()
    if not sub or sub.tier != 'pro':
        return jsonify({'error': 'Pro subscription required'}), 403
    
    ai_learning = AILearning.query.filter_by(user_id=user_id).first()
    if not ai_learning:
        ai_learning = AILearning(user_id=user_id)
        db.session.add(ai_learning)
    
    if ai_learning.last_draft_reset != date.today():
        ai_learning.drafts_generated_today = 0
        ai_learning.last_draft_reset = date.today()
    
    db.session.commit()
    
    daily_limit = ai_learning.daily_draft_limit or 3
    generated_today = ai_learning.drafts_generated_today or 0
    
    if generated_today >= daily_limit:
        return jsonify({
            'error': 'Daily draft limit reached',
            'daily_limit': daily_limit,
            'generated_today': generated_today,
            'remaining': 0
        }), 429
    
    pending_count = GeneratedDraft.query.filter_by(project_id=project_id, status='pending').count()
    if pending_count >= 3:
        return jsonify({'error': 'Maximum 3 pending drafts. Approve or skip existing drafts first.'}), 400
    
    drafts_to_generate = 3 - pending_count
    
    existing_drafts = GeneratedDraft.query.filter_by(project_id=project_id).all()
    used_angles = [d.angle_used for d in existing_drafts if d.angle_used]
    used_vibes = [d.vibe_used for d in existing_drafts if d.vibe_used]
    used_hooks = [d.hook_type for d in existing_drafts if d.hook_type]
    
    learned_patterns = {
        'hooks': ai_learning.learned_hooks if ai_learning else [],
        'voices': ai_learning.learned_voices if ai_learning else [],
        'styles': ai_learning.learned_styles if ai_learning else [],
        'topics': ai_learning.learned_topics if ai_learning else []
    }
    
    topic = project.description or project.name or "general content"
    trend_data = None
    try:
        from context_engine import research_trends
        trend_data = research_trends(topic)
    except Exception as e:
        logging.warning(f"Trend research failed: {e}")
        trend_data = {'hooks': [], 'formats': [], 'visuals': [], 'sounds': []}
    
    all_angles = ['contrarian', 'evidence-first', 'story-driven', 'philosophical', 'urgent', 'reflective', 'satirical', 'educational']
    all_vibes = ['serious', 'playful', 'urgent', 'reflective', 'provocative', 'calm', 'intense', 'witty']
    all_hook_types = ['question', 'bold-claim', 'statistic', 'story-opener', 'controversy', 'revelation', 'challenge', 'prediction']
    
    available_angles = [a for a in all_angles if a not in used_angles]
    available_vibes = [v for v in all_vibes if v not in used_vibes]
    available_hooks = [h for h in all_hook_types if h not in used_hooks]
    
    if not available_angles:
        available_angles = all_angles
    if not available_vibes:
        available_vibes = all_vibes
    if not available_hooks:
        available_hooks = all_hook_types
    
    generated_drafts = []
    
    from context_engine import get_template_guidelines
    template_type = project.template_type or 'start_from_scratch'
    template_dna = get_template_guidelines(template_type)
    
    for i in range(drafts_to_generate):
        angle = available_angles[i % len(available_angles)]
        vibe = available_vibes[i % len(available_vibes)]
        hook_type = available_hooks[i % len(available_hooks)]
        
        prompt = f"""Generate a 35-75 second video script for the topic: "{topic}"

TEMPLATE: {template_type.upper().replace('_', ' ')}
TEMPLATE TONE: {template_dna['tone']}
TEMPLATE VOICE: {template_dna['voice']}
TEMPLATE HOOK STYLE: {template_dna['hook_style']}
TEMPLATE PACING: {template_dna['pacing']}
HOW TO APPLY TRENDS: {template_dna['trend_application']}
ALLOWED FOR THIS TEMPLATE: {', '.join(template_dna['allowed_overrides'])}

TREND RESEARCH (apply WITHIN the template tone):
{json.dumps(trend_data, indent=2) if trend_data else 'No trend data available - lean on template defaults'}

USER'S LEARNED PATTERNS (incorporate their style):
{json.dumps(learned_patterns, indent=2)}

CONSTRAINTS FOR THIS DRAFT:
- Angle: {angle} (the perspective/approach)
- Vibe: {vibe} (the emotional tone)
- Hook Type: {hook_type} (how to start)

IMPORTANT: Stay in the template's voice. Trends inform HOW you execute, not WHAT tone you use.

UPLOADED CLIPS TO REFERENCE:
{json.dumps(project.uploaded_clips or [], indent=2)}

Generate a complete script with:
1. A strong hook using the {hook_type} format
2. Clear anchor points: HOOK, CLAIM, EVIDENCE, PIVOT, COUNTER, CLOSER
3. Natural, human-sounding dialogue
4. Visual suggestions that match trending formats
5. Sound/music suggestions based on what's working (only if it genuinely helps)

Output as JSON:
{{
  "script": "The full script text with speaker labels if multi-character",
  "visual_plan": [{{"scene": 1, "description": "...", "source_suggestion": "..."}}],
  "sound_plan": {{"music_vibe": "...", "sfx_suggestions": ["..."], "reasoning": "why these sounds work for this content"}}
}}"""

        try:
            from context_engine import call_ai
            response = call_ai(prompt)
            
            try:
                if '```json' in response:
                    response = response.split('```json')[1].split('```')[0]
                elif '```' in response:
                    response = response.split('```')[1].split('```')[0]
                draft_data = json.loads(response.strip())
            except json.JSONDecodeError:
                draft_data = {
                    'script': response,
                    'visual_plan': [],
                    'sound_plan': {}
                }
            
            draft = GeneratedDraft(
                project_id=project_id,
                user_id=user_id,
                script=draft_data.get('script', ''),
                visual_plan=draft_data.get('visual_plan'),
                sound_plan=draft_data.get('sound_plan'),
                angle_used=angle,
                vibe_used=vibe,
                hook_type=hook_type,
                clips_used=project.uploaded_clips,
                trend_data=trend_data
            )
            db.session.add(draft)
            generated_drafts.append(draft)
            
        except Exception as e:
            logging.error(f"Draft generation failed: {e}")
            continue
    
    if generated_drafts:
        ai_learning.drafts_generated_today = (ai_learning.drafts_generated_today or 0) + len(generated_drafts)
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'drafts_generated': len(generated_drafts),
        'daily_limit': daily_limit,
        'generated_today': ai_learning.drafts_generated_today,
        'remaining': max(0, daily_limit - ai_learning.drafts_generated_today),
        'drafts': [{
            'id': d.id,
            'script': d.script,
            'visual_plan': d.visual_plan,
            'sound_plan': d.sound_plan,
            'angle_used': d.angle_used,
            'vibe_used': d.vibe_used,
            'hook_type': d.hook_type
        } for d in generated_drafts]
    })


@projects_bp.route('/generated-drafts/<int:draft_id>/action', methods=['POST'])
def draft_action(draft_id):
    """Handle draft feedback - like (approve) or dislike (skip with AI self-analysis)."""
    user_id = get_user_id()
    if not user_id:
        return jsonify({'error': 'Not authenticated'}), 401
    
    draft = GeneratedDraft.query.filter_by(id=draft_id, user_id=user_id).first()
    if not draft:
        return jsonify({'error': 'Draft not found'}), 404
    
    data = request.get_json() or {}
    action = data.get('action')
    
    if action not in ['approve', 'skip']:
        return jsonify({'error': 'Invalid action. Use "approve" or "skip"'}), 400
    
    ai_learning = AILearning.query.filter_by(user_id=user_id).first()
    
    if action == 'approve':
        project = Project.query.get(draft.project_id)
        if project:
            project.script = draft.script
            project.visual_plan = draft.visual_plan
        draft.status = 'approved'
        
        if ai_learning:
            learned_hooks = ai_learning.learned_hooks or []
            first_line = draft.script.split('\n')[0][:100] if draft.script else ''
            if first_line and first_line not in learned_hooks:
                learned_hooks.append(first_line)
                ai_learning.learned_hooks = learned_hooks[:30]
            
            learned_styles = ai_learning.learned_styles or []
            style_pattern = {
                'angle': draft.angle_used,
                'vibe': draft.vibe_used,
                'hook_type': draft.hook_type,
                'success': True
            }
            learned_styles.append(style_pattern)
            ai_learning.learned_styles = learned_styles[-50:]
    else:
        draft.status = 'skipped'
        
        if ai_learning:
            try:
                from context_engine import call_ai
                analysis_prompt = f"""You generated a draft that was rejected. Analyze internally why it failed based on these guidelines:

CORE RULES:
- Hooks must be direct, not clickbait
- No filler, no buzzwords, no trend-chasing language
- Every line logically leads to the next
- Ending must close the loop
- Calm, clear, grounded tone - never sarcastic, smug, or preachy

THE REJECTED DRAFT:
Angle: {draft.angle_used}
Vibe: {draft.vibe_used}
Hook Type: {draft.hook_type}
Script (first 500 chars): {(draft.script or '')[:500]}

Analyze in 2-3 sentences what likely went wrong. Be specific about which guideline was violated. Output JSON:
{{"likely_issue": "...", "guideline_violated": "...", "avoid_in_future": "..."}}"""
                
                analysis = call_ai(analysis_prompt)
                try:
                    if '```json' in analysis:
                        analysis = analysis.split('```json')[1].split('```')[0]
                    elif '```' in analysis:
                        analysis = analysis.split('```')[1].split('```')[0]
                    analysis_data = json.loads(analysis.strip())
                except:
                    analysis_data = {'likely_issue': 'Could not parse analysis', 'raw': analysis[:200]}
                
                dislike_learnings = ai_learning.dislike_learnings or []
                dislike_learnings.append({
                    'draft_id': draft_id,
                    'angle': draft.angle_used,
                    'vibe': draft.vibe_used,
                    'hook_type': draft.hook_type,
                    'analysis': analysis_data
                })
                ai_learning.dislike_learnings = dislike_learnings[-20:]
            except Exception as e:
                logging.warning(f"AI self-analysis failed: {e}")
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'action': action,
        'project_id': draft.project_id
    })


@projects_bp.route('/draft-settings', methods=['GET'])
def get_draft_settings():
    """Get user's draft generation settings."""
    user_id = get_user_id()
    if not user_id:
        return jsonify({'daily_limit': 3, 'generated_today': 0, 'remaining': 3})
    
    ai_learning = AILearning.query.filter_by(user_id=user_id).first()
    if not ai_learning:
        return jsonify({'daily_limit': 3, 'generated_today': 0, 'remaining': 3})
    
    if ai_learning.last_draft_reset != date.today():
        ai_learning.drafts_generated_today = 0
        ai_learning.last_draft_reset = date.today()
        db.session.commit()
    
    daily_limit = ai_learning.daily_draft_limit or 3
    generated = ai_learning.drafts_generated_today or 0
    
    return jsonify({
        'daily_limit': daily_limit,
        'generated_today': generated,
        'remaining': max(0, daily_limit - generated)
    })


@projects_bp.route('/draft-settings', methods=['POST'])
def update_draft_settings():
    """Update user's daily draft limit (1-10)."""
    user_id = get_user_id()
    if not user_id:
        return jsonify({'error': 'Not authenticated'}), 401
    
    data = request.get_json() or {}
    new_limit = data.get('daily_limit')
    
    if not isinstance(new_limit, int) or new_limit < 1 or new_limit > 10:
        return jsonify({'error': 'Daily limit must be between 1 and 10'}), 400
    
    ai_learning = AILearning.query.filter_by(user_id=user_id).first()
    if not ai_learning:
        ai_learning = AILearning(user_id=user_id, daily_draft_limit=new_limit)
        db.session.add(ai_learning)
    else:
        ai_learning.daily_draft_limit = new_limit
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'daily_limit': new_limit
    })


@projects_bp.route('/auto-generate-status', methods=['GET'])
def get_auto_generate_status():
    """Get user's auto-generate eligibility status."""
    user_id = get_user_id()
    if not user_id:
        return jsonify({
            'eligible': False,
            'reason': 'not_authenticated',
            'liked_count': 0,
            'required_likes': 5,
            'has_pro': False
        })
    
    sub = Subscription.query.filter_by(user_id=user_id).first()
    has_pro = sub and sub.tier == 'pro'
    
    liked_count = Project.query.filter_by(user_id=user_id, liked=True).count()
    
    eligible = has_pro and liked_count >= 5
    
    if not has_pro:
        reason = 'needs_pro'
    elif liked_count < 5:
        reason = 'needs_likes'
    else:
        reason = 'eligible'
    
    return jsonify({
        'eligible': eligible,
        'reason': reason,
        'liked_count': liked_count,
        'required_likes': 5,
        'has_pro': has_pro
    })


@projects_bp.route('/generate-project-metadata', methods=['POST'])
def generate_project_metadata():
    """Generate AI project name (3 words max) and description from idea/script."""
    from context_engine import call_ai
    
    data = request.get_json() or {}
    idea = data.get('idea', '')
    script = data.get('script', '')
    
    content = script if script else idea
    if not content:
        return jsonify({'success': False, 'error': 'No content provided'})
    
    prompt = f"""Based on this content, generate a project name and description.

Content: {content[:1500]}

Rules:
1. Project name: Maximum 3 words, punchy and memorable (like "Oslo Accord Truth" or "Power Dynamics")
2. Description: One sentence, under 15 words, capturing the core idea

Return ONLY valid JSON:
{{"name": "Three Word Name", "description": "One sentence description here."}}"""

    try:
        logging.info(f"[ProjectMetadata] Generating title for: {content[:50]}...")
        response = call_ai(prompt, max_tokens=100)
        logging.info(f"[ProjectMetadata] AI response: {response}")
        
        json_match = re.search(r'\{[^}]+\}', response if isinstance(response, str) else json.dumps(response))
        if json_match:
            metadata = json.loads(json_match.group())
            name = metadata.get('name', 'Untitled')[:50]
            logging.info(f"[ProjectMetadata] Generated name: {name}")
            return jsonify({
                'success': True,
                'name': name,
                'description': metadata.get('description', '')[:200]
            })
    except Exception as e:
        logging.warning(f"[ProjectMetadata] AI failed: {e}")
    
    words = content.split()[:3]
    fallback_name = ' '.join(words)[:50]
    logging.info(f"[ProjectMetadata] Using fallback name: {fallback_name}")
    return jsonify({
        'success': True,
        'name': fallback_name,
        'description': content[:100]
    })
