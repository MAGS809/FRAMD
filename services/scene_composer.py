"""
Scene Composer - Merges all ScenePlan entries into one coherent timeline.
Handles gap-filling, ordering by brief structure, and consistent post-processing.
"""
import json
from context_engine import call_ai, SYSTEM_GUARDRAILS

ANCHOR_ORDER = ['hook', 'claim', 'evidence', 'pivot', 'counter', 'closer']


def order_scenes_by_structure(scenes, brief=None):
    """
    Order scenes according to the brief's narrative structure.
    Uses anchor types (hook, claim, evidence, etc.) to determine order.
    Scenes without anchor types are placed based on AI analysis.
    """
    anchored = {s['anchor_type']: s for s in scenes if s.get('anchor_type') in ANCHOR_ORDER}
    unanchored = [s for s in scenes if s.get('anchor_type') not in ANCHOR_ORDER]

    ordered = []
    for anchor in ANCHOR_ORDER:
        if anchor in anchored:
            ordered.append(anchored[anchor])

    if unanchored:
        if brief:
            prompt = f"""These scenes need to be placed in the timeline.
Brief: {brief}
Already ordered scenes: {json.dumps([s.get('script_text', '')[:50] for s in ordered])}
Unplaced scenes: {json.dumps([{'index': i, 'text': s.get('script_text', '')[:50], 'source_type': s.get('source_type')} for i, s in enumerate(unanchored)])}

Return a JSON array of indices in the order they should be inserted, and where:
{{"placements": [{{"scene_index": 0, "insert_after": 2}}]}}"""
            result = call_ai(prompt=prompt, system_prompt=SYSTEM_GUARDRAILS, json_output=True, max_tokens=200)
            if isinstance(result, dict) and 'placements' in result:
                for p in result['placements']:
                    idx = p.get('scene_index', 0)
                    after = p.get('insert_after', len(ordered))
                    if idx < len(unanchored):
                        ordered.insert(min(after + 1, len(ordered)), unanchored[idx])
                        unanchored[idx] = None
                unanchored = [s for s in unanchored if s is not None]

        ordered.extend(unanchored)

    for i, scene in enumerate(ordered):
        scene['scene_index'] = i

    return ordered


def identify_gaps(scenes, target_duration=30):
    """
    Identify gaps in the timeline that need filling.
    Returns list of gap positions with suggested content.
    """
    total_duration = sum(s.get('duration', 0) or 0 for s in scenes)
    gaps = []

    if total_duration < target_duration:
        remaining = target_duration - total_duration
        gap_count = max(1, int(remaining / 5))
        per_gap = remaining / gap_count

        for i in range(gap_count):
            insert_pos = min(i * (len(scenes) // max(gap_count, 1)) + 1, len(scenes))
            gaps.append({
                'insert_position': insert_pos,
                'duration': round(per_gap, 1),
                'suggested_source': 'stock',
                'reason': 'timeline gap fill'
            })

    return gaps


def fill_gaps_with_ai(gaps, brief, visual_structure):
    """
    Use AI to determine what content should fill each gap.
    Returns scene specifications for gap-filling content.
    """
    if not gaps:
        return []

    prompt = f"""Fill these timeline gaps with appropriate content.

Brief: {brief}
Visual structure: {json.dumps(visual_structure)}
Gaps to fill: {json.dumps(gaps)}

For each gap, decide the best source:
- 'stock': realistic b-roll that matches the topic
- 'dalle': AI-generated image for abstract/stylized moments
- 'transition': a visual transition or breathing room

Return JSON array:
[{{
    "gap_index": 0,
    "source_type": "stock|dalle|transition",
    "content_description": "what should appear",
    "visual_container": "fullscreen|card|frame",
    "search_query": "stock search terms if stock",
    "duration": 3.0,
    "estimated_cost": 0.15
}}]"""

    result = call_ai(prompt=prompt, system_prompt=SYSTEM_GUARDRAILS, json_output=True, max_tokens=400)

    if isinstance(result, list):
        return result
    if isinstance(result, dict) and 'gaps' in result:
        return result['gaps']

    return [{'gap_index': i, 'source_type': 'stock', 'content_description': 'contextual b-roll',
             'visual_container': visual_structure.get('layout_type', 'fullscreen'),
             'search_query': brief[:50] if brief else 'abstract', 'duration': g['duration'],
             'estimated_cost': 0.15} for i, g in enumerate(gaps)]


def build_unified_timeline(scenes, visual_structure, overlays=None):
    """
    Build the final timeline specification from ordered scenes.
    Applies consistent color grading, transitions, and overlay placement.
    Returns a complete timeline ready for rendering.
    """
    timeline = {
        'visual_structure': visual_structure,
        'total_duration': sum(s.get('duration', 0) or 0 for s in scenes),
        'scene_count': len(scenes),
        'color_grade': {
            'palette': visual_structure.get('color_palette', []),
            'grain': visual_structure.get('grain_level', 15),
            'contrast': visual_structure.get('contrast_curve', 'normal')
        },
        'scenes': [],
        'overlays': overlays or []
    }

    current_time = 0.0
    for i, scene in enumerate(scenes):
        duration = scene.get('duration', 0) or 3.0

        timeline_scene = {
            'index': i,
            'source_type': scene.get('source_type', 'stock'),
            'source_config': scene.get('source_config', {}),
            'visual_container': scene.get('visual_container', visual_structure.get('layout_type', 'fullscreen')),
            'container_config': scene.get('container_config', visual_structure.get('container_style', {})),
            'start_time': current_time,
            'end_time': current_time + duration,
            'duration': duration,
            'script_text': scene.get('script_text', ''),
            'transition_in': scene.get('transition_in') or visual_structure.get('transition_style', 'cut'),
            'transition_out': scene.get('transition_out') or visual_structure.get('transition_style', 'cut'),
            'post_processing': {
                'color_match': True,
                'grain_match': True,
                'crop_to_container': scene.get('visual_container', 'fullscreen') != 'fullscreen'
            }
        }

        timeline['scenes'].append(timeline_scene)
        current_time += duration

    timeline['total_duration'] = current_time
    return timeline


def generate_overlay_plan(brief, visual_structure, scenes):
    """
    AI generates matching overlays for the entire timeline.
    Overlays are style-matched to the video's visual identity.
    """
    prompt = f"""Generate an overlay plan for this video.

Brief: {brief}
Visual structure: {json.dumps(visual_structure)}
Scene count: {len(scenes)}
Total duration: {sum(s.get('duration', 0) or 0 for s in scenes)}s

Suggest overlays that match the visual style. Available types:
- caption: word-synced captions (requires audio)
- lower_third: name/title bar
- text: custom text overlay
- progress_bar: engagement hook
- cta: call-to-action banner
- watermark: branding element

Return JSON:
{{
    "overlays": [{{
        "type": "caption|lower_third|text|progress_bar|cta|watermark",
        "content": "text content",
        "position": "bottom_center|top_right|bottom_left|center",
        "start_time": 0.0,
        "end_time": null,
        "style": {{
            "font_size": 24,
            "color": "#hex",
            "background": "#hex or transparent"
        }}
    }}],
    "caption_style": "bold_pop|clean_minimal|boxed|gradient_glow|street_style"
}}"""

    result = call_ai(prompt=prompt, system_prompt=SYSTEM_GUARDRAILS, json_output=True, max_tokens=400)

    if isinstance(result, dict) and result:
        return result

    return {"overlays": [], "caption_style": "clean_minimal"}
