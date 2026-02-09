"""
Visual Director - Pre-plans all visuals for coherence.
Establishes visual structure (cards, frames, split screens, etc.) and ensures 
ALL sources (stock, user clips, AI-generated) conform to the same design language.
Stock footage is NEVER raw — it's always placed INSIDE the video's visual containers.
"""
import json
from context_engine import call_ai, SYSTEM_GUARDRAILS


def analyze_visual_structure(brief, template_data=None, source_count=0):
    """
    Given a brief and optional template, determine the video's visual structure.
    Returns a visual_structure dict with:
    - layout_type: 'cards', 'frames', 'split_screen', 'fullscreen', 'collage', 'cinematic'
    - container_style: how each scene container looks (rounded corners, borders, shadows, etc.)
    - color_palette: list of 3-5 hex colors derived from the brief's mood
    - motion_style: 'smooth', 'dynamic', 'static', 'kinetic'
    - transition_style: 'cut', 'slide', 'fade', 'morph', 'zoom'
    - grain_level: 0-100
    - contrast_curve: 'flat', 'normal', 'high', 'cinematic'
    """
    prompt = f"""Analyze this video brief and determine the ideal visual structure.

Brief: {brief}
Template data: {json.dumps(template_data) if template_data else 'None'}
Number of source videos: {source_count}

Return a JSON object with these exact fields:
{{
    "layout_type": "cards|frames|split_screen|fullscreen|collage|cinematic",
    "container_style": {{
        "border_radius": 0-24,
        "has_border": true/false,
        "border_color": "#hex or null",
        "shadow": "none|subtle|strong",
        "padding": 0-20
    }},
    "color_palette": ["#hex1", "#hex2", "#hex3"],
    "motion_style": "smooth|dynamic|static|kinetic",
    "transition_style": "cut|slide|fade|morph|zoom",
    "grain_level": 0-100,
    "contrast_curve": "flat|normal|high|cinematic"
}}

Guidelines:
- For explainer/educational content: use 'cards' or 'frames' layout
- For hype/energy content: use 'cinematic' or 'collage'
- For comparison content: use 'split_screen'
- For storytelling: use 'fullscreen' or 'cinematic'
- Color palette should match the mood (warm for motivational, cool for tech, etc.)
- If multiple sources, prefer layouts that can showcase variety (cards, collage)"""

    result = call_ai(prompt=prompt, system_prompt=SYSTEM_GUARDRAILS, json_output=True, max_tokens=400)

    if isinstance(result, dict) and result:
        return result

    return {
        "layout_type": "fullscreen",
        "container_style": {"border_radius": 0, "has_border": False, "border_color": None, "shadow": "none", "padding": 0},
        "color_palette": ["#0a1f14", "#ffd60a", "#1a1a1a"],
        "motion_style": "smooth",
        "transition_style": "cut",
        "grain_level": 15,
        "contrast_curve": "normal"
    }


def get_stock_search_context(visual_structure, scene_text, surrounding_scenes=None):
    """
    Generate smart stock search parameters that ensure stock footage matches 
    the video's visual identity. Stock is never raw — it must conform to the design language.
    Returns search query, required properties, and post-processing instructions.
    """
    prompt = f"""You are sourcing stock footage for a scene in a video with this visual structure:
{json.dumps(visual_structure)}

Scene text/context: {scene_text}
Surrounding scenes: {json.dumps(surrounding_scenes) if surrounding_scenes else 'None'}

Return a JSON object:
{{
    "search_query": "specific, detailed stock search query",
    "required_properties": {{
        "min_resolution": "720p|1080p",
        "color_temperature": "warm|neutral|cool",
        "movement": "static|slow|moderate|fast",
        "framing": "wide|medium|close-up|aerial"
    }},
    "post_processing": {{
        "color_shift": "description of color grading to match palette",
        "crop_to_container": true,
        "speed_adjust": 1.0,
        "grain_match": true
    }}
}}

The stock footage will be placed INSIDE a '{visual_structure.get("layout_type", "fullscreen")}' container.
It must look like it was always part of the design, not dropped in as a separate clip."""

    result = call_ai(prompt=prompt, system_prompt=SYSTEM_GUARDRAILS, json_output=True, max_tokens=300)

    if isinstance(result, dict) and result:
        return result

    return {
        "search_query": scene_text[:100] if scene_text else "abstract background",
        "required_properties": {"min_resolution": "720p", "color_temperature": "neutral", "movement": "slow", "framing": "medium"},
        "post_processing": {"color_shift": "match video palette", "crop_to_container": True, "speed_adjust": 1.0, "grain_match": True}
    }


def validate_source_coherence(scenes_data, visual_structure):
    """
    Check if all planned scenes will look coherent together given the visual structure.
    Returns validation result with suggestions for improvement.
    """
    prompt = f"""Review this scene plan for visual coherence:

Visual Structure: {json.dumps(visual_structure)}
Scenes: {json.dumps(scenes_data)}

Check:
1. Will all scenes look like they belong in the same video?
2. Are there jarring transitions between different source types?
3. Does the pacing match the motion_style?
4. Will stock footage blend with user clips and AI-generated content?

Return JSON:
{{
    "is_coherent": true/false,
    "coherence_score": 0-100,
    "issues": ["list of specific issues"],
    "suggestions": ["list of improvements"]
}}"""

    result = call_ai(prompt=prompt, system_prompt=SYSTEM_GUARDRAILS, json_output=True, max_tokens=300)

    if isinstance(result, dict) and result:
        return result

    return {"is_coherent": True, "coherence_score": 75, "issues": [], "suggestions": []}
