"""
Chat routes blueprint.
Handles chat/conversation endpoints.
"""
import json
import os
import subprocess
from flask import Blueprint, request, jsonify
from flask_login import current_user

from extensions import db
from models import Project, ProjectSource, ScenePlan, CommunityTemplate, Conversation, OverlayTemplate, ProjectOverlay, MonthlyUsage
from context_engine import call_ai, SYSTEM_GUARDRAILS
from routes.utils import get_user_id
from routes.overlays import get_or_create_monthly_usage, CLIPPER_MONTHLY_CAP

chat_bp = Blueprint('chat', __name__)

CHAT_PROMPT_TEMPLATE = """User message: {message}

Current mode: {mode}

CONVERSATION CONTEXT:
{history}

{brief_context}

{overlay_context}

UNIFIED PIPELINE FLOW:
You are Framd's AI director helping users create videos through a brief-first architecture.

BRIEF-FIRST APPROACH:
1. When a user describes their idea with NO mode selected, extract the core thesis and suggest a production approach.
   - Identify the main argument or story the user wants to tell.
   - Suggest whether they should use clip mode (repurpose existing footage), remix mode (AI-transform footage), or a combination.
2. When files have been uploaded, acknowledge them and help the user decide between clip vs remix for EACH file.
   - Clip mode: extracts the best moments from the original footage (transcript-driven).
   - Remix mode: uses the footage as a motion skeleton for AI-generated visuals (Runway-powered).
3. When NO files are uploaded, mention that community templates can be used as a starting point.
   - Community templates are free but include a small Framd watermark.
   - The watermark can be removed by upgrading or purchasing the template.
4. Once the brief and source decisions are clear, the pipeline will build a scene plan with cost estimates.

COST AWARENESS:
- Clip scene: $0.10/scene
- Stock footage scene: $0.15/scene
- DALL-E generated scene: $0.30/scene
- Remix/Runway scene: $0.20-0.40/second depending on quality tier

Respond as a structured JSON object:
{{
    "response": "Your conversational response to the user",
    "needs_clarification": true/false,
    "ready_to_generate": true/false,
    "suggested_mode": "remix|clipper|simple|null",
    "overlay_suggestions": [],
    "extracted_thesis": "The core thesis/argument if identifiable, otherwise null",
    "suggested_approach": "clip|remix|mixed|template|null",
    "quick_replies": ["Option A", "Option B"]
}}

RULES:
- "needs_clarification": true ONLY if you genuinely need critical info that would fundamentally change the output (e.g. the user gave zero direction). If you can make a reasonable creative decision, MAKE IT. Don't ask.
- "ready_to_generate": true ONLY if the user has explicitly confirmed they want to proceed with generation AND you have enough info
- "response": natural, concise response. 1-3 sentences MAX. If asking a question, ask exactly ONE. No bullet lists, no disclaimers, no policy explanations. Sound like a sharp creative director — get to the point fast.
- "suggested_mode": suggest a mode if the user hasn't chosen one and their intent is clear, otherwise null
- "extracted_thesis": if the user describes a video idea, extract the core thesis or argument. null if not applicable.
- "suggested_approach": suggest the best production approach based on available sources. null if unclear.
- "quick_replies": 2-4 short tappable options the user can click instead of typing. ALWAYS include these when your response implies a choice or confirmation. Examples: ["Yes, build it", "Change the tone first"], ["Remix mode", "Clip mode", "Mix both"]. Keep each under 5 words. If no choices apply, use empty array.
- "overlay_suggestions": ONLY for clipper mode. If overlays could enhance the clip, suggest them with precise descriptive language. Each suggestion is an object with "type" (caption|logo|lower_third|text|watermark|progress_bar|cta) and "reason" (why this overlay would work for their content). NEVER auto-apply overlays — only suggest. Always leave room for user input. If the user hasn't asked about overlays, set to empty array.

DECISIVENESS (CRITICAL):
- You are a creative director. Make decisions. Don't ask permission for things you can decide yourself.
- If the user says "make it a call to action" — decide HOW. Don't ask "what kind of CTA?"
- If the user says "however you see fit" — that means PROCEED, don't ask another question.
- MAX 2 clarifying questions per project. After that, make your best creative call and build the scene plan.
- When you have: thesis + source material + general direction → BUILD THE SCENE PLAN. Don't stall.

ANTI-VERBOSITY (CRITICAL):
- NEVER dump walls of bullet points at the user.
- NEVER list what you will or won't do with visuals/content.
- NEVER front-load with disclaimers, warnings, or "transparency" notes.
- If you need info, ask ONE short question. That's it. Move on when they answer.
- Wrong: "This is a sensitive topic. I want to be transparent about my approach. I will NOT source imagery that: [5 bullet points]. Instead I would focus on: [5 more bullet points]. Here are my questions: [3 numbered questions]"
- Right: "Got it. Who's the audience for this?"
- Think creative director, not compliance officer."""

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


SCENE_PLAN_PROMPT = """You are building a scene-by-scene plan for a short-form video.

PROJECT BRIEF: {brief}
MODE: {mode}
UPLOADED SOURCES: {sources}

Create a scene plan for a {target_duration}-second video. Each scene follows the narrative anchor structure.

Return a JSON array of scenes:
[
  {{
    "scene_index": 1,
    "anchor_type": "HOOK|CLAIM|EVIDENCE|PIVOT|COUNTER|CLOSER",
    "script_text": "The voiceover line for this scene",
    "source_type": "{source_type}",
    "visual_description": "What the viewer sees",
    "visual_container": "fullscreen|split_screen|card|frame",
    "duration": 5.0,
    "transition_in": "cut|fade|slide",
    "transition_out": "cut|fade|slide"
  }}
]

RULES:
- Total duration must be close to {target_duration} seconds
- Use the anchor structure: HOOK (3s) → CLAIM → EVIDENCE → PIVOT → COUNTER (optional) → CLOSER
- Each script_text line must be punchy, no filler
- For remix mode: source_type is "remix" for all scenes
- For clipper mode: source_type is "clip" for all scenes
- For simple mode: source_type can be "stock", "dalle", or "remix"
- Visual descriptions should be specific and cinematic
- End with a clear closing beat (CTA if specified)
- Return ONLY the JSON array, nothing else"""


def build_scene_plan(project_id, user_id, mode, project):
    sources = ProjectSource.query.filter_by(project_id=project_id).all()
    source_list = ', '.join([f"{s.file_name} ({s.processing_mode})" for s in sources]) if sources else 'None'
    
    brief = project.brief or 'No brief provided'
    
    source_type_map = {'remix': 'remix', 'clipper': 'clip', 'simple': 'stock'}
    primary_source_type = source_type_map.get(mode, 'stock')
    
    total_duration = 60
    if sources:
        for s in sources:
            if s.duration and s.duration > 0:
                total_duration = min(s.duration, 60)
                break
    
    prompt = SCENE_PLAN_PROMPT.format(
        brief=brief,
        mode=mode,
        sources=source_list,
        target_duration=int(total_duration),
        source_type=primary_source_type
    )
    
    try:
        result = call_ai(
            prompt=prompt,
            system_prompt="You are a video scene planner. Return only valid JSON arrays.",
            json_output=True,
            max_tokens=1500
        )
        
        scenes_data = result if isinstance(result, list) else result.get('scenes', result.get('scene_plan', []))
        if isinstance(scenes_data, dict):
            for key in scenes_data:
                if isinstance(scenes_data[key], list):
                    scenes_data = scenes_data[key]
                    break
            else:
                scenes_data = []
        
        if not scenes_data or not isinstance(scenes_data, list):
            print(f"[Scene Plan] No valid scenes from AI. Raw result type: {type(result)}")
            return [], 0, {}
        
        ScenePlan.query.filter_by(project_id=project_id).delete()
        
        per_scene_rates = {
            'clip': 0.10,
            'stock': 0.15,
            'dalle': 0.30
        }
        per_second_rate_remix = 0.25
        
        scene_plan_data = []
        total_cost = 0
        cost_breakdown = {}
        
        for i, scene in enumerate(scenes_data):
            s_type = scene.get('source_type', primary_source_type)
            duration = float(scene.get('duration', 5.0))
            if s_type == 'remix':
                estimated_cost = round(duration * per_second_rate_remix, 2)
            else:
                estimated_cost = per_scene_rates.get(s_type, 0.15)
            
            sp = ScenePlan(
                project_id=project_id,
                scene_index=i + 1,
                source_type=s_type,
                anchor_type=scene.get('anchor_type', 'CLAIM'),
                script_text=scene.get('script_text', ''),
                visual_container=scene.get('visual_container', 'fullscreen'),
                duration=duration,
                start_time=sum(float(s.get('duration', 5.0)) for s in scenes_data[:i]),
                end_time=sum(float(s.get('duration', 5.0)) for s in scenes_data[:i+1]),
                transition_in=scene.get('transition_in', 'cut'),
                transition_out=scene.get('transition_out', 'cut'),
                estimated_cost=estimated_cost,
                source_config={'visual_description': scene.get('visual_description', '')},
                render_status='planned'
            )
            db.session.add(sp)
            
            scene_plan_data.append({
                'scene_index': i + 1,
                'source_type': s_type,
                'script_text': scene.get('script_text', ''),
                'visual_container': scene.get('visual_container', 'fullscreen'),
                'visual_description': scene.get('visual_description', ''),
                'duration': duration,
                'estimated_cost': estimated_cost,
                'anchor_type': scene.get('anchor_type', 'CLAIM'),
                'transition_in': scene.get('transition_in', 'cut'),
                'transition_out': scene.get('transition_out', 'cut')
            })
            
            total_cost += estimated_cost
            cost_breakdown[s_type] = cost_breakdown.get(s_type, 0) + estimated_cost
        
        db.session.commit()
        return scene_plan_data, round(total_cost, 2), cost_breakdown
        
    except Exception as e:
        db.session.rollback()
        print(f"[Scene Plan Error] {e}")
        return [], 0, {}


@chat_bp.route('/api/chat', methods=['POST'])
def api_chat():
    user_id = get_user_id()
    if not user_id:
        return jsonify({'ok': False, 'error': 'Not authenticated'}), 401
    
    data = request.get_json()
    message = data.get('message', '').strip()
    project_id = data.get('project_id')
    mode = data.get('mode')
    uploaded_files = data.get('uploaded_files', [])
    approve_scene_plan = data.get('approve_scene_plan', False)
    
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

    if uploaded_files:
        import glob as glob_mod
        upload_dir = 'uploads'
        existing_count = ProjectSource.query.filter_by(project_id=project_id).count()
        for i, uf in enumerate(uploaded_files):
            job_id = uf.get('job_id', '')
            file_name = uf.get('file_name', 'unknown')
            processing_mode = uf.get('processing_mode', 'clip')
            if not job_id:
                continue
            matches = glob_mod.glob(os.path.join(upload_dir, f"{job_id}_*"))
            if not matches:
                continue
            file_path = matches[0]
            ext = file_name.rsplit('.', 1)[-1].lower() if '.' in file_name else ''
            file_type = 'video' if ext in ('mp4', 'mov', 'avi', 'webm', 'mkv') else 'audio' if ext in ('mp3', 'wav', 'm4a', 'aac') else 'other'
            duration = None
            try:
                probe = subprocess.run(
                    ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration', '-of', 'json', file_path],
                    capture_output=True, text=True, timeout=30
                )
                probe_data = json.loads(probe.stdout)
                duration = float(probe_data.get('format', {}).get('duration', 0))
            except Exception:
                pass
            source = ProjectSource(
                project_id=project_id,
                user_id=user_id,
                file_path=file_path,
                file_name=file_name,
                file_type=file_type,
                duration=duration,
                processing_mode=processing_mode,
                processing_status='uploaded',
                sort_order=existing_count + i,
            )
            db.session.add(source)
        db.session.commit()
    
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
    
    brief_context = ""
    try:
        sources = ProjectSource.query.filter_by(project_id=project_id).order_by(ProjectSource.sort_order).all()
        if sources:
            source_lines = []
            for s in sources:
                status_label = s.processing_status or 'pending'
                mode_label = s.processing_mode or 'clip'
                source_lines.append(f"- {s.file_name} ({s.file_type}, {mode_label} mode, {status_label})")
                if s.transcript:
                    source_lines.append(f"  Transcript preview: {s.transcript[:150]}...")
            brief_context = "UPLOADED SOURCES:\n" + "\n".join(source_lines)
        else:
            template_count = CommunityTemplate.query.filter_by(is_public=True).count()
            brief_context = f"NO FILES UPLOADED. {template_count} community templates are available (free with watermark)."

        if project.brief:
            brief_context += f"\n\nPROJECT BRIEF:\n{project.brief}"
    except Exception:
        pass

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
            brief_context=brief_context,
            overlay_context=overlay_context,
        )
        
        response = call_ai(
            prompt=prompt,
            system_prompt=SYSTEM_GUARDRAILS,
            json_output=True,
            max_tokens=500
        )
        
        overlay_suggestions = []
        extracted_thesis = None
        suggested_approach = None
        quick_replies = []
        if isinstance(response, dict):
            ai_response = response.get('response', '')
            needs_clarification = response.get('needs_clarification', False)
            ready_to_generate = response.get('ready_to_generate', False)
            suggested_mode = response.get('suggested_mode')
            overlay_suggestions = response.get('overlay_suggestions', [])
            extracted_thesis = response.get('extracted_thesis')
            suggested_approach = response.get('suggested_approach')
            quick_replies = response.get('quick_replies', [])
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
        extracted_thesis = None
        suggested_approach = None
        quick_replies = []
    
    ai_conv = Conversation(
        user_id=user_id,
        role='assistant',
        content=json.dumps({'project_id': project_id, 'text': ai_response})
    )
    db.session.add(ai_conv)
    db.session.commit()
    
    resolved_mode = mode if mode and mode != 'auto' else suggested_mode
    effective_mode = resolved_mode or 'auto'
    
    if extracted_thesis and project:
        try:
            project.brief = extracted_thesis
            db.session.commit()
        except Exception:
            db.session.rollback()

    scene_plan_data = []
    total_cost = 0
    cost_breakdown = {}
    
    if ready_to_generate and effective_mode in ['remix', 'simple', 'clipper']:
        scene_plan_data, total_cost, cost_breakdown = build_scene_plan(
            project_id, user_id, effective_mode, project
        )

    trigger_generation = approve_scene_plan and effective_mode in ['remix', 'simple', 'clipper']

    job_data = None
    if trigger_generation:
        job_data = {
            'mode': effective_mode,
            'project_name': project.name,
            'project_id': project_id,
            'user_message': message
        }
    
    result = {
        'ok': True,
        'response': ai_response,
        'project_id': project_id,
        'project_name': project.name,
        'needs_clarification': needs_clarification,
        'trigger_generation': trigger_generation,
        'job_data': job_data,
        'suggested_mode': suggested_mode,
        'overlay_suggestions': overlay_suggestions if mode == 'clipper' else [],
        'extracted_thesis': extracted_thesis,
        'suggested_approach': suggested_approach,
        'quick_replies': quick_replies,
    }
    
    if scene_plan_data:
        result['scene_plan'] = scene_plan_data
        result['total_cost'] = total_cost
        result['cost_breakdown'] = cost_breakdown
    
    return jsonify(result)


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
