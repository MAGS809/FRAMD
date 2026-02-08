from flask import Blueprint, request, jsonify, session
from extensions import db
import logging

feedback_bp = Blueprint('feedback_bp', __name__)


def get_user_id():
    from flask_login import current_user
    if current_user.is_authenticated:
        return current_user.id
    return session.get('dev_user_id') or session.get('anonymous_user_id', 'anonymous')


@feedback_bp.route('/submit-feedback', methods=['POST'])
def submit_feedback():
    """Submit project feedback and get AI self-assessment."""
    from models import ProjectFeedback, AILearning, Project
    from flask_login import current_user
    import os
    from openai import OpenAI

    data = request.json
    project_id = data.get('project_id')

    user_id = None
    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('anonymous_user_id', 'anonymous')

    ratings_summary = []
    if data.get('script_rating'):
        ratings_summary.append(f"Script: {data['script_rating']}")
    if data.get('voice_rating'):
        ratings_summary.append(f"Voice: {data['voice_rating']}")
    if data.get('visuals_rating'):
        ratings_summary.append(f"Visuals: {data['visuals_rating']}")
    if data.get('soundfx_rating'):
        ratings_summary.append(f"Sound FX: {data['soundfx_rating']}")
    if data.get('overall_rating'):
        ratings_summary.append(f"Overall: {data['overall_rating']}")

    user_feedback = data.get('feedback_text', '')
    severity = data.get('severity', 'minor')
    script_used = data.get('script', '')

    try:
        client = OpenAI(
            api_key=os.environ.get("XAI_API_KEY"),
            base_url="https://api.x.ai/v1"
        )

        reflection_prompt = f"""You are Echo Engine, an AI that creates video content. A user just finished a project and gave you feedback.

User's Ratings:
{chr(10).join(ratings_summary) if ratings_summary else 'No specific ratings given'}

User's Notes:
{user_feedback if user_feedback else 'No additional notes'}

Severity Level: {severity}

Script Used:
{script_used[:500] if script_used else 'Not provided'}...

Based on this feedback, provide TWO things:

1. WHAT YOU LEARNED (2-3 sentences): Be specific and honest about what this teaches you about this user's preferences. Reference specific elements if possible.

2. WHAT TO IMPROVE (2-3 sentences): Be honest about weaknesses and what you'll do differently next time.

Also estimate how much you learned:
- If feedback was mostly positive with minor notes: LOW learning (2-3%)
- If feedback was mixed with specific critiques: MEDIUM learning (4-6%)  
- If feedback was critical with actionable insights: HIGH learning (7-10%)

Respond in this exact JSON format:
{{"learned": "Your honest reflection on what you learned...", "improve": "What you will do differently...", "learning_points": 5}}

Be genuine and humble. Don't be generic - reference specific aspects of THIS project."""

        response = client.chat.completions.create(
            model="grok-3-fast",
            messages=[{"role": "user", "content": reflection_prompt}],
            max_tokens=400
        )

        reflection_text = response.choices[0].message.content.strip()

        import json
        import re
        json_match = re.search(r'\{[\s\S]*\}', reflection_text)
        if json_match:
            reflection_data = json.loads(json_match.group())
            ai_learned = reflection_data.get('learned', 'I processed your feedback.')
            ai_to_improve = reflection_data.get('improve', 'I will apply these insights.')
        else:
            ai_learned = "I noted your feedback for future reference."
            ai_to_improve = "I'll work on being more aligned with your preferences."

    except Exception as e:
        print(f"AI reflection error: {e}")
        ai_learned = "I received your feedback and will learn from it."
        ai_to_improve = "I'll focus on improving based on your notes."

    import random
    if severity == 'critical':
        learning_points = random.randint(7, 10)
    elif severity == 'moderate':
        learning_points = random.randint(4, 6)
    else:
        learning_points = random.randint(2, 3)

    try:
        ai_learning = AILearning.query.filter_by(user_id=user_id).first()
        was_already_unlocked = False
        old_progress = 0

        if ai_learning:
            old_progress = ai_learning.learning_progress
            was_already_unlocked = ai_learning.can_auto_generate
        else:
            ai_learning = AILearning(
                user_id=user_id,
                total_projects=0,
                successful_projects=0,
                learning_progress=0,
                learned_hooks=[],
                learned_voices=[],
                learned_styles=[],
                learned_topics=[],
                can_auto_generate=False
            )
            db.session.add(ai_learning)

        ai_learning.total_projects += 1
        new_progress = min(ai_learning.learning_progress + learning_points, 100)
        ai_learning.learning_progress = new_progress

        if data.get('overall_rating') in ['great', 'ok']:
            ai_learning.successful_projects += 1

        can_auto_generate = (
            ai_learning.successful_projects >= 5 and 
            ai_learning.learning_progress >= 50
        )
        ai_learning.can_auto_generate = can_auto_generate

        feedback = ProjectFeedback(
            user_id=user_id,
            project_id=project_id if project_id else None,
            script_rating=data.get('script_rating'),
            voice_rating=data.get('voice_rating'),
            visuals_rating=data.get('visuals_rating'),
            soundfx_rating=data.get('soundfx_rating'),
            overall_rating=data.get('overall_rating'),
            feedback_text=user_feedback,
            severity=severity,
            ai_learned=ai_learned,
            ai_to_improve=ai_to_improve,
            learning_points_gained=learning_points
        )
        db.session.add(feedback)
        db.session.commit()

        return jsonify({
            'success': True,
            'ai_learned': ai_learned,
            'ai_to_improve': ai_to_improve,
            'learning_points_gained': learning_points,
            'old_progress': old_progress,
            'new_progress': new_progress,
            'can_auto_generate': can_auto_generate,
            'was_already_unlocked': was_already_unlocked
        })

    except Exception as e:
        print(f"Feedback save error: {e}")
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': 'Failed to save feedback to database',
            'ai_learned': ai_learned,
            'ai_to_improve': ai_to_improve,
            'learning_points_gained': 0
        }), 500


@feedback_bp.route('/video-feedback', methods=['POST'])
def video_feedback():
    """Save video like/dislike feedback."""
    from models import VideoFeedback, Project, AILearning, GlobalPattern
    from flask_login import current_user

    data = request.json
    project_id = data.get('project_id')
    liked = data.get('liked')
    comment = data.get('comment')
    script = data.get('script', '')
    revision_number = data.get('revision_number', 0)

    user_id = None
    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('anonymous_user_id', 'anonymous')

    try:
        feedback = VideoFeedback(
            project_id=project_id if project_id else None,
            user_id=user_id,
            liked=liked,
            comment=comment,
            script_version=script[:2000] if script else None,
            revision_number=revision_number
        )
        db.session.add(feedback)

        if project_id:
            project = Project.query.get(project_id)
            if project:
                project.liked = liked
                project.revision_count = revision_number
                if liked:
                    project.is_successful = True
                    project.success_score = max(project.success_score, 80)

        ai_learning = AILearning.query.filter_by(user_id=user_id).first()
        if ai_learning:
            if liked:
                ai_learning.successful_projects += 1
                ai_learning.learning_progress = min(ai_learning.learning_progress + 3, 100)
            else:
                ai_learning.learning_progress = min(ai_learning.learning_progress + 5, 100)

        if liked:
            pattern = GlobalPattern.query.filter_by(pattern_type='like_rate').first()
            if pattern:
                pattern.success_count += 1
                pattern.usage_count += 1
                pattern.success_rate = pattern.success_count / max(pattern.usage_count, 1)
            else:
                pattern = GlobalPattern(
                    pattern_type='like_rate',
                    pattern_data={'description': 'Video like/dislike ratio'},
                    success_count=1,
                    usage_count=1,
                    success_rate=1.0
                )
                db.session.add(pattern)

            if revision_number > 0:
                prev_feedback = VideoFeedback.query.filter_by(
                    project_id=project_id,
                    user_id=user_id,
                    liked=False
                ).order_by(VideoFeedback.created_at.desc()).first()

                if prev_feedback and prev_feedback.ai_analysis:
                    pattern_type = prev_feedback.ai_analysis.get('pattern')
                    if pattern_type:
                        feedback_pattern = GlobalPattern.query.filter_by(
                            pattern_type=f"feedback_{pattern_type}"
                        ).first()
                        if feedback_pattern:
                            feedback_pattern.success_count += 1
                            feedback_pattern.success_rate = feedback_pattern.success_count / max(feedback_pattern.usage_count, 1)
        else:
            pattern = GlobalPattern.query.filter_by(pattern_type='like_rate').first()
            if pattern:
                pattern.usage_count += 1
                pattern.success_rate = pattern.success_count / max(pattern.usage_count, 1)

        db.session.commit()

        return jsonify({'success': True})

    except Exception as e:
        print(f"Video feedback error: {e}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@feedback_bp.route('/refine-from-feedback', methods=['POST'])
def refine_from_feedback():
    """Refine script based on user feedback using AI."""
    from models import VideoFeedback, Project, Subscription, GlobalPattern
    from flask_login import current_user
    import os
    from openai import OpenAI

    data = request.json
    project_id = data.get('project_id')
    script = data.get('script', '')
    feedback = data.get('feedback', '')

    user_id = None
    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('anonymous_user_id', 'anonymous')

    MAX_FREE_REVISIONS = 3
    revision_number = 1

    if project_id:
        project = Project.query.get(project_id)
        if project:
            revision_number = (project.revision_count or 0) + 1

    is_pro = False
    if user_id:
        sub = Subscription.query.filter_by(user_id=user_id).first()
        is_pro = sub and sub.is_active()

    if not is_pro and revision_number > MAX_FREE_REVISIONS:
        return jsonify({
            'success': False,
            'error': 'Revision limit reached. Upgrade to Pro for unlimited revisions.',
            'requires_subscription': True,
            'revisions_used': revision_number - 1,
            'max_revisions': MAX_FREE_REVISIONS
        }), 403

    if not script:
        return jsonify({'error': 'No script to refine'}), 400

    if not feedback:
        return jsonify({'error': 'No feedback provided'}), 400

    try:
        client = OpenAI(
            api_key=os.environ.get("XAI_API_KEY"),
            base_url="https://api.x.ai/v1"
        )

        past_feedbacks = VideoFeedback.query.filter_by(user_id=user_id, liked=False).order_by(VideoFeedback.created_at.desc()).limit(5).all()
        past_feedback_summary = ""
        if past_feedbacks:
            past_feedback_summary = "\n".join([f"- {fb.comment}" for fb in past_feedbacks if fb.comment])

        successful_patterns = GlobalPattern.query.filter(
            GlobalPattern.pattern_type.like('feedback_%'),
            GlobalPattern.success_rate > 0.5
        ).order_by(GlobalPattern.success_rate.desc()).limit(3).all()

        pattern_insights = ""
        if successful_patterns:
            pattern_insights = "LEARNED PATTERNS THAT WORK:\n" + "\n".join([
                f"- When users complain about '{p.pattern_type.replace('feedback_', '')}', fixes that address it directly have {int(p.success_rate * 100)}% success rate"
                for p in successful_patterns
            ])

        refine_prompt = f"""You are Krakd — a script refinement engine. The user disliked their video and provided specific feedback.

ORIGINAL SCRIPT:
{script}

USER'S FEEDBACK (what they want fixed):
{feedback}

PREVIOUS FEEDBACK FROM THIS USER (patterns to learn from):
{past_feedback_summary if past_feedback_summary else 'No previous feedback'}

{pattern_insights}

REVISION NUMBER: {revision_number}

YOUR TASK:
1. Analyze the user's feedback carefully
2. Identify the specific problems they mentioned
3. Revise the script to address EXACTLY what they asked for
4. Keep the core thesis and structure intact unless they asked to change it
5. Make targeted improvements, not complete rewrites

RULES:
- If they say "too slow" → tighten dialogue, cut filler
- If they say "too robotic" → make dialogue more conversational and natural
- If they say "wrong tone" → adjust the voice/style
- If they say "visuals don't match" → update VISUAL tags
- Be specific with your changes

Output the refined script in the same format as the original (plain text screenplay format).
Do NOT explain what you changed — just output the refined script."""

        response = client.chat.completions.create(
            model="grok-3",
            messages=[{"role": "user", "content": refine_prompt}],
            max_tokens=2048
        )

        refined_script = response.choices[0].message.content.strip()

        analysis_prompt = f"""Based on this feedback: "{feedback}"
And this script revision, briefly summarize in JSON:
{{"issue": "one line describing the main problem", "fix_applied": "one line describing the fix", "pattern": "one word category like 'pacing', 'tone', 'visuals', 'dialogue'"}}"""

        analysis_response = client.chat.completions.create(
            model="grok-3-fast",
            messages=[{"role": "user", "content": analysis_prompt}],
            max_tokens=200
        )

        import json
        import re
        analysis_text = analysis_response.choices[0].message.content.strip()
        json_match = re.search(r'\{[\s\S]*\}', analysis_text)
        ai_analysis = {}
        if json_match:
            try:
                ai_analysis = json.loads(json_match.group())
            except:
                ai_analysis = {'issue': feedback, 'fix_applied': 'Script refined', 'pattern': 'general'}

        last_feedback = VideoFeedback.query.filter_by(
            project_id=project_id,
            user_id=user_id
        ).order_by(VideoFeedback.created_at.desc()).first()

        if last_feedback:
            last_feedback.ai_analysis = ai_analysis

        if project_id:
            project = Project.query.get(project_id)
            if project:
                project.script = refined_script
                project.revision_count = revision_number

        if ai_analysis.get('pattern'):
            pattern = GlobalPattern.query.filter_by(
                pattern_type=f"feedback_{ai_analysis['pattern']}"
            ).first()
            if pattern:
                pattern.usage_count += 1
            else:
                pattern = GlobalPattern(
                    pattern_type=f"feedback_{ai_analysis['pattern']}",
                    pattern_data={'description': f"Common feedback: {ai_analysis['pattern']}"},
                    success_count=0,
                    usage_count=1,
                    success_rate=0.0
                )
                db.session.add(pattern)

        db.session.commit()

        return jsonify({
            'success': True,
            'refined_script': refined_script,
            'ai_message': f"I adjusted the script based on your feedback about {ai_analysis.get('pattern', 'the content')}. Review it and regenerate when ready.",
            'analysis': ai_analysis,
            'revision_number': revision_number
        })

    except Exception as e:
        print(f"Refinement error: {e}")
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@feedback_bp.route('/ai-improvement-stats', methods=['GET'])
def ai_improvement_stats():
    """Get AI improvement statistics."""
    from models import GlobalPattern, VideoFeedback

    try:
        like_pattern = GlobalPattern.query.filter_by(pattern_type='like_rate').first()
        like_rate = like_pattern.success_rate if like_pattern else 0.0
        total_feedbacks = like_pattern.usage_count if like_pattern else 0

        feedback_patterns = GlobalPattern.query.filter(
            GlobalPattern.pattern_type.like('feedback_%')
        ).order_by(GlobalPattern.usage_count.desc()).limit(5).all()

        patterns = [{
            'type': p.pattern_type.replace('feedback_', ''),
            'count': p.usage_count,
            'description': p.pattern_data.get('description', '')
        } for p in feedback_patterns]

        return jsonify({
            'success': True,
            'like_rate': round(like_rate * 100, 1),
            'total_feedbacks': total_feedbacks,
            'common_issues': patterns
        })

    except Exception as e:
        print(f"Stats error: {e}")
        return jsonify({'error': str(e)}), 500


@feedback_bp.route('/record-video-feedback', methods=['POST'])
def record_video_feedback():
    """Record video feedback for AI learning."""
    from models import VideoFeedback, VisualLearning
    from flask_login import current_user

    user_id = None
    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('dev_user_id')

    if not user_id:
        return jsonify({'success': False, 'error': 'User not authenticated'})

    data = request.get_json()
    feedback_type = data.get('feedback_type', 'unknown')
    details = data.get('details', '')
    video_data = data.get('video_data', {})

    try:
        feedback = VideoFeedback(
            user_id=user_id,
            liked=(feedback_type == 'positive'),
            comment=f"{feedback_type}: {details}",
            revision_number=video_data.get('revision_count', 0)
        )
        db.session.add(feedback)

        content_type = video_data.get('content_type', 'general')
        if content_type and feedback_type == 'positive':
            learning = VisualLearning(
                content_type=content_type,
                scene_position='general',
                source_type=video_data.get('source_type', 'mixed'),
                feedback='positive',
                scene_text_sample=details[:200] if details else ''
            )
            db.session.add(learning)

        db.session.commit()

        return jsonify({'success': True})
    except Exception as e:
        print(f"[record-video-feedback] Error: {e}")
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)})
