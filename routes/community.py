from flask import Blueprint, request, jsonify
from extensions import db
from models import CommunityTemplate, Project
from context_engine import call_ai, SYSTEM_GUARDRAILS
from trend_research import research_topic_trends
from routes.utils import get_user_id
import json
import logging

community_bp = Blueprint('community', __name__)


@community_bp.route('/api/community/match-templates', methods=['POST'])
def match_templates():
    data = request.get_json()
    if not data or not data.get('brief'):
        return jsonify({'error': 'brief is required'}), 400

    brief = data['brief']
    topic = data.get('topic', '')
    tone = data.get('tone', '')

    matched = []

    if topic or tone:
        query = CommunityTemplate.query.filter(CommunityTemplate.is_public == True)
        if topic and tone:
            tier1_results = query.filter(
                CommunityTemplate.topic_tags.cast(db.Text).ilike(f'%{topic}%'),
                CommunityTemplate.tone_tags.cast(db.Text).ilike(f'%{tone}%')
            ).order_by(CommunityTemplate.usage_count.desc()).limit(6).all()
            matched.extend(tier1_results)

        if len(matched) < 3:
            existing_ids = [t.id for t in matched]
            tier2_query = CommunityTemplate.query.filter(
                CommunityTemplate.is_public == True,
                ~CommunityTemplate.id.in_(existing_ids) if existing_ids else True
            )
            if topic:
                topic_matches = tier2_query.filter(
                    CommunityTemplate.topic_tags.cast(db.Text).ilike(f'%{topic}%')
                ).order_by(CommunityTemplate.usage_count.desc()).limit(6 - len(matched)).all()
                matched.extend(topic_matches)

            if len(matched) < 3 and tone:
                existing_ids = [t.id for t in matched]
                tone_matches = CommunityTemplate.query.filter(
                    CommunityTemplate.is_public == True,
                    ~CommunityTemplate.id.in_(existing_ids) if existing_ids else True,
                    CommunityTemplate.tone_tags.cast(db.Text).ilike(f'%{tone}%')
                ).order_by(CommunityTemplate.usage_count.desc()).limit(6 - len(matched)).all()
                matched.extend(tone_matches)
    else:
        matched = CommunityTemplate.query.filter(
            CommunityTemplate.is_public == True
        ).order_by(CommunityTemplate.usage_count.desc()).limit(6).all()

    if len(matched) < 1:
        try:
            trend_data = research_topic_trends(topic or brief[:50])

            prompt = f"""Analyze this video brief and create a template structure for it.

BRIEF: {brief}
TOPIC: {topic or 'general'}
TONE: {tone or 'neutral'}
TREND DATA: {json.dumps(trend_data.get('patterns', {})) if trend_data else 'none'}

Create a community template with:
1. A catchy template name
2. A description of what this template does
3. Topic tags (list of relevant topics)
4. Tone tags (list of tone descriptors)
5. Structure tags (list of structural elements like "hook-evidence-cta")
6. A visual structure (JSON object describing scene layout)
7. A scene blueprint (JSON array of scene objects)

Output JSON:
{{
    "name": "template name",
    "description": "what this template produces",
    "topic_tags": ["tag1", "tag2"],
    "tone_tags": ["tone1", "tone2"],
    "structure_tags": ["structure1"],
    "visual_structure": {{"layout": "vertical", "scenes": 5, "style": "dynamic"}},
    "scene_blueprint": [
        {{"scene": 1, "type": "hook", "duration": 3, "visual": "bold text overlay"}},
        {{"scene": 2, "type": "evidence", "duration": 5, "visual": "b-roll with stats"}},
        {{"scene": 3, "type": "cta", "duration": 2, "visual": "branded outro"}}
    ]
}}"""

            ai_result = call_ai(prompt, SYSTEM_GUARDRAILS, json_output=True, max_tokens=1024)

            if ai_result and isinstance(ai_result, dict):
                new_template = CommunityTemplate(
                    creator_id=None,
                    name=ai_result.get('name', f'AI Template for {topic or "your brief"}'),
                    description=ai_result.get('description', f'Auto-generated template based on: {brief[:100]}'),
                    topic_tags=ai_result.get('topic_tags', [topic] if topic else []),
                    tone_tags=ai_result.get('tone_tags', [tone] if tone else []),
                    structure_tags=ai_result.get('structure_tags', []),
                    visual_structure=ai_result.get('visual_structure'),
                    scene_blueprint=ai_result.get('scene_blueprint'),
                    is_ai_generated=True,
                    trend_data=trend_data,
                    is_public=True,
                )
                db.session.add(new_template)
                db.session.commit()
                matched.append(new_template)
        except Exception as e:
            logging.error(f"AI template generation failed: {e}")

    templates_out = [t.to_dict() for t in matched]
    return jsonify({'templates': templates_out, 'count': len(templates_out)})


@community_bp.route('/api/community/templates', methods=['GET'])
def list_templates():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    topic = request.args.get('topic', '')
    tone = request.args.get('tone', '')
    featured_only = request.args.get('featured_only', '').lower() == 'true'

    query = CommunityTemplate.query.filter(CommunityTemplate.is_public == True)

    if topic:
        query = query.filter(CommunityTemplate.topic_tags.cast(db.Text).ilike(f'%{topic}%'))
    if tone:
        query = query.filter(CommunityTemplate.tone_tags.cast(db.Text).ilike(f'%{tone}%'))
    if featured_only:
        query = query.filter(CommunityTemplate.is_featured == True)

    query = query.order_by(CommunityTemplate.usage_count.desc())
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)

    return jsonify({
        'templates': [t.to_dict() for t in pagination.items],
        'total': pagination.total,
        'page': pagination.page,
        'pages': pagination.pages,
    })


@community_bp.route('/api/community/templates', methods=['POST'])
def create_template():
    user_id = get_user_id()
    if not user_id:
        return jsonify({'error': 'Authentication required'}), 401

    data = request.get_json()
    if not data or not data.get('name'):
        return jsonify({'error': 'name is required'}), 400

    template = CommunityTemplate(
        creator_id=user_id,
        name=data['name'],
        description=data.get('description', ''),
        topic_tags=data.get('topic_tags', []),
        tone_tags=data.get('tone_tags', []),
        structure_tags=data.get('structure_tags', []),
        visual_structure=data.get('visual_structure'),
        scene_blueprint=data.get('scene_blueprint'),
        is_ai_generated=False,
        is_public=True,
    )
    db.session.add(template)
    db.session.commit()

    return jsonify({'template': template.to_dict()}), 201


@community_bp.route('/api/community/templates/<int:template_id>/like', methods=['POST'])
def like_template(template_id):
    template = CommunityTemplate.query.get(template_id)
    if not template:
        return jsonify({'error': 'Template not found'}), 404

    template.like_count = (template.like_count or 0) + 1
    db.session.commit()

    return jsonify({'like_count': template.like_count})


@community_bp.route('/api/community/check-watermark-removal', methods=['POST'])
def check_watermark_removal():
    data = request.get_json()
    if not data or not data.get('project_id') or not data.get('edit_description'):
        return jsonify({'error': 'project_id and edit_description are required'}), 400

    project_id = data['project_id']
    edit_description = data['edit_description']

    project = Project.query.get(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    if not project.community_template_id:
        return jsonify({'can_remove': True, 'reason': 'No community template attached to this project.'})

    template = CommunityTemplate.query.get(project.community_template_id)
    if not template:
        return jsonify({'can_remove': True, 'reason': 'Original template no longer exists.'})

    if template.is_ai_generated:
        return jsonify({
            'can_remove': True,
            'reason': 'AI-generated templates (F/Echo) can always have their watermark removed.'
        })

    prompt = f"""Evaluate whether the following edit to a video project represents a meaningful structural change that transforms the work enough to remove the original creator's watermark attribution.

ORIGINAL TEMPLATE: {template.name}
TEMPLATE DESCRIPTION: {template.description or 'N/A'}
TEMPLATE STRUCTURE: {json.dumps(template.visual_structure) if template.visual_structure else 'N/A'}

USER'S EDIT DESCRIPTION: {edit_description}

A watermark can be removed ONLY if the edit is a "meaningful structural change that changes flow and can't be linked to the original."

This means:
- Simply changing colors, fonts, or text is NOT enough
- Rearranging scenes, adding new scenes, changing the narrative flow IS enough
- Creating a fundamentally different story structure IS enough
- Minor tweaks to timing or transitions is NOT enough

Output JSON:
{{
    "can_remove": true/false,
    "reason": "explanation of why the watermark can or cannot be removed"
}}"""

    try:
        result = call_ai(prompt, SYSTEM_GUARDRAILS, json_output=True, max_tokens=512)
        if result and isinstance(result, dict):
            return jsonify({
                'can_remove': bool(result.get('can_remove', False)),
                'reason': result.get('reason', 'Unable to determine.')
            })
    except Exception as e:
        logging.error(f"Watermark check AI call failed: {e}")

    return jsonify({
        'can_remove': False,
        'reason': 'Unable to evaluate edits at this time. Please try again.'
    })
