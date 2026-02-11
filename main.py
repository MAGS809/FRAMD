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

# Only load Replit auth when running on Replit (REPL_ID is set)
if os.environ.get('REPL_ID'):
    from replit_auth import make_replit_blueprint
    app.register_blueprint(make_replit_blueprint(), url_prefix="/auth")

@app.before_request
def make_session_permanent():
    session.permanent = True

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
