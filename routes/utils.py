"""
Shared utilities for route blueprints.
"""
import os
import requests


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
