"""
Chat routes blueprint.
Handles chat/conversation endpoints.
"""
import json
from flask import Blueprint, request, jsonify
from flask_login import current_user

from extensions import db
from models import Project, Conversation
from context_engine import call_ai
from routes.utils import get_user_id

chat_bp = Blueprint('chat', __name__)


@chat_bp.route('/api/chat', methods=['POST'])
def api_chat():
    user_id = get_user_id()
    if not user_id:
        return jsonify({'ok': False, 'error': 'Not authenticated'}), 401
    
    data = request.get_json()
    message = data.get('message', '').strip()
    project_id = data.get('project_id')
    mode = data.get('mode')
    
    if not message:
        return jsonify({'ok': False, 'error': 'No message provided'}), 400
    
    project = None
    if project_id:
        project = Project.query.filter_by(id=project_id, user_id=user_id).first()
    
    if not project:
        project_name = message[:50] + '...' if len(message) > 50 else message
        project = Project(
            user_id=user_id,
            name=project_name,
            template_type=mode or 'auto',
            status='draft'
        )
        db.session.add(project)
        db.session.commit()
        project_id = project.id
    
    user_conv = Conversation(
        user_id=user_id,
        role='user',
        content=json.dumps({'project_id': project_id, 'text': message})
    )
    db.session.add(user_conv)
    db.session.commit()
    
    ai_role = """You are an AI video editor for Framd. Your purpose is to create videos that match the user's vision.

YOUR JOB:
1. Transform videos while preserving motion and structure (Remix mode)
2. Extract the best moments from long content (Clipper mode)
3. Create original content using stock and AI visuals (Simple Stock mode)
4. Ask questions when critical information is missing
5. Rate your own work honestly - minimum 7.5 to show user

YOU MUST ASK WHEN:
- Brand colors not specified
- Tone/direction unclear (serious? funny? educational?)
- Target audience unknown
- Missing logo, assets, or brand materials
- Vague request that could go multiple directions

Be helpful, concise, and focused on delivering great video content."""

    try:
        response = call_ai(
            prompt=f"User message: {message}\n\nCurrent mode: {mode or 'not selected'}\n\nRespond naturally as a video creation assistant. If you need more information to proceed, ask ONE clear question.",
            system_prompt=ai_role,
            json_output=False,
            max_tokens=500
        )
        
        if isinstance(response, dict):
            ai_response = response.get('response', response.get('text', str(response)))
        else:
            ai_response = str(response)
        
        needs_clarification = any(q in ai_response.lower() for q in ['?', 'what', 'which', 'how', 'could you', 'can you'])
        
    except Exception as e:
        ai_response = "I'm ready to help you create your video. What would you like to make?"
        needs_clarification = True
    
    ai_conv = Conversation(
        user_id=user_id,
        role='assistant',
        content=json.dumps({'project_id': project_id, 'text': ai_response})
    )
    db.session.add(ai_conv)
    db.session.commit()
    
    generation_ready = any(phrase in message.lower() for phrase in [
        'generate', 'create video', 'make video', 'start generation',
        'build video', 'render', "let's go", "looks good", "that's perfect"
    ]) and not needs_clarification
    
    trigger_generation = False
    job_data = None
    
    if generation_ready and mode in ['remix', 'simple', 'clipper']:
        trigger_generation = True
        job_data = {
            'mode': mode,
            'project_name': project.name,
            'project_id': project_id,
            'user_message': message
        }
    
    return jsonify({
        'ok': True,
        'response': ai_response,
        'project_id': project_id,
        'project_name': project.name,
        'needs_clarification': needs_clarification,
        'trigger_generation': trigger_generation,
        'job_data': job_data
    })


@chat_bp.route('/api/project/<int:project_id>/chat', methods=['GET'])
def api_get_project_chat(project_id):
    user_id = get_user_id()
    if not user_id:
        return jsonify({'ok': False, 'error': 'Not authenticated'}), 401
    
    project = Project.query.filter_by(id=project_id, user_id=user_id).first()
    if not project:
        return jsonify({'ok': False, 'error': 'Project not found'}), 404
    
    conversations = Conversation.query.filter_by(user_id=user_id).order_by(Conversation.created_at.asc()).all()
    
    messages = []
    for conv in conversations:
        try:
            content = json.loads(conv.content) if conv.content else {}
            if content.get('project_id') == project_id:
                messages.append({
                    'role': conv.role,
                    'content': content.get('text', ''),
                    'created_at': conv.created_at.isoformat() if conv.created_at else None
                })
        except:
            pass
    
    return jsonify({
        'ok': True,
        'messages': messages,
        'mode': project.template_type,
        'name': project.name
    })


@chat_bp.route('/api/project/<int:project_id>/rename', methods=['POST'])
def api_rename_project(project_id):
    user_id = get_user_id()
    if not user_id:
        return jsonify({'ok': False, 'error': 'Not authenticated'}), 401
    
    project = Project.query.filter_by(id=project_id, user_id=user_id).first()
    if not project:
        return jsonify({'ok': False, 'error': 'Project not found'}), 404
    
    data = request.get_json()
    new_name = data.get('name', '').strip()
    
    if not new_name:
        return jsonify({'ok': False, 'error': 'Name cannot be empty'}), 400
    
    project.name = new_name[:100]
    db.session.commit()
    
    return jsonify({'ok': True, 'name': project.name})
