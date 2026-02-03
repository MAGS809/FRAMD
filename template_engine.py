"""
Template Engine - Extracts video templates with element-level precision.
Analyzes uploaded videos to create reusable templates with named element slots.
"""

import os
import json
import logging
import subprocess
import base64
import tempfile
from typing import List, Dict, Any, Optional

ELEMENT_GROUPS = {
    'branding': ['logo_main', 'logo_secondary', 'watermark', 'brand_colors', 'brand_font'],
    'text': ['headline', 'subheadline', 'body_text', 'caption', 'label', 'cta_text', 'stat_number', 'quote', 'hashtag'],
    'visuals': ['background', 'product_shot', 'person_shot', 'b_roll', 'screenshot', 'graphic', 'photo', 'overlay', 'frame'],
    'motion': ['transition', 'animation_in', 'animation_out', 'parallax_layer', 'particle_effect', 'kinetic_text'],
    'interactive': ['button', 'qr_code', 'link_preview', 'poll_graphic'],
    'data': ['chart', 'timeline', 'comparison', 'list_item', 'price_tag'],
    'audio': ['voiceover', 'sound_effect']
}

ANIMATION_TYPES = [
    'fade', 'slide_left', 'slide_right', 'slide_up', 'slide_down',
    'zoom_in', 'zoom_out', 'pop', 'bounce', 'rotate', 'flip',
    'wipe_left', 'wipe_right', 'dissolve', 'none'
]

MOTION_TYPES = [
    'static', 'slow_zoom_in', 'slow_zoom_out', 'pan_left', 'pan_right',
    'pan_up', 'pan_down', 'tracking', 'handheld', 'parallax'
]


def extract_frames_for_analysis(video_path: str, num_frames: int = 10) -> List[Dict]:
    """Extract frames at regular intervals for element detection."""
    frames = []
    temp_dir = tempfile.mkdtemp()
    
    try:
        probe_cmd = ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', '-show_streams', video_path]
        result = subprocess.run(probe_cmd, capture_output=True, text=True)
        probe_data = json.loads(result.stdout)
        
        duration = float(probe_data['format']['duration'])
        fps = 30
        for stream in probe_data.get('streams', []):
            if stream.get('codec_type') == 'video':
                fps_str = stream.get('r_frame_rate', '30/1')
                if '/' in fps_str:
                    num, den = fps_str.split('/')
                    fps = float(num) / float(den) if float(den) > 0 else 30
                break
        
        interval = duration / (num_frames + 1)
        
        for i in range(num_frames):
            timestamp = interval * (i + 1)
            frame_path = os.path.join(temp_dir, f'frame_{i:03d}.jpg')
            
            cmd = [
                'ffmpeg', '-y', '-ss', str(timestamp),
                '-i', video_path, '-vframes', '1',
                '-q:v', '2', frame_path
            ]
            subprocess.run(cmd, capture_output=True)
            
            if os.path.exists(frame_path):
                with open(frame_path, 'rb') as f:
                    frame_b64 = base64.b64encode(f.read()).decode('utf-8')
                
                frames.append({
                    'index': i,
                    'timestamp': timestamp,
                    'path': frame_path,
                    'base64': frame_b64,
                    'start_time': timestamp - (interval / 2),
                    'end_time': timestamp + (interval / 2)
                })
        
        return frames, duration, fps
        
    except Exception as e:
        logging.error(f"Frame extraction failed: {e}")
        return [], 0, 30


def analyze_frame_elements(frame_b64: str, timestamp: float, anthropic_client=None, openai_client=None) -> List[Dict]:
    """Analyze a single frame to detect all elements with positions and properties."""
    
    prompt = """Analyze this video frame and identify ALL visual elements present. For each element, provide:

Output ONLY valid JSON array:
[
    {
        "element_type": "headline|subheadline|logo_main|product_shot|person_shot|background|button|cta_text|stat_number|graphic|overlay|caption|watermark|etc",
        "element_group": "branding|text|visuals|motion|interactive|data",
        "display_name": "Human readable name (e.g., 'Main Headline', 'Product Image')",
        "position": {
            "x": 0.0-1.0,
            "y": 0.0-1.0,
            "width": 0.0-1.0,
            "height": 0.0-1.0
        },
        "z_index": 0-10,
        "content_description": "What this element shows/says",
        "original_content": "Exact text if readable, or brief visual description",
        "style_properties": {
            "font_style": "bold|regular|light|etc",
            "color": "#hex or description",
            "background": "#hex or transparent",
            "effects": ["shadow", "glow", "outline", "none"]
        },
        "animation_detected": "fade|slide_left|zoom|pop|none|unknown",
        "is_swappable": true/false,
        "swap_prompt_hint": "What kind of content should replace this"
    }
]

Detect:
- ALL text (headlines, labels, CTAs, stats, captions)
- ALL logos and brand elements
- Product shots and person shots
- Buttons and interactive elements
- Overlays, graphics, frames
- Background layers

Be thorough - even small text elements matter."""

    try:
        if anthropic_client:
            response = anthropic_client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2000,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": frame_b64}},
                        {"type": "text", "text": prompt}
                    ]
                }]
            )
            result_text = response.content[0].text
        elif openai_client:
            response = openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{frame_b64}"}}
                    ]
                }],
                max_tokens=2000,
                timeout=180
            )
            result_text = response.choices[0].message.content
        else:
            return []
        
        cleaned = result_text.strip()
        if cleaned.startswith('```'):
            cleaned = cleaned.split('```')[1]
            if cleaned.startswith('json'):
                cleaned = cleaned[4:]
        
        elements = json.loads(cleaned)
        
        for elem in elements:
            elem['detected_at_timestamp'] = timestamp
        
        return elements
        
    except Exception as e:
        logging.warning(f"Frame element analysis failed: {e}")
        return []


def detect_transitions(frames: List[Dict], anthropic_client=None, openai_client=None) -> List[Dict]:
    """Detect transitions between frames."""
    transitions = []
    
    for i in range(len(frames) - 1):
        current = frames[i]
        next_frame = frames[i + 1]
        
        prompt = f"""Compare these two consecutive video frames and detect any transition between them.

Output ONLY valid JSON:
{{
    "has_transition": true/false,
    "transition_type": "cut|dissolve|fade|wipe_left|wipe_right|zoom|slide|none",
    "transition_duration_estimate": 0.0-1.0,
    "scene_change": true/false,
    "sfx_cue": "whoosh|impact|ding|none"
}}"""

        try:
            if anthropic_client:
                response = anthropic_client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=300,
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": current['base64']}},
                            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": next_frame['base64']}},
                            {"type": "text", "text": prompt}
                        ]
                    }]
                )
                result_text = response.content[0].text
            elif openai_client:
                response = openai_client.chat.completions.create(
                    model="gpt-4o",
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{current['base64']}"}},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{next_frame['base64']}"}}
                        ]
                    }],
                    max_tokens=300,
                    timeout=60
                )
                result_text = response.choices[0].message.content
            else:
                continue
            
            cleaned = result_text.strip()
            if cleaned.startswith('```'):
                cleaned = cleaned.split('```')[1]
                if cleaned.startswith('json'):
                    cleaned = cleaned[4:]
            
            transition_data = json.loads(cleaned)
            transition_data['from_timestamp'] = current['timestamp']
            transition_data['to_timestamp'] = next_frame['timestamp']
            transitions.append(transition_data)
            
        except Exception as e:
            logging.warning(f"Transition detection failed between frames {i} and {i+1}: {e}")
    
    return transitions


def merge_elements_across_frames(all_frame_elements: List[List[Dict]]) -> List[Dict]:
    """Merge elements detected across multiple frames into unique elements with timing."""
    merged = {}
    
    for frame_idx, frame_elements in enumerate(all_frame_elements):
        for elem in frame_elements:
            key = f"{elem.get('element_type')}_{elem.get('display_name', '')}_{elem.get('position', {}).get('x', 0):.1f}"
            
            if key not in merged:
                merged[key] = {
                    **elem,
                    'first_seen_frame': frame_idx,
                    'last_seen_frame': frame_idx,
                    'occurrence_count': 1
                }
            else:
                merged[key]['last_seen_frame'] = frame_idx
                merged[key]['occurrence_count'] += 1
    
    unique_elements = []
    counter = {}
    
    for key, elem in merged.items():
        elem_type = elem.get('element_type', 'unknown')
        if elem_type not in counter:
            counter[elem_type] = 0
        counter[elem_type] += 1
        
        elem['name'] = f"{elem_type}_{counter[elem_type]}"
        unique_elements.append(elem)
    
    return unique_elements


def extract_template(video_path: str, template_name: str, anthropic_client=None, openai_client=None) -> Dict:
    """
    Main function: Extract a complete template from a video.
    Returns template data with all elements, transitions, and timing.
    """
    logging.info(f"Starting template extraction for: {video_path}")
    
    frames, duration, fps = extract_frames_for_analysis(video_path, num_frames=8)
    
    if not frames:
        return {'error': 'Failed to extract frames from video'}
    
    all_frame_elements = []
    for frame in frames:
        elements = analyze_frame_elements(
            frame['base64'], 
            frame['timestamp'],
            anthropic_client=anthropic_client,
            openai_client=openai_client
        )
        all_frame_elements.append(elements)
        logging.info(f"Frame {frame['index']}: detected {len(elements)} elements")
    
    transitions = detect_transitions(frames, anthropic_client=anthropic_client, openai_client=openai_client)
    
    merged_elements = merge_elements_across_frames(all_frame_elements)
    
    num_frames_total = len(frames)
    for elem in merged_elements:
        first_frame = elem.get('first_seen_frame', 0)
        last_frame = elem.get('last_seen_frame', num_frames_total - 1)
        
        if first_frame < len(frames):
            elem['start_time'] = frames[first_frame].get('start_time', 0)
        if last_frame < len(frames):
            elem['end_time'] = frames[last_frame].get('end_time', duration)
        elem['duration'] = elem.get('end_time', duration) - elem.get('start_time', 0)
    
    template = {
        'name': template_name,
        'source_video_path': video_path,
        'duration': duration,
        'fps': fps,
        'frame_count': len(frames),
        'elements': merged_elements,
        'transitions': transitions,
        'element_count': len(merged_elements),
        'element_summary': {
            group: len([e for e in merged_elements if e.get('element_group') == group])
            for group in ELEMENT_GROUPS.keys()
        }
    }
    
    logging.info(f"Template extraction complete: {len(merged_elements)} elements, {len(transitions)} transitions")
    
    return template


def match_template_to_request(request_text: str, templates: List[Dict], anthropic_client=None) -> Optional[int]:
    """AI selects the best template for a user's request."""
    
    if not templates:
        return None
    
    template_summaries = []
    for i, t in enumerate(templates):
        summary = f"Template {i}: '{t.get('name')}' - {t.get('element_count', 0)} elements, {t.get('duration', 0):.1f}s duration. Elements: {t.get('element_summary', {})}"
        template_summaries.append(summary)
    
    prompt = f"""User wants to create a video about: "{request_text}"

Available templates:
{chr(10).join(template_summaries)}

Which template index (0-{len(templates)-1}) would work best for this request?
Consider:
- Does the template have appropriate elements (product shots for products, person shots for testimonials, etc.)
- Does the duration fit the content type
- Does the element mix match what the user needs

Output ONLY the template index number (e.g., "0" or "2")."""

    try:
        if anthropic_client:
            response = anthropic_client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=50,
                messages=[{"role": "user", "content": prompt}]
            )
            result = response.content[0].text.strip()
            return int(result)
    except Exception as e:
        logging.warning(f"Template matching failed: {e}")
    
    return 0


def generate_element_content(element: Dict, user_request: str, user_assets: Dict = None, anthropic_client=None) -> Dict:
    """Generate new content for a template element based on user request."""
    
    elem_type = element.get('element_type', 'unknown')
    elem_group = element.get('element_group', 'visuals')
    swap_hint = element.get('swap_prompt_hint', '')
    original = element.get('original_content', '')
    
    prompt = f"""Generate replacement content for this video element:

Element type: {elem_type}
Element group: {elem_group}
Original content: {original}
Swap hint: {swap_hint}

User's video request: "{user_request}"

Output ONLY valid JSON:
{{
    "new_content": "The new text/description for this element",
    "generation_prompt": "If this needs AI image generation, the DALL-E prompt to use",
    "stock_search_query": "If using stock, the search query",
    "source_recommendation": "dalle|stock|user_asset|text_only",
    "style_adjustments": {{}}
}}"""

    try:
        if anthropic_client:
            response = anthropic_client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}]
            )
            result_text = response.content[0].text.strip()
            if result_text.startswith('```'):
                result_text = result_text.split('```')[1]
                if result_text.startswith('json'):
                    result_text = result_text[4:]
            return json.loads(result_text)
    except Exception as e:
        logging.warning(f"Element content generation failed: {e}")
    
    return {
        'new_content': f"[{elem_type}]",
        'source_recommendation': 'text_only'
    }
