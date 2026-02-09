"""
Overlay templates blueprint.
Handles CRUD for overlay templates, elements, project overlays, and monthly usage tracking.
"""
import json
from datetime import datetime
from flask import Blueprint, request, jsonify
from extensions import db
from models import OverlayTemplate, OverlayElement, ProjectOverlay, MonthlyUsage
from routes.utils import get_user_id

overlays_bp = Blueprint('overlays', __name__)

OVERLAY_ELEMENT_TYPES = [
    'caption', 'logo', 'lower_third', 'text', 'watermark', 'progress_bar', 'cta'
]

CLIPPER_BASE_PRICE = 0.49
CLIPPER_MAX_PRICE = 1.49
CLIPPER_MONTHLY_CAP = 29.99

OVERLAY_PRICING = {
    'caption': 0.20,
    'logo': 0.10,
    'lower_third': 0.15,
    'text': 0.05,
    'watermark': 0.05,
    'progress_bar': 0.10,
    'cta': 0.10,
}


def calculate_clip_price(overlay_elements):
    price = CLIPPER_BASE_PRICE
    for el in overlay_elements:
        el_type = el.get('element_type', '') if isinstance(el, dict) else getattr(el, 'element_type', '')
        price += OVERLAY_PRICING.get(el_type, 0)
    return min(price, CLIPPER_MAX_PRICE)


def get_or_create_monthly_usage(user_id):
    month_key = datetime.now().strftime('%Y-%m')
    usage = MonthlyUsage.query.filter_by(user_id=user_id, month=month_key).first()
    if not usage:
        usage = MonthlyUsage(user_id=user_id, month=month_key)
        db.session.add(usage)
        db.session.commit()
    return usage


def check_and_charge(user_id, overlay_elements):
    usage = get_or_create_monthly_usage(user_id)
    if usage.cap_reached:
        return 0.0, usage

    price = calculate_clip_price(overlay_elements)
    new_total = usage.clipper_spend + price

    if new_total >= CLIPPER_MONTHLY_CAP:
        actual_charge = max(0, CLIPPER_MONTHLY_CAP - usage.clipper_spend)
        usage.clipper_spend = CLIPPER_MONTHLY_CAP
        usage.cap_reached = True
    else:
        actual_charge = price
        usage.clipper_spend = new_total

    usage.clip_count += 1
    db.session.commit()
    return actual_charge, usage


@overlays_bp.route('/api/overlays/templates', methods=['GET'])
def list_templates():
    user_id = get_user_id()
    if not user_id:
        return jsonify({'ok': False, 'error': 'Not authenticated'}), 401

    permanent = OverlayTemplate.query.filter_by(
        user_id=user_id, is_permanent=True
    ).order_by(OverlayTemplate.updated_at.desc()).all()

    recent = OverlayTemplate.query.filter_by(
        user_id=user_id, is_permanent=False
    ).order_by(OverlayTemplate.updated_at.desc()).limit(10).all()

    return jsonify({
        'ok': True,
        'saved': [t.to_dict() for t in permanent],
        'recent': [t.to_dict() for t in recent],
    })


@overlays_bp.route('/api/overlays/templates', methods=['POST'])
def create_template():
    user_id = get_user_id()
    if not user_id:
        return jsonify({'ok': False, 'error': 'Not authenticated'}), 401

    data = request.get_json()
    name = data.get('name', 'Untitled Overlay').strip()
    is_permanent = data.get('is_permanent', False)
    elements = data.get('elements', [])

    template = OverlayTemplate(
        user_id=user_id,
        name=name[:255],
        is_permanent=is_permanent,
    )
    db.session.add(template)
    db.session.flush()

    for i, el in enumerate(elements):
        el_type = el.get('element_type', '')
        if el_type not in OVERLAY_ELEMENT_TYPES:
            continue
        element = OverlayElement(
            template_id=template.id,
            element_type=el_type,
            position=el.get('position', {}),
            style=el.get('style', {}),
            content=el.get('content', {}),
            layer_order=el.get('layer_order', i),
            is_visible=el.get('is_visible', True),
        )
        db.session.add(element)

    db.session.commit()
    return jsonify({'ok': True, 'template': template.to_dict()}), 201


@overlays_bp.route('/api/overlays/templates/<int:template_id>', methods=['GET'])
def get_template(template_id):
    user_id = get_user_id()
    if not user_id:
        return jsonify({'ok': False, 'error': 'Not authenticated'}), 401

    template = OverlayTemplate.query.filter_by(id=template_id, user_id=user_id).first()
    if not template:
        return jsonify({'ok': False, 'error': 'Template not found'}), 404

    return jsonify({'ok': True, 'template': template.to_dict()})


@overlays_bp.route('/api/overlays/templates/<int:template_id>', methods=['PUT'])
def update_template(template_id):
    user_id = get_user_id()
    if not user_id:
        return jsonify({'ok': False, 'error': 'Not authenticated'}), 401

    template = OverlayTemplate.query.filter_by(id=template_id, user_id=user_id).first()
    if not template:
        return jsonify({'ok': False, 'error': 'Template not found'}), 404

    data = request.get_json()
    if 'name' in data:
        template.name = data['name'][:255]

    if 'elements' in data:
        OverlayElement.query.filter_by(template_id=template.id).delete()
        for i, el in enumerate(data['elements']):
            el_type = el.get('element_type', '')
            if el_type not in OVERLAY_ELEMENT_TYPES:
                continue
            element = OverlayElement(
                template_id=template.id,
                element_type=el_type,
                position=el.get('position', {}),
                style=el.get('style', {}),
                content=el.get('content', {}),
                layer_order=el.get('layer_order', i),
                is_visible=el.get('is_visible', True),
            )
            db.session.add(element)

    db.session.commit()
    return jsonify({'ok': True, 'template': template.to_dict()})


@overlays_bp.route('/api/overlays/templates/<int:template_id>', methods=['DELETE'])
def delete_template(template_id):
    user_id = get_user_id()
    if not user_id:
        return jsonify({'ok': False, 'error': 'Not authenticated'}), 401

    template = OverlayTemplate.query.filter_by(id=template_id, user_id=user_id).first()
    if not template:
        return jsonify({'ok': False, 'error': 'Template not found'}), 404

    db.session.delete(template)
    db.session.commit()
    return jsonify({'ok': True})


@overlays_bp.route('/api/overlays/templates/<int:template_id>/promote', methods=['POST'])
def promote_template(template_id):
    user_id = get_user_id()
    if not user_id:
        return jsonify({'ok': False, 'error': 'Not authenticated'}), 401

    template = OverlayTemplate.query.filter_by(id=template_id, user_id=user_id).first()
    if not template:
        return jsonify({'ok': False, 'error': 'Template not found'}), 404

    template.is_permanent = True
    db.session.commit()
    return jsonify({'ok': True, 'template': template.to_dict()})


@overlays_bp.route('/api/projects/<int:project_id>/overlays', methods=['GET'])
def get_project_overlays(project_id):
    user_id = get_user_id()
    if not user_id:
        return jsonify({'ok': False, 'error': 'Not authenticated'}), 401

    overlays = ProjectOverlay.query.filter_by(
        project_id=project_id, user_id=user_id
    ).order_by(ProjectOverlay.applied_at.desc()).all()

    return jsonify({
        'ok': True,
        'overlays': [o.to_dict() for o in overlays],
    })


@overlays_bp.route('/api/projects/<int:project_id>/overlays', methods=['POST'])
def apply_overlay_to_project(project_id):
    user_id = get_user_id()
    if not user_id:
        return jsonify({'ok': False, 'error': 'Not authenticated'}), 401

    data = request.get_json()
    template_id = data.get('template_id')
    overlay_config = data.get('overlay_config', {})

    ProjectOverlay.query.filter_by(
        project_id=project_id, user_id=user_id
    ).update({'is_active': False})

    project_overlay = ProjectOverlay(
        project_id=project_id,
        user_id=user_id,
        template_id=template_id,
        overlay_config=overlay_config,
        is_active=True,
    )
    db.session.add(project_overlay)

    if template_id:
        template = OverlayTemplate.query.get(template_id)
        if template:
            template.usage_count += 1

    db.session.commit()
    return jsonify({'ok': True, 'overlay': project_overlay.to_dict()})


@overlays_bp.route('/api/overlays/usage', methods=['GET'])
def get_usage():
    user_id = get_user_id()
    if not user_id:
        return jsonify({'ok': False, 'error': 'Not authenticated'}), 401

    usage = get_or_create_monthly_usage(user_id)
    remaining = max(0, CLIPPER_MONTHLY_CAP - usage.clipper_spend)

    return jsonify({
        'ok': True,
        'month': usage.month,
        'spent': round(usage.clipper_spend, 2),
        'clip_count': usage.clip_count,
        'cap': CLIPPER_MONTHLY_CAP,
        'remaining': round(remaining, 2),
        'cap_reached': usage.cap_reached,
        'cap_progress': min(100, round((usage.clipper_spend / CLIPPER_MONTHLY_CAP) * 100, 1)),
    })


@overlays_bp.route('/api/overlays/price-estimate', methods=['POST'])
def price_estimate():
    user_id = get_user_id()
    if not user_id:
        return jsonify({'ok': False, 'error': 'Not authenticated'}), 401

    data = request.get_json()
    elements = data.get('elements', [])
    price = calculate_clip_price(elements)

    usage = get_or_create_monthly_usage(user_id)
    if usage.cap_reached:
        price = 0.0

    return jsonify({
        'ok': True,
        'estimated_price': round(price, 2),
        'base_price': CLIPPER_BASE_PRICE,
        'overlay_cost': round(price - CLIPPER_BASE_PRICE, 2),
        'cap_reached': usage.cap_reached,
    })


@overlays_bp.route('/api/overlays/element-types', methods=['GET'])
def get_element_types():
    return jsonify({
        'ok': True,
        'types': [
            {
                'type': 'caption',
                'label': 'Captions',
                'description': 'Word-by-word synced captions from your audio',
                'cost': OVERLAY_PRICING['caption'],
                'icon': 'subtitles',
            },
            {
                'type': 'logo',
                'label': 'Logo',
                'description': 'Upload your logo and position it anywhere on the video',
                'cost': OVERLAY_PRICING['logo'],
                'icon': 'badge',
            },
            {
                'type': 'lower_third',
                'label': 'Lower Third',
                'description': 'Name and title bar that slides in from the bottom',
                'cost': OVERLAY_PRICING['lower_third'],
                'icon': 'view_agenda',
            },
            {
                'type': 'text',
                'label': 'Text Overlay',
                'description': 'Custom text with your choice of font, color, and animation',
                'cost': OVERLAY_PRICING['text'],
                'icon': 'text_fields',
            },
            {
                'type': 'watermark',
                'label': 'Watermark',
                'description': 'Semi-transparent branding overlay',
                'cost': OVERLAY_PRICING['watermark'],
                'icon': 'branding_watermark',
            },
            {
                'type': 'progress_bar',
                'label': 'Progress Bar',
                'description': 'Engagement hook that shows video progress',
                'cost': OVERLAY_PRICING['progress_bar'],
                'icon': 'linear_scale',
            },
            {
                'type': 'cta',
                'label': 'Call to Action',
                'description': 'Banners like "Follow", "Link in bio", "Subscribe"',
                'cost': OVERLAY_PRICING['cta'],
                'icon': 'campaign',
            },
        ],
    })
