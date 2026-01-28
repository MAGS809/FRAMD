from datetime import datetime
from app import db
from flask_dance.consumer.storage.sqla import OAuthConsumerMixin
from flask_login import UserMixin
from sqlalchemy import UniqueConstraint

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.String, primary_key=True)
    email = db.Column(db.String, unique=True, nullable=True)
    first_name = db.Column(db.String, nullable=True)
    last_name = db.Column(db.String, nullable=True)
    profile_image_url = db.Column(db.String, nullable=True)
    tokens = db.Column(db.Integer, default=120)
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
    user_id = db.Column(db.String, db.ForeignKey('users.id'), nullable=False)
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
    user_id = db.Column(db.String, db.ForeignKey('users.id'), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(50), default='draft')
    script = db.Column(db.Text, nullable=True)
    visual_plan = db.Column(db.JSON, nullable=True)
    voice_assignments = db.Column(db.JSON, nullable=True)
    caption_settings = db.Column(db.JSON, nullable=True)
    video_path = db.Column(db.String(500), nullable=True)
    is_successful = db.Column(db.Boolean, default=False)
    success_score = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    user = db.relationship('User', backref=db.backref('projects', lazy='dynamic'))


class AILearning(db.Model):
    __tablename__ = 'ai_learning'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String, db.ForeignKey('users.id'), nullable=False)
    total_projects = db.Column(db.Integer, default=0)
    successful_projects = db.Column(db.Integer, default=0)
    learning_progress = db.Column(db.Integer, default=0)
    learned_hooks = db.Column(db.JSON, default=list)
    learned_voices = db.Column(db.JSON, default=list)
    learned_styles = db.Column(db.JSON, default=list)
    learned_topics = db.Column(db.JSON, default=list)
    can_auto_generate = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    user = db.relationship('User', backref=db.backref('ai_learning', uselist=False))


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
    user_id = db.Column(db.String, db.ForeignKey('users.id'), unique=True, nullable=False)
    stripe_customer_id = db.Column(db.String(255), nullable=True)
    stripe_subscription_id = db.Column(db.String(255), nullable=True)
    tier = db.Column(db.String(20), default='free')
    status = db.Column(db.String(20), default='inactive')
    current_period_end = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    user = db.relationship('User', backref=db.backref('subscription', uselist=False))
    
    def is_active(self):
        return self.status == 'active' and self.tier == 'pro'


class HostedVideo(db.Model):
    __tablename__ = 'hosted_videos'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String, db.ForeignKey('users.id'), nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=True)
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
