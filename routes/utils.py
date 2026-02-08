"""
Shared utilities for route blueprints.
"""
import os
import logging
import requests
from functools import wraps

_rate_limit_table_created = False


def _ensure_rate_limit_table():
    """Create rate_limits table at startup (called once)."""
    global _rate_limit_table_created
    if _rate_limit_table_created:
        return
    import psycopg2
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        return
    try:
        conn = psycopg2.connect(db_url)
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS rate_limits (
                    id SERIAL PRIMARY KEY,
                    client_key VARCHAR(255) NOT NULL,
                    request_time TIMESTAMP NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_rate_limits_key_time
                ON rate_limits (client_key, request_time)
            """)
        conn.commit()
        conn.close()
        _rate_limit_table_created = True
    except Exception as e:
        logging.warning(f"Rate limit table creation failed: {e}")


def rate_limit(limit=30, window=60):
    """Database-backed rate limiting decorator. Default: 30 requests per 60 seconds."""
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            import psycopg2
            from flask import request, jsonify
            from flask_login import current_user
            from datetime import datetime, timedelta

            if current_user.is_authenticated:
                key = f"user:{current_user.id}"
            else:
                key = f"ip:{request.remote_addr}"

            db_url = os.environ.get("DATABASE_URL")
            if not db_url:
                return f(*args, **kwargs)

            _ensure_rate_limit_table()
            cutoff = datetime.utcnow() - timedelta(seconds=window)

            try:
                conn = psycopg2.connect(db_url)
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM rate_limits WHERE request_time < %s",
                        (cutoff,)
                    )
                    cur.execute(
                        "SELECT COUNT(*) FROM rate_limits WHERE client_key = %s AND request_time > %s",
                        (key, cutoff)
                    )
                    count = cur.fetchone()[0]

                    if count >= limit:
                        conn.commit()
                        conn.close()
                        return jsonify({'error': 'Rate limit exceeded. Please slow down.'}), 429

                    cur.execute(
                        "INSERT INTO rate_limits (client_key, request_time) VALUES (%s, NOW())",
                        (key,)
                    )
                    conn.commit()
                conn.close()
            except Exception as e:
                logging.warning(f"Rate limit check failed: {e}")

            return f(*args, **kwargs)
        return wrapped
    return decorator


def format_user_error(error_msg):
    """Convert technical error messages to user-friendly versions."""
    error_lower = error_msg.lower()

    if 'api key' in error_lower or 'authentication' in error_lower:
        return "We're having trouble connecting to our AI service. Please try again in a moment."
    elif 'rate limit' in error_lower:
        return "Our AI is handling a lot of requests right now. Please wait a minute and try again."
    elif 'timeout' in error_lower or 'timed out' in error_lower:
        return "This is taking longer than expected. Please try again with a shorter script."
    elif 'no visual content' in error_lower or 'no scenes' in error_lower:
        return "Please add some visual content before generating your video."
    elif 'no audio' in error_lower or 'voiceover' in error_lower:
        return "Please generate a voiceover first before creating the video."
    elif 'insufficient tokens' in error_lower or 'not enough tokens' in error_lower:
        return "You don't have enough tokens for this video. Please add more tokens or upgrade your plan."
    elif 'file not found' in error_lower or 'no such file' in error_lower:
        return "Some files are missing. Please try regenerating your content."
    elif 'ffmpeg' in error_lower:
        return "There was an issue assembling your video. Please try again."
    elif 'connection' in error_lower or 'network' in error_lower:
        return "Connection issue. Please check your internet and try again."
    elif 'invalid' in error_lower and 'url' in error_lower:
        return "One of the media links appears to be broken. Try refreshing your visual content."
    else:
        return f"Something went wrong: {error_msg[:100]}. Please try again or contact support."


def get_stripe_credentials():
    """Fetch Stripe credentials from Replit connection API."""
    hostname = os.environ.get('REPLIT_CONNECTORS_HOSTNAME')
    repl_identity = os.environ.get('REPL_IDENTITY')
    web_repl_renewal = os.environ.get('WEB_REPL_RENEWAL')
    
    if repl_identity:
        x_replit_token = 'repl ' + repl_identity
    elif web_repl_renewal:
        x_replit_token = 'depl ' + web_repl_renewal
    else:
        return None, None
    
    is_production = os.environ.get('REPLIT_DEPLOYMENT') == '1'
    target_env = 'production' if is_production else 'development'
    
    url = f"https://{hostname}/api/v2/connection?include_secrets=true&connector_names=stripe&environment={target_env}"
    
    response = requests.get(url, headers={
        'Accept': 'application/json',
        'X_REPLIT_TOKEN': x_replit_token
    })
    
    data = response.json()
    connection = data.get('items', [{}])[0]
    settings = connection.get('settings', {})
    
    return settings.get('publishable'), settings.get('secret')


TOKEN_PACKAGES = {
    50: 500,     # 50 tokens = $5.00
    100: 200,    # 100 tokens = $2.00 (legacy)
    150: 1200,   # 150 tokens = $12.00
    400: 2500,   # 400 tokens = $25.00
    500: 800,    # 500 tokens = $8.00 (legacy)
    1000: 5000,  # 1000 tokens = $50.00
    2000: 2500   # 2000 tokens = $25.00 (legacy)
}

SUBSCRIPTION_TIERS = {
    'creator': {
        'name': 'Framd Creator',
        'price_cents': 1000,
        'tokens': 300,
        'description': '300 tokens/month, video export, premium voices'
    },
    'pro': {
        'name': 'Framd Pro',
        'price_cents': 2500,
        'tokens': 1000,
        'description': '1000 tokens/month, unlimited revisions, auto-generator'
    }
}

TIER_TOKENS = {'free': 50, 'creator': 300, 'pro': 1000}


def get_base_url():
    """Get base URL for redirects."""
    domains = os.environ.get('REPLIT_DOMAINS', 'localhost:5000')
    domain = domains.split(',')[0] if domains else 'localhost:5000'
    protocol = 'https' if 'replit' in domain else 'http'
    return f"{protocol}://{domain}"


def get_user_id():
    """Get user ID - supports both authenticated users and dev mode."""
    from flask import session
    from flask_login import current_user
    if current_user.is_authenticated:
        return current_user.id
    if session.get('dev_mode'):
        return 'dev_user'
    return None
