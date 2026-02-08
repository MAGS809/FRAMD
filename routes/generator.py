from flask import Blueprint, request, jsonify, session
from extensions import db
import os
import json
import logging

generator_bp = Blueprint('generator_bp', __name__)


def get_user_id():
    from flask_login import current_user
    if current_user.is_authenticated:
        return current_user.id
    if session.get('dev_mode'):
        return 'dev_user'
    return session.get('dev_user_id')


@generator_bp.route('/generator-settings', methods=['GET', 'POST'])
def generator_settings():
    """Get or update user generator settings for auto-generation."""
    from models import GeneratorSettings
    from flask_login import current_user

    user_id = None
    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('dev_user_id')

    if not user_id:
        return jsonify({'error': 'Authentication required'}), 401

    if request.method == 'GET':
        settings = GeneratorSettings.query.filter_by(user_id=user_id).first()
        if not settings:
            return jsonify({
                'success': True,
                'settings': {
                    'tone': 'neutral',
                    'format_type': 'explainer',
                    'target_length': 45,
                    'voice_style': 'news_anchor',
                    'enabled_topics': [],
                    'auto_enabled': False
                }
            })
        return jsonify({
            'success': True,
            'settings': {
                'tone': settings.tone,
                'format_type': settings.format_type,
                'target_length': settings.target_length,
                'voice_style': settings.voice_style,
                'enabled_topics': settings.enabled_topics or [],
                'auto_enabled': settings.auto_enabled
            }
        })

    data = request.get_json()
    settings = GeneratorSettings.query.filter_by(user_id=user_id).first()
    if not settings:
        settings = GeneratorSettings(user_id=user_id)
        db.session.add(settings)

    if 'tone' in data:
        settings.tone = data['tone']
    if 'format_type' in data:
        settings.format_type = data['format_type']
    if 'target_length' in data:
        settings.target_length = max(35, min(75, int(data['target_length'])))
    if 'voice_style' in data:
        settings.voice_style = data['voice_style']
    if 'enabled_topics' in data:
        settings.enabled_topics = data['enabled_topics']
    if 'auto_enabled' in data:
        settings.auto_enabled = data['auto_enabled']

    db.session.commit()

    return jsonify({
        'success': True,
        'message': 'Settings updated',
        'settings': {
            'tone': settings.tone,
            'format_type': settings.format_type,
            'target_length': settings.target_length,
            'voice_style': settings.voice_style,
            'enabled_topics': settings.enabled_topics or [],
            'auto_enabled': settings.auto_enabled
        }
    })


@generator_bp.route('/generator-confidence', methods=['GET'])
def generator_confidence():
    """Calculate AI confidence for auto-generation based on liked videos."""
    from models import Project, AILearning, GlobalPattern, VideoFeedback

    UNLOCK_THRESHOLD = 5

    user_id = get_user_id()
    if not user_id:
        return jsonify({'error': 'Authentication required'}), 401

    try:
        liked_count = VideoFeedback.query.filter_by(user_id=user_id, liked=True).count()
        total_with_feedback = VideoFeedback.query.filter_by(user_id=user_id).count()

        success_rate = (liked_count / total_with_feedback * 100) if total_with_feedback > 0 else 0

        is_unlocked = liked_count >= UNLOCK_THRESHOLD

        if is_unlocked:
            progress_message = "Auto-Generate unlocked!"
        else:
            remaining = UNLOCK_THRESHOLD - liked_count
            progress_message = f"{liked_count}/{UNLOCK_THRESHOLD} videos liked to unlock"

        learned_patterns = GlobalPattern.query.filter(
            GlobalPattern.success_count > 0
        ).count()

        return jsonify({
            'success': True,
            'liked_count': liked_count,
            'total_projects': total_with_feedback,
            'success_rate': round(success_rate, 1),
            'unlock_threshold': UNLOCK_THRESHOLD,
            'is_unlocked': is_unlocked,
            'progress_message': progress_message,
            'learned_patterns': learned_patterns,
            'confidence_score': min(100, (liked_count / UNLOCK_THRESHOLD) * 100)
        })

    except Exception as e:
        print(f"Confidence calculation error: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@generator_bp.route('/auto-generate', methods=['POST'])
def auto_generate():
    """Auto-generate content using learned patterns, user settings, and template-specific styling."""
    from models import Project, GeneratorSettings, GlobalPattern, AILearning, VideoFeedback
    from flask_login import current_user
    from context_engine import get_template_guidelines, research_trends, TEMPLATE_VISUAL_FX

    UNLOCK_THRESHOLD = 5

    user_id = None
    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('dev_user_id')

    if not user_id:
        return jsonify({'error': 'Authentication required'}), 401

    liked_count = VideoFeedback.query.filter_by(user_id=user_id, liked=True).count()
    if liked_count < UNLOCK_THRESHOLD:
        return jsonify({
            'error': 'Auto-generation not unlocked',
            'message': f'Need {UNLOCK_THRESHOLD - liked_count} more liked videos to unlock',
            'requires_unlock': True
        }), 403

    settings = GeneratorSettings.query.filter_by(user_id=user_id).first()
    if not settings:
        settings = GeneratorSettings(user_id=user_id)

    successful_patterns = GlobalPattern.query.filter(
        GlobalPattern.success_rate > 0.5
    ).order_by(GlobalPattern.success_rate.desc()).limit(5).all()

    pattern_hints = []
    for p in successful_patterns:
        if p.pattern_data.get('description'):
            pattern_hints.append(p.pattern_data['description'])

    ai_learning = AILearning.query.filter_by(user_id=user_id).first()
    learned_hooks = ai_learning.learned_hooks if ai_learning else []
    learned_styles = ai_learning.learned_styles if ai_learning else []

    data = request.get_json() or {}
    topic = data.get('topic', '')
    template_type = data.get('template', 'start_from_scratch')

    template_dna = get_template_guidelines(template_type)

    trend_data = None
    try:
        trend_data = research_trends(topic or 'general content creation')
    except Exception as e:
        logging.warning(f"Trend research failed in auto-generate: {e}")
        trend_data = {'hooks': [], 'formats': [], 'visuals': [], 'sounds': []}

    prompt = f"""Generate a complete short-form video script based on user preferences, learned patterns, and current trends.

TEMPLATE: {template_type.upper().replace('_', ' ')}
TEMPLATE TONE: {template_dna['tone']}
TEMPLATE VOICE: {template_dna['voice']}
TEMPLATE HOOK STYLE: {template_dna['hook_style']}
TEMPLATE PACING: {template_dna['pacing']}
HOW TO APPLY TRENDS: {template_dna['trend_application']}

USER SETTINGS:
- Tone: {settings.tone}
- Format: {settings.format_type}
- Target Length: {settings.target_length} seconds
- Voice Style: {settings.voice_style}
- Preferred Topics: {', '.join(settings.enabled_topics) if settings.enabled_topics else 'General'}

CURRENT TRENDS (apply within template tone):
{json.dumps(trend_data, indent=2) if trend_data else 'No trend data available'}

LEARNED PATTERNS (from previous successful content):
{chr(10).join(f'- {hint}' for hint in pattern_hints[:3]) if pattern_hints else '- No specific patterns learned yet'}

LEARNED HOOKS: {', '.join(learned_hooks[:3]) if learned_hooks else 'None'}
LEARNED STYLES: {', '.join(learned_styles[:3]) if learned_styles else 'None'}

TOPIC/IDEA: {topic if topic else 'Generate based on user preferences and trending topics'}

Generate a complete {settings.target_length}-second video script following the thesis-driven anchor structure:
1. HOOK - {template_dna['hook_style']} opener that grabs attention
2. CLAIM - Core thesis statement
3. EVIDENCE - Supporting points (2-3 max)
4. PIVOT - Unexpected angle or reframe
5. CLOSER - Return to thesis with impact

CRITICAL: Stay in the {template_type.replace('_', ' ')} template voice. Trends inform HOW you execute, not WHAT tone you use.

Output the script with clear character lines formatted as:
[CHARACTER]: dialogue

Include [PAUSE] and [BEAT] markers for pacing.

Also output a visual plan and sound plan as JSON:
{{
  "script": "The full script with speaker labels",
  "visual_plan": [{{"scene": 1, "description": "...", "timing": "0-5s"}}],
  "sound_plan": {{"music_vibe": "...", "sfx_suggestions": ["..."]}}
}}
"""

    try:
        xai_api_key = os.environ.get('XAI_API_KEY')
        if not xai_api_key:
            return jsonify({'error': 'AI configuration error'}), 500

        from openai import OpenAI
        client = OpenAI(
            api_key=xai_api_key,
            base_url="https://api.x.ai/v1"
        )

        response = client.chat.completions.create(
            model="grok-3",
            messages=[
                {"role": "system", "content": "You are Krakd, a thesis-driven content engine. Generate clear, honest, human-feeling scripts that respect complexity. Prioritize clarity, integrity, and resonance over virality."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=2000
        )

        generated_response = response.choices[0].message.content

        script_text = generated_response
        visual_plan = []
        sound_plan = {}

        try:
            if '```json' in generated_response:
                json_str = generated_response.split('```json')[1].split('```')[0]
            elif '```' in generated_response:
                json_str = generated_response.split('```')[1].split('```')[0]
            else:
                json_str = generated_response

            parsed = json.loads(json_str.strip())
            script_text = parsed.get('script', generated_response)
            visual_plan = parsed.get('visual_plan', [])
            sound_plan = parsed.get('sound_plan', {})
        except (json.JSONDecodeError, IndexError):
            script_text = generated_response

        template_fx = TEMPLATE_VISUAL_FX.get(template_type, TEMPLATE_VISUAL_FX['start_from_scratch'])

        project = Project(
            user_id=user_id,
            name=f"Auto-Generated: {topic[:50]}" if topic else "Auto-Generated Content",
            description="Generated using AI learning, trends, and user preferences",
            script=script_text,
            template_type=template_type,
            visual_plan=visual_plan,
            sound_plan=sound_plan,
            status='draft',
            workflow_step=3
        )
        db.session.add(project)
        db.session.commit()

        return jsonify({
            'success': True,
            'project_id': project.id,
            'script': script_text,
            'visual_plan': visual_plan,
            'sound_plan': sound_plan,
            'template': {
                'type': template_type,
                'dna': template_dna,
                'visual_fx': template_fx
            },
            'settings_used': {
                'tone': settings.tone,
                'format_type': settings.format_type,
                'target_length': settings.target_length,
                'voice_style': settings.voice_style
            },
            'trends_applied': bool(trend_data and (trend_data.get('hooks') or trend_data.get('formats'))),
            'patterns_applied': len(pattern_hints)
        })

    except Exception as e:
        print(f"Auto-generate error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
