"""
Payment routes blueprint.
Handles Stripe payments, subscriptions, and token management.
"""
import os
import json
import stripe
from datetime import datetime
from flask import Blueprint, request, jsonify, session, redirect
from flask_login import current_user

from extensions import db
from models import Subscription, UserTokens
from routes.utils import (
    get_stripe_credentials, TOKEN_PACKAGES, SUBSCRIPTION_TIERS,
    TIER_TOKENS, get_base_url
)

payments_bp = Blueprint('payments', __name__)


@payments_bp.route('/create-checkout-session', methods=['POST'])
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
        base_url = get_base_url()
        
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


@payments_bp.route('/create-token-checkout', methods=['POST'])
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
        base_url = get_base_url()
        
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


@payments_bp.route('/create-subscription', methods=['POST'])
def create_subscription():
    """Create a Stripe subscription checkout session for Creator or Pro tier."""
    try:
        data = request.get_json() or {}
        tier = data.get('tier', 'pro')
        
        if tier not in SUBSCRIPTION_TIERS:
            return jsonify({'error': 'Invalid tier'}), 400
        
        tier_info = SUBSCRIPTION_TIERS[tier]
        
        _, secret_key = get_stripe_credentials()
        if not secret_key:
            return jsonify({'error': 'Payment not configured'}), 500
        
        stripe.api_key = secret_key
        base_url = get_base_url()
        
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


@payments_bp.route('/subscribe')
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
    base_url = get_base_url()
    
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
        return redirect(checkout_session.url or '/?error=no_url')
    except Exception as e:
        print(f"Stripe error: {e}")
        return redirect('/?error=checkout_failed')


@payments_bp.route('/create-customer-portal', methods=['POST'])
def customer_portal():
    """Create a Stripe Customer Portal session for managing subscriptions."""
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
    
    base_url = get_base_url()
    
    try:
        portal_session = stripe.billing_portal.Session.create(
            customer=sub.stripe_customer_id,
            return_url=f'{base_url}/?settings=billing'
        )
        return jsonify({'url': portal_session.url})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@payments_bp.route('/subscription-status', methods=['GET'])
def subscription_status():
    """Check current user's subscription status and token balance."""
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


@payments_bp.route('/stripe-webhook', methods=['POST'])
def stripe_webhook():
    """Handle Stripe webhook events."""
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


@payments_bp.route('/add-tokens', methods=['POST'])
def add_tokens():
    """Add tokens after successful payment."""
    data = request.get_json()
    amount = data.get('amount', 0)
    
    if amount > 0:
        token_entry = UserTokens.query.first()
        if token_entry:
            token_entry.balance += amount
            db.session.commit()
            return jsonify({'success': True, 'balance': token_entry.balance})
    
    return jsonify({'success': False, 'error': 'Invalid amount'}), 400


@payments_bp.route('/get-tokens', methods=['GET'])
def get_tokens():
    """Get current token balance."""
    token_entry = UserTokens.query.first()
    return jsonify({
        'success': True,
        'balance': token_entry.balance if token_entry else 0
    })


@payments_bp.route('/deduct-tokens', methods=['POST'])
def deduct_tokens():
    """Deduct tokens for video generation."""
    data = request.get_json()
    amount = data.get('amount', 35)
    token_entry = UserTokens.query.first()
    if not token_entry:
        return jsonify({'success': False, 'error': 'No token entry found'}), 404
    token_entry.balance -= amount
    db.session.commit()
    return jsonify({'success': True, 'balance': token_entry.balance})
