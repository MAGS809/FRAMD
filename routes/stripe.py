from flask import Blueprint, request, jsonify, session, redirect
import os
import json
import stripe
import requests
import logging
from extensions import db

stripe_bp = Blueprint('stripe_bp', __name__)


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
        'price_cents': 1000,  # $10/month
        'tokens': 300,
        'description': '300 tokens/month, video export, premium voices'
    },
    'pro': {
        'name': 'Framd Pro',
        'price_cents': 2500,  # $25/month
        'tokens': 1000,
        'description': '1000 tokens/month, unlimited revisions, auto-generator'
    }
}


@stripe_bp.route('/create-checkout-session', methods=['POST'])
def create_checkout_session():
    """Create a Stripe checkout session for token purchase."""
    try:
        data = request.get_json()
        token_amount = data.get('amount', 500)
        
        if token_amount not in TOKEN_PACKAGES:
            return jsonify({'error': 'Invalid token amount'}), 400
        
        price_cents = TOKEN_PACKAGES[token_amount]
        
        _, secret_key = get_stripe_credentials()
        if not secret_key:
            return jsonify({'error': 'Payment not configured'}), 500
        
        stripe.api_key = secret_key
        
        domains = os.environ.get('REPLIT_DOMAINS', 'localhost:5000')
        domain = domains.split(',')[0] if domains else 'localhost:5000'
        protocol = 'https' if 'replit' in domain else 'http'
        base_url = f"{protocol}://{domain}"
        
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': f'{token_amount} Tokens',
                        'description': f'Krakd Post Assembler - {token_amount} tokens for content creation',
                    },
                    'unit_amount': price_cents,
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=f'{base_url}/?success=true&tokens={token_amount}',
            cancel_url=f'{base_url}/?canceled=true',
            metadata={
                'token_amount': str(token_amount)
            }
        )
        
        return jsonify({'url': checkout_session.url})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@stripe_bp.route('/create-token-checkout', methods=['POST'])
def create_token_checkout():
    """Create a Stripe checkout session for direct token purchase."""
    try:
        data = request.get_json()
        token_amount = data.get('tokens')
        
        if not token_amount:
            return jsonify({'error': 'Missing token amount'}), 400
        
        if token_amount not in TOKEN_PACKAGES:
            return jsonify({'error': 'Invalid token amount'}), 400
        
        price_cents = TOKEN_PACKAGES[token_amount]
        
        _, secret_key = get_stripe_credentials()
        if not secret_key:
            return jsonify({'error': 'Payment not configured'}), 500
        
        stripe.api_key = secret_key
        
        domains = os.environ.get('REPLIT_DOMAINS', 'localhost:5000')
        domain = domains.split(',')[0] if domains else 'localhost:5000'
        protocol = 'https' if 'replit' in domain else 'http'
        base_url = f"{protocol}://{domain}"
        
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': f'{token_amount} Framd Tokens',
                        'description': f'Unlock video rendering, AI voices, auto-generator & all premium features. Tokens never expire.',
                    },
                    'unit_amount': price_cents,
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=f'{base_url}/?success=true&tokens={token_amount}',
            cancel_url=f'{base_url}/?canceled=true',
            metadata={
                'token_amount': str(token_amount),
                'purchase_type': 'token_pack'
            }
        )
        
        return jsonify({'url': checkout_session.url})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@stripe_bp.route('/create-subscription', methods=['POST'])
def create_subscription():
    """Create a Stripe subscription checkout session for Creator or Pro tier."""
    try:
        from models import Subscription
        from flask_login import current_user
        
        data = request.get_json() or {}
        tier = data.get('tier', 'pro')
        
        if tier not in SUBSCRIPTION_TIERS:
            return jsonify({'error': 'Invalid tier'}), 400
        
        tier_info = SUBSCRIPTION_TIERS[tier]
        
        _, secret_key = get_stripe_credentials()
        if not secret_key:
            return jsonify({'error': 'Payment not configured'}), 500
        
        stripe.api_key = secret_key
        
        domains = os.environ.get('REPLIT_DOMAINS', 'localhost:5000')
        domain = domains.split(',')[0] if domains else 'localhost:5000'
        protocol = 'https' if 'replit' in domain else 'http'
        base_url = f"{protocol}://{domain}"
        
        user_id = None
        if current_user.is_authenticated:
            user_id = current_user.id
        else:
            user_id = session.get('dev_user_id', 'dev_user')
        
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': tier_info['name'],
                        'description': tier_info['description'],
                    },
                    'unit_amount': tier_info['price_cents'],
                    'recurring': {
                        'interval': 'month',
                    },
                },
                'quantity': 1,
            }],
            mode='subscription',
            success_url=f'{base_url}/?subscription=success&tier={tier}',
            cancel_url=f'{base_url}/?subscription=canceled',
            metadata={
                'user_id': user_id,
                'plan': tier
            }
        )
        
        return jsonify({'url': checkout_session.url})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@stripe_bp.route('/subscribe')
def subscribe_redirect():
    """Redirect to subscription checkout based on tier query param."""
    tier = request.args.get('tier', 'pro')
    
    if tier not in SUBSCRIPTION_TIERS:
        tier = 'pro'
    
    tier_info = SUBSCRIPTION_TIERS[tier]
    
    _, secret_key = get_stripe_credentials()
    if not secret_key:
        return redirect('/?error=payment_not_configured')
    
    stripe.api_key = secret_key
    
    domains = os.environ.get('REPLIT_DOMAINS', 'localhost:5000')
    domain = domains.split(',')[0] if domains else 'localhost:5000'
    protocol = 'https' if 'replit' in domain else 'http'
    base_url = f"{protocol}://{domain}"
    
    from flask_login import current_user
    user_id = None
    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('dev_user_id', 'dev_user')
    
    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': tier_info['name'],
                        'description': tier_info['description'],
                    },
                    'unit_amount': tier_info['price_cents'],
                    'recurring': {
                        'interval': 'month',
                    },
                },
                'quantity': 1,
            }],
            mode='subscription',
            success_url=f'{base_url}/?subscription=success&tier={tier}',
            cancel_url=f'{base_url}/?subscription=canceled',
            metadata={
                'user_id': user_id,
                'plan': tier
            }
        )
        return redirect(checkout_session.url)
    except Exception as e:
        print(f"Stripe error: {e}")
        return redirect(f'/?error=checkout_failed')


@stripe_bp.route('/create-customer-portal', methods=['POST'])
def customer_portal():
    """Create a Stripe Customer Portal session for managing subscriptions."""
    from models import Subscription
    from flask_login import current_user
    
    _, secret_key = get_stripe_credentials()
    if not secret_key:
        return jsonify({'error': 'Payment not configured'}), 500
    
    stripe.api_key = secret_key
    
    user_id = None
    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('dev_user_id')
    
    if not user_id:
        return jsonify({'error': 'Not logged in'}), 401
    
    sub = Subscription.query.filter_by(user_id=user_id).first()
    if not sub or not sub.stripe_customer_id:
        return jsonify({'error': 'No active subscription found'}), 404
    
    domains = os.environ.get('REPLIT_DOMAINS', 'localhost:5000')
    domain = domains.split(',')[0] if domains else 'localhost:5000'
    protocol = 'https' if 'replit' in domain else 'http'
    base_url = f"{protocol}://{domain}"
    
    try:
        portal_session = stripe.billing_portal.Session.create(
            customer=sub.stripe_customer_id,
            return_url=f'{base_url}/?settings=billing'
        )
        return jsonify({'url': portal_session.url})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@stripe_bp.route('/subscription-status', methods=['GET'])
def subscription_status():
    """Check current user's subscription status and token balance."""
    from models import Subscription, User
    from flask_login import current_user
    from datetime import datetime
    
    if session.get('dev_mode'):
        return jsonify({
            'tier': 'pro', 
            'status': 'active', 
            'is_pro': True, 
            'lifetime': True,
            'token_balance': 1000,
            'monthly_tokens': 1000
        })
    
    user_id = None
    user_email = None
    if current_user.is_authenticated:
        user_id = current_user.id
        user_email = current_user.email
    else:
        user_id = session.get('dev_user_id')
    
    if not user_id:
        return jsonify({
            'tier': 'free', 
            'status': 'inactive', 
            'is_pro': False,
            'token_balance': 50,
            'monthly_tokens': 50
        })
    
    if user_email and user_email.lower() == 'alonbenmeir9@gmail.com':
        return jsonify({
            'tier': 'pro', 
            'status': 'active', 
            'is_pro': True, 
            'lifetime': True,
            'token_balance': 1000,
            'monthly_tokens': 1000
        })
    
    sub = Subscription.query.filter_by(user_id=user_id).first()
    if sub:
        tier_tokens = {'free': 50, 'creator': 300, 'pro': 1000}
        monthly = tier_tokens.get(sub.tier, 50)
        
        if sub.token_balance is None:
            sub.token_balance = monthly
            db.session.commit()
        
        return jsonify({
            'tier': sub.tier,
            'status': sub.status,
            'is_pro': sub.tier == 'pro' and sub.status == 'active',
            'is_creator': sub.tier == 'creator' and sub.status == 'active',
            'token_balance': sub.token_balance,
            'monthly_tokens': monthly,
            'current_period_end': sub.current_period_end.isoformat() if sub.current_period_end else None
        })
    
    return jsonify({
        'tier': 'free', 
        'status': 'inactive', 
        'is_pro': False,
        'token_balance': 50,
        'monthly_tokens': 50
    })


@stripe_bp.route('/stripe-webhook', methods=['POST'])
def stripe_webhook():
    """Handle Stripe webhook events."""
    from models import Subscription, UserTokens
    from datetime import datetime
    
    payload = request.get_data()
    sig_header = request.headers.get('Stripe-Signature')
    
    _, secret_key = get_stripe_credentials()
    if not secret_key:
        return jsonify({'error': 'Payment not configured'}), 500
    
    stripe.api_key = secret_key
    
    try:
        event = stripe.Event.construct_from(json.loads(payload), stripe.api_key)
    except ValueError:
        return jsonify({'error': 'Invalid payload'}), 400
    
    TIER_TOKENS = {'free': 50, 'creator': 300, 'pro': 1000}
    
    if event['type'] == 'checkout.session.completed':
        session_data = event['data']['object']
        
        if session_data.get('mode') == 'subscription':
            user_id = session_data.get('metadata', {}).get('user_id')
            plan = session_data.get('metadata', {}).get('plan', 'pro')
            subscription_id = session_data.get('subscription')
            customer_id = session_data.get('customer')
            
            if user_id:
                sub = Subscription.query.filter_by(user_id=user_id).first()
                if not sub:
                    sub = Subscription(user_id=user_id)
                    db.session.add(sub)
                
                sub.stripe_customer_id = customer_id
                sub.stripe_subscription_id = subscription_id
                sub.tier = plan
                sub.status = 'active'
                sub.token_balance = TIER_TOKENS.get(plan, 300)
                sub.token_refresh_date = datetime.now()
                db.session.commit()
                print(f"[stripe-webhook] New {plan} subscription for {user_id}, {sub.token_balance} tokens")
        else:
            token_amount = int(session_data.get('metadata', {}).get('token_amount', 0))
            if token_amount > 0:
                token_entry = UserTokens.query.first()
                if token_entry:
                    token_entry.balance += token_amount
                    db.session.commit()
    
    elif event['type'] == 'invoice.paid':
        invoice_data = event['data']['object']
        subscription_id = invoice_data.get('subscription')
        
        if subscription_id:
            sub = Subscription.query.filter_by(stripe_subscription_id=subscription_id).first()
            if sub and sub.status == 'active':
                sub.token_balance = TIER_TOKENS.get(sub.tier, 50)
                sub.token_refresh_date = datetime.now()
                db.session.commit()
                print(f"[stripe-webhook] Token refresh for {sub.user_id}: {sub.token_balance} tokens")
    
    elif event['type'] == 'customer.subscription.updated':
        subscription_data = event['data']['object']
        stripe_sub_id = subscription_data.get('id')
        status = subscription_data.get('status')
        period_end = subscription_data.get('current_period_end')
        
        sub = Subscription.query.filter_by(stripe_subscription_id=stripe_sub_id).first()
        if sub:
            sub.status = 'active' if status == 'active' else 'inactive'
            if period_end:
                sub.current_period_end = datetime.fromtimestamp(period_end)
            db.session.commit()
    
    elif event['type'] == 'customer.subscription.deleted':
        subscription_data = event['data']['object']
        stripe_sub_id = subscription_data.get('id')
        
        sub = Subscription.query.filter_by(stripe_subscription_id=stripe_sub_id).first()
        if sub:
            sub.status = 'canceled'
            sub.tier = 'free'
            sub.token_balance = TIER_TOKENS['free']
            db.session.commit()
    
    return jsonify({'received': True})

@stripe_bp.route('/add-tokens', methods=['POST'])
def add_tokens():
    """Add tokens after successful payment (called from frontend on success)."""
    from models import UserTokens
    
    data = request.get_json()
    amount = data.get('amount', 0)
    
    if amount > 0:
        token_entry = UserTokens.query.first()
        if token_entry:
            token_entry.balance += amount
            db.session.commit()
            return jsonify({'success': True, 'balance': token_entry.balance})
    
    return jsonify({'success': False, 'error': 'Invalid amount'}), 400

@stripe_bp.route('/get-tokens', methods=['GET'])
def get_tokens():
    from models import User, Subscription, UserTokens
    from flask_login import current_user
    
    TIER_TOKENS = {'free': 50, 'creator': 300, 'pro': 1000}
    user_id = _get_user_id()
    if user_id:
        user = User.query.get(user_id)
        if user:
            sub = Subscription.query.filter_by(user_id=user_id).first()
            tier = sub.tier if sub and sub.status == 'active' else 'free'
            monthly_tokens = TIER_TOKENS.get(tier, 50)
            return jsonify({
                'success': True,
                'balance': user.tokens or 0,
                'monthly_tokens': monthly_tokens,
                'tier': tier
            })
    token_entry = UserTokens.query.first()
    return jsonify({
        'success': True,
        'balance': token_entry.balance if token_entry else 0,
        'monthly_tokens': 50,
        'tier': 'free'
    })

@stripe_bp.route('/deduct-tokens', methods=['POST'])
def deduct_tokens():
    from models import User, UserTokens
    
    data = request.get_json()
    amount = data.get('amount', 35)
    user_id = _get_user_id()
    if user_id:
        user = User.query.get(user_id)
        if user:
            user.tokens = max(0, (user.tokens or 0) - amount)
            db.session.commit()
            return jsonify({'success': True, 'balance': user.tokens})
    token_entry = UserTokens.query.first()
    if token_entry:
        token_entry.balance -= amount
        db.session.commit()
        return jsonify({'success': True, 'balance': token_entry.balance})
    return jsonify({'success': False, 'error': 'No token record'}), 400


def _get_user_id():
    """Get user ID - supports both authenticated users and dev mode."""
    from flask_login import current_user
    if current_user.is_authenticated:
        return current_user.id
    if session.get('dev_mode'):
        return 'dev_user'
    return None
