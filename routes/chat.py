"""
Chat routes blueprint.
Handles chat/conversation endpoints.
"""
import json
from flask import Blueprint, request, jsonify
from flask_login import current_user

from extensions import db
from models import Project, Conversation, OverlayTemplate, ProjectOverlay, MonthlyUsage
from context_engine import call_ai, SYSTEM_GUARDRAILS
from routes.utils import get_user_id
from routes.overlays import get_or_create_monthly_usage, CLIPPER_MONTHLY_CAP

chat_bp = Blueprint('chat', __name__)

CHAT_PROMPT_TEMPLATE = """User message: {message}

Current mode: {mode}

CONVERSATION CONTEXT:
{history}

{overlay_context}

Respond as a structured JSON object:
{{
    "response": "Your conversational response to the user",
    "needs_clarification": true/false,
    "ready_to_generate": true/false,
    "suggested_mode": "remix|clipper|simple|null",
    "overlay_suggestions": []
}}

RULES:
- "needs_clarification": true ONLY if you genuinely need critical info (brand colors, tone, audience, direction)
- "ready_to_generate": true ONLY if the user has explicitly confirmed they want to proceed with generation AND you have enough info
- "response": natural, concise response. If asking a question, ask exactly ONE.
- "suggested_mode": suggest a mode if the user hasn't chosen one and their intent is clear, otherwise null
- "overlay_suggestions": ONLY for clipper mode. If overlays could enhance the clip, suggest them with precise descriptive language. Each suggestion is an object with "type" (caption|logo|lower_third|text|watermark|progress_bar|cta) and "reason" (why this overlay would work for their content). NEVER auto-apply overlays — only suggest. Always leave room for user input. If the user hasn't asked about overlays, set to empty array."""

CLIPPER_OVERLAY_CONTEXT = """CLIPPER OVERLAY SYSTEM:
You are helping the user clip their video and optionally add overlays. Available overlay types:
- caption: Word-by-word synced captions from audio ($0.20)
- logo: Upload and position a logo ($0.10)
- lower_third: Name/title bar that slides in ($0.15)
- text: Custom text with font, color, animation ($0.05)
- watermark: Semi-transparent branding ($0.05)
- progress_bar: Engagement hook showing video progress ($0.10)
- cta: Call-to-action banner like "Follow", "Link in bio" ($0.10)

OVERLAY BEHAVIOR RULES:
1. Overlays are ALWAYS at the user's discretion. Never add anything without asking.
2. When suggesting, use precise descriptive language: "This clip has dialogue — would you like word-synced captions? I'd suggest positioning them at the bottom center with a bold style, but you pick what feels right."
3. For each overlay element, ask specific questions about position, style, and content. Don't guess.
4. If the user has saved overlay templates, mention they can apply one with a single click.
5. After the user confirms their overlay choices, offer to save the configuration as a template for reuse.

{recent_overlays}
{saved_templates}
Pricing: $0.49 base per clip, up to $1.49 with overlays. Monthly cap: $29.99 (free clips after cap).
{usage_info}"""


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
    
    recent_convos = Conversation.query.filter_by(user_id=user_id).order_by(
        Conversation.created_at.desc()
    ).limit(10).all()
    history_lines = []
    for conv in reversed(recent_convos):
        try:
            c = json.loads(conv.content) if conv.content else {}
            if c.get('project_id') == project_id:
                history_lines.append(f"{conv.role}: {c.get('text', '')[:200]}")
        except:
            pass
    history_text = "\n".join(history_lines[-6:]) if history_lines else "First message"
    
    overlay_context = ""
    if mode == 'clipper':
        recent_overlay_text = ""
        saved_template_text = ""
        usage_text = ""
        try:
            recent_overlays = ProjectOverlay.query.filter_by(
                user_id=user_id
            ).order_by(ProjectOverlay.applied_at.desc()).limit(3).all()
            if recent_overlays:
                recent_names = [o.template.name if o.template else 'Custom overlay' for o in recent_overlays]
                recent_overlay_text = f"User's recent overlays: {', '.join(recent_names)}. Mention they can reapply these."

            saved_templates = OverlayTemplate.query.filter_by(
                user_id=user_id, is_permanent=True
            ).order_by(OverlayTemplate.usage_count.desc()).limit(5).all()
            if saved_templates:
                saved_names = [t.name for t in saved_templates]
                saved_template_text = f"User's saved overlay templates: {', '.join(saved_names)}. They can apply any of these with one click."

            usage = get_or_create_monthly_usage(user_id)
            remaining = max(0, CLIPPER_MONTHLY_CAP - usage.clipper_spend)
            if usage.cap_reached:
                usage_text = "User has reached the $29.99 monthly cap — all clips are FREE for the rest of the month."
            else:
                usage_text = f"Monthly spend so far: ${usage.clipper_spend:.2f} of $29.99 cap ({usage.clip_count} clips). ${remaining:.2f} remaining before unlimited."
        except Exception:
            pass

        overlay_context = CLIPPER_OVERLAY_CONTEXT.format(
            recent_overlays=recent_overlay_text,
            saved_templates=saved_template_text,
            usage_info=usage_text,
        )

    try:
        prompt = CHAT_PROMPT_TEMPLATE.format(
            message=message,
            mode=mode or 'not selected',
            history=history_text,
            overlay_context=overlay_context,
        )
        
        response = call_ai(
            prompt=prompt,
            system_prompt=SYSTEM_GUARDRAILS,
            json_output=True,
            max_tokens=500
        )
        
        overlay_suggestions = []
        if isinstance(response, dict):
            ai_response = response.get('response', '')
            needs_clarification = response.get('needs_clarification', False)
            ready_to_generate = response.get('ready_to_generate', False)
            suggested_mode = response.get('suggested_mode')
            overlay_suggestions = response.get('overlay_suggestions', [])
        else:
            ai_response = str(response) if response else "I'm ready to help you create your video. What would you like to make?"
            needs_clarification = True
            ready_to_generate = False
            suggested_mode = None
        
    except Exception as e:
        ai_response = "I'm ready to help you create your video. What would you like to make?"
        needs_clarification = True
        ready_to_generate = False
        suggested_mode = None
        overlay_suggestions = []
    
    ai_conv = Conversation(
        user_id=user_id,
        role='assistant',
        content=json.dumps({'project_id': project_id, 'text': ai_response})
    )
    db.session.add(ai_conv)
    db.session.commit()
    
    effective_mode = mode or suggested_mode
    trigger_generation = ready_to_generate and effective_mode in ['remix', 'simple', 'clipper']
    
    job_data = None
    if trigger_generation:
        job_data = {
            'mode': effective_mode,
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
        'job_data': job_data,
        'suggested_mode': suggested_mode,
        'overlay_suggestions': overlay_suggestions if mode == 'clipper' else [],
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
