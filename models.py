from datetime import datetime
from extensions import db
from flask_dance.consumer.storage.sqla import OAuthConsumerMixin
from flask_login import UserMixin
from sqlalchemy import UniqueConstraint, Index

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.String, primary_key=True)
    email = db.Column(db.String, unique=True, nullable=True)
    first_name = db.Column(db.String, nullable=True)
    last_name = db.Column(db.String, nullable=True)
    profile_image_url = db.Column(db.String, nullable=True)
    tokens = db.Column(db.Integer, default=120)
    free_video_generations = db.Column(db.Integer, default=1)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    conversations = db.relationship('Conversation', backref='user', lazy='dynamic')
    preferences = db.relationship('UserPreference', backref='user', uselist=False)

class OAuth(OAuthConsumerMixin, db.Model):
    user_id = db.Column(db.String, db.ForeignKey(User.id))
    browser_session_key = db.Column(db.String, nullable=False)
    user = db.relationship(User)
    __table_args__ = (UniqueConstraint(
        'user_id',
        'browser_session_key',
        'provider',
        name='uq_user_browser_session_key_provider',
    ),)

class Conversation(db.Model):
    __tablename__ = 'conversations'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String, db.ForeignKey('users.id'), nullable=False, index=True)
    role = db.Column(db.String(20), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)

class UserPreference(db.Model):
    __tablename__ = 'user_preferences'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String, db.ForeignKey('users.id'), unique=True, nullable=False)
    preferred_voice = db.Column(db.String(50), default='news_anchor')
    preferred_format = db.Column(db.String(20), default='9:16')
    style_preferences = db.Column(db.JSON, default={})
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)


class Project(db.Model):
    __tablename__ = 'projects'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String, db.ForeignKey('users.id'), nullable=False, index=True)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(50), default='draft')
    script = db.Column(db.Text, nullable=True)
    visual_plan = db.Column(db.JSON, nullable=True)
    sound_plan = db.Column(db.JSON, nullable=True)  # Music/FX suggestions from AI generation
    voice_assignments = db.Column(db.JSON, nullable=True)
    caption_settings = db.Column(db.JSON, nullable=True)
    video_path = db.Column(db.String(500), nullable=True)
    workflow_step = db.Column(db.Integer, default=1)  # 1-8 workflow progress
    is_successful = db.Column(db.Boolean, default=False)
    success_score = db.Column(db.Integer, default=0)
    revision_count = db.Column(db.Integer, default=0)  # Track revision attempts
    liked = db.Column(db.Boolean, nullable=True)  # True=liked, False=disliked, None=no feedback
    auto_generate_enabled = db.Column(db.Boolean, default=False)  # AI auto-generation toggle
    uploaded_clips = db.Column(db.JSON, nullable=True)  # List of clip paths for AI to use
    template_type = db.Column(db.String(50), default='start_from_scratch')  # Template used for this project
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    user = db.relationship('User', backref=db.backref('projects', lazy='dynamic'))
    feedbacks = db.relationship('VideoFeedback', backref='project', lazy='dynamic')
    generated_drafts = db.relationship('GeneratedDraft', backref='project', lazy='dynamic')


class VideoFeedback(db.Model):
    __tablename__ = 'video_feedbacks'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=True, index=True)
    user_id = db.Column(db.String, db.ForeignKey('users.id'), nullable=False, index=True)
    liked = db.Column(db.Boolean, nullable=False)  # True=liked, False=disliked
    comment = db.Column(db.Text, nullable=True)  # User's feedback comment
    script_version = db.Column(db.Text, nullable=True)  # Script at time of feedback
    revision_number = db.Column(db.Integer, default=0)  # Which revision this is
    ai_analysis = db.Column(db.JSON, nullable=True)  # AI's self-analysis of what went wrong
    created_at = db.Column(db.DateTime, default=datetime.now)
    
    user = db.relationship('User', backref=db.backref('video_feedbacks', lazy='dynamic'))


class AILearning(db.Model):
    __tablename__ = 'ai_learning'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String, db.ForeignKey('users.id'), nullable=False, index=True)
    total_projects = db.Column(db.Integer, default=0)
    successful_projects = db.Column(db.Integer, default=0)
    learning_progress = db.Column(db.Integer, default=0)
    learned_hooks = db.Column(db.JSON, default=list)
    learned_voices = db.Column(db.JSON, default=list)
    learned_styles = db.Column(db.JSON, default=list)
    learned_topics = db.Column(db.JSON, default=list)
    can_auto_generate = db.Column(db.Boolean, default=False)
    daily_draft_limit = db.Column(db.Integer, default=3)  # 1-10 configurable limit
    drafts_generated_today = db.Column(db.Integer, default=0)
    last_draft_reset = db.Column(db.Date, default=datetime.now().date)
    dislike_learnings = db.Column(db.JSON, default=list)  # AI's internal analysis of why drafts failed
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    user = db.relationship('User', backref=db.backref('ai_learning', uselist=False))


class GeneratedDraft(db.Model):
    __tablename__ = 'generated_drafts'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False, index=True)
    user_id = db.Column(db.String, db.ForeignKey('users.id'), nullable=False, index=True)
    script = db.Column(db.Text, nullable=False)
    visual_plan = db.Column(db.JSON, nullable=True)
    sound_plan = db.Column(db.JSON, nullable=True)  # Music/FX suggestions from trend research
    status = db.Column(db.String(20), default='pending')  # pending, approved, skipped
    angle_used = db.Column(db.String(100), nullable=True)  # contrarian, evidence-first, story, etc.
    vibe_used = db.Column(db.String(100), nullable=True)  # serious, playful, urgent, reflective
    hook_type = db.Column(db.String(100), nullable=True)  # question, statistic, bold claim
    clips_used = db.Column(db.JSON, nullable=True)  # Which uploaded clips were used
    trend_data = db.Column(db.JSON, nullable=True)  # Trend research that informed this draft
    created_at = db.Column(db.DateTime, default=datetime.now)
    
    user = db.relationship('User', backref=db.backref('generated_drafts', lazy='dynamic'))


class GlobalPattern(db.Model):
    __tablename__ = 'global_patterns'
    id = db.Column(db.Integer, primary_key=True)
    pattern_type = db.Column(db.String(50), nullable=False)
    pattern_data = db.Column(db.JSON, nullable=False)
    success_count = db.Column(db.Integer, default=0)
    usage_count = db.Column(db.Integer, default=0)
    success_rate = db.Column(db.Float, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)


class Subscription(db.Model):
    __tablename__ = 'subscriptions'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String, db.ForeignKey('users.id'), unique=True, nullable=False, index=True)
    stripe_customer_id = db.Column(db.String(255), nullable=True)
    stripe_subscription_id = db.Column(db.String(255), nullable=True)
    tier = db.Column(db.String(20), default='free')  # 'free', 'creator', 'pro'
    status = db.Column(db.String(20), default='inactive')
    token_balance = db.Column(db.Integer, default=50)  # Monthly tokens
    token_refresh_date = db.Column(db.DateTime, nullable=True)  # When tokens refresh
    current_period_end = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    user = db.relationship('User', backref=db.backref('subscription', uselist=False))
    
    def is_active(self):
        return self.status == 'active' and self.tier in ('creator', 'pro')
    
    def is_pro(self):
        return self.status == 'active' and self.tier == 'pro'
    
    def get_monthly_tokens(self):
        tier_tokens = {'free': 50, 'creator': 300, 'pro': 1000}
        return tier_tokens.get(self.tier, 50)


class HostedVideo(db.Model):
    __tablename__ = 'hosted_videos'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String, db.ForeignKey('users.id'), nullable=False, index=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=True, index=True)
    title = db.Column(db.String(255), nullable=False)
    public_id = db.Column(db.String(64), unique=True, nullable=False)
    video_path = db.Column(db.String(500), nullable=False)
    thumbnail_path = db.Column(db.String(500), nullable=True)
    views = db.Column(db.Integer, default=0)
    is_public = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    user = db.relationship('User', backref=db.backref('hosted_videos', lazy='dynamic'))
    project = db.relationship('Project', backref=db.backref('hosted_video', uselist=False))


class FeedItem(db.Model):
    __tablename__ = 'feed_items'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String, db.ForeignKey('users.id'), nullable=True)
    content_type = db.Column(db.String(50), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    script = db.Column(db.Text, nullable=True)
    visual_preview = db.Column(db.String(500), nullable=True)
    video_path = db.Column(db.String(500), nullable=True)
    topic = db.Column(db.String(100), nullable=True)
    hook_style = db.Column(db.String(50), nullable=True)
    voice_style = db.Column(db.String(50), nullable=True)
    is_global = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    
    user = db.relationship('User', backref=db.backref('feed_items', lazy='dynamic'))


class SwipeFeedback(db.Model):
    __tablename__ = 'swipe_feedback'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String, db.ForeignKey('users.id'), nullable=False)
    feed_item_id = db.Column(db.Integer, db.ForeignKey('feed_items.id'), nullable=False)
    action = db.Column(db.String(20), nullable=False)
    feedback_text = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    
    user = db.relationship('User', backref=db.backref('swipe_feedback', lazy='dynamic'))
    feed_item = db.relationship('FeedItem', backref=db.backref('feedback', lazy='dynamic'))


class ProjectFeedback(db.Model):
    __tablename__ = 'project_feedback'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String, db.ForeignKey('users.id'), nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=True)
    
    script_rating = db.Column(db.String(20), nullable=True)
    voice_rating = db.Column(db.String(20), nullable=True)
    visuals_rating = db.Column(db.String(20), nullable=True)
    soundfx_rating = db.Column(db.String(20), nullable=True)
    overall_rating = db.Column(db.String(20), nullable=True)
    
    feedback_text = db.Column(db.Text, nullable=True)
    severity = db.Column(db.String(20), default='minor')
    
    ai_learned = db.Column(db.Text, nullable=True)
    ai_to_improve = db.Column(db.Text, nullable=True)
    learning_points_gained = db.Column(db.Integer, default=0)
    
    created_at = db.Column(db.DateTime, default=datetime.now)
    
    user = db.relationship('User', backref=db.backref('project_feedback', lazy='dynamic'))
    project = db.relationship('Project', backref=db.backref('feedback', uselist=False))


class GeneratorSettings(db.Model):
    """User-configurable settings for auto-generation"""
    __tablename__ = 'generator_settings'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String, db.ForeignKey('users.id'), unique=True, nullable=False)
    
    # Content controls
    tone = db.Column(db.String(50), default='neutral')  # neutral, passionate, calm, urgent, witty
    format_type = db.Column(db.String(50), default='explainer')  # explainer, opinion, story, breakdown
    target_length = db.Column(db.Integer, default=45)  # seconds: 35-75
    voice_style = db.Column(db.String(50), default='news_anchor')  # maps to character persona
    
    # Topic preferences (JSON list of enabled topics)
    enabled_topics = db.Column(db.JSON, default=list)  # e.g., ["politics", "tech", "culture"]
    
    # Auto-generation toggle
    auto_enabled = db.Column(db.Boolean, default=False)  # User toggle (can only enable when unlocked)
    
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    user = db.relationship('User', backref=db.backref('generator_settings', uselist=False))


class SourceContent(db.Model):
    """Source material submitted for clipping - videos, transcripts, links."""
    __tablename__ = 'source_content'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String, db.ForeignKey('users.id'), nullable=False)
    
    content_type = db.Column(db.String(50), nullable=False)
    source_url = db.Column(db.Text, nullable=True)
    transcript = db.Column(db.Text, nullable=True)
    file_path = db.Column(db.String(500), nullable=True)
    
    extracted_thesis = db.Column(db.Text, nullable=True)
    extracted_anchors = db.Column(db.JSON, nullable=True)
    extracted_thought_changes = db.Column(db.JSON, nullable=True)
    
    learned_hooks = db.Column(db.JSON, nullable=True)
    learned_pacing = db.Column(db.JSON, nullable=True)
    learned_structure = db.Column(db.JSON, nullable=True)
    learned_style = db.Column(db.JSON, nullable=True)
    
    clips_generated = db.Column(db.Integer, default=0)
    quality_score = db.Column(db.Float, nullable=True)
    
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    user = db.relationship('User', backref=db.backref('source_content', lazy='dynamic'))


class ProjectThesis(db.Model):
    """Core thesis that drives a project - the single idea everything else serves."""
    __tablename__ = 'project_thesis'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    user_id = db.Column(db.String, db.ForeignKey('users.id'), nullable=False)
    
    thesis_statement = db.Column(db.Text, nullable=False)
    thesis_type = db.Column(db.String(50), nullable=True)
    core_claim = db.Column(db.Text, nullable=True)
    target_audience = db.Column(db.Text, nullable=True)
    intended_impact = db.Column(db.Text, nullable=True)
    
    confidence_score = db.Column(db.Float, default=1.0)
    is_user_confirmed = db.Column(db.Boolean, default=False)
    
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    project = db.relationship('Project', backref=db.backref('thesis', uselist=False))
    user = db.relationship('User', backref=db.backref('theses', lazy='dynamic'))


class ScriptAnchor(db.Model):
    """Key anchor points in a script - statements the argument builds around."""
    __tablename__ = 'script_anchors'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    
    anchor_text = db.Column(db.Text, nullable=False)
    anchor_type = db.Column(db.String(50), nullable=False)
    position = db.Column(db.Integer, nullable=False)
    
    supports_thesis = db.Column(db.Boolean, default=True)
    is_hook = db.Column(db.Boolean, default=False)
    is_closer = db.Column(db.Boolean, default=False)
    
    visual_intent = db.Column(db.String(100), nullable=True)
    emotional_beat = db.Column(db.String(50), nullable=True)
    
    created_at = db.Column(db.DateTime, default=datetime.now)
    
    project = db.relationship('Project', backref=db.backref('anchors', lazy='dynamic'))


class ThoughtChange(db.Model):
    """Detected thought transitions in content - potential clip points."""
    __tablename__ = 'thought_changes'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=True)
    source_content_id = db.Column(db.Integer, db.ForeignKey('source_content.id'), nullable=True)
    
    position = db.Column(db.Float, nullable=False)
    timestamp = db.Column(db.Float, nullable=True)
    
    from_idea = db.Column(db.Text, nullable=True)
    to_idea = db.Column(db.Text, nullable=True)
    transition_type = db.Column(db.String(50), nullable=False)
    
    should_clip = db.Column(db.Boolean, default=False)
    clip_reasoning = db.Column(db.Text, nullable=True)
    clarity_improvement = db.Column(db.Float, nullable=True)
    retention_improvement = db.Column(db.Float, nullable=True)
    
    created_at = db.Column(db.DateTime, default=datetime.now)
    
    project = db.relationship('Project', backref=db.backref('project_thought_changes', lazy='dynamic'))
    source_content = db.relationship('SourceContent', backref=db.backref('detected_thought_changes', lazy='dynamic'))


class VideoHistory(db.Model):
    """Track generated videos for download history"""
    __tablename__ = 'video_history'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String, db.ForeignKey('users.id'), nullable=False, index=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=True, index=True)
    project_name = db.Column(db.String(255), nullable=False)
    video_path = db.Column(db.String(500), nullable=False)
    thumbnail_path = db.Column(db.String(500), nullable=True)
    duration_seconds = db.Column(db.Float, nullable=True)
    format = db.Column(db.String(20), default='9:16')
    file_size_bytes = db.Column(db.Integer, nullable=True)
    captions_data = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    
    user = db.relationship('User', backref=db.backref('video_history', lazy='dynamic'))
    project = db.relationship('Project', backref=db.backref('video_history', lazy='dynamic'))


class EmailNotification(db.Model):
    """Track email notification preferences and history"""
    __tablename__ = 'email_notifications'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String, db.ForeignKey('users.id'), nullable=False, index=True)
    notification_type = db.Column(db.String(50), nullable=False)
    enabled = db.Column(db.Boolean, default=True)
    last_sent = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    
    user = db.relationship('User', backref=db.backref('email_notifications', lazy='dynamic'))


class UserTokens(db.Model):
    """Global token balance (legacy - prefer Subscription.token_balance)"""
    __tablename__ = 'user_tokens'
    id = db.Column(db.Integer, primary_key=True)
    balance = db.Column(db.Integer, default=120)
    last_updated = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())


class MediaAsset(db.Model):
    """Legal media assets with licensing metadata - stores LINKS only, not files."""
    __tablename__ = 'media_asset'
    id = db.Column(db.String(255), primary_key=True)
    source_page = db.Column(db.Text)
    download_url = db.Column(db.Text, nullable=False)
    thumbnail_url = db.Column(db.Text)
    source = db.Column(db.String(50), nullable=False, index=True)
    license = db.Column(db.String(100), nullable=False)
    license_url = db.Column(db.Text)
    commercial_use_allowed = db.Column(db.Boolean, default=True)
    derivatives_allowed = db.Column(db.Boolean, default=True)
    attribution_required = db.Column(db.Boolean, default=False)
    attribution_text = db.Column(db.Text)
    content_type = db.Column(db.String(20), nullable=False, index=True)
    duration_sec = db.Column(db.Float)
    resolution = db.Column(db.String(20))
    description = db.Column(db.Text)
    tags = db.Column(db.JSON)
    safe_flags = db.Column(db.JSON)
    status = db.Column(db.String(20), default='safe', index=True)
    use_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, server_default=db.func.now())


class KeywordAssetCache(db.Model):
    """Cache keyword → asset associations for faster visual curation."""
    __tablename__ = 'keyword_asset_cache'
    id = db.Column(db.Integer, primary_key=True)
    keyword = db.Column(db.String(255), nullable=False, index=True)
    context = db.Column(db.String(100))
    asset_id = db.Column(db.String(255), db.ForeignKey('media_asset.id'), nullable=False, index=True)
    relevance_score = db.Column(db.Float, default=1.0)
    use_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, server_default=db.func.now())


class SourceDocument(db.Model):
    """Source documents/citations for education reels."""
    __tablename__ = 'source_document'
    id = db.Column(db.Integer, primary_key=True)
    url = db.Column(db.Text, nullable=False, unique=True)
    doc_type = db.Column(db.String(20))
    title = db.Column(db.Text)
    author = db.Column(db.Text)
    publisher = db.Column(db.String(255))
    publish_date = db.Column(db.String(100))
    preview_method = db.Column(db.String(30))
    preview_image_path = db.Column(db.Text)
    excerpts = db.Column(db.JSON)
    og_image = db.Column(db.Text)
    verified = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now())


class VideoTemplate(db.Model):
    __tablename__ = 'video_templates'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String, db.ForeignKey('users.id'), nullable=False, index=True)
    name = db.Column(db.String(255), nullable=False)
    source_video_path = db.Column(db.String(500), nullable=True)
    duration = db.Column(db.Float, nullable=True)
    scene_count = db.Column(db.Integer, default=1)
    scenes = db.Column(db.JSON, nullable=True)
    aesthetic = db.Column(db.JSON, nullable=True)
    transitions = db.Column(db.JSON, nullable=True)
    text_patterns = db.Column(db.JSON, nullable=True)
    audio_profile = db.Column(db.JSON, nullable=True)
    thumbnail_path = db.Column(db.String(500), nullable=True)
    usage_count = db.Column(db.Integer, default=0)
    is_public = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    user = db.relationship('User', backref=db.backref('video_templates', lazy='dynamic'))


class ReskinFeedback(db.Model):
    """Global learning system for video re-skinning quality across all accounts"""
    __tablename__ = 'reskin_feedback'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String, db.ForeignKey('users.id'), nullable=True, index=True)
    
    # What was created
    source_dna = db.Column(db.JSON, nullable=True)
    topic = db.Column(db.String(500), nullable=True)
    visual_sources = db.Column(db.JSON, nullable=True)
    
    # Quality scores (from AI self-review)
    ai_quality_score = db.Column(db.Float, nullable=True)
    visual_match_score = db.Column(db.Float, nullable=True)
    brand_alignment_score = db.Column(db.Float, nullable=True)
    coherence_score = db.Column(db.Float, nullable=True)
    
    # User feedback
    user_liked = db.Column(db.Boolean, nullable=True)
    user_comment = db.Column(db.Text, nullable=True)
    regenerated = db.Column(db.Boolean, default=False)
    
    # What worked / what didn't
    successful_visuals = db.Column(db.JSON, nullable=True)
    failed_visuals = db.Column(db.JSON, nullable=True)
    search_queries_used = db.Column(db.JSON, nullable=True)
    
    created_at = db.Column(db.DateTime, default=datetime.now)
    
    user = db.relationship('User', backref=db.backref('reskin_feedback', lazy='dynamic'))


class VisualMatch(db.Model):
    """Track which visual search queries work for which intents globally"""
    __tablename__ = 'visual_matches'
    id = db.Column(db.Integer, primary_key=True)
    
    scene_intent = db.Column(db.String(500), nullable=False)
    scene_type = db.Column(db.String(50), nullable=True)
    topic_category = db.Column(db.String(100), nullable=True)
    
    search_query = db.Column(db.String(500), nullable=False)
    source = db.Column(db.String(50), nullable=True)
    
    success_count = db.Column(db.Integer, default=0)
    fail_count = db.Column(db.Integer, default=0)
    success_rate = db.Column(db.Float, default=0.0)
    
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)


class VisualLearning(db.Model):
    """Track which visual source decisions work well for content types"""
    __tablename__ = 'visual_learning'
    id = db.Column(db.Integer, primary_key=True)
    
    content_type = db.Column(db.String(50), nullable=False, index=True)
    scene_position = db.Column(db.String(20), nullable=False)
    source_type = db.Column(db.String(50), nullable=False)
    feedback = db.Column(db.String(20), default='positive')
    scene_text_sample = db.Column(db.String(200), nullable=True)
    
    created_at = db.Column(db.DateTime, default=datetime.now)


class VisualPlan(db.Model):
    """Store visual plans for videos - enables revision without re-planning"""
    __tablename__ = 'visual_plans'
    id = db.Column(db.Integer, primary_key=True)
    
    user_id = db.Column(db.String, db.ForeignKey('users.id'), nullable=True, index=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=True)
    plan_id = db.Column(db.String(20), nullable=False, unique=True)
    
    content_type = db.Column(db.String(50), nullable=True)
    color_palette = db.Column(db.JSON, default=[])
    editing_dna = db.Column(db.JSON, default={})
    scenes = db.Column(db.JSON, default=[])
    
    script_hash = db.Column(db.String(64), nullable=True)
    
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)


class PreviewVideo(db.Model):
    """Track preview videos before final render - enables watermark removal flow"""
    __tablename__ = 'preview_videos'
    id = db.Column(db.Integer, primary_key=True)
    
    user_id = db.Column(db.String, db.ForeignKey('users.id'), nullable=True, index=True)
    preview_path = db.Column(db.String(500), nullable=False)
    final_path = db.Column(db.String(500), nullable=True)
    plan_id = db.Column(db.String(20), nullable=True)
    
    is_finalized = db.Column(db.Boolean, default=False)
    revision_count = db.Column(db.Integer, default=0)
    feedback_history = db.Column(db.JSON, default=[])
    
    created_at = db.Column(db.DateTime, default=datetime.now)
    finalized_at = db.Column(db.DateTime, nullable=True)
