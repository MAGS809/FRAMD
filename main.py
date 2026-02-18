import os
import sys

REQUIRED_ENV_VARS = [
        ("DATABASE_URL", "PostgreSQL database connection"),
]

OPTIONAL_ENV_VARS = [
        ("OPENAI_API_KEY", "OpenAI API for transcription and images"),
        ("AI_INTEGRATIONS_ANTHROPIC_API_KEY", "Claude AI for content generation"),
        ("RUNWAY_API_KEY", "Runway video generation (Remix mode)"),
        ("SHOTSTACK_API_KEY", "Shotstack video assembly"),
        ("PEXELS_API_KEY", "Pexels stock video search"),
        ("ELEVENLABS_API_KEY", "ElevenLabs text-to-speech"),
]

def _get_env_value(var_name):
        """Get env var value, accepting alternate names for known vars."""
        val = os.environ.get(var_name)
        if val:
                    return val
                # Accept ANTHROPIC_API_KEY as alias for AI_INTEGRATIONS_ANTHROPIC_API_KEY
                if var_name == "AI_INTEGRATIONS_ANTHROPIC_API_KEY":
                            return os.environ.get("ANTHROPIC_API_KEY")
                        # Accept AI_INTEGRATIONS_OPENAI_API_KEY as alias for OPENAI_API_KEY
                        if var_name == "OPENAI_API_KEY":
                                    return os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY")
                                return None

def validate_environment():
        """Check required environment variables and warn about optional ones."""
    missing_required = []
    missing_optional = []

    for var_name, description in REQUIRED_ENV_VARS:
                if not _get_env_value(var_name):
                                missing_required.append(f"  - {var_name}: {description}")

            for var_name, description in OPTIONAL_ENV_VARS:
                        if not _get_env_value(var_name):
                                        missing_optional.append(f"  - {var_name}: {description}")

                    if missing_required:
                                print("\n" + "=" * 60)
                                print("STARTUP ERROR: Missing required environment variables")
                                print("=" * 60)
                                print("\nThe following environment variables must be set:\n")
                                print("\n".join(missing_required))
                                print("\nPlease add these secrets in the Replit Secrets tab.")
                                print("=" * 60 + "\n")
                                sys.exit(1)

    if missing_optional:
                print("\n[Startup] Warning: Some optional API keys are not set:")
                for item in missing_optional:
                                print(item)
                            print("  (Some features may be limited)\n")

    print("[Startup] Environment validation complete")

validate_environment()

from app import app, db, session
import models

with app.app_context():
        db.create_all()

# Always initialize Flask-Login for user session management
from flask_login import LoginManager
login_manager = LoginManager(app)
from models import User

@login_manager.user_loader
def load_user(user_id):
        return User.query.get(user_id)

# Load auth blueprint based on environment
if os.environ.get('REPL_ID'):
        from replit_auth import make_replit_blueprint
    app.register_blueprint(make_replit_blueprint(), url_prefix="/auth")
else:
    # Email/password auth + OAuth for non-Replit deployments (Railway, etc.)
        import uuid
    from flask import Blueprint, redirect, url_for as flask_url_for, request, render_template, flash
    from flask_login import login_user, logout_user
    from werkzeug.security import generate_password_hash, check_password_hash
    from authlib.integrations.flask_client import OAuth

    replit_auth_stub = Blueprint('replit_auth', __name__)

    # --- OAuth setup ---
    oauth = OAuth(app)

    # Google OAuth
    if os.environ.get('GOOGLE_CLIENT_ID'):
                oauth.register(
                    name='google',
                    client_id=os.environ.get('GOOGLE_CLIENT_ID'),
                    client_secret=os.environ.get('GOOGLE_CLIENT_SECRET'),
                    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
                    client_kwargs={'scope': 'openid email profile'},
    )

    # Facebook OAuth
    if os.environ.get('FACEBOOK_CLIENT_ID'):
                oauth.register(
                    name='facebook',
                    client_id=os.environ.get('FACEBOOK_CLIENT_ID'),
                    client_secret=os.environ.get('FACEBOOK_CLIENT_SECRET'),
                    access_token_url='https://graph.facebook.com/v18.0/oauth/access_token',
                    authorize_url='https://www.facebook.com/v18.0/dialog/oauth',
                    api_base_url='https://graph.facebook.com/v18.0/',
                    client_kwargs={'scope': 'email public_profile'},
    )

    # Apple OAuth
    if os.environ.get('APPLE_CLIENT_ID'):
                oauth.register(
                    name='apple',
                    client_id=os.environ.get('APPLE_CLIENT_ID'),
                    client_secret=os.environ.get('APPLE_CLIENT_SECRET'),
                    authorize_url='https://appleid.apple.com/auth/authorize',
                    access_token_url='https://appleid.apple.com/auth/token',
                    client_kwargs={'scope': 'name email', 'response_mode': 'form_post'},
                    server_metadata_url=None,
                    jwks_uri='https://appleid.apple.com/auth/keys',
    )

    def _get_or_create_oauth_user(email, first_name=None, last_name=None, provider=None):
                """Find existing user by email or create a new one from OAuth login."""
        user = User.query.filter_by(email=email).first()
        if not user:
                        user = User()
                        user.id = str(uuid.uuid4())
                        user.email = email
                        user.first_name = first_name
                        user.last_name = last_name
                        user.tokens = 120
                        db.session.add(user)
                        db.session.commit()
                    return user

    # --- OAuth routes ---
    @replit_auth_stub.route('/google')
    def google_login():
                if not os.environ.get('GOOGLE_CLIENT_ID'):
                                return render_template('login.html', error='Google login is not configured.')
                            redirect_uri = flask_url_for('replit_auth.google_callback', _external=True)
        return oauth.google.authorize_redirect(redirect_uri)

    @replit_auth_stub.route('/google/callback')
    def google_callback():
                try:
                                token = oauth.google.authorize_access_token()
                                userinfo = token.get('userinfo')
                                if not userinfo:
                                                    userinfo = oauth.google.get('https://openidconnect.googleapis.com/v1/userinfo').json()
                                                email = userinfo.get('email')
            if not email:
                                return render_template('login.html', error='Could not get email from Google.')
            user = _get_or_create_oauth_user(
                                email=email,
                                first_name=userinfo.get('given_name'),
                                last_name=userinfo.get('family_name'),
                                provider='google'
            )
            login_user(user)
            return redirect('/')
except Exception as e:
            print(f"Google OAuth error: {e}")
            return render_template('login.html', error='Google login failed. Please try again.')

    @replit_auth_stub.route('/facebook')
    def facebook_login():
                if not os.environ.get('FACEBOOK_CLIENT_ID'):
                                return render_template('login.html', error='Facebook login is not configured.')
        redirect_uri = flask_url_for('replit_auth.facebook_callback', _external=True)
        return oauth.facebook.authorize_redirect(redirect_uri)

    @replit_auth_stub.route('/facebook/callback')
    def facebook_callback():
                try:
                                token = oauth.facebook.authorize_access_token()
            resp = oauth.facebook.get('me?fields=id,name,email,first_name,last_name')
            profile = resp.json()
            email = profile.get('email')
            if not email:
                                return render_template('login.html', error='Could not get email from Facebook. Make sure email permissions are granted.')
            user = _get_or_create_oauth_user(
                                email=email,
                                first_name=profile.get('first_name'),
                                last_name=profile.get('last_name'),
                                provider='facebook'
            )
            login_user(user)
            return redirect('/')
except Exception as e:
            print(f"Facebook OAuth error: {e}")
            return render_template('login.html', error='Facebook login failed. Please try again.')

    @replit_auth_stub.route('/apple')
    def apple_login():
                if not os.environ.get('APPLE_CLIENT_ID'):
                                return render_template('login.html', error='Apple login is not configured.')
        redirect_uri = flask_url_for('replit_auth.apple_callback', _external=True)
        return oauth.apple.authorize_redirect(redirect_uri)

    @replit_auth_stub.route('/apple/callback', methods=['GET', 'POST'])
    def apple_callback():
                try:
                                token = oauth.apple.authorize_access_token()
            import jwt
            id_token = token.get('id_token')
            claims = jwt.decode(id_token, options={"verify_signature": False})
            email = claims.get('email')
            if not email:
                                return render_template('login.html', error='Could not get email from Apple.')
            user_data = request.form.get('user')
            first_name = None
            last_name = None
            if user_data:
                                import json
                user_info = json.loads(user_data)
                name = user_info.get('name', {})
                first_name = name.get('firstName')
                last_name = name.get('lastName')
            user = _get_or_create_oauth_user(
                                email=email,
                                first_name=first_name,
                                last_name=last_name,
                                provider='apple'
            )
            login_user(user)
            return redirect('/')
except Exception as e:
            print(f"Apple OAuth error: {e}")
            return render_template('login.html', error='Apple login failed. Please try again.')

    # --- Email/password routes ---
    @replit_auth_stub.route('/login', methods=['GET', 'POST'])
    def login():
                if request.method == 'POST':
                                email = request.form.get('email', '').strip().lower()
            password = request.form.get('password', '')
            if not email or not password:
                                return render_template('login.html', error='Email and password are required.')
            user = User.query.filter_by(email=email).first()
            if user and user.password_hash and check_password_hash(user.password_hash, password):
                                login_user(user)
                next_url = request.args.get('next', '/')
                return redirect(next_url)
else:
                return render_template('login.html', error='Invalid email or password.')
        return render_template('login.html')

    @replit_auth_stub.route('/signup', methods=['GET', 'POST'])
    def signup():
                if request.method == 'POST':
                                email = request.form.get('email', '').strip().lower()
            password = request.form.get('password', '')
            first_name = request.form.get('first_name', '').strip()
            last_name = request.form.get('last_name', '').strip()
            if not email or not password:
                                return render_template('signup.html', error='Email and password are required.')
            if len(password) < 6:
                                return render_template('signup.html', error='Password must be at least 6 characters.')
            existing = User.query.filter_by(email=email).first()
            if existing:
                                return render_template('signup.html', error='An account with this email already exists.')
            user = User()
            user.id = str(uuid.uuid4())
            user.email = email
            user.password_hash = generate_password_hash(password)
            user.first_name = first_name or None
            user.last_name = last_name or None
            user.tokens = 120
            db.session.add(user)
            db.session.commit()
            login_user(user)
            return redirect('/')
        return render_template('signup.html')

    @replit_auth_stub.route('/logout')
    def logout():
                logout_user()
        return redirect('/')

    app.register_blueprint(replit_auth_stub, url_prefix="/auth")

@app.before_request
def make_session_permanent():
        session.permanent = True

if __name__ == "__main__":
        app.run(host="0.0.0.0", port=5000, debug=True)
