"""
context_engine.py - Backward-compatible re-export module.

This module was refactored into focused sub-modules:
- ai_client.py: AI provider setup and core call function
- trend_research.py: Trend research via DuckDuckGo
- stock_search.py: Visual content sourcing (Unsplash, Pixabay, Pexels, Wikimedia)
- audio_processor.py: Audio extraction, transcription, video clipping
- script_generator.py: Script/thesis generation, templates, visual planning

All exports are re-exported here for backward compatibility.
"""

from ai_client import (
    call_ai,
    extract_json_from_text,
    SYSTEM_GUARDRAILS,
    claude_client,
    xai_client,
    openai_client,
    client,
)

from trend_research import (
    research_topic_trends,
    _trend_cache,
)

from stock_search import (
    extract_keywords_from_script,
    search_stock_videos,
    detect_characters_in_scene,
    search_unsplash,
    search_pixabay,
    search_pixabay_videos,
    search_pexels,
    search_wikimedia_images,
    search_visuals_unified,
    search_pexels_safe,
    get_scene_visuals,
)

from audio_processor import (
    extract_audio,
    transcribe_audio,
    analyze_ideas,
    find_clip_timestamps,
    generate_captions,
    cut_video_clip,
    concatenate_clips,
    process_source_for_clipping,
    learn_from_source_content,
)

from script_generator import (
    TEMPLATE_TONE_DNA,
    TEMPLATE_VISUAL_FX,
    get_template_visual_fx,
    get_template_guidelines,
    generate_video_description,
    get_user_context,
    get_learning_context,
    save_conversation,
    build_personalized_prompt,
    generate_script,
    validate_loop_score,
    ai_approval_gate,
    build_post_from_script,
    extract_thesis,
    extract_thesis_and_generate_script,
    identify_anchors,
    detect_thought_changes,
    classify_content_type,
    build_visual_layers,
    generate_visual_plan,
    generate_thesis_driven_script,
    get_source_learning_context,
    get_global_patterns_context,
    process_video,
    unified_content_engine,
    analyze_editing_patterns_global,
    store_global_patterns,
    get_global_learned_patterns,
    ai_self_critique,
    store_ai_learnings,
    analyze_remix_input,
    orchestrate_remix_sources,
    record_remix_success,
)


research_trends = research_topic_trends


def get_ai_client():
    return client


def build_visual_fx_filter(template_type, width=1080, height=1920):
    fx = get_template_visual_fx(template_type) if isinstance(template_type, str) else template_type
    filters = []

    color_grades = {
        'high_contrast': 'eq=contrast=1.3:brightness=0.05:saturation=1.2',
        'clean_bright': 'eq=contrast=1.1:brightness=0.1:saturation=1.0',
        'warm_cinematic': 'colorbalance=rs=0.05:gs=-0.02:bs=-0.05,eq=contrast=1.15:saturation=1.1',
        'neutral_sharp': 'eq=contrast=1.1:saturation=0.95,unsharp=5:5:1.0',
        'warm_intimate': 'colorbalance=rs=0.08:gs=0.02:bs=-0.05,eq=contrast=1.05:brightness=0.05',
        'saturated_pop': 'eq=contrast=1.2:saturation=1.4:brightness=0.05',
        'polished_commercial': 'eq=contrast=1.15:brightness=0.08:saturation=1.05,unsharp=3:3:0.8',
        'vibrant_social': 'eq=contrast=1.25:saturation=1.3:brightness=0.03',
        'natural': 'eq=contrast=1.05:saturation=1.0',
    }

    color_grade = fx.get('color_grade', 'natural') if isinstance(fx, dict) else 'natural'
    if color_grade in color_grades:
        filters.append(color_grades[color_grade])

    vignette = fx.get('vignette', 0) if isinstance(fx, dict) else 0
    if vignette and vignette > 0:
        angle = max(0.3, 1.0 - vignette)
        filters.append(f'vignette=angle={angle}')

    return ','.join(filters) if filters else 'null'
