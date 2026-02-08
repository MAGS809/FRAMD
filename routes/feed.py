from flask import Blueprint, request, jsonify, session, render_template
from extensions import db
import os
import json
import logging

feed_bp = Blueprint('feed_bp', __name__)


def get_user_id():
    from flask_login import current_user
    if current_user.is_authenticated:
        return current_user.id
    return session.get('dev_user_id')


@feed_bp.route('/host-video', methods=['POST'])
def host_video():
    """Host a video with a public shareable URL (Pro subscribers only)."""
    import uuid
    from models import Subscription, HostedVideo
    from flask_login import current_user

    user_id = get_user_id()

    sub = Subscription.query.filter_by(user_id=user_id).first() if user_id else None
    if not sub or not sub.is_active():
        return jsonify({
            'error': 'Pro subscription required',
            'requires_subscription': True
        }), 403

    data = request.get_json()
    video_path = data.get('video_path')
    title = data.get('title', 'Untitled Video')
    project_id = data.get('project_id')

    if not video_path:
        return jsonify({'error': 'Video path required'}), 400

    public_id = uuid.uuid4().hex[:12]

    hosted = HostedVideo(
        user_id=user_id,
        project_id=project_id,
        title=title,
        public_id=public_id,
        video_path=video_path
    )
    db.session.add(hosted)
    db.session.commit()

    domains = os.environ.get('REPLIT_DOMAINS', 'localhost:5000')
    domain = domains.split(',')[0] if domains else 'localhost:5000'
    protocol = 'https' if 'replit' in domain else 'http'

    return jsonify({
        'success': True,
        'public_id': public_id,
        'share_url': f'{protocol}://{domain}/v/{public_id}',
        'title': title
    })


@feed_bp.route('/my-hosted-videos', methods=['GET'])
def my_hosted_videos():
    """Get list of user's hosted videos."""
    from models import HostedVideo

    user_id = get_user_id()

    if not user_id:
        return jsonify({'videos': []})

    videos = HostedVideo.query.filter_by(user_id=user_id).order_by(HostedVideo.created_at.desc()).all()

    domains = os.environ.get('REPLIT_DOMAINS', 'localhost:5000')
    domain = domains.split(',')[0] if domains else 'localhost:5000'
    protocol = 'https' if 'replit' in domain else 'http'

    return jsonify({
        'videos': [{
            'id': v.id,
            'title': v.title,
            'public_id': v.public_id,
            'share_url': f'{protocol}://{domain}/v/{v.public_id}',
            'views': v.views,
            'is_public': v.is_public,
            'created_at': v.created_at.isoformat()
        } for v in videos]
    })


@feed_bp.route('/v/<public_id>')
def view_hosted_video(public_id):
    """Public video view page."""
    from models import HostedVideo

    video = HostedVideo.query.filter_by(public_id=public_id, is_public=True).first()
    if not video:
        return "Video not found", 404

    video.views += 1
    db.session.commit()

    return render_template('video_view.html', video=video)


@feed_bp.route('/feed/items', methods=['GET'])
def get_feed_items():
    """Get AI-generated content for the swipe feed."""
    from models import FeedItem, SwipeFeedback
    from sqlalchemy import or_

    user_id = get_user_id()

    already_swiped = []
    if user_id:
        already_swiped = [f.feed_item_id for f in SwipeFeedback.query.filter_by(user_id=user_id).all()]

    query = FeedItem.query
    if user_id:
        query = query.filter(or_(FeedItem.is_global == True, FeedItem.user_id == user_id))
    else:
        query = query.filter(FeedItem.is_global == True)

    if already_swiped:
        query = query.filter(FeedItem.id.notin_(already_swiped))

    items = query.order_by(FeedItem.created_at.desc()).limit(20).all()

    return jsonify({
        'items': [{
            'id': item.id,
            'content_type': item.content_type,
            'title': item.title,
            'script': item.script,
            'visual_preview': item.visual_preview,
            'video_path': item.video_path,
            'topic': item.topic,
            'hook_style': item.hook_style,
            'voice_style': item.voice_style
        } for item in items]
    })


@feed_bp.route('/feed/generate', methods=['POST'])
def generate_feed_content():
    """Generate AI content for the feed based on user's existing projects."""
    from models import FeedItem, AILearning, Project
    from openai import OpenAI

    user_id = get_user_id()

    data = request.get_json() or {}
    topic = data.get('topic', '')

    user_projects = []
    if user_id:
        projects = Project.query.filter_by(user_id=user_id).order_by(Project.updated_at.desc()).limit(5).all()
        for p in projects:
            if p.script:
                user_projects.append({
                    'title': p.title,
                    'script': p.script[:500]
                })

    user_preferences = None
    if user_id:
        learning = AILearning.query.filter_by(user_id=user_id).first()
        if learning:
            user_preferences = {
                'hooks': learning.learned_hooks,
                'voices': learning.learned_voices,
                'styles': learning.learned_styles,
                'topics': learning.learned_topics
            }

    recent_feedback = None
    if user_id:
        from models import SwipeFeedback
        feedback_entries = SwipeFeedback.query.filter(
            SwipeFeedback.user_id == user_id,
            SwipeFeedback.feedback_text != None,
            SwipeFeedback.feedback_text != ''
        ).order_by(SwipeFeedback.created_at.desc()).limit(5).all()
        if feedback_entries:
            recent_feedback = [f.feedback_text for f in feedback_entries]

    try:
        if user_projects:
            system_prompt = """You are a short-form video script generator. Based on the user's existing projects and style, create a NEW script idea that matches their voice and interests.

The user has created these projects:
""" + "\n".join([f"- {p['title']}: {p['script'][:200]}..." for p in user_projects[:3]])

            system_prompt += """

Create a fresh script idea inspired by their style but on a new angle or topic.

Return JSON with:
- title: Catchy title (max 60 chars)
- script: The full script with clear hooks and pacing
- hook_style: The hook type used (question, stat, story, controversy)
- topic: The main topic category
- inspiration: Brief note on which project inspired this"""
        else:
            system_prompt = """You are a short-form video script generator. Create a punchy, engaging script for a 30-60 second video.
        
Return JSON with:
- title: Catchy title (max 60 chars)
- script: The full script with clear hooks and pacing
- hook_style: The hook type used (question, stat, story, controversy)
- topic: The main topic category"""

        personalization_notes = []
        if user_preferences:
            if user_preferences.get('hooks'):
                personalization_notes.append(f"Hook styles they like: {', '.join(user_preferences['hooks'][:3])}")
            if user_preferences.get('topics'):
                personalization_notes.append(f"Topics they enjoy: {', '.join(user_preferences['topics'][:3])}")
            if user_preferences.get('voices'):
                personalization_notes.append(f"Voice styles they prefer: {', '.join(user_preferences['voices'][:3])}")
            if user_preferences.get('styles'):
                personalization_notes.append(f"Content styles they like: {', '.join(user_preferences['styles'][:3])}")

        if recent_feedback:
            personalization_notes.append(f"Recent feedback on content: {'; '.join(recent_feedback[:3])}")

        if personalization_notes:
            system_prompt += "\n\nUser preferences to incorporate:\n" + "\n".join(personalization_notes)

        prompt_message = "Create a new script idea" if user_projects else "Create a viral short-form script about: trending news"

        xai_client = OpenAI(
            api_key=os.environ.get("XAI_API_KEY"),
            base_url="https://api.x.ai/v1"
        )

        response = xai_client.chat.completions.create(
            model="grok-3",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt_message}
            ],
            response_format={"type": "json_object"}
        )

        result = json.loads(response.choices[0].message.content)

        feed_item = FeedItem(
            user_id=user_id,
            content_type='script',
            title=result.get('title', topic)[:255],
            script=result.get('script', ''),
            topic=result.get('topic', topic)[:100],
            hook_style=result.get('hook_style', 'question')[:50],
            is_global=user_id is None
        )
        db.session.add(feed_item)
        db.session.commit()

        return jsonify({
            'success': True,
            'item': {
                'id': feed_item.id,
                'title': feed_item.title,
                'script': feed_item.script,
                'topic': feed_item.topic,
                'hook_style': feed_item.hook_style
            }
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@feed_bp.route('/feed/swipe', methods=['POST'])
def record_swipe():
    """Record a swipe action (like/skip) and optional feedback."""
    from models import SwipeFeedback, FeedItem, AILearning

    user_id = get_user_id()

    if not user_id:
        return jsonify({'error': 'User required'}), 401

    data = request.get_json()
    item_id = data.get('item_id')
    action = data.get('action')
    feedback_text = data.get('feedback', '')

    if not item_id or action not in ['like', 'skip']:
        return jsonify({'error': 'Invalid swipe data'}), 400

    item = FeedItem.query.get(item_id)
    if not item:
        return jsonify({'error': 'Item not found'}), 404

    feedback = SwipeFeedback(
        user_id=user_id,
        feed_item_id=item_id,
        action=action,
        feedback_text=feedback_text
    )
    db.session.add(feedback)

    if action == 'like':
        learning = AILearning.query.filter_by(user_id=user_id).first()
        if not learning:
            learning = AILearning(user_id=user_id)
            db.session.add(learning)

        if item.hook_style and item.hook_style not in (learning.learned_hooks or []):
            hooks = learning.learned_hooks or []
            hooks.append(item.hook_style)
            learning.learned_hooks = hooks[-10:]

        if item.topic and item.topic not in (learning.learned_topics or []):
            topics = learning.learned_topics or []
            topics.append(item.topic)
            learning.learned_topics = topics[-10:]

    db.session.commit()

    return jsonify({'success': True, 'action': action})


@feed_bp.route('/feed/liked', methods=['GET'])
def get_liked_items():
    """Get user's liked feed items."""
    from models import SwipeFeedback, FeedItem

    user_id = get_user_id()

    if not user_id:
        return jsonify({'items': []})

    liked = SwipeFeedback.query.filter_by(user_id=user_id, action='like').order_by(SwipeFeedback.created_at.desc()).all()
    item_ids = [l.feed_item_id for l in liked]
    items = FeedItem.query.filter(FeedItem.id.in_(item_ids)).all() if item_ids else []

    return jsonify({
        'items': [{
            'id': item.id,
            'title': item.title,
            'script': item.script,
            'topic': item.topic,
            'hook_style': item.hook_style
        } for item in items]
    })
