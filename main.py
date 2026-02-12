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


def validate_environment():
    """Check required environment variables and warn about optional ones."""
    missing_required = []
    missing_optional = []

    for var_name, description in REQUIRED_ENV_VARS:
        if not os.environ.get(var_name):
            missing_required.append(f"  - {var_name}: {description}")

    for var_name, description in OPTIONAL_ENV_VARS:
        if not os.environ.get(var_name):
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
        # Email/password auth for non-Replit deployments (Railway, etc.)
        import uuid
        from flask import Blueprint, redirect, url_for as flask_url_for, request, render_template, flash
        from flask_login import login_user, logout_user
        from werkzeug.security import generate_password_hash, check_password_hash

        replit_auth_stub = Blueprint('replit_auth', __name__)

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
