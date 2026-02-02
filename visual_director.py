"""
Visual Director AI - Plans all visuals before generation for coherent, professional output.

This module:
1. Analyzes the full script to understand content type and themes
2. Creates a visual plan with style, color palette, and source decisions per scene
3. Decides optimal source for each scene: stock photo, DALL-E, or user content
4. Outputs a coherent plan that the render pipeline executes
"""

import os
import json
import hashlib
from typing import List, Dict, Optional, Any
from datetime import datetime

# Content type patterns for automatic detection
CONTENT_TYPE_PATTERNS = {
    'podcast': ['podcast', 'episode', 'interview', 'conversation', 'guest', 'host'],
    'explainer': ['explain', 'how to', 'tutorial', 'guide', 'learn', 'understand'],
    'hot_take': ['hot take', 'opinion', 'controversial', 'debate', 'unpopular'],
    'ad': ['ad', 'advertisement', 'promo', 'promote', 'buy', 'sale', 'discount'],
    'story': ['story', 'narrative', 'once upon', 'journey', 'adventure'],
    'news': ['breaking', 'news', 'update', 'report', 'announcement'],
    'meme': ['meme', 'funny', 'joke', 'humor', 'lol', 'comedy']
}

# Editing patterns per content type
EDITING_DNA = {
    'podcast': {
        'pacing': 'medium',
        'cut_style': 'speaker_focused',
        'visual_preference': ['stock_people', 'user_content', 'b_roll'],
        'color_mood': 'warm_professional',
        'transitions': 'smooth_fade'
    },
    'explainer': {
        'pacing': 'measured',
        'cut_style': 'topic_driven',
        'visual_preference': ['diagrams', 'stock', 'ai_generated'],
        'color_mood': 'clean_modern',
        'transitions': 'slide'
    },
    'hot_take': {
        'pacing': 'fast',
        'cut_style': 'punchy_cuts',
        'visual_preference': ['ai_generated', 'stock_dramatic', 'meme_style'],
        'color_mood': 'bold_contrast',
        'transitions': 'quick_cut'
    },
    'ad': {
        'pacing': 'dynamic',
        'cut_style': 'product_focused',
        'visual_preference': ['user_content', 'stock_lifestyle', 'ai_generated'],
        'color_mood': 'brand_aligned',
        'transitions': 'energetic'
    },
    'story': {
        'pacing': 'cinematic',
        'cut_style': 'narrative_flow',
        'visual_preference': ['ai_generated', 'stock_cinematic'],
        'color_mood': 'atmospheric',
        'transitions': 'cinematic_fade'
    },
    'news': {
        'pacing': 'urgent',
        'cut_style': 'news_style',
        'visual_preference': ['stock_news', 'graphics'],
        'color_mood': 'professional_serious',
        'transitions': 'news_wipe'
    },
    'meme': {
        'pacing': 'chaotic',
        'cut_style': 'meme_cuts',
        'visual_preference': ['ai_generated', 'meme_templates'],
        'color_mood': 'vibrant_saturated',
        'transitions': 'jump_cut'
    }
}

# Color palettes per mood
COLOR_PALETTES = {
    'warm_professional': ['#2D3436', '#636E72', '#DFE6E9', '#FDCB6E', '#E17055'],
    'clean_modern': ['#FFFFFF', '#F5F6FA', '#2C3E50', '#3498DB', '#1ABC9C'],
    'bold_contrast': ['#000000', '#FFFFFF', '#E74C3C', '#F39C12', '#9B59B6'],
    'brand_aligned': ['#000000', '#FFFFFF', '#FFD60A', '#1A1A1A'],  # Uses Framd colors
    'atmospheric': ['#1A1A2E', '#16213E', '#0F3460', '#E94560', '#533483'],
    'professional_serious': ['#2C3E50', '#34495E', '#ECF0F1', '#E74C3C', '#3498DB'],
    'vibrant_saturated': ['#FF6B6B', '#4ECDC4', '#45B7D1', '#96CEB4', '#FFEAA7']
}

# Color grading profiles for Source Merging Engine
COLOR_GRADING_PROFILES = {
    'warm_cinematic': {
        'name': 'Warm Cinematic',
        'description': 'Rich warm tones with subtle orange shadows',
        'ffmpeg_filter': 'colorbalance=rs=0.1:gs=-0.05:bs=-0.1:rm=0.05:gm=0:bm=-0.05,eq=saturation=1.1:contrast=1.05',
        'mood': 'intimate, personal, storytelling'
    },
    'cool_professional': {
        'name': 'Cool Professional',
        'description': 'Clean blue-tinted look for business content',
        'ffmpeg_filter': 'colorbalance=rs=-0.05:gs=0:bs=0.1:rm=-0.02:gm=0:bm=0.05,eq=saturation=0.95:contrast=1.1',
        'mood': 'corporate, trustworthy, modern'
    },
    'punchy_vibrant': {
        'name': 'Punchy Vibrant',
        'description': 'High contrast, saturated colors for impact',
        'ffmpeg_filter': 'eq=saturation=1.3:contrast=1.2:brightness=0.02,unsharp=5:5:1.0:5:5:0.0',
        'mood': 'energetic, bold, attention-grabbing'
    },
    'muted_film': {
        'name': 'Muted Film',
        'description': 'Desaturated vintage film aesthetic',
        'ffmpeg_filter': 'colorbalance=rs=0.05:gs=0.02:bs=-0.05,eq=saturation=0.8:contrast=1.05,curves=vintage',
        'mood': 'nostalgic, artistic, thoughtful'
    },
    'clean_neutral': {
        'name': 'Clean Neutral',
        'description': 'Balanced, natural look with slight polish',
        'ffmpeg_filter': 'eq=saturation=1.0:contrast=1.05:brightness=0.01',
        'mood': 'clear, honest, straightforward'
    }
}

# Transition effects for Source Merging Engine
TRANSITION_EFFECTS = {
    'cross_dissolve': {
        'name': 'Cross Dissolve',
        'duration': 0.5,
        'ffmpeg_filter': 'xfade=transition=fade:duration=0.5',
        'energy': 'calm'
    },
    'zoom_in': {
        'name': 'Zoom In',
        'duration': 0.3,
        'ffmpeg_filter': 'xfade=transition=zoomin:duration=0.3',
        'energy': 'moderate'
    },
    'wipe_right': {
        'name': 'Wipe Right',
        'duration': 0.4,
        'ffmpeg_filter': 'xfade=transition=wiperight:duration=0.4',
        'energy': 'moderate'
    },
    'quick_cut': {
        'name': 'Quick Cut',
        'duration': 0.1,
        'ffmpeg_filter': 'xfade=transition=fade:duration=0.1',
        'energy': 'high'
    },
    'slide_left': {
        'name': 'Slide Left',
        'duration': 0.35,
        'ffmpeg_filter': 'xfade=transition=slideleft:duration=0.35',
        'energy': 'moderate'
    },
    'radial_wipe': {
        'name': 'Radial Wipe',
        'duration': 0.5,
        'ffmpeg_filter': 'xfade=transition=radial:duration=0.5',
        'energy': 'dramatic'
    }
}

# Caption templates with GetCaptions-style features
CAPTION_TEMPLATES = {
    'bold_pop': {
        'name': 'Bold Pop',
        'description': 'Large bold text with strong pop animation',
        'font_family': 'Space Grotesk',
        'font_weight': 'bold',
        'font_size': 64,
        'text_color': '#FFFFFF',
        'highlight_color': '#FFD60A',
        'background': 'none',
        'animation': 'pop_scale',
        'animation_intensity': 1.15
    },
    'clean_minimal': {
        'name': 'Clean Minimal',
        'description': 'Thin elegant font with subtle highlight',
        'font_family': 'Inter',
        'font_weight': 'normal',
        'font_size': 48,
        'text_color': '#FFFFFF',
        'highlight_color': '#4ECDC4',
        'background': 'none',
        'animation': 'fade_highlight',
        'animation_intensity': 1.0
    },
    'boxed': {
        'name': 'Boxed',
        'description': 'Text with rounded background pill',
        'font_family': 'Inter',
        'font_weight': 'semibold',
        'font_size': 52,
        'text_color': '#000000',
        'highlight_color': '#FFD60A',
        'background': 'pill',
        'background_color': '#FFFFFF',
        'animation': 'pop_scale',
        'animation_intensity': 1.1
    },
    'gradient_glow': {
        'name': 'Gradient Glow',
        'description': 'Gradient text with glow on active word',
        'font_family': 'Space Grotesk',
        'font_weight': 'bold',
        'font_size': 56,
        'text_color': '#FFFFFF',
        'highlight_color': '#FF6B6B',
        'gradient': ['#FF6B6B', '#FFD60A'],
        'background': 'glow',
        'animation': 'glow_pulse',
        'animation_intensity': 1.2
    },
    'street_style': {
        'name': 'Street Style',
        'description': 'All caps heavy weight with punchy animation',
        'font_family': 'Space Grotesk',
        'font_weight': 'bold',
        'font_size': 60,
        'text_color': '#FFFFFF',
        'highlight_color': '#FF3366',
        'text_transform': 'uppercase',
        'background': 'none',
        'stroke': '#000000',
        'stroke_width': 3,
        'animation': 'bounce',
        'animation_intensity': 1.25
    }
}


def detect_content_type(script: str, user_intent: str = '') -> str:
    """
    Detect the content type from script and user intent.
    Returns one of: podcast, explainer, hot_take, ad, story, news, meme, or 'general'
    """
    combined_text = f"{script} {user_intent}".lower()
    
    scores = {}
    for content_type, patterns in CONTENT_TYPE_PATTERNS.items():
        score = sum(1 for pattern in patterns if pattern in combined_text)
        if score > 0:
            scores[content_type] = score
    
    if scores:
        return max(scores, key=scores.get)
    return 'general'


def get_editing_dna(content_type: str) -> Dict:
    """Get the editing DNA for a content type."""
    return EDITING_DNA.get(content_type, EDITING_DNA['explainer'])


def analyze_scene_needs(scene_text: str, scene_index: int, total_scenes: int) -> Dict:
    """
    Analyze what a single scene needs visually.
    Returns recommendations for visual source and style.
    """
    text_lower = scene_text.lower()
    
    # Determine scene position (hook, middle, closer)
    if scene_index == 0:
        position = 'hook'
    elif scene_index == total_scenes - 1:
        position = 'closer'
    else:
        position = 'middle'
    
    # Detect if scene needs real people
    needs_real_people = any(word in text_lower for word in [
        'person', 'people', 'someone', 'man', 'woman', 'team', 'employee',
        'customer', 'user', 'audience', 'speaker', 'host', 'guest'
    ])
    
    # Detect if scene is abstract/conceptual
    is_abstract = any(word in text_lower for word in [
        'concept', 'idea', 'imagine', 'vision', 'dream', 'future',
        'abstract', 'feeling', 'emotion', 'energy', 'power'
    ])
    
    # Detect if scene needs specific objects/products
    needs_product = any(word in text_lower for word in [
        'product', 'feature', 'app', 'tool', 'software', 'device',
        'service', 'platform', 'solution'
    ])
    
    # Determine best source
    if needs_real_people:
        recommended_source = 'stock'
        reason = 'Real people look more authentic than AI-generated faces'
    elif is_abstract:
        recommended_source = 'dalle'
        reason = 'Abstract concepts benefit from AI creativity'
    elif needs_product:
        recommended_source = 'user_content'
        reason = 'Product shots should use actual user content when available'
    else:
        # Default based on position
        if position == 'hook':
            recommended_source = 'dalle'
            reason = 'AI can create attention-grabbing hook visuals'
        elif position == 'closer':
            recommended_source = 'stock'
            reason = 'Professional stock for strong closing'
        else:
            recommended_source = 'stock'
            reason = 'Stock provides reliable mid-content visuals'
    
    return {
        'position': position,
        'needs_real_people': needs_real_people,
        'is_abstract': is_abstract,
        'needs_product': needs_product,
        'recommended_source': recommended_source,
        'source_reason': reason
    }


def create_visual_plan(
    script: str,
    user_intent: str = '',
    user_content: List[str] = None,
    template_type: str = None
) -> Dict:
    """
    Create a comprehensive visual plan for the entire video.
    
    Args:
        script: The full script text
        user_intent: What the user asked for (e.g., "make an ad for my podcast")
        user_content: List of user-provided image/video paths
        template_type: The template type if specified
    
    Returns:
        A visual plan dict with:
        - content_type: detected type
        - editing_dna: pacing, cut style, etc.
        - color_palette: colors to use
        - scenes: list of scene plans with source decisions
    """
    user_content = user_content or []
    
    # Detect content type
    content_type = detect_content_type(script, user_intent)
    if template_type and template_type != 'start_from_scratch':
        # Override with template type if specified
        template_to_content = {
            'hot_take': 'hot_take',
            'explainer': 'explainer',
            'make_an_ad': 'ad',
            'meme_funny': 'meme',
            'tiktok_edit': 'hot_take',
            'youtube_shorts': 'explainer',
            'motivational': 'story',
            'educational': 'explainer',
            'product_demo': 'ad'
        }
        content_type = template_to_content.get(template_type.lower().replace(' ', '_'), content_type)
    
    # Get editing DNA for this content type
    editing_dna = get_editing_dna(content_type)
    
    # Get color palette
    color_mood = editing_dna.get('color_mood', 'clean_modern')
    color_palette = COLOR_PALETTES.get(color_mood, COLOR_PALETTES['clean_modern'])
    
    # Split script into scenes (by paragraph or sentence groups)
    scenes_text = split_script_to_scenes(script)
    
    # Analyze each scene
    scene_plans = []
    user_content_index = 0
    
    for i, scene_text in enumerate(scenes_text):
        analysis = analyze_scene_needs(scene_text, i, len(scenes_text))
        
        # Check if we should use user content
        source = analysis['recommended_source']
        source_path = None
        
        if source == 'user_content' and user_content_index < len(user_content):
            source_path = user_content[user_content_index]
            user_content_index += 1
        elif source == 'user_content':
            # No user content available, fall back
            source = 'stock' if analysis['needs_real_people'] else 'dalle'
        
        scene_plan = {
            'index': i,
            'text': scene_text,
            'position': analysis['position'],
            'source': source,
            'source_path': source_path,
            'source_reason': analysis['source_reason'],
            'needs_real_people': analysis['needs_real_people'],
            'is_abstract': analysis['is_abstract'],
            'style_notes': get_style_notes(content_type, analysis['position']),
            'prompt_enhancement': get_prompt_enhancement(content_type, color_palette)
        }
        scene_plans.append(scene_plan)
    
    return {
        'content_type': content_type,
        'editing_dna': editing_dna,
        'color_palette': color_palette,
        'color_mood': color_mood,
        'total_scenes': len(scene_plans),
        'scenes': scene_plans,
        'created_at': datetime.utcnow().isoformat(),
        'plan_id': hashlib.md5(script.encode()).hexdigest()[:12]
    }


def split_script_to_scenes(script: str, max_scenes: int = 8) -> List[str]:
    """Split script into logical scenes."""
    # Split by double newlines first (paragraphs)
    paragraphs = [p.strip() for p in script.split('\n\n') if p.strip()]
    
    if len(paragraphs) >= 3:
        scenes = paragraphs
    else:
        # Split by single newlines
        lines = [l.strip() for l in script.split('\n') if l.strip()]
        if len(lines) >= 3:
            scenes = lines
        else:
            # Split by sentences
            import re
            sentences = re.split(r'(?<=[.!?])\s+', script)
            scenes = [s.strip() for s in sentences if s.strip()]
    
    # Limit to max_scenes by combining if needed
    if len(scenes) > max_scenes:
        combined = []
        chunk_size = len(scenes) // max_scenes + 1
        for i in range(0, len(scenes), chunk_size):
            combined.append(' '.join(scenes[i:i+chunk_size]))
        scenes = combined[:max_scenes]
    
    return scenes if scenes else [script]


def get_style_notes(content_type: str, position: str) -> str:
    """Get style notes for a scene based on content type and position."""
    style_map = {
        ('podcast', 'hook'): 'Dynamic, engaging, hint at the conversation topic',
        ('podcast', 'middle'): 'Professional, conversational, speaker-focused B-roll',
        ('podcast', 'closer'): 'Memorable, call-to-action friendly',
        ('hot_take', 'hook'): 'Bold, attention-grabbing, slightly provocative',
        ('hot_take', 'middle'): 'Supporting visuals, fast-paced energy',
        ('hot_take', 'closer'): 'Impactful, mic-drop moment',
        ('ad', 'hook'): 'Problem visualization or desire trigger',
        ('ad', 'middle'): 'Solution showcase, feature highlights',
        ('ad', 'closer'): 'Strong CTA, aspirational outcome',
        ('explainer', 'hook'): 'Question visualization, curiosity trigger',
        ('explainer', 'middle'): 'Clear, educational, step-by-step',
        ('explainer', 'closer'): 'Summary visual, key takeaway',
    }
    return style_map.get((content_type, position), 'Clean, professional, on-topic')


def get_prompt_enhancement(content_type: str, color_palette: List[str]) -> str:
    """Get DALL-E prompt enhancement based on content type and colors."""
    color_desc = ', '.join(color_palette[:3])
    
    enhancements = {
        'podcast': f'Professional podcast studio aesthetic, warm lighting, color palette: {color_desc}',
        'explainer': f'Clean minimalist style, educational infographic aesthetic, colors: {color_desc}',
        'hot_take': f'Bold dramatic lighting, high contrast, social media viral style, colors: {color_desc}',
        'ad': f'Premium commercial photography style, aspirational, brand colors: {color_desc}',
        'story': f'Cinematic film still, atmospheric, narrative depth, colors: {color_desc}',
        'meme': f'Internet culture aesthetic, vibrant saturated colors, meme-worthy composition',
        'news': f'News broadcast style, professional, urgent, trustworthy, colors: {color_desc}'
    }
    return enhancements.get(content_type, f'Professional, high quality, colors: {color_desc}')


def enhance_dalle_prompt(base_prompt: str, visual_plan: Dict, scene_plan: Dict) -> str:
    """
    Enhance a DALL-E prompt with visual plan context for coherent generation.
    """
    enhancement = scene_plan.get('prompt_enhancement', '')
    style_notes = scene_plan.get('style_notes', '')
    
    # Build enhanced prompt
    enhanced = f"{base_prompt}. {enhancement}. {style_notes}"
    
    # Add consistency markers
    enhanced += ". High resolution, professional quality, no text or watermarks"
    
    return enhanced


def get_stock_search_query(scene_text: str, scene_plan: Dict) -> str:
    """
    Generate an optimized stock photo search query for a scene.
    """
    import re
    words = re.findall(r'\b[a-zA-Z]{3,}\b', scene_text.lower())
    
    stop_words = {'the', 'and', 'for', 'that', 'this', 'with', 'are', 'was', 'have', 'has', 'been'}
    keywords = [w for w in words if w not in stop_words][:5]
    
    if scene_plan.get('needs_real_people'):
        keywords.append('person')
    if scene_plan.get('position') == 'hook':
        keywords.append('dynamic')
    
    return ' '.join(keywords)


def search_stock_for_scene(scene_plan: Dict) -> Optional[Dict]:
    """
    Search for stock photos that match a scene's needs.
    Uses Pexels API via context_engine.
    """
    try:
        from context_engine import search_pexels_safe
        
        query = get_stock_search_query(scene_plan.get('text', ''), scene_plan)
        results = search_pexels_safe(query, per_page=3)
        
        if results:
            return {
                'source': 'stock',
                'url': results[0].get('url'),
                'thumbnail': results[0].get('thumbnail'),
                'query_used': query,
                'alternatives': results[1:] if len(results) > 1 else []
            }
        return None
    except Exception as e:
        print(f"[Visual Director] Stock search failed: {e}")
        return None


def execute_visual_plan(visual_plan: Dict) -> List[Dict]:
    """
    Execute a visual plan by fetching/generating visuals for each scene.
    Returns a list of scenes with their visual assets ready for rendering.
    """
    executed_scenes = []
    
    for scene in visual_plan.get('scenes', []):
        source = scene.get('source', 'stock')
        scene_result = {
            'index': scene.get('index', 0),
            'text': scene.get('text', ''),
            'position': scene.get('position', 'middle'),
            'visual': None,
            'source_type': source,
            'source_query': None
        }
        
        if source == 'user_content' and scene.get('source_path'):
            scene_result['visual'] = scene.get('source_path')
            scene_result['source_type'] = 'user_content'
        elif source == 'stock':
            stock_result = search_stock_for_scene(scene)
            if stock_result:
                scene_result['visual'] = stock_result.get('url') or stock_result.get('thumbnail')
                scene_result['source_query'] = stock_result.get('query_used')
                scene_result['alternatives'] = stock_result.get('alternatives', [])
            else:
                scene_result['source_type'] = 'dalle'
        elif source == 'dalle':
            enhanced_prompt = enhance_dalle_prompt(scene.get('text', ''), visual_plan, scene)
            scene_result['dalle_prompt'] = enhanced_prompt
            scene_result['source_type'] = 'dalle'
        
        executed_scenes.append(scene_result)
    
    return executed_scenes


def tag_successful_visual(scene_result: Dict, content_type: str, feedback: str = 'positive'):
    """
    Tag a visual as successful for learning.
    """
    try:
        from models import VisualLearning, db
        
        record = VisualLearning(
            content_type=content_type,
            scene_position=scene_result.get('position', 'middle'),
            source_type=scene_result.get('source_type', 'stock'),
            feedback=feedback,
            scene_text_sample=scene_result.get('text', '')[:200]
        )
        db.session.add(record)
        db.session.commit()
        print(f"[Visual Director] Tagged successful visual: {scene_result.get('source_type')}")
    except Exception as e:
        print(f"[Visual Director] Failed to tag visual: {e}")


# ============================================================
# SOURCE MERGING ENGINE
# Unified system to blend stock/DALL-E/user content seamlessly
# ============================================================

def recommend_color_style(content_type: str, script: str = '', user_history: List[Dict] = None) -> Dict:
    """
    AI recommends best color grading style for this project.
    Returns recommendation + 2-3 alternatives with visual preview info.
    """
    # Map content types to recommended styles
    content_style_map = {
        'podcast': 'warm_cinematic',
        'explainer': 'clean_neutral',
        'hot_take': 'punchy_vibrant',
        'ad': 'punchy_vibrant',
        'story': 'muted_film',
        'news': 'cool_professional',
        'meme': 'punchy_vibrant'
    }
    
    # Get recommended style based on content
    recommended_key = content_style_map.get(content_type, 'clean_neutral')
    recommended = COLOR_GRADING_PROFILES[recommended_key].copy()
    recommended['key'] = recommended_key
    
    # Generate alternatives (exclude recommended)
    all_keys = list(COLOR_GRADING_PROFILES.keys())
    all_keys.remove(recommended_key)
    alternatives = []
    for key in all_keys[:3]:
        alt = COLOR_GRADING_PROFILES[key].copy()
        alt['key'] = key
        alternatives.append(alt)
    
    return {
        'recommended': recommended,
        'alternatives': alternatives,
        'reasoning': f"Based on your {content_type} content, {recommended['name']} will create {recommended['mood']} feel."
    }


def select_transition_for_scenes(content_type: str, scene_energy: str = 'moderate') -> str:
    """
    Select appropriate transition based on content type and scene energy.
    """
    # Map content types to transition preferences
    content_transition_map = {
        'podcast': 'cross_dissolve',
        'explainer': 'slide_left',
        'hot_take': 'quick_cut',
        'ad': 'zoom_in',
        'story': 'cross_dissolve',
        'news': 'wipe_right',
        'meme': 'quick_cut'
    }
    
    # Energy overrides
    if scene_energy == 'high':
        return 'quick_cut'
    elif scene_energy == 'dramatic':
        return 'radial_wipe'
    
    return content_transition_map.get(content_type, 'cross_dissolve')


def build_merge_filter_chain(
    color_style: str,
    apply_grain: bool = True,
    transition_type: str = 'cross_dissolve'
) -> str:
    """
    Build FFmpeg filter chain that applies color grading, grain, and prepares for transitions.
    All in one pass for efficiency.
    """
    filters = []
    
    # Color grading
    if color_style in COLOR_GRADING_PROFILES:
        filters.append(COLOR_GRADING_PROFILES[color_style]['ffmpeg_filter'])
    
    # Film grain overlay (subtle noise to unify sources)
    if apply_grain:
        filters.append('noise=alls=8:allf=t')
    
    # Slight vignette for cohesion
    filters.append('vignette=PI/5')
    
    return ','.join(filters)


def get_merging_config(content_type: str, user_preferences: Dict = None) -> Dict:
    """
    Get full merging configuration for a project.
    Combines color style, transitions, and grain settings.
    """
    prefs = user_preferences or {}
    
    # Get color recommendation
    color_rec = recommend_color_style(content_type)
    selected_style = prefs.get('color_style', color_rec['recommended']['key'])
    
    # Get transition type
    transition = select_transition_for_scenes(content_type)
    
    # Grain preference (default on)
    apply_grain = prefs.get('film_grain', True)
    
    # Build filter chain
    filter_chain = build_merge_filter_chain(selected_style, apply_grain, transition)
    
    return {
        'color_style': selected_style,
        'color_profile': COLOR_GRADING_PROFILES.get(selected_style, {}),
        'transition': TRANSITION_EFFECTS.get(transition, {}),
        'apply_grain': apply_grain,
        'filter_chain': filter_chain,
        'color_recommendation': color_rec
    }


def apply_merging_to_ffmpeg_command(
    base_filter: str,
    content_type: str = 'general',
    color_style: str = None,
    film_grain: bool = True
) -> str:
    """
    Combine existing FFmpeg filter with Source Merging Engine filters.
    Returns the complete filter chain string for -vf parameter.
    """
    filters = []
    
    # Add base filter if provided
    if base_filter and base_filter.strip():
        filters.append(base_filter)
    
    # Get merging config
    config = get_merging_config(content_type, {
        'color_style': color_style,
        'film_grain': film_grain
    })
    
    # Add merging filter chain
    if config.get('filter_chain'):
        filters.append(config['filter_chain'])
    
    return ','.join(filters) if filters else ''


def get_caption_ffmpeg_params(template_key: str, text: str, position: str = 'bottom') -> Dict:
    """
    Get FFmpeg drawtext parameters for a caption template.
    Returns parameters for word-by-word rendering with animations.
    """
    template = CAPTION_TEMPLATES.get(template_key, CAPTION_TEMPLATES['bold_pop'])
    
    # Map position to y coordinate
    y_positions = {
        'top': 'h*0.1',
        'middle': '(h-text_h)/2',
        'center': '(h-text_h)/2',
        'bottom': 'h*0.85'
    }
    y_pos = y_positions.get(position, 'h*0.85')
    
    # Build font style
    font_file = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
    font_size = template.get('font_size', 48)
    text_color = template.get('text_color', '#FFFFFF').replace('#', '0x')
    highlight_color = template.get('highlight_color', '#FFD60A').replace('#', '0x')
    
    # Handle stroke/border
    border_w = template.get('stroke_width', 2)
    
    drawtext_params = {
        'fontfile': font_file,
        'fontsize': font_size,
        'fontcolor': text_color,
        'borderw': border_w,
        'bordercolor': '0x000000',
        'x': '(w-text_w)/2',
        'y': y_pos,
        'text': text
    }
    
    return {
        'template': template,
        'drawtext': drawtext_params,
        'highlight_color': highlight_color
    }


# ============================================================
# CAPTION STYLE SYSTEM
# AI-curated caption styles with refresh/history
# ============================================================

def recommend_caption_style(content_type: str, user_history: List[Dict] = None) -> Dict:
    """
    AI recommends best caption template for this project.
    Returns recommendation with preview info.
    """
    # Map content types to caption style preferences
    content_caption_map = {
        'podcast': 'clean_minimal',
        'explainer': 'boxed',
        'hot_take': 'street_style',
        'ad': 'bold_pop',
        'story': 'gradient_glow',
        'news': 'boxed',
        'meme': 'street_style'
    }
    
    recommended_key = content_caption_map.get(content_type, 'bold_pop')
    recommended = CAPTION_TEMPLATES[recommended_key].copy()
    recommended['key'] = recommended_key
    
    return {
        'recommended': recommended,
        'all_templates': {k: v.copy() for k, v in CAPTION_TEMPLATES.items()},
        'reasoning': f"{recommended['name']} works best for {content_type} content."
    }


def get_caption_style_history(user_id: str, limit: int = 10) -> List[Dict]:
    """
    Get user's caption style history for back/forward navigation.
    """
    try:
        from models import CaptionStyleHistory
        
        history = CaptionStyleHistory.query.filter_by(user_id=user_id)\
            .order_by(CaptionStyleHistory.created_at.desc())\
            .limit(limit).all()
        
        return [
            {
                'id': h.id,
                'template_key': h.template_key,
                'template_name': CAPTION_TEMPLATES.get(h.template_key, {}).get('name', ''),
                'created_at': h.created_at.isoformat()
            }
            for h in history
        ]
    except Exception as e:
        print(f"[Caption] Failed to get history: {e}")
        return []


def save_caption_style_choice(user_id: str, template_key: str, was_refresh: bool = False):
    """
    Save user's caption style choice to history.
    """
    try:
        from models import CaptionStyleHistory, db
        
        record = CaptionStyleHistory(
            user_id=user_id,
            template_key=template_key,
            was_refresh=was_refresh
        )
        db.session.add(record)
        db.session.commit()
    except Exception as e:
        print(f"[Caption] Failed to save choice: {e}")


# Learning system - track which visuals worked well
class VisualLearningTracker:
    """Track successful visual decisions for future improvement."""
    
    def __init__(self, db_session=None):
        self.db_session = db_session
    
    def record_success(self, visual_plan: Dict, scene_index: int, feedback: str = 'positive'):
        """Record a successful visual decision."""
        try:
            from models import VisualLearning, db
            
            scene = visual_plan['scenes'][scene_index]
            record = VisualLearning(
                content_type=visual_plan['content_type'],
                scene_position=scene['position'],
                source_type=scene['source'],
                feedback=feedback,
                scene_text_sample=scene['text'][:200],
                created_at=datetime.utcnow()
            )
            db.session.add(record)
            db.session.commit()
        except Exception as e:
            print(f"[VisualLearning] Failed to record: {e}")
    
    def get_recommendations(self, content_type: str, position: str) -> Dict:
        """Get learned recommendations based on past successes."""
        try:
            from models import VisualLearning
            
            successes = VisualLearning.query.filter_by(
                content_type=content_type,
                scene_position=position,
                feedback='positive'
            ).limit(50).all()
            
            if not successes:
                return {}
            
            # Count source type preferences
            source_counts = {}
            for s in successes:
                source_counts[s.source_type] = source_counts.get(s.source_type, 0) + 1
            
            preferred_source = max(source_counts, key=source_counts.get)
            
            return {
                'preferred_source': preferred_source,
                'confidence': source_counts[preferred_source] / len(successes)
            }
        except Exception as e:
            print(f"[VisualLearning] Failed to get recommendations: {e}")
            return {}


def apply_caption_template(caption_settings: Dict, template_key: str = None) -> Dict:
    """
    Centralized helper to apply caption template overrides to caption settings.
    Use this in all render paths to ensure consistent template application.
    
    Args:
        caption_settings: Current caption settings dict
        template_key: Optional template key to apply (overrides settings['template'])
    
    Returns:
        Updated caption settings dict with template values applied
    """
    settings = caption_settings.copy()
    
    # Check for template key in settings if not provided
    key = template_key or settings.get('template')
    if not key:
        return settings
    
    template = CAPTION_TEMPLATES.get(key)
    if not template:
        return settings
    
    # Apply template values to settings
    settings['textColor'] = template.get('text_color', settings.get('textColor', '#FFFFFF'))
    settings['color'] = template.get('text_color', settings.get('color', '#FFFFFF'))
    settings['highlightColor'] = template.get('highlight_color', settings.get('highlightColor', '#FFD60A'))
    
    # Font size mapping
    font_size = template.get('font_size', 48)
    if font_size >= 64:
        settings['size'] = 'large'
    elif font_size >= 48:
        settings['size'] = 'medium'
    else:
        settings['size'] = 'small'
    
    settings['weight'] = template.get('font_weight', settings.get('weight', 'bold'))
    
    # Background handling
    bg = template.get('background', 'none')
    settings['background'] = bg in ['pill', 'box']
    
    # Text transform
    settings['uppercase'] = template.get('text_transform', '') == 'uppercase'
    
    # Outline/stroke
    settings['outline'] = template.get('stroke_width', 0) > 0
    
    # Animation (word-by-word with highlight)
    settings['animation'] = 'highlight'
    
    # Store template reference
    settings['template'] = key
    settings['template_name'] = template.get('name', key)
    
    print(f"[Captions] Applied template: {key}")
    return settings
