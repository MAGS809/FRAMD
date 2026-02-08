import os
import json
from ai_client import call_ai, SYSTEM_GUARDRAILS
from trend_research import research_topic_trends
from stock_search import extract_keywords_from_script, search_stock_videos

TEMPLATE_TONE_DNA = {
    'hot_take': {
        'tone': 'assertive',
        'voice': 'Provocative, punchy, confident. Takes a clear stance.',
        'hook_style': 'Bold claim or controversy opener. Grabs attention through honest provocation.',
        'pacing': 'Fast opener, measured middle, sharp close',
        'trend_application': 'Use trend hooks and controversy patterns. Lean into what sparks debate.',
        'allowed_overrides': ['provocative hooks', 'strong opinions', 'direct confrontation of ideas']
    },
    'explainer': {
        'tone': 'clear',
        'voice': 'Patient, educational, authoritative without being condescending.',
        'hook_style': 'Question or surprising fact that reveals a knowledge gap.',
        'pacing': 'Steady build, each point lands before the next',
        'trend_application': 'Use trending formats for explanation (visual metaphors, step patterns).',
        'allowed_overrides': ['extended metaphors if clarifying', 'slower pacing']
    },
    'story_time': {
        'tone': 'narrative',
        'voice': 'Immersive, personal, draws listener into the story.',
        'hook_style': 'Story opener that creates immediate intrigue or tension.',
        'pacing': 'Tension build, emotional beats, satisfying resolution',
        'trend_application': 'Use trending story structures and emotional arc patterns.',
        'allowed_overrides': ['longer sentences for flow', 'emotional language', 'personal tone']
    },
    'commentary': {
        'tone': 'analytical',
        'voice': 'Sharp, observational, sees what others miss.',
        'hook_style': 'Observation that reframes how we see something familiar.',
        'pacing': 'Setup, insight, implication',
        'trend_application': 'Use trending commentary formats, evidence presentation styles.',
        'allowed_overrides': ['rebuttals', 'critique of popular opinions']
    },
    'open_letter': {
        'tone': 'direct',
        'voice': 'Personal, sincere, speaks to someone specific (even if abstract).',
        'hook_style': 'Direct address that establishes the relationship and stakes.',
        'pacing': 'Build emotional weight, land with conviction',
        'trend_application': 'Use emotional pacing patterns that trend. Structure for impact.',
        'allowed_overrides': ['emotional directness', 'personal address', 'vulnerability']
    },
    'meme_funny': {
        'tone': 'comedic',
        'voice': 'Witty, timing-focused, meme-literate. Humor IS the point.',
        'hook_style': 'Subverted expectation, absurd setup, or relatable frustration.',
        'pacing': 'Setup, pause, punchline. Timing is everything.',
        'trend_application': 'Use trending meme formats, comedic structures, viral patterns.',
        'allowed_overrides': ['meme logic', 'absurdist humor', 'self-aware meta', 'rapid cuts']
    },
    'make_an_ad': {
        'tone': 'persuasive',
        'voice': 'Urgent, benefit-focused, creates desire without manipulation.',
        'hook_style': 'Problem statement or aspiration that the viewer feels.',
        'pacing': 'Problem, solution, proof, CTA',
        'trend_application': 'Use trending ad formats, social proof patterns, CTA styles.',
        'allowed_overrides': ['urgency language', 'CTAs', 'social proof', 'benefit stacking']
    },
    'tiktok_edit': {
        'tone': 'energetic',
        'voice': 'Fast, visual-first, trend-forward. Native to the platform.',
        'hook_style': 'Immediate visual or audio hook. No slow intros.',
        'pacing': 'Rapid, sync to audio, constant movement',
        'trend_application': 'Mirror current TikTok trends directly. Sound sync, transitions, effects.',
        'allowed_overrides': ['trend-chasing', 'fast cuts', 'audio-driven structure', 'platform-native language']
    },
    'start_from_scratch': {
        'tone': 'adaptive',
        'voice': 'Neutral baseline. Adapts to content needs.',
        'hook_style': 'Context-appropriate. Let the content dictate.',
        'pacing': 'Balanced, content-driven',
        'trend_application': 'Apply relevant trends based on what the content becomes.',
        'allowed_overrides': ['flexible based on content direction']
    }
}

TEMPLATE_VISUAL_FX = {
    'hot_take': {
        'color_grade': 'high_contrast',
        'vignette': 0.3,
        'shake_intensity': 0.15,
        'text_style': 'bold_impact',
        'transitions': ['zoom_in', 'flash', 'glitch'],
        'fx_tags': ['impact', 'whoosh', 'tension']
    },
    'explainer': {
        'color_grade': 'clean_bright',
        'vignette': 0.1,
        'shake_intensity': 0,
        'text_style': 'clean_modern',
        'transitions': ['fade', 'slide', 'reveal'],
        'fx_tags': ['beep', 'reveal']
    },
    'story_time': {
        'color_grade': 'warm_cinematic',
        'vignette': 0.25,
        'shake_intensity': 0.05,
        'text_style': 'elegant_serif',
        'transitions': ['fade', 'dissolve'],
        'fx_tags': ['tension', 'heartbeat', 'reveal']
    },
    'commentary': {
        'color_grade': 'neutral_sharp',
        'vignette': 0.15,
        'shake_intensity': 0.1,
        'text_style': 'clean_bold',
        'transitions': ['cut', 'zoom_in'],
        'fx_tags': ['whoosh', 'impact']
    },
    'open_letter': {
        'color_grade': 'warm_intimate',
        'vignette': 0.35,
        'shake_intensity': 0,
        'text_style': 'handwritten_feel',
        'transitions': ['fade', 'soft_blur'],
        'fx_tags': ['heartbeat', 'wind']
    },
    'meme_funny': {
        'color_grade': 'saturated_pop',
        'vignette': 0,
        'shake_intensity': 0.25,
        'text_style': 'meme_impact',
        'transitions': ['zoom_punch', 'shake', 'glitch', 'flash'],
        'fx_tags': ['whoosh', 'beep', 'static']
    },
    'make_an_ad': {
        'color_grade': 'polished_commercial',
        'vignette': 0.1,
        'shake_intensity': 0,
        'text_style': 'premium_clean',
        'transitions': ['slide', 'reveal', 'zoom_out'],
        'fx_tags': ['reveal', 'whoosh']
    },
    'tiktok_edit': {
        'color_grade': 'vibrant_social',
        'vignette': 0,
        'shake_intensity': 0.2,
        'text_style': 'tiktok_native',
        'transitions': ['beat_sync', 'flash', 'zoom_punch', 'shake'],
        'fx_tags': ['whoosh', 'impact', 'rumble']
    },
    'start_from_scratch': {
        'color_grade': 'natural',
        'vignette': 0.1,
        'shake_intensity': 0,
        'text_style': 'clean_modern',
        'transitions': ['fade', 'cut'],
        'fx_tags': ['whoosh']
    }
}


def get_template_visual_fx(template_type: str) -> dict:
    template_key = template_type.lower().replace(' ', '_').replace('-', '_')
    return TEMPLATE_VISUAL_FX.get(template_key, TEMPLATE_VISUAL_FX['start_from_scratch'])


def get_template_guidelines(template_type: str) -> dict:
    template_key = template_type.lower().replace(' ', '_').replace('-', '_')
    return TEMPLATE_TONE_DNA.get(template_key, TEMPLATE_TONE_DNA['start_from_scratch'])


def generate_video_description(script_text: str, trend_sources: list = None, include_hashtags: bool = True) -> dict:
    sources_context = ""
    if trend_sources:
        sources_context = f"\nResearch sources used: {', '.join([s.get('title', s.get('url', ''))[:50] for s in trend_sources[:3]])}"
    
    prompt = f"""Generate a compelling social media description for this video.

SCRIPT/CONTENT:
{script_text[:2000]}
{sources_context}

Create:
1. A hook line (attention-grabbing first line)
2. 2-3 sentences summarizing the value
3. Call to action
{'4. 3-5 relevant hashtags' if include_hashtags else ''}

Keep it under 300 characters for Instagram/TikTok compatibility.

Output JSON:
{{
    "description": "The full description text ready to post",
    "hook_line": "Just the hook line",
    "hashtags": ["tag1", "tag2", "tag3"]
}}"""

    result = call_ai(prompt, SYSTEM_GUARDRAILS, json_output=True, max_tokens=512)
    return result if result else {
        "description": "Check out this video!",
        "hook_line": "Check out this video!",
        "hashtags": ["content", "video", "viral"]
    }


def get_user_context(user_id: str, limit: int = 10) -> str:
    from app import db
    from models import Conversation, UserPreference
    
    context_parts = []
    
    try:
        prefs = UserPreference.query.filter_by(user_id=user_id).first()
        if prefs:
            context_parts.append(f"User Preferences: Voice={prefs.preferred_voice}, Format={prefs.preferred_format}")
            if prefs.style_preferences:
                context_parts.append(f"Style: {json.dumps(prefs.style_preferences)}")
        
        recent = Conversation.query.filter_by(user_id=user_id).order_by(
            Conversation.created_at.desc()
        ).limit(limit).all()
        
        if recent:
            history_summary = []
            for conv in reversed(recent):
                role = "User" if conv.role == "user" else "AI"
                text = conv.content[:200] + "..." if len(conv.content) > 200 else conv.content
                history_summary.append(f"{role}: {text}")
            
            if history_summary:
                context_parts.append("Recent conversation context:\n" + "\n".join(history_summary))
        
        learning_context = get_learning_context(user_id)
        if learning_context:
            context_parts.append(learning_context)
    
    except Exception as e:
        print(f"Error fetching user context: {e}")
    
    return "\n\n".join(context_parts) if context_parts else ""


def get_learning_context(user_id: str) -> str:
    from app import db
    from models import ProjectFeedback, AILearning
    
    try:
        ai_learning = AILearning.query.filter_by(user_id=user_id).first()
        if not ai_learning or ai_learning.learning_progress < 5:
            return ""
        
        learning_parts = []
        
        learning_parts.append(f"Learning Progress: {ai_learning.learning_progress}% (Projects: {ai_learning.total_projects}, Successful: {ai_learning.successful_projects})")
        
        if ai_learning.can_auto_generate:
            learning_parts.append("Status: Ready for auto-generation")
        
        recent_feedback = ProjectFeedback.query.filter_by(user_id=user_id).order_by(
            ProjectFeedback.created_at.desc()
        ).limit(5).all()
        
        if recent_feedback:
            insights = []
            patterns = {
                'script': {'great': 0, 'ok': 0, 'weak': 0},
                'voice': {'great': 0, 'ok': 0, 'weak': 0},
                'visuals': {'great': 0, 'ok': 0, 'weak': 0},
                'soundfx': {'great': 0, 'ok': 0, 'weak': 0}
            }
            
            for fb in recent_feedback:
                if fb.script_rating and fb.script_rating in patterns['script']:
                    patterns['script'][fb.script_rating] += 1
                if fb.voice_rating and fb.voice_rating in patterns['voice']:
                    patterns['voice'][fb.voice_rating] += 1
                if fb.visuals_rating and fb.visuals_rating in patterns['visuals']:
                    patterns['visuals'][fb.visuals_rating] += 1
                if fb.soundfx_rating and fb.soundfx_rating in patterns['soundfx']:
                    patterns['soundfx'][fb.soundfx_rating] += 1
                
                if fb.ai_to_improve and fb.severity in ['moderate', 'critical']:
                    insights.append(fb.ai_to_improve)
            
            pattern_guidance = []
            for category, counts in patterns.items():
                if counts['weak'] >= 2:
                    pattern_guidance.append(f"- {category.upper()}: User frequently rates this weak - needs significant improvement")
                elif counts['great'] >= 3:
                    pattern_guidance.append(f"- {category.upper()}: User loves your {category} work - keep this style")
            
            if pattern_guidance:
                learning_parts.append("Pattern Analysis:\n" + "\n".join(pattern_guidance))
            
            if insights:
                learning_parts.append("Key Improvements to Apply:\n- " + "\n- ".join(insights[:3]))
        
        return "## LEARNED USER PREFERENCES:\n" + "\n".join(learning_parts) if learning_parts else ""
    
    except Exception as e:
        print(f"Error fetching learning context: {e}")
        return ""


def save_conversation(user_id: str, role: str, content: str):
    from app import db
    from models import Conversation
    
    try:
        conv = Conversation(user_id=user_id, role=role, content=content)
        db.session.add(conv)
        db.session.commit()
    except Exception as e:
        print(f"Error saving conversation: {e}")
        db.session.rollback()


def build_personalized_prompt(user_id: str, base_prompt: str) -> str:
    user_context = get_user_context(user_id)
    
    if user_context:
        return f"{base_prompt}\n\n## USER CONTEXT (Learn from this):\n{user_context}"
    return base_prompt


def generate_script(idea: dict, transcript: str, duration: int = 30, use_trends: bool = True, template_type: str = 'start_from_scratch') -> dict:
    template = get_template_guidelines(template_type)
    
    trend_context = ""
    trend_data = None
    trend_quality = "full"
    
    if use_trends:
        topic = idea.get('idea', '')[:100]
        trend_data = research_topic_trends(topic)
        if trend_data and trend_data.get('patterns'):
            patterns = trend_data['patterns']
            hooks_found = len(patterns.get('hooks', []))
            formats_found = len(patterns.get('formats', []))
            
            if hooks_found < 2 or formats_found < 2:
                trend_quality = "partial"
            
            trend_context = f"""
TREND INTELLIGENCE (apply WITHIN the template tone):
- Successful hooks: {', '.join(patterns.get('hooks', [])[:3]) or 'Limited data - use template defaults'}
- Popular formats: {', '.join(patterns.get('formats', [])[:3]) or 'Limited data - use template defaults'}
- Visual styles: {', '.join(patterns.get('visuals', [])[:3]) or 'Limited data - use template defaults'}
- Effective framings: {', '.join(patterns.get('framings', [])[:3]) or 'Limited data - use template defaults'}

{"NOTE: Limited trend data for this niche topic. Lean more heavily on template tone and structure." if trend_quality == "partial" else "Apply these patterns while staying true to the template voice."}
"""
        else:
            trend_quality = "none"
            trend_context = """
TREND INTELLIGENCE: No specific trend data found for this topic.
Focus entirely on the template tone and structure. The template knows what works.
"""
    
    template_guidance = f"""
TEMPLATE: {template_type.upper().replace('_', ' ')}
TONE: {template['tone']}
VOICE: {template['voice']}
HOOK STYLE: {template['hook_style']}
PACING: {template['pacing']}
HOW TO USE TRENDS: {template['trend_application']}
ALLOWED FOR THIS TEMPLATE: {', '.join(template['allowed_overrides'])}
"""
    
    prompt = f"""Write a {duration}-second video script based on this idea:

IDEA: {idea['idea']}
TYPE: {idea['type']}
CONTEXT: {idea.get('context', 'N/A')}
{template_guidance}
{trend_context}
FULL TRANSCRIPT FOR REFERENCE:
{transcript[:8000]}

The script must contain:
1. HOOK: Follow the template's hook style. Apply trend patterns within that style.
2. CORE_CLAIM: The central argument or observation (2-3 sentences)
3. GROUNDING: Explanation that provides context and nuance (2-3 sentences)
4. CLOSING: A line that reinforces meaning, matching the template's pacing

IMPORTANT: Stay in the template's voice. Trends inform HOW you execute, not WHAT tone you use.

Also specify:
- TONE: Use "{template['tone']}" (from template)
- VISUAL_INTENT: One of [supportive, neutral, contextual, contrasting]

Output as JSON with keys: hook, core_claim, grounding, closing, tone, visual_intent, full_script, template_used"""

    result = call_ai(prompt, SYSTEM_GUARDRAILS, json_output=True, max_tokens=2048)
    
    if result:
        result['template_used'] = template_type
        result['trend_quality'] = trend_quality
        if trend_data:
            result['trend_intel'] = {
                'patterns_used': trend_data.get('patterns', {}),
                'sources': trend_data.get('sources', [])[:3],
                'quality': trend_quality
            }
    
    return result


def validate_loop_score(thesis: str, script) -> dict:
    if isinstance(script, str):
        full_script = script
        lines = [l.strip() for l in script.strip().split('\n') if l.strip()]
        closing = lines[-1] if lines else ''
    else:
        full_script = script.get('full_script', '')
        closing = script.get('closing', '')
    
    prompt = f"""Analyze how well this script "closes the loop" back to its thesis.

THESIS: {thesis}

FULL SCRIPT:
{full_script}

CLOSING LINE: {closing}

A strong loop means:
1. The ending explicitly reconnects to the thesis
2. The viewer's understanding moves toward the thesis
3. No clip ends on evidence or contrast without meaning resolution

Score this script's loop closure from 0.0 to 1.0 where:
- 0.0-0.3: Weak loop - ending drifts from thesis, needs rewrite
- 0.4-0.6: Moderate loop - connection exists but could be stronger
- 0.7-0.85: Strong loop - clear reconnection to thesis
- 0.86-1.0: Excellent loop - thesis is reinforced powerfully

Output JSON with:
- "loop_score": float (0.0-1.0)
- "loop_strength": "weak" | "moderate" | "strong" | "excellent"
- "analysis": Brief explanation of the connection (2-3 sentences)
- "issues": Array of specific problems if score < 0.7
- "suggested_fix": If score < 0.7, propose a rewritten closing line that better connects to thesis
- "fix_type": "rewrite_landing" | "extend_ending" | "add_reframe" | null"""

    result = call_ai(prompt, SYSTEM_GUARDRAILS, json_output=True, max_tokens=1024)
    if not result:
        return {
            "loop_score": 0.5,
            "loop_strength": "moderate",
            "analysis": "Unable to analyze loop closure",
            "issues": [],
            "suggested_fix": None,
            "fix_type": None
        }
    return result


def ai_approval_gate(script: dict, visual_plan: list) -> dict:
    prompt = f"""As the Calligra Compliance Officer, review this proposed post against our Constitution.

PROPOSED SCRIPT:
{script.get('full_script', '')}

VISUAL PLAN:
{json.dumps(visual_plan)}

CONSTITUTIONAL REQUIREMENTS:
1. No juvenile humor (bathroom, sex, shock).
2. No sexualized visuals (Hard Ban on bikinis, lingerie, erotic poses).
3. No brands or celebrities.
4. Calm, restrained tone. No internet slang or meme speak.
5. Clarity over noise. Meaning over metrics.

Output JSON:
{{
    "approved": true/false,
    "reasoning": "Brief explanation of decision",
    "required_changes": ["List of changes if rejected, otherwise empty"]
}}"""

    result = call_ai(prompt, SYSTEM_GUARDRAILS, json_output=True, max_tokens=1024)
    return result if result else {"approved": False, "reasoning": "Approval engine error", "required_changes": []}


def build_post_from_script(user_script: str) -> dict:
    keywords_data = extract_keywords_from_script(user_script)
    
    all_keywords = (
        keywords_data.get("primary_keywords", []) + 
        keywords_data.get("mood_keywords", [])
    )
    
    stock_videos = search_stock_videos(all_keywords)
    
    refined_script_prompt = f"""Based on this pitch/idea, write a polished short-form video script.

ORIGINAL PITCH:
{user_script}

EXTRACTED TONE: {keywords_data.get('tone', 'neutral')}
HOOK SUMMARY: {keywords_data.get('hook_summary', '')}
VISUAL SUGGESTIONS: {', '.join(keywords_data.get('visual_suggestions', []))}

Write a complete script with:
- HOOK (first 3 seconds - grab attention)
- SETUP (10 seconds - establish the context)
- PAYOFF (the insight, joke, or revelation)
- CALL TO ACTION (what should viewer think/do)

Keep it punchy. Match the tone. Make every word count.

Output as JSON:
{{
    "hook": "opening line",
    "setup": "context paragraph",
    "payoff": "the main point",
    "cta": "closing thought or question",
    "suggested_duration": "15/30/60 seconds"
}}"""

    refined_script = call_ai(refined_script_prompt, SYSTEM_GUARDRAILS, json_output=True, max_tokens=1024)
    if not refined_script:
        refined_script = {
            "hook": "",
            "setup": user_script,
            "payoff": "",
            "cta": "",
            "suggested_duration": "30 seconds"
        }
    
    return {
        "original_pitch": user_script,
        "keywords": keywords_data,
        "refined_script": refined_script,
        "stock_videos": stock_videos,
        "attribution_required": [v.get("attribution") for v in stock_videos if v.get("attribution")]
    }


def extract_thesis(content: str, content_type: str = "idea", has_clarification: bool = False) -> dict:
    clarification_instruction = ""
    if has_clarification:
        clarification_instruction = """
IMPORTANT: The user has already provided clarification about their angle/direction.
You MUST proceed with generating a thesis. Do NOT set requires_clarification to true.
Use the clarification they provided to determine the angle and generate the thesis."""
    else:
        clarification_instruction = """
If the content is unclear or could go multiple directions, set requires_clarification to true and provide:
1. A clear, direct question (not listing options in the question text)
2. 3-4 short, distinct answer options (each 2-6 words max)"""
    
    prompt = f"""Analyze this {content_type} and extract the SINGLE CORE THESIS.

CONTENT:
{content[:8000]}

A thesis is NOT:
- A topic ("politics", "technology")
- A summary of multiple points
- A vague observation

A thesis IS:
- One specific claim or insight
- Something that can be argued for or against
- The central idea that all other points should support

{clarification_instruction}

Output JSON:
{{
    "thesis_statement": "One clear sentence stating the core claim",
    "thesis_type": "one of [argument, observation, revelation, challenge, question]",
    "core_claim": "The underlying truth being asserted",
    "target_audience": "Who needs to hear this and why",
    "intended_impact": "What should change in the viewer's mind",
    "confidence": 0.0-1.0 confidence score,
    "requires_clarification": true/false,
    "clarification_question": "A clear, simple question WITHOUT listing options in it",
    "clarification_options": ["Short option 1", "Short option 2", "Short option 3"]
}}

IMPORTANT for clarification_options:
- Each option should be 2-6 words max
- Options should be distinct, meaningful choices
- Do NOT repeat parts of the question in the options
- Examples of GOOD options: ["The hypocrisy", "The cover-up", "The human cost"]
- Examples of BAD options: ["What specific pattern", "Revelation in these files matters"]"""

    result = call_ai(prompt, SYSTEM_GUARDRAILS, json_output=True, max_tokens=1024)
    return result if result else {"thesis_statement": "", "confidence": 0.0, "requires_clarification": True}


def extract_thesis_and_generate_script(content: str, user_context: str = "", learned_patterns: dict = None, has_clarification: bool = False) -> dict:
    learning_section = ""
    if learned_patterns:
        learning_section = f"""
LEARNED FROM YOUR PREVIOUS CONTENT:
- Hook styles that work: {learned_patterns.get('hooks', 'None yet')}
- Pacing preferences: {learned_patterns.get('pacing', 'Default')}
- Structure patterns: {learned_patterns.get('structure', 'Standard')}
- Voice/style: {learned_patterns.get('style', 'Default')}
"""

    clarification_instruction = ""
    if has_clarification:
        clarification_instruction = """
IMPORTANT: The user has already provided clarification about their angle/direction.
You MUST proceed with generating the thesis and script. Do NOT set requires_clarification to true.
Use the clarification they provided to determine the angle."""
    else:
        clarification_instruction = """
If the content is too vague to write a compelling script, set requires_clarification to true.
But if you can reasonably infer an angle, proceed with your best interpretation."""

    prompt = f"""Analyze this content, extract the CORE THESIS, and write a SHORT-FORM VIDEO SCRIPT in ONE response.

CONTENT:
{content[:6000]}

{clarification_instruction}

{learning_section}

{user_context}

THESIS REQUIREMENTS:
- One specific claim or insight (not a topic)
- Something that can be argued for or against
- The central idea that all script lines must support

SCRIPT REQUIREMENTS:
1. EVERY line must serve the thesis
2. HOOK must grab attention in 3 seconds
3. 30-60 seconds total (punchy, no filler)
4. CLOSER must bring viewer back to core claim

Output JSON:
{{
    "requires_clarification": true/false,
    "clarification_question": "If unclear, a simple question",
    "clarification_options": ["Option 1", "Option 2", "Option 3"],
    "thesis": {{
        "thesis_statement": "One clear sentence stating the core claim",
        "thesis_type": "argument/observation/revelation/challenge/question",
        "core_claim": "The underlying truth being asserted",
        "target_audience": "Who needs to hear this",
        "intended_impact": "What should change in viewer's mind",
        "confidence": 0.8
    }},
    "script": {{
        "full_script": "Complete script text",
        "hook": "Opening 3-second hook",
        "closer": "Final statement",
        "tone": "calm/urgent/ironic/analytical/reflective",
        "visual_direction": "Overall visual approach",
        "estimated_duration": "30/45/60 seconds"
    }}
}}

IMPORTANT: Generate BOTH thesis AND script together. Respond with valid JSON only."""

    result = call_ai(prompt, SYSTEM_GUARDRAILS, json_output=True, max_tokens=3000)
    
    if not result:
        return {
            "requires_clarification": True,
            "clarification_question": "What's the main point you want to make?",
            "thesis": {"thesis_statement": "", "confidence": 0.0},
            "script": None
        }
    
    return result


def identify_anchors(script: str, thesis: str) -> list:
    prompt = f"""Analyze this script and identify the ANCHOR POINTS.

THESIS (the core claim this script must prove):
{thesis}

SCRIPT:
{script}

Anchor points are:
- Key statements that DIRECTLY support the thesis
- Moments that structure the argument (not every sentence)
- The "pillars" - remove them and the argument collapses

Types of anchors:
- HOOK: First statement that grabs attention and hints at thesis
- CLAIM: Direct assertion supporting thesis
- EVIDENCE: Fact or example that proves a claim
- PIVOT: Transition to new supporting point
- COUNTER: Acknowledgment of opposing view (strengthens argument)
- CLOSER: Final statement that reinforces thesis

Output JSON array:
[
    {{
        "anchor_text": "The exact text of this anchor",
        "anchor_type": "HOOK/CLAIM/EVIDENCE/PIVOT/COUNTER/CLOSER",
        "position": 1,
        "supports_thesis": true/false,
        "is_hook": true/false,
        "is_closer": true/false,
        "visual_intent": "What visual would support this moment",
        "emotional_beat": "tension/relief/revelation/challenge/resolution"
    }}
]

Only include TRUE anchors. A 60-second script might have 3-5 anchors, not 15."""

    result = call_ai(prompt, SYSTEM_GUARDRAILS, json_output=True, max_tokens=2048)
    if isinstance(result, dict):
        anchors = result.get('anchors', [])
        return anchors if isinstance(anchors, list) else []
    elif isinstance(result, list):
        return result
    return []


def detect_thought_changes(content: str, content_type: str = "script") -> list:
    prompt = f"""Analyze this {content_type} for THOUGHT CHANGES.

CONTENT:
{content}

A thought change occurs when:
- The argument shifts to a new point
- A counter-argument is introduced
- The emotional register changes
- A revelation or payoff arrives
- A new example or evidence begins

For EACH thought change, evaluate:
1. Would cutting here IMPROVE clarity? (not just "is this a transition")
2. Would cutting here IMPROVE retention? (does a cut serve the viewer)
3. If continuous flow works better, mark should_clip as false

Output JSON array:
[
    {{
        "position": percentage through content (0.0-1.0),
        "from_idea": "What idea/point is ending",
        "to_idea": "What idea/point is beginning",
        "transition_type": "pivot/revelation/counter/escalation/resolution",
        "should_clip": true/false,
        "clip_reasoning": "Why cutting here helps (or why continuous is better)",
        "clarity_improvement": 0.0-1.0 (how much clearer with cut),
        "retention_improvement": 0.0-1.0 (how much more engaging with cut)
    }}
]

Be CONSERVATIVE. Don't over-clip. If the flow is good, keep it continuous."""

    result = call_ai(prompt, SYSTEM_GUARDRAILS, json_output=True, max_tokens=2048)
    if isinstance(result, dict):
        return result.get('thought_changes', result)
    return result if result else []


def classify_content_type(script: str, thesis: str = "") -> dict:
    prompt = f"""Analyze this content and classify its type.

SCRIPT:
{script[:2000]}

THESIS (if available):
{thesis}

Content types:
1. INFORMATIVE - Educational, analytical, news-style. Audience expects to LEARN something.
   Visual approach: Text callouts, data overlays, article screenshots, source citations, split-screen comparisons
   
2. COMEDIC - Humor-driven, entertainment-focused. Audience expects to be AMUSED.
   Visual approach: Quick cuts, reaction overlays, meme-style text pops, exaggerated visuals
   
3. INSPIRING - Motivational, emotional, aspirational. Audience expects to FEEL something.
   Visual approach: Cinematic backgrounds, quote overlays, dramatic pacing, powerful imagery

Output JSON:
{{
    "content_type": "informative/comedic/inspiring",
    "confidence": 0.0-1.0,
    "reasoning": "Brief explanation of why this classification",
    "visual_style": {{
        "primary_layer": "background/overlay/split-screen",
        "text_treatment": "callouts/meme-style/quotes",
        "pacing": "steady/quick-cuts/dramatic",
        "suggested_overlays": ["list of overlay types that would work"]
    }},
    "composition_hints": ["specific visual ideas for this content"]
}}"""

    result = call_ai(prompt, SYSTEM_GUARDRAILS, json_output=True, max_tokens=1024)
    if not result:
        return {"content_type": "informative", "confidence": 0.5}
    
    valid_types = ["informative", "comedic", "inspiring"]
    content_type = result.get("content_type", "informative").lower()
    if content_type not in valid_types:
        content_type = "informative"
    result["content_type"] = content_type
    
    return result


def build_visual_layers(script: str, content_classification: dict, anchors: list = None) -> dict:
    content_type = content_classification.get("content_type", "informative")
    visual_style = content_classification.get("visual_style", {})
    
    layer_templates = {
        "informative": {
            "background": {"type": "subtle", "opacity": 0.7, "blur": True},
            "overlays": [
                {"type": "text_callout", "position": "lower_third", "style": "clean"},
                {"type": "data_popup", "position": "center", "animation": "fade_in"},
                {"type": "source_citation", "position": "bottom", "style": "minimal"}
            ],
            "text_style": "professional",
            "transitions": "smooth_fade"
        },
        "comedic": {
            "background": {"type": "dynamic", "opacity": 1.0, "blur": False},
            "overlays": [
                {"type": "reaction_pop", "position": "corner", "style": "bold"},
                {"type": "meme_text", "position": "center", "animation": "zoom_in"},
                {"type": "sound_effect_visual", "position": "floating", "style": "fun"}
            ],
            "text_style": "impact",
            "transitions": "quick_cut"
        },
        "inspiring": {
            "background": {"type": "cinematic", "opacity": 0.9, "blur": False},
            "overlays": [
                {"type": "quote_overlay", "position": "center", "style": "elegant"},
                {"type": "gradient_fade", "position": "bottom", "animation": "slow_reveal"}
            ],
            "text_style": "serif_elegant",
            "transitions": "dramatic_fade"
        }
    }
    
    template = layer_templates.get(content_type, layer_templates["informative"])
    
    layers = {
        "background_layer": template["background"],
        "overlay_layers": [],
        "text_layers": [],
        "effect_layers": [],
        "composition_order": ["background", "overlays", "text", "effects"],
        "content_type": content_type,
        "text_style": template["text_style"],
        "transitions": template["transitions"]
    }
    
    if anchors:
        for i, anchor in enumerate(anchors):
            anchor_type = anchor.get("anchor_type", "CLAIM")
            position = anchor.get("position", i + 1)
            
            if content_type == "informative":
                if anchor_type == "EVIDENCE":
                    layers["overlay_layers"].append({
                        "type": "data_popup",
                        "content": anchor.get("anchor_text", ""),
                        "timing": f"anchor_{position}",
                        "position": "center_right",
                        "animation": "slide_in"
                    })
                elif anchor_type == "CLAIM":
                    layers["text_layers"].append({
                        "type": "callout",
                        "content": anchor.get("anchor_text", ""),
                        "timing": f"anchor_{position}",
                        "position": "lower_third",
                        "style": "highlight"
                    })
            elif content_type == "inspiring":
                if anchor_type in ["HOOK", "CLOSER"]:
                    layers["text_layers"].append({
                        "type": "quote_overlay",
                        "content": anchor.get("anchor_text", ""),
                        "timing": f"anchor_{position}",
                        "position": "center",
                        "style": "dramatic"
                    })
    
    layers["composition_hints"] = content_classification.get("composition_hints", [])
    layers["suggested_overlays"] = visual_style.get("suggested_overlays", [])
    
    return layers


def generate_visual_plan(script: str, thesis: str, anchors: list = None) -> dict:
    classification = classify_content_type(script, thesis)
    
    layers = build_visual_layers(script, classification, anchors)
    
    prompt = f"""Based on this script and classification, suggest specific visuals.

SCRIPT:
{script[:1500]}

CONTENT TYPE: {classification.get('content_type', 'informative')}
VISUAL STYLE: {classification.get('visual_style', {})}

Generate specific visual assets needed:

For INFORMATIVE content, include:
- Article/source screenshots to fetch
- Data visualizations to create
- Text callouts with specific wording

For COMEDIC content, include:
- Reaction images/clips
- Meme-style text overlays
- Visual gags that match the humor

For INSPIRING content, include:
- Cinematic footage types
- Quote overlays with exact text
- Emotional imagery descriptions

Output JSON:
{{
    "background_assets": [
        {{"description": "what to search for", "timing": "when to show", "purpose": "why this visual"}}
    ],
    "overlay_assets": [
        {{"type": "text_callout/data_popup/quote/reaction", "content": "exact text or description", "timing": "when", "position": "where"}}
    ],
    "article_screenshots": [
        {{"search_query": "what article to find", "purpose": "why this source", "timing": "when to show"}}
    ],
    "text_callouts": [
        {{"text": "exact callout text", "timing": "when", "style": "highlight/subtle/dramatic"}}
    ]
}}"""

    visual_assets = call_ai(prompt, SYSTEM_GUARDRAILS, json_output=True, max_tokens=2048)
    if not visual_assets:
        visual_assets = {}
    
    return {
        "classification": classification,
        "layers": layers,
        "assets": visual_assets,
        "composition_ready": True
    }


def generate_thesis_driven_script(thesis: dict, user_context: str = "", learned_patterns: dict = None) -> dict:
    learning_section = ""
    if learned_patterns:
        learning_section = f"""
LEARNED FROM YOUR PREVIOUS CONTENT:
- Hook styles that work: {learned_patterns.get('hooks', 'None yet')}
- Pacing preferences: {learned_patterns.get('pacing', 'Default')}
- Structure patterns: {learned_patterns.get('structure', 'Standard')}
- Voice/style: {learned_patterns.get('style', 'Default')}
"""

    prompt = f"""Write a SHORT-FORM VIDEO SCRIPT that serves this thesis.

THESIS: {thesis.get('thesis_statement', '')}
CORE CLAIM: {thesis.get('core_claim', '')}
TARGET AUDIENCE: {thesis.get('target_audience', 'General')}
INTENDED IMPACT: {thesis.get('intended_impact', 'Make viewer think')}

{learning_section}

{user_context}

RULES:
1. EVERY line must serve the thesis - no tangents, no filler
2. HOOK must hint at thesis without giving it away
3. ANCHORS must be clearly structured (claim → evidence → payoff)
4. THOUGHT CHANGES only where they improve clarity
5. CLOSER must bring viewer back to core claim

Output JSON:
{{
    "full_script": "Complete script text",
    "hook": "Opening 3-second hook",
    "anchors": ["List of anchor statements in the script"],
    "thought_change_points": ["List of positions where cuts would help"],
    "closer": "Final statement",
    "tone": "calm/urgent/ironic/analytical/reflective",
    "visual_direction": "Overall visual approach",
    "estimated_duration": "30/45/60 seconds",
    "thesis_reinforcement": "How the script proves the thesis"
}}"""

    return call_ai(prompt, SYSTEM_GUARDRAILS, json_output=True, max_tokens=2048)


def get_source_learning_context(user_id: str) -> str:
    from app import db
    from models import SourceContent
    
    try:
        sources = SourceContent.query.filter_by(user_id=user_id).order_by(
            SourceContent.created_at.desc()
        ).limit(10).all()
        
        if not sources:
            return ""
        
        learning_parts = []
        
        all_hooks = []
        all_pacing = []
        all_structure = []
        all_style = []
        
        for src in sources:
            if src.learned_hooks:
                all_hooks.extend(src.learned_hooks if isinstance(src.learned_hooks, list) else [src.learned_hooks])
            if src.learned_pacing:
                all_pacing.append(src.learned_pacing)
            if src.learned_structure:
                all_structure.append(src.learned_structure)
            if src.learned_style:
                all_style.append(src.learned_style)
        
        if all_hooks:
            top_hooks = sorted(all_hooks, key=lambda x: x.get('effectiveness', 0) if isinstance(x, dict) else 0, reverse=True)[:3]
            learning_parts.append(f"Effective hook patterns: {json.dumps(top_hooks)}")
        
        if all_style:
            learning_parts.append(f"Preferred style: {json.dumps(all_style[0])}")
        
        if all_pacing:
            learning_parts.append(f"Pacing preferences: {json.dumps(all_pacing[0])}")
        
        return "## LEARNED FROM YOUR CLIPPED CONTENT:\n" + "\n".join(learning_parts) if learning_parts else ""
    
    except Exception as e:
        print(f"Error fetching source learning context: {e}")
        return ""


def get_global_patterns_context() -> str:
    from app import db
    
    try:
        patterns = get_global_learned_patterns(db.session)
        
        if not patterns:
            return ""
        
        pattern_lines = []
        for p in patterns[:5]:
            pattern_lines.append(f"- {p['type'].upper()}: {p['description']} (success: {p['success_rate']:.0%})")
        
        return "## GLOBALLY LEARNED PATTERNS (from all successful content):\n" + "\n".join(pattern_lines)
    
    except Exception as e:
        print(f"Error fetching global patterns: {e}")
        return ""


def process_video(
    video_path: str,
    output_dir: str = "output",
    max_clips: int = 3,
    clip_duration: int = 30,
    aspect_ratio: str = "9:16"
) -> list:
    from audio_processor import extract_audio, transcribe_audio, find_clip_timestamps, generate_captions, cut_video_clip, concatenate_clips, analyze_ideas
    
    os.makedirs(output_dir, exist_ok=True)
    results = []
    
    audio_path = os.path.join(output_dir, "temp_audio.wav")
    if not extract_audio(video_path, audio_path):
        return [{"error": "Failed to extract audio from video"}]
    
    print("Transcribing audio...")
    transcript_data = transcribe_audio(audio_path)
    
    print("Analyzing ideas...")
    ideas = analyze_ideas(transcript_data['full_text'])
    
    top_ideas = sorted(ideas, key=lambda x: x.get('strength', 0), reverse=True)[:max_clips]
    
    for i, idea in enumerate(top_ideas):
        print(f"Processing idea {i+1}: {idea['idea'][:50]}...")
        
        script = generate_script(idea, transcript_data['full_text'], clip_duration)
        
        clip_info = find_clip_timestamps(script, transcript_data['segments'])
        
        captions = generate_captions(script, idea)
        
        output_clips = []
        for j, clip in enumerate(clip_info.get('clips', [])[:3]):
            clip_output = os.path.join(output_dir, f"clip_{i}_{j}.mp4")
            if cut_video_clip(
                video_path, clip_output,
                clip['start'], clip['end'],
                aspect_ratio
            ):
                output_clips.append(clip_output)
        
        if len(output_clips) > 1:
            final_output = os.path.join(output_dir, f"final_{i}.mp4")
            concatenate_clips(output_clips, final_output)
        elif output_clips:
            final_output = output_clips[0]
        else:
            final_output = None
        
        results.append({
            'idea': idea,
            'script': script,
            'clips': clip_info,
            'captions': captions,
            'output_file': final_output
        })
    
    if os.path.exists(audio_path):
        os.unlink(audio_path)
    
    return results


def unified_content_engine(user_input: str, user_id: str, mode: str = "auto", has_media: bool = False, clarification_count: int = 0, force_generate: bool = False) -> dict:
    from audio_processor import process_source_for_clipping, learn_from_source_content
    
    if has_media and mode == "auto":
        mode = "media_options"
        detection = {"mode": "media_options"}
    elif mode == "auto":
        input_lower = user_input.lower().strip()
        refine_keywords = ['edit', 'rewrite', 'adjust', 'change', 'modify', 'update the script', 'fix']
        is_refine = any(kw in input_lower for kw in refine_keywords)
        
        if is_refine:
            mode = 'refine'
            detection = {"mode": "refine"}
        elif len(user_input.strip()) > 50:
            mode = 'create'
            detection = {"mode": "create"}
        elif len(user_input.strip()) < 20:
            mode = 'greeting'
            detection = {"mode": "greeting"}
        else:
            detection_prompt = f"""Analyze this user input. Is it:
1. GREETING: Just hello/hi with no content
2. CREATING: Starting a new idea/topic

Output JSON: {{"mode": "greeting/create"}}"""
            system = "You analyze user intent. Be concise."
            detection = call_ai(detection_prompt, system, json_output=True, max_tokens=64)
            mode = detection.get('mode', 'create') if detection else 'create'
    else:
        detection = {"mode": mode}
    
    user_context = get_user_context(user_id)
    source_learning = get_source_learning_context(user_id)
    global_patterns = get_global_patterns_context()
    
    context_parts = [user_context]
    if source_learning:
        context_parts.append(source_learning)
    if global_patterns:
        context_parts.append(global_patterns)
    full_context = "\n\n".join(context_parts)
    
    if mode == "greeting":
        return {
            "mode": "greeting",
            "status": "conversational",
            "reply": "What's on your mind the world should get to know?",
            "needs_content": True
        }
    
    if mode == "clip_video":
        result = process_source_for_clipping(user_input)
        if result.get('status') == 'ready':
            learnings = learn_from_source_content(user_input, result.get('recommended_clips', []))
            result['learnings'] = learnings
            thesis_statement = result.get('thesis', {}).get('thesis_statement', '')
            if thesis_statement:
                classification = classify_content_type(user_input[:1500], thesis_statement)
                result['content_type'] = classification.get('content_type', 'informative')
                result['visual_style'] = classification.get('visual_style', {})
        return {"mode": "clip_video", "result": result, "status": "ready"}
    
    if mode == "inspire_visuals":
        return {
            "mode": "inspire_visuals",
            "status": "ready",
            "message": "Media analyzed. Visual curation will incorporate insights from your reference.",
            "source_analyzed": True
        }
    
    if mode == "media_options":
        return {
            "mode": "media_options",
            "status": "needs_choice",
            "options": [
                {
                    "id": "inspire_visuals",
                    "label": "Inspire my visuals",
                    "description": "Use this clip's content to inform visual curation for your script"
                },
                {
                    "id": "clip_video",
                    "label": "Clip this video",
                    "description": "Extract segments directly from this video using anchor points"
                }
            ],
            "question": "What would you like to do with this video?"
        }
    
    has_clarification = clarification_count > 0 or force_generate
    
    learned_patterns = {}
    try:
        from models import SourceContent
        from app import db
        sources = SourceContent.query.filter_by(user_id=user_id).limit(5).all()
        if sources:
            for src in sources:
                if src.learned_hooks:
                    learned_patterns['hooks'] = src.learned_hooks
                if src.learned_pacing:
                    learned_patterns['pacing'] = src.learned_pacing
                if src.learned_structure:
                    learned_patterns['structure'] = src.learned_structure
                if src.learned_style:
                    learned_patterns['style'] = src.learned_style
    except:
        pass
    
    combined_result = extract_thesis_and_generate_script(
        user_input, 
        full_context, 
        learned_patterns, 
        has_clarification=has_clarification
    )
    
    if combined_result.get('requires_clarification', False) and not force_generate and clarification_count < 2:
        question = combined_result.get('clarification_question', 'What is the main point you want to make?')
        options = combined_result.get('clarification_options', [])
        
        if options and len(options) >= 2:
            clean_options = []
            for opt in options[:4]:
                if isinstance(opt, str) and len(opt.strip()) > 0:
                    opt_clean = opt.strip()
                    if len(opt_clean) <= 50 and not opt_clean.lower().startswith('what'):
                        clean_options.append(opt_clean)
            options = clean_options
            if options:
                options.append('Something else...')
        
        return {
            "mode": "create",
            "status": "needs_clarification", 
            "thesis": combined_result.get('thesis', {}),
            "question": question,
            "options": options,
            "clarification_number": clarification_count + 1
        }
    
    thesis = combined_result.get('thesis', {})
    script = combined_result.get('script', {})
    
    if (force_generate or clarification_count >= 2) and (not thesis.get('thesis_statement') or combined_result.get('requires_clarification')):
        force_prompt = f"""Generate content for a short-form video. Proceed without further clarification.

USER INPUT:
{user_input[:2000]}

Based on your knowledge, generate a compelling thesis AND script for a 30-60 second video.
Make smart assumptions about the angle and tone.

Output JSON:
{{
    "thesis": {{
        "thesis_statement": "One clear sentence stating the core claim",
        "thesis_type": "argument/observation/revelation",
        "core_claim": "The underlying truth",
        "target_audience": "Who needs to hear this",
        "intended_impact": "What should change in viewer's mind",
        "confidence": 0.8
    }},
    "script": {{
        "full_script": "Complete script text (30-60 seconds)",
        "hook": "Opening hook",
        "closer": "Final statement",
        "tone": "calm/urgent/ironic",
        "estimated_duration": "45 seconds"
    }}
}}

IMPORTANT: Generate BOTH thesis AND script. Do NOT ask for clarification."""
        
        forced_result = call_ai(force_prompt, SYSTEM_GUARDRAILS, json_output=True, max_tokens=3000)
        if forced_result:
            thesis = forced_result.get('thesis', thesis)
            script = forced_result.get('script', script)
    
    if not thesis.get('thesis_statement'):
        thesis = {
            'thesis_statement': f"An exploration of: {user_input[:100]}",
            'thesis_type': 'observation',
            'core_claim': user_input[:300],
            'target_audience': 'General audience',
            'intended_impact': 'Inform and entertain',
            'confidence': 0.6
        }
    
    if not script or not script.get('full_script'):
        script = generate_thesis_driven_script(thesis, full_context, learned_patterns)
    
    anchors = identify_anchors(script.get('full_script', ''), thesis.get('thesis_statement', ''))
    thought_changes = detect_thought_changes(script.get('full_script', ''))
    
    visual_plan = generate_visual_plan(
        script.get('full_script', ''),
        thesis.get('thesis_statement', ''),
        anchors
    )
    
    return {
        "mode": "create",
        "status": "ready",
        "thesis": thesis,
        "script": script,
        "anchors": anchors,
        "thought_changes": thought_changes,
        "learned_patterns_applied": bool(learned_patterns),
        "content_type": visual_plan.get("classification", {}).get("content_type", "informative"),
        "visual_plan": visual_plan
    }


def analyze_editing_patterns_global(video_data: dict, clips_data: list = None) -> dict:
    transcript = video_data.get('transcript', '')
    recommended_clips = clips_data or video_data.get('recommended_clips', [])
    
    if not transcript and not recommended_clips:
        return {'patterns': [], 'success': False}
    
    prompt = f"""Analyze this video content for GLOBAL editing patterns that can help improve future content for ALL users.

TRANSCRIPT (if any):
{transcript[:4000]}

CLIPS/SEGMENTS (if any):
{json.dumps(recommended_clips[:10], indent=2) if recommended_clips else 'None'}

Extract UNIVERSAL editing patterns that work well:

1. PACING PATTERNS: Cut timing, segment lengths, rhythm
2. TRANSITION PATTERNS: How segments flow together
3. HOOK PATTERNS: Opening techniques that grab attention
4. STRUCTURE PATTERNS: How content is organized
5. EMOTIONAL BEATS: Where intensity rises/falls

Output JSON:
{{
    "editing_patterns": [
        {{
            "pattern_type": "pacing|transition|hook|structure|emotional",
            "description": "What the pattern is",
            "example": "Brief example from content",
            "strength": 0.0-1.0
        }}
    ],
    "avg_segment_duration": 3.5,
    "total_segments": 6,
    "dominant_style": "fast_cuts|moderate|slow_build",
    "key_insight": "One-sentence summary of what makes this content work"
}}

IMPORTANT: Respond with valid JSON only."""

    try:
        response = call_ai(prompt, max_tokens=800)
        result = json.loads(response)
        return {'patterns': result.get('editing_patterns', []), 'success': True, 'analysis': result}
    except Exception as e:
        print(f"[Global Learning] Error analyzing patterns: {e}")
        return {'patterns': [], 'success': False, 'error': str(e)}


def store_global_patterns(patterns: list, db_session=None):
    if not patterns or not db_session:
        return False
    
    try:
        from models import GlobalPattern
        
        for pattern in patterns:
            pattern_type = f"editing_{pattern.get('pattern_type', 'general')}"
            pattern_data = {
                'description': pattern.get('description', ''),
                'example': pattern.get('example', ''),
                'strength': pattern.get('strength', 0.5)
            }
            
            existing = db_session.query(GlobalPattern).filter_by(
                pattern_type=pattern_type
            ).first()
            
            if existing:
                existing.usage_count += 1
                if pattern.get('strength', 0.5) > 0.7:
                    existing.success_count += 1
                existing.success_rate = existing.success_count / max(existing.usage_count, 1)
                if pattern.get('strength', 0.5) > existing.pattern_data.get('strength', 0):
                    existing.pattern_data = pattern_data
            else:
                new_pattern = GlobalPattern(
                    pattern_type=pattern_type,
                    pattern_data=pattern_data,
                    success_count=1 if pattern.get('strength', 0.5) > 0.7 else 0,
                    usage_count=1,
                    success_rate=1.0 if pattern.get('strength', 0.5) > 0.7 else 0.0
                )
                db_session.add(new_pattern)
        
        db_session.commit()
        print(f"[Global Learning] Stored {len(patterns)} patterns")
        return True
    except Exception as e:
        print(f"[Global Learning] Error storing patterns: {e}")
        return False


def get_global_learned_patterns(db_session=None) -> list:
    if not db_session:
        return []
    
    try:
        from models import GlobalPattern
        
        top_patterns = db_session.query(GlobalPattern).filter(
            GlobalPattern.pattern_type.like('editing_%'),
            GlobalPattern.success_rate > 0.5,
            GlobalPattern.usage_count >= 3
        ).order_by(GlobalPattern.success_rate.desc()).limit(10).all()
        
        return [{
            'type': p.pattern_type.replace('editing_', ''),
            'description': p.pattern_data.get('description', ''),
            'example': p.pattern_data.get('example', ''),
            'success_rate': p.success_rate
        } for p in top_patterns]
    except Exception as e:
        print(f"[Global Learning] Error retrieving patterns: {e}")
        return []


def ai_self_critique(project_data: dict, user_accepted: bool = True) -> dict:
    script = project_data.get('script', '')
    visual_plan = project_data.get('visual_plan', {})
    template = project_data.get('template', 'start_from_scratch')
    user_feedback = project_data.get('user_feedback', '')
    original_request = project_data.get('original_request', '')
    
    critique_prompt = f"""You just created a video that the user {"accepted and downloaded" if user_accepted else "rejected"}.

ORIGINAL USER REQUEST:
{original_request}

SCRIPT YOU CREATED:
{script}

VISUAL APPROACH:
{json.dumps(visual_plan, indent=2) if isinstance(visual_plan, dict) else str(visual_plan)}

TEMPLATE USED: {template}

USER FEEDBACK (if any): {user_feedback or "None provided"}

Now be CRITICAL of your own work. Analyze honestly:

1. WHAT YOU DID WELL:
- List specific things that worked (hook, pacing, visuals, message clarity)
- Be specific - cite actual lines or decisions

2. WHAT YOU DIDN'T DO WELL:
- List specific weaknesses or missed opportunities
- What could have been better? Be honest.

3. DID YOU TRULY SERVE THE USER'S INTENT?
- Did you understand what they actually wanted?
- Did you add anything unnecessary?
- Did you miss anything important?

4. LEARNINGS FOR NEXT TIME:
- What patterns should you repeat?
- What patterns should you avoid?
- How can you serve similar requests better?

Return JSON with:
{{
    "did_well": ["specific thing 1", "specific thing 2"],
    "did_poorly": ["specific weakness 1", "specific weakness 2"],
    "served_intent_score": 0.0-1.0,
    "intent_analysis": "explanation of how well you understood and served the request",
    "learnings_to_repeat": ["pattern to repeat"],
    "learnings_to_avoid": ["pattern to avoid"],
    "overall_self_score": 0.0-10.0,
    "honest_assessment": "one sentence summary of your performance"
}}
"""
    
    try:
        result = call_ai(critique_prompt, SYSTEM_GUARDRAILS, json_output=True, max_tokens=1024)
        
        if result:
            result['user_accepted'] = user_accepted
            result['project_id'] = project_data.get('project_id')
            print(f"[AI Self-Critique] Score: {result.get('overall_self_score', 'N/A')}/10")
            print(f"[AI Self-Critique] Did well: {result.get('did_well', [])}")
            print(f"[AI Self-Critique] Did poorly: {result.get('did_poorly', [])}")
        
        return result
    except Exception as e:
        print(f"[AI Self-Critique] Error: {e}")
        return {
            'error': str(e),
            'user_accepted': user_accepted
        }


def store_ai_learnings(critique_result: dict, db_session=None) -> bool:
    if not db_session or not critique_result:
        return False
    
    try:
        from models import AILearning, GlobalPattern
        
        user_id = critique_result.get('user_id')
        if not user_id:
            return False
        
        ai_learning = db_session.query(AILearning).filter_by(user_id=user_id).first()
        if not ai_learning:
            return False
        
        current_learnings = ai_learning.dislike_learnings or []
        
        new_learning = {
            'timestamp': str(datetime.now()) if 'datetime' in dir() else 'now',
            'project_id': critique_result.get('project_id'),
            'accepted': critique_result.get('user_accepted', False),
            'score': critique_result.get('overall_self_score', 0),
            'did_well': critique_result.get('did_well', []),
            'did_poorly': critique_result.get('did_poorly', []),
            'to_repeat': critique_result.get('learnings_to_repeat', []),
            'to_avoid': critique_result.get('learnings_to_avoid', [])
        }
        
        current_learnings.append(new_learning)
        if len(current_learnings) > 50:
            current_learnings = current_learnings[-50:]
        
        ai_learning.dislike_learnings = current_learnings
        db_session.commit()
        
        print(f"[AI Learning] Stored critique for user {user_id}")
        return True
    except Exception as e:
        print(f"[AI Learning] Error storing: {e}")
        return False


def analyze_remix_input(user_input: str, uploaded_files: list = None, user_context: str = "") -> dict:
    file_context = ""
    if uploaded_files:
        file_context = f"\nUPLOADED FILES:\n" + "\n".join([
            f"- {f.get('name', 'file')}: {f.get('type', 'unknown')} ({f.get('size', 0)} bytes)"
            for f in uploaded_files
        ])
    
    prompt = f"""Analyze this Remix request. Your job is to intelligently understand:

1. TEMPLATE BASE: What structure/format should this video follow?
   - Is this an Explainer? Hot Take? Ad? Story? Meme?
   - What editing style, pacing, and visual approach?
   
2. CONTENT TO IMPLEMENT: What is the user's unique message?
   - What's the core thesis/idea?
   - What specific points need to be made?
   - Any brand elements, logos, or custom requirements?

USER INPUT:
{user_input}
{file_context}

CONVERSATION CONTEXT:
{user_context[:500] if user_context else "First message in conversation"}

AVAILABLE TEMPLATES AND THEIR CHARACTERISTICS:
- hot_take: Provocative, punchy, fast opener, sharp close. Bold claims.
- explainer: Educational, patient, question hook, steady build.
- story_time: Narrative, immersive, tension build, emotional beats.
- commentary: Analytical, observational, insight-driven.
- meme_funny: Comedic, timing-focused, subverted expectations.
- make_an_ad: Persuasive, benefit-focused, problem/solution, CTA.
- tiktok_edit: Fast, visual-first, trend-forward, audio-synced.
- open_letter: Direct address, personal, emotional weight.

DECISION LOGIC:
- If you can clearly identify BOTH the template base AND the content, proceed.
- If the template is clear but content details are missing (brand colors, target audience, specific points), proceed with defaults.
- If the content is clear but template choice is ambiguous between 2+ options, ASK.
- If both are unclear, ASK ONE focused question.

Output JSON:
{{
    "needs_clarification": true/false,
    "clarification_question": "ONE focused question if needed, otherwise null",
    "confidence": 0.0-1.0,
    "analysis": {{
        "detected_template": "template_name or null",
        "template_confidence": 0.0-1.0,
        "template_alternatives": ["other possible templates"],
        "detected_content": {{
            "core_thesis": "the main point/message",
            "key_points": ["point 1", "point 2"],
            "tone": "detected tone",
            "target_audience": "who this is for or 'general'"
        }},
        "has_uploaded_content": true/false,
        "missing_info": ["list of important missing details"]
    }},
    "remix_plan": {{
        "template_to_use": "final template choice",
        "editing_style": "description of editing approach",
        "visual_approach": "how to handle visuals (Runway + stock + user files)",
        "pacing": "fast/moderate/slow",
        "estimated_duration": 30,
        "source_priority": ["runway", "stock", "user_files"]
    }}
}}

IMPORTANT: Default to proceeding. Only ask if genuinely critical info is missing."""

    result = call_ai(prompt, SYSTEM_GUARDRAILS, json_output=True, max_tokens=1024)
    
    if not result:
        return {
            "needs_clarification": True,
            "clarification_question": "What kind of video would you like to create? (Explainer, Ad, Story, etc.)",
            "confidence": 0,
            "analysis": {}
        }
    
    if result.get("needs_clarification") and result.get("clarification_question"):
        print(f"[Remix Workflow] Needs clarification: {result.get('clarification_question')}")
    else:
        template = result.get("remix_plan", {}).get("template_to_use", "unknown")
        confidence = result.get("confidence", 0)
        print(f"[Remix Workflow] Auto-detected template: {template} (confidence: {confidence})")
    
    return result


def orchestrate_remix_sources(remix_plan: dict, user_files: list = None) -> dict:
    template = remix_plan.get("remix_plan", {}).get("template_to_use", "explainer")
    visual_approach = remix_plan.get("remix_plan", {}).get("visual_approach", "")
    content = remix_plan.get("analysis", {}).get("detected_content", {})
    
    prompt = f"""Create orchestration instructions for a multi-source Remix video.

TEMPLATE: {template}
VISUAL APPROACH: {visual_approach}
CONTENT THESIS: {content.get('core_thesis', '')}
KEY POINTS: {json.dumps(content.get('key_points', []))}
HAS USER FILES: {bool(user_files)}
USER FILES: {json.dumps([f.get('name', 'file') for f in (user_files or [])]) if user_files else 'None'}

Create instructions for each source in the video production pipeline:

1. RUNWAY API: What AI-generated video transformations are needed?
2. STOCK SOURCES: What stock footage/images should be searched for?
3. USER FILES: How should user's uploaded content be incorporated?
4. VIDEO EDITOR: How should all sources be merged? (timing, transitions, layering)

Output JSON:
{{
    "runway_instructions": {{
        "generation_type": "image_to_video|video_to_video|text_to_video",
        "style_prompt": "visual style description for Runway",
        "motion_guidance": "how motion should flow",
        "scenes": [
            {{"scene_num": 1, "duration": 5, "runway_prompt": "specific prompt for this scene"}}
        ]
    }},
    "stock_instructions": {{
        "search_queries": ["query 1", "query 2"],
        "preferred_style": "cinematic|documentary|modern|vintage",
        "scenes_needing_stock": [1, 3, 5],
        "avoid": ["what to avoid in stock selection"]
    }},
    "user_file_instructions": {{
        "incorporation_method": "overlay|replace|blend",
        "placement": ["where user files should appear"],
        "treatment": "how to process user files to match style"
    }},
    "editor_instructions": {{
        "transition_style": "cut|fade|zoom|whip",
        "pacing_bpm": 120,
        "color_grade": "warm|cool|neutral|cinematic",
        "caption_style": "bold_pop|clean_minimal|boxed",
        "audio_sync": true/false,
        "render_priority": ["scene order or priority notes"]
    }},
    "estimated_api_calls": {{
        "runway_seconds": 30,
        "stock_queries": 5,
        "estimated_cost": 5.10
    }}
}}"""

    result = call_ai(prompt, SYSTEM_GUARDRAILS, json_output=True, max_tokens=1024)
    
    if result:
        print(f"[Remix Orchestration] Runway: {result.get('runway_instructions', {}).get('generation_type', 'N/A')}")
        print(f"[Remix Orchestration] Stock queries: {len(result.get('stock_instructions', {}).get('search_queries', []))}")
        print(f"[Remix Orchestration] Estimated cost: ${result.get('estimated_api_calls', {}).get('estimated_cost', 5.10)}")
    
    return result if result else {}


def record_remix_success(remix_result: dict, user_feedback: str = "accepted", db_session=None) -> bool:
    if not db_session or not remix_result:
        return False
    
    try:
        from models import GlobalPattern
        
        pattern_type = f"remix_{remix_result.get('template', 'general')}"
        
        runway_instructions = remix_result.get('runway_instructions', {})
        editor_instructions = remix_result.get('editor_instructions', {})
        
        pattern_data = {
            'runway_style': runway_instructions.get('style_prompt', ''),
            'generation_type': runway_instructions.get('generation_type', ''),
            'color_grade': editor_instructions.get('color_grade', ''),
            'transition_style': editor_instructions.get('transition_style', ''),
            'pacing_bpm': editor_instructions.get('pacing_bpm', 120),
            'user_feedback': user_feedback
        }
        
        existing = db_session.query(GlobalPattern).filter_by(
            pattern_type=pattern_type
        ).first()
        
        success = user_feedback in ['accepted', 'downloaded', 'liked']
        
        if existing:
            existing.usage_count += 1
            if success:
                existing.success_count += 1
            existing.success_rate = existing.success_count / max(existing.usage_count, 1)
            if success and existing.success_rate > 0.7:
                existing.pattern_data = pattern_data
        else:
            from models import GlobalPattern
            new_pattern = GlobalPattern(
                pattern_type=pattern_type,
                pattern_data=pattern_data,
                success_count=1 if success else 0,
                usage_count=1,
                success_rate=1.0 if success else 0.0
            )
            db_session.add(new_pattern)
        
        db_session.commit()
        print(f"[Remix Learning] Recorded {'success' if success else 'attempt'} for {pattern_type}")
        return True
        
    except Exception as e:
        print(f"[Remix Learning] Error: {e}")
        return False
