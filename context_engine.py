import os
import json
import subprocess
import tempfile
import requests
from typing import Optional, List
from openai import OpenAI
import anthropic
from duckduckgo_search import DDGS

# Topic trend research cache to avoid redundant searches
_trend_cache = {}

# Template-Tone DNA: Each template has a fixed tone and the AI applies trends WITHIN that tone
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
    """Get the visual FX settings for a specific template."""
    template_key = template_type.lower().replace(' ', '_').replace('-', '_')
    return TEMPLATE_VISUAL_FX.get(template_key, TEMPLATE_VISUAL_FX['start_from_scratch'])

def get_template_guidelines(template_type: str) -> dict:
    """Get the tone DNA and guidelines for a specific template."""
    template_key = template_type.lower().replace(' ', '_').replace('-', '_')
    return TEMPLATE_TONE_DNA.get(template_key, TEMPLATE_TONE_DNA['start_from_scratch'])

# API Keys
XAI_API_KEY = os.environ.get("XAI_API_KEY")
UNSPLASH_ACCESS_KEY = os.environ.get("UNSPLASH_ACCESS_KEY")
PIXABAY_API_KEY = os.environ.get("PIXABAY_API_KEY")
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY")

# Claude client (primary AI via Replit AI Integrations)
claude_client = anthropic.Anthropic(
    api_key=os.environ.get("AI_INTEGRATIONS_ANTHROPIC_API_KEY"),
    base_url=os.environ.get("AI_INTEGRATIONS_ANTHROPIC_BASE_URL")
)

# xAI client (fallback)
xai_client = OpenAI(
    api_key=XAI_API_KEY,
    base_url="https://api.x.ai/v1"
)

# OpenAI client for audio transcription
openai_client = OpenAI(
    api_key=os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY"),
    base_url=os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL")
)

# Alias for backwards compatibility
client = xai_client

SYSTEM_GUARDRAILS = """You are the Framd AI - a video editing brain, not a content factory. Your purpose is to create videos that match the user's vision with precision and care.

IDENTITY (ALL MODES - REMIX, CLIPPER, SIMPLE STOCK):
You are ONE unified intelligence. The same philosophy applies whether you're:
- REMIX: Transforming existing video while preserving motion/structure
- CLIPPER: Extracting the best moments from long content
- SIMPLE STOCK: Creating original content from stock and AI visuals

YOUR JOB:
1. Understand what the user actually wants (not what you think they want)
2. Ask ONE clear question when critical info is missing
3. Create content that serves their specific goal
4. Be critical of your own work - learn from every output

YOU MUST ASK WHEN:
- Brand colors not specified (don't guess)
- Tone/direction unclear (serious? funny? educational?)
- Target audience unknown (who is this for?)
- Missing logo, assets, or brand materials
- Vague request that could go multiple directions

CORE OPERATING PRINCIPLE:
Intent → Script → Visual → Edit → Deliver
- NEVER select visuals before understanding the message
- EVERY visual choice must serve the script
- EVERY cut must have a purpose

SHORT-FORM CONTENT MASTERY:
You understand that short-form video (TikTok, Reels, Shorts) is about MESSAGE COMPRESSION, not content compression.

THE 3-SECOND RULE:
- The viewer decides to stay or scroll in 3 seconds
- Front-load the value: lead with the insight, not the setup
- First line must create a knowledge gap or emotional hook

ONE IDEA PER VIDEO:
- Each video = ONE clear message, ONE takeaway
- If you can't state the point in one sentence, the script is bloated
- Cut everything that doesn't serve the core message

PUNCHY DELIVERY PRINCIPLES:
- 60 seconds MAX for most content (30-45 is ideal)
- Every sentence earns its place or gets cut
- No throat-clearing ("So basically...", "Let me explain...")
- No filler words or phrases
- End on the punchline or revelation, not a summary

HOOK FORMULAS THAT WORK:
- Counterintuitive truth: "The thing nobody tells you about X..."
- Direct challenge: "Stop doing X. Here's why."
- Curiosity gap: "This changed how I think about X..."
- Pattern interrupt: Start mid-thought, mid-action

RHYTHM & PACING:
- Short sentences hit harder
- Vary sentence length for rhythm
- Strategic pauses > constant talking
- Match visual cuts to voice rhythm

WHAT KILLS SHORT-FORM:
- Slow builds without payoff
- Explaining what you're about to explain
- Multiple tangents or side points
- Asking viewers to wait for the good part
- Generic intros that could apply to any video

TONE & VOICE:
- Calm, confident, clear, restrained, and thoughtful.
- Intelligent Humor: Subtle, observational, timing-based. Never loud, never childish.
- Rule: If the humor can be removed and the message still works, it's correct.

HARD BOUNDARIES:
- NO juvenile or cheap humor (bathroom, sexual, or shock value).
- SEXUAL/GRAPHIC CONTENT: Do not reference or describe. Use neutral phrasing like "We'll skip ahead" or "Moving on" if acknowledgment is unavoidable. Silence is preferred.
- VISUAL BAN: Strictly NO sexualized or thirst-driven content (bikinis, lingerie, erotic poses, etc.).

VISUAL SOURCING:
- Unsplash, Pixabay, Wikimedia Commons ONLY.
- Generic search queries only. No celebrities, no brands.

POLITICAL/SOCIAL:
- No ragebait, slogans, or demonization. 
- Expose contradictions calmly; let conclusions emerge naturally.

FORMATTING RULES:
- NEVER use hyphens or dashes in any generated content. Use colons, commas, or restructure sentences instead.
- Keep punctuation clean and simple.

"Clarity over noise. Meaning over metrics. Thought before output." """


def extract_json_from_text(text: str) -> dict:
    """Extract JSON from text, handling various formats like markdown code blocks."""
    import re
    text = text.strip()
    
    # Try direct parse first
    try:
        return json.loads(text)
    except:
        pass
    
    # Try extracting from code blocks
    if "```" in text:
        match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except:
                pass
    
    # Try finding JSON object in text
    match = re.search(r'\{[\s\S]*\}', text)
    if match:
        try:
            return json.loads(match.group())
        except:
            pass
    
    # Try finding JSON array in text
    match = re.search(r'\[[\s\S]*\]', text)
    if match:
        try:
            return json.loads(match.group())
        except:
            pass
    
    return {}


def call_ai(prompt: str, system_prompt: str = None, json_output: bool = True, max_tokens: int = 2048) -> dict:
    """
    Call Claude as primary AI, with xAI fallback.
    Returns parsed JSON response or empty dict on failure.
    """
    system = system_prompt or SYSTEM_GUARDRAILS
    
    # For JSON output, add explicit instruction for Claude
    final_prompt = prompt
    if json_output:
        final_prompt = prompt + "\n\nIMPORTANT: Respond with valid JSON only. No additional text."
    
    # Try Claude first (primary)
    try:
        response = claude_client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": final_prompt}]
        )
        content = response.content[0].text if response.content else ""
        print(f"[Claude] Success, response length: {len(content)}")
        
        if json_output:
            result = extract_json_from_text(content)
            if result:
                return result
            print(f"[Claude] JSON extraction failed, falling back to xAI...")
        else:
            return {"text": content}
    except Exception as e:
        print(f"[Claude Error] {e}, falling back to xAI...")
    
    # Fallback to xAI
    try:
        kwargs = {
            "model": "grok-3",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt}
            ],
            "max_completion_tokens": max_tokens
        }
        if json_output:
            kwargs["response_format"] = {"type": "json_object"}
        
        response = xai_client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content or ""
        print(f"[xAI] Success, response length: {len(content)}")
        
        if json_output:
            result = extract_json_from_text(content)
            return result if result else {}
        return {"text": content}
    except Exception as e:
        print(f"[xAI Error] {e}")
        return {}


def generate_video_description(script_text: str, trend_sources: list = None, include_hashtags: bool = True) -> dict:
    """Generate a social media description for the video."""
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


def research_topic_trends(topic: str, target_platform: str = "all") -> dict:
    """
    Research how a topic is being discussed across platforms.
    Returns insights on successful formats, hooks, visuals, and framings.
    Uses caching to avoid redundant searches.
    """
    global _trend_cache
    
    cache_key = f"{topic}:{target_platform}"
    if cache_key in _trend_cache:
        print(f"[TrendIntel] Using cached research for: {topic}")
        return _trend_cache[cache_key]
    
    print(f"[TrendIntel] Researching trends for: {topic}")
    
    platforms = ["Twitter", "Instagram Reels", "TikTok", "YouTube Shorts"] if target_platform == "all" else [target_platform]
    
    search_results = []
    try:
        with DDGS() as ddgs:
            for platform in platforms:
                query = f"{topic} {platform} viral video format 2025"
                results = list(ddgs.text(query, max_results=3))
                for r in results:
                    search_results.append({
                        "platform": platform,
                        "title": r.get("title", ""),
                        "snippet": r.get("body", ""),
                        "source": r.get("href", "")
                    })
                    
            general_query = f"{topic} short form video trends hooks what works"
            general_results = list(ddgs.text(general_query, max_results=5))
            for r in general_results:
                search_results.append({
                    "platform": "general",
                    "title": r.get("title", ""),
                    "snippet": r.get("body", ""),
                    "source": r.get("href", "")
                })
    except Exception as e:
        print(f"[TrendIntel] Web search error: {e}")
        search_results = []
    
    if not search_results:
        default_result = {
            "topic": topic,
            "patterns": {
                "hooks": ["Direct question hook", "Controversial statement", "Statistics lead"],
                "formats": ["Talking head with text overlay", "Documentary style", "Quick cuts with captions"],
                "visuals": ["Professional lighting", "Clean background", "Dynamic b-roll"],
                "framings": ["Educational angle", "Personal story", "News commentary"]
            },
            "platform_insights": {},
            "sources": [],
            "cached": False
        }
        return default_result
    
    search_context = "\n".join([
        f"[{r['platform']}] {r['title']}: {r['snippet']}"
        for r in search_results[:15]
    ])
    
    prompt = f"""Analyze this web research about how "{topic}" is being discussed in short-form video content.

RESEARCH FINDINGS:
{search_context}

Based on this research, extract:

1. HOOKS: What opening lines/techniques are working for this topic?
2. FORMATS: What video formats are being used? (talking head, documentary, reaction, etc.)
3. VISUALS: What imagery/b-roll styles are associated with this topic?
4. FRAMINGS: What angles/perspectives are creators taking?
5. PLATFORM SPECIFICS: Any platform-specific patterns noticed?

Output JSON:
{{
    "patterns": {{
        "hooks": ["specific hook style 1", "specific hook style 2", "specific hook style 3"],
        "formats": ["format 1", "format 2", "format 3"],
        "visuals": ["visual style 1", "visual style 2", "visual style 3"],
        "framings": ["framing angle 1", "framing angle 2", "framing angle 3"]
    }},
    "platform_insights": {{
        "Twitter": "what works on Twitter for this topic",
        "Instagram": "what works on Instagram for this topic",
        "TikTok": "what works on TikTok for this topic",
        "YouTube": "what works on YouTube Shorts for this topic"
    }},
    "successful_examples": ["brief description of a successful video format found"],
    "avoid": ["what to avoid based on research"]
}}

Focus on ACTIONABLE patterns that can inform content creation."""

    result = call_ai(prompt, SYSTEM_GUARDRAILS, json_output=True, max_tokens=1024)
    
    if result:
        result["topic"] = topic
        result["sources"] = [{"title": r["title"], "url": r["source"]} for r in search_results[:5]]
        result["cached"] = False
        _trend_cache[cache_key] = result
        print(f"[TrendIntel] Research complete for: {topic}")
        return result
    
    return {
        "topic": topic,
        "patterns": {
            "hooks": ["Direct statement", "Question hook", "Statistic lead"],
            "formats": ["Talking head", "Text overlay", "Documentary"],
            "visuals": ["Professional", "Authentic", "Dynamic"],
            "framings": ["Educational", "Commentary", "Personal"]
        },
        "platform_insights": {},
        "sources": [],
        "cached": False
    }


def get_user_context(user_id: str, limit: int = 10) -> str:
    """
    Build context from user's conversation history for personalized AI responses.
    Returns a summary of the user's preferences and patterns.
    """
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
        
        # Add learning insights from feedback
        learning_context = get_learning_context(user_id)
        if learning_context:
            context_parts.append(learning_context)
    
    except Exception as e:
        print(f"Error fetching user context: {e}")
    
    return "\n\n".join(context_parts) if context_parts else ""


def get_learning_context(user_id: str) -> str:
    """
    Get accumulated learnings from user feedback to inform AI behavior.
    Returns insights the AI should apply when generating content.
    """
    from app import db
    from models import ProjectFeedback, AILearning
    
    try:
        # Get AI learning record
        ai_learning = AILearning.query.filter_by(user_id=user_id).first()
        if not ai_learning or ai_learning.learning_progress < 5:
            return ""
        
        learning_parts = []
        
        # Add overall learning progress
        learning_parts.append(f"Learning Progress: {ai_learning.learning_progress}% (Projects: {ai_learning.total_projects}, Successful: {ai_learning.successful_projects})")
        
        if ai_learning.can_auto_generate:
            learning_parts.append("Status: Ready for auto-generation")
        
        # Get recent feedback insights
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
                
                # Collect specific improvement notes from AI
                if fb.ai_to_improve and fb.severity in ['moderate', 'critical']:
                    insights.append(fb.ai_to_improve)
            
            # Analyze patterns for guidance
            pattern_guidance = []
            for category, counts in patterns.items():
                if counts['weak'] >= 2:
                    pattern_guidance.append(f"- {category.upper()}: User frequently rates this weak - needs significant improvement")
                elif counts['great'] >= 3:
                    pattern_guidance.append(f"- {category.upper()}: User loves your {category} work - keep this style")
            
            if pattern_guidance:
                learning_parts.append("Pattern Analysis:\n" + "\n".join(pattern_guidance))
            
            # Add recent improvement notes (limit to avoid bloat)
            if insights:
                learning_parts.append("Key Improvements to Apply:\n- " + "\n- ".join(insights[:3]))
        
        return "## LEARNED USER PREFERENCES:\n" + "\n".join(learning_parts) if learning_parts else ""
    
    except Exception as e:
        print(f"Error fetching learning context: {e}")
        return ""


def save_conversation(user_id: str, role: str, content: str):
    """Save a conversation message to the database for learning."""
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
    """Build a personalized system prompt incorporating user history."""
    user_context = get_user_context(user_id)
    
    if user_context:
        return f"{base_prompt}\n\n## USER CONTEXT (Learn from this):\n{user_context}"
    return base_prompt


def extract_audio(video_path: str, output_path: str) -> bool:
    """Extract audio from video file using FFmpeg."""
    try:
        cmd = [
            'ffmpeg', '-y', '-i', video_path,
            '-vn', '-acodec', 'pcm_s16le',
            '-ar', '16000', '-ac', '1',
            output_path
        ]
        subprocess.run(cmd, capture_output=True, check=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error extracting audio: {e}")
        return False


def transcribe_audio(audio_path: str) -> dict:
    """Transcribe audio file and return transcript with timestamps."""
    with open(audio_path, 'rb') as audio_file:
        response = openai_client.audio.transcriptions.create(
            model="gpt-4o-mini-transcribe",
            file=audio_file,
            response_format="verbose_json",
            timestamp_granularities=["segment"]
        )
    
    segments = []
    if response and hasattr(response, 'segments') and response.segments:
        for seg in response.segments:
            segments.append({
                'start': seg.start,
                'end': seg.end,
                'text': seg.text
            })
    
    return {
        'full_text': response.text if response else "",
        'segments': segments
    }


def analyze_ideas(transcript: str) -> list[dict]:
    """Analyze transcript to identify key ideas, claims, and contradictions."""
    prompt = f"""Analyze this transcript and identify the key ideas, claims, assumptions, and potential contradictions.

TRANSCRIPT:
{transcript}

Output a JSON array of ideas, each with:
- "idea": The core idea or claim (1-2 sentences)
- "type": One of ["claim", "assumption", "contradiction", "insight", "question"]
- "strength": 1-10 rating of how well-supported/articulated this idea is
- "context": Brief context of why this matters
- "timestamp_hint": Approximate location in the transcript (beginning/middle/end)

Focus on substance, not viral moments. Identify ideas worth exploring, not soundbites."""

    result = call_ai(prompt, SYSTEM_GUARDRAILS, json_output=True, max_tokens=4096)
    if isinstance(result, dict):
        return result.get('ideas', result)
    return result if result else []


def generate_script(idea: dict, transcript: str, duration: int = 30, use_trends: bool = True, template_type: str = 'start_from_scratch') -> dict:
    """Generate a script for a specific idea. Template-driven with trend research applied within template tone."""
    
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
    """Validate how well the script closes back to the thesis. Returns loop score and fix suggestions."""
    # Handle both string and dict input
    if isinstance(script, str):
        full_script = script
        # Try to extract closing from the last line
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


def get_scene_visuals(scene_text: str, scene_type: str, keywords: list = None, topic_trends: dict = None) -> dict:
    """Get AI-curated visual suggestions for a specific scene/anchor using lateral thinking and trend research."""
    keywords_str = ", ".join(keywords) if keywords else ""
    
    trend_visual_context = ""
    if topic_trends and topic_trends.get('patterns', {}).get('visuals'):
        trend_visuals = topic_trends['patterns']['visuals']
        trend_visual_context = f"""
TREND INTELLIGENCE - Visual styles working for this topic:
{', '.join(trend_visuals[:4])}
Use these visual patterns to inform your search queries.
"""
    
    prompt = f"""You are a visual researcher who thinks LATERALLY. Your job is to find stock imagery that REPRESENTS the idea, not matches literal words.

SCENE TYPE: {scene_type}
SCENE TEXT: "{scene_text}"
KEYWORDS: {keywords_str}
{trend_visual_context}

## LATERAL THINKING METHOD
Ask yourself: "What image would a viewer ASSOCIATE with this message?" — NOT "What words are in this sentence?"

Stock sites have common imagery. Search for what EXISTS, not what you wish existed.

## EXAMPLES BY CONTENT TYPE

TECH/SOFTWARE:
- "Clip tools make you scrub timelines" → "filmmaker editing computer", "video production workspace", "creative professional laptop", "digital timeline interface"
- "AI does the heavy lifting" → "robot arm assembly", "automation machinery", "hands-free workflow", "futuristic technology"
- "Stop wasting hours on editing" → "clock time lapse", "frustrated person desk", "hourglass sand falling", "productive workflow"

BUSINESS/STARTUP:
- "Most startups fail in year one" → "empty office chairs", "closed business sign", "entrepreneur stressed", "financial charts declining"
- "Scale your revenue" → "growth chart upward", "team celebrating success", "money stacks", "expanding cityscape"

LIFESTYLE/SELF-IMPROVEMENT:
- "Break free from the 9-5" → "person leaving office building", "laptop beach view", "sunrise freedom", "open road driving"
- "Build habits that stick" → "morning routine coffee", "gym workout", "journal writing", "calendar checkmarks"

DOCUMENTARY/NEWS:
- "Political corruption exposed" → "courthouse steps", "politician podium", "gavel courtroom", "redacted documents"
- "The truth they hide" → "shredded paper", "closed door meeting", "surveillance camera", "locked filing cabinet"

CREATIVE/ARTISTIC:
- "Your story deserves to be heard" → "microphone spotlight", "audience listening", "storyteller stage", "emotional performance"
- "Create content that resonates" → "creator studio setup", "audience engagement", "viral social media", "authentic moment"

## BAD QUERIES (will return garbage):
- Abstract nouns: "truth", "success", "implications"
- Full sentences: "what really happened"  
- Brand names without context: "Adobe Premiere"
- Overly specific tech: "timeline scrubbing feature"

## GOOD QUERIES use:
- People doing actions: "filmmaker editing", "entrepreneur working"
- Objects with context: "laptop creative workspace", "camera studio setup"
- Emotional scenes: "frustrated person computer", "celebrating team office"
- Universal visuals: "clock spinning", "growth chart", "sunrise city"

Output JSON:
{{
    "visual_concept": "One sentence describing what visual REPRESENTS this idea",
    "search_queries": ["lateral visual 1", "lateral visual 2", "lateral visual 3", "lateral visual 4"],
    "background_queries": ["atmospheric setting 1", "cinematic backdrop 2"],
    "visual_style": "tech | lifestyle | documentary | creative | business | atmospheric",
    "motion": "static | slow_pan | zoom | dynamic",
    "mood": "inspiring | tense | hopeful | dramatic | calm | energetic"
}}

Think like a music video director: What B-ROLL represents this feeling?"""

    result = call_ai(prompt, SYSTEM_GUARDRAILS, json_output=True, max_tokens=512)
    if not result:
        return {
            "visual_concept": "Supportive visual for this scene",
            "search_queries": ["documentary footage", "news archive", "dramatic lighting"],
            "background_queries": ["dark cinematic background", "dramatic atmosphere"],
            "visual_style": "atmospheric",
            "motion": "static",
            "mood": "neutral"
        }
    return result


def find_clip_timestamps(script: dict, transcript_segments: list) -> list:
    """Find the best transcript segments that support the script."""
    segments_text = "\n".join([
        f"[{s['start']:.1f}s - {s['end']:.1f}s]: {s['text']}" 
        for s in transcript_segments
    ])
    
    prompt = f"""Given this script and transcript segments, identify which segments best support the script.

SCRIPT:
{script.get('full_script', '')}

TONE: {script.get('tone', 'calm')}
VISUAL INTENT: {script.get('visual_intent', 'supportive')}

TRANSCRIPT SEGMENTS:
{segments_text}

Select segments that:
1. Directly support or illustrate the script's argument
2. Match the intended tone
3. Provide the clearest articulation of the idea

Output JSON with:
- "clips": Array of {{"start": float, "end": float, "purpose": string}}
- "total_duration": Estimated total clip duration in seconds
- "notes": Any important considerations for editing

Only select clips that genuinely support the script. No filler."""

    result = call_ai(prompt, SYSTEM_GUARDRAILS, json_output=True, max_tokens=2048)
    return result if result else {"clips": [], "total_duration": 0, "notes": ""}


def generate_captions(script: dict, idea: dict) -> dict:
    """Generate restrained captions and context for the clip."""
    prompt = f"""Generate social media captions for this video clip.

SCRIPT SUMMARY: {script.get('full_script', '')[:500]}
CORE IDEA: {idea['idea']}

Generate:
1. CAPTION: One restrained caption (no hashtag spam, no rage bait)
2. CLARIFYING: One sentence that adds context
3. QUESTION: One reflective question for the audience

Keep it thoughtful, not clickbait. Output as JSON."""

    return call_ai(prompt, SYSTEM_GUARDRAILS, json_output=True, max_tokens=512)


def cut_video_clip(
    input_path: str, 
    output_path: str, 
    start: float, 
    end: float,
    aspect_ratio: str = "9:16"
) -> bool:
    """Cut a video clip using FFmpeg with specified aspect ratio."""
    
    aspect_filters = {
        "9:16": "crop=ih*9/16:ih,scale=1080:1920",
        "1:1": "crop=min(iw\\,ih):min(iw\\,ih),scale=1080:1080",
        "4:5": "crop=ih*4/5:ih,scale=1080:1350",
        "16:9": "crop=iw:iw*9/16,scale=1920:1080"
    }
    
    vf = aspect_filters.get(aspect_ratio, aspect_filters["9:16"])
    
    try:
        cmd = [
            'ffmpeg', '-y',
            '-i', input_path,
            '-ss', str(start),
            '-to', str(end),
            '-vf', vf,
            '-c:v', 'libx264',
            '-c:a', 'aac',
            '-preset', 'fast',
            output_path
        ]
        subprocess.run(cmd, capture_output=True, check=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error cutting video: {e}")
        return False


def concatenate_clips(clip_paths: list, output_path: str) -> bool:
    """Concatenate multiple video clips into one."""
    if not clip_paths:
        return False
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        for path in clip_paths:
            f.write(f"file '{path}'\n")
        list_file = f.name
    
    try:
        cmd = [
            'ffmpeg', '-y',
            '-f', 'concat',
            '-safe', '0',
            '-i', list_file,
            '-c', 'copy',
            output_path
        ]
        subprocess.run(cmd, capture_output=True, check=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error concatenating clips: {e}")
        return False
    finally:
        os.unlink(list_file)


def process_video(
    video_path: str,
    output_dir: str = "output",
    max_clips: int = 3,
    clip_duration: int = 30,
    aspect_ratio: str = "9:16"
) -> list:
    """Main pipeline: Process a video and generate script-first clips."""
    
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


def ai_approval_gate(script: dict, visual_plan: list) -> dict:
    """AI Gatekeeper that checks for constitution violations before any asset is fetched or post is assembled."""
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


def extract_keywords_from_script(script: str) -> dict:
    """Extract nuanced keywords from a user's script/pitch that capture humor, tone, and message."""
    prompt = f"""Analyze this script/pitch and extract keywords that capture the NUANCE of what they're trying to say.

SCRIPT/PITCH:
{script}

Extract keywords that would help find supporting video footage. Think about:
- The TONE (funny, serious, dramatic, absurd, ironic)
- The VISUAL MOOD (dark, bright, chaotic, calm, intimate)
- KEY CONCEPTS (the actual subjects being discussed)
- EMOTIONAL BEATS (tension, relief, surprise, realization)
- METAPHORS or ANALOGIES implied

Output JSON with:
{{
    "primary_keywords": ["list of 3-5 main search terms for stock footage"],
    "mood_keywords": ["list of 2-3 mood/atmosphere words"],
    "visual_suggestions": ["list of 2-3 specific visual ideas that would support this script"],
    "tone": "one word describing overall tone",
    "hook_summary": "one sentence capturing the core message"
}}"""

    result = call_ai(prompt, SYSTEM_GUARDRAILS, json_output=True, max_tokens=1024)
    if not result:
        return {
            "primary_keywords": [],
            "mood_keywords": [],
            "visual_suggestions": [],
            "tone": "neutral",
            "hook_summary": script[:100]
        }
    return result


def search_stock_videos(keywords: list[str], per_page: int = 5) -> list[dict]:
    """Search Pexels for copyright-free stock videos matching keywords."""
    if not PEXELS_API_KEY:
        return []
    
    all_videos = []
    headers = {"Authorization": PEXELS_API_KEY}
    
    for keyword in keywords[:3]:
        url = "https://api.pexels.com/videos/search"
        params = {
            "query": keyword,
            "per_page": per_page,
            "orientation": "landscape"
        }
        
        try:
            response = requests.get(url, headers=headers, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                for video in data.get("videos", []):
                    video_files = video.get("video_files", [])
                    best_file = None
                    for vf in video_files:
                        if vf.get("quality") == "hd" or not best_file:
                            best_file = vf
                    
                    all_videos.append({
                        "id": video.get("id"),
                        "keyword": keyword,
                        "duration": video.get("duration"),
                        "preview_url": video.get("image"),
                        "video_url": best_file.get("link") if best_file else None,
                        "pexels_url": video.get("url"),
                        "photographer": video.get("user", {}).get("name", "Unknown"),
                        "attribution": f"Video by {video.get('user', {}).get('name', 'Unknown')} on Pexels"
                    })
        except Exception as e:
            print(f"Error searching Pexels for '{keyword}': {e}")
    
    return all_videos


def detect_characters_in_scene(scene_text: str) -> dict:
    """Detect characters, people, or historical figures mentioned in scene text."""
    prompt = f"""Analyze this scene text and identify any characters or people mentioned:

"{scene_text}"

For each character found, determine:
1. Name (if mentioned)
2. Type: "historical" (real historical figure), "celebrity" (modern public figure), or "generic" (unnamed person reference)
3. Search query to find representative imagery (generic terms, no copyrighted names)

Output JSON:
{{
    "characters": [
        {{"name": "character name or description", "type": "historical/celebrity/generic", "search_query": "safe search terms for imagery"}}
    ],
    "has_people": true/false
}}

For historical figures like Einstein, use "scientist portrait" not the name.
For generic references like "a leader", use "leader silhouette professional".
If no people/characters mentioned, return empty array."""
    
    system = "Analyze text and identify any people, characters, or figures mentioned."
    result = call_ai(prompt, system, json_output=True, max_tokens=512)
    return result if result else {"characters": [], "has_people": False}


def search_unsplash(query: str, per_page: int = 6) -> list[dict]:
    """Search Unsplash for high quality stock images."""
    if not UNSPLASH_ACCESS_KEY:
        return []
    
    url = "https://api.unsplash.com/search/photos"
    params = {
        "query": query,
        "per_page": per_page,
        "orientation": "landscape"
    }
    headers = {"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}"}
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            images = []
            for photo in data.get("results", []):
                images.append({
                    "id": f"unsplash_{photo.get('id')}",
                    "url": photo.get("urls", {}).get("regular"),
                    "thumbnail": photo.get("urls", {}).get("small"),
                    "alt": photo.get("alt_description") or query
                })
            return images
    except Exception as e:
        print(f"Error searching Unsplash for '{query}': {e}")
    
    return []


def search_pixabay(query: str, per_page: int = 6) -> list[dict]:
    """Search Pixabay for free stock images and videos."""
    if not PIXABAY_API_KEY:
        return []
    
    url = "https://pixabay.com/api/"
    params = {
        "key": PIXABAY_API_KEY,
        "q": query,
        "per_page": per_page,
        "orientation": "horizontal",
        "safesearch": "true",
        "image_type": "photo"
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            images = []
            for hit in data.get("hits", []):
                images.append({
                    "id": f"pixabay_{hit.get('id')}",
                    "url": hit.get("largeImageURL") or hit.get("webformatURL"),
                    "thumbnail": hit.get("previewURL"),
                    "alt": query
                })
            return images
    except Exception as e:
        print(f"Error searching Pixabay for '{query}': {e}")
    
    return []


def search_pixabay_videos(query: str, per_page: int = 4) -> list[dict]:
    """Search Pixabay for free stock videos."""
    if not PIXABAY_API_KEY:
        return []
    
    url = "https://pixabay.com/api/videos/"
    params = {
        "key": PIXABAY_API_KEY,
        "q": query,
        "per_page": per_page,
        "safesearch": "true"
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            videos = []
            for hit in data.get("hits", []):
                video_files = hit.get("videos", {})
                medium = video_files.get("medium", {})
                videos.append({
                    "id": f"pixabay_v_{hit.get('id')}",
                    "download_url": medium.get("url"),
                    "thumbnail": f"https://i.vimeocdn.com/video/{hit.get('picture_id')}_640x360.jpg",
                    "title": query,
                    "duration": hit.get("duration", 0)
                })
            return videos
    except Exception as e:
        print(f"Error searching Pixabay videos for '{query}': {e}")
    
    return []


def search_pexels(query: str, per_page: int = 6) -> list[dict]:
    """Search Pexels for stock images (fallback source)."""
    if not PEXELS_API_KEY:
        return []
    
    headers = {"Authorization": PEXELS_API_KEY}
    url = "https://api.pexels.com/v1/search"
    params = {
        "query": query,
        "per_page": per_page,
        "orientation": "landscape"
    }
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            images = []
            for photo in data.get("photos", []):
                images.append({
                    "id": f"pexels_{photo.get('id')}",
                    "url": photo.get("src", {}).get("large2x") or photo.get("src", {}).get("large"),
                    "thumbnail": photo.get("src", {}).get("medium"),
                    "alt": photo.get("alt", query)
                })
            return images
    except Exception as e:
        print(f"Error searching Pexels for '{query}': {e}")
    
    return []


def search_wikimedia_images(query: str, per_page: int = 4) -> list[dict]:
    """Search Wikimedia Commons for images (documentary, historical, archival)."""
    try:
        search_url = 'https://commons.wikimedia.org/w/api.php'
        headers = {'User-Agent': 'EchoEngine/1.0 (content creation tool)'}
        
        search_params = {
            'action': 'query',
            'format': 'json',
            'generator': 'search',
            'gsrnamespace': 6,
            'gsrsearch': f'{query}',
            'gsrlimit': per_page * 2,
            'prop': 'imageinfo',
            'iiprop': 'url|extmetadata',
            'iiurlwidth': 800
        }
        
        response = requests.get(search_url, params=search_params, headers=headers, timeout=10)
        print(f"[Wikimedia Images] Query: '{query}', Status: {response.status_code}")
        
        if response.status_code != 200:
            return []
        
        data = response.json()
        pages = data.get('query', {}).get('pages', {})
        print(f"[Wikimedia Images] Found {len(pages)} pages")
        
        images = []
        for page_id, page in pages.items():
            if page_id == '-1':
                continue
            
            imageinfo = page.get('imageinfo', [{}])[0]
            thumb_url = imageinfo.get('thumburl') or imageinfo.get('url')
            full_url = imageinfo.get('url')
            
            # More lenient: use full_url as thumbnail if no thumb
            if not thumb_url and full_url:
                thumb_url = full_url
            
            if full_url:
                title = page.get('title', '').replace('File:', '').replace('_', ' ')
                images.append({
                    "id": f"wikimedia_{page.get('pageid')}",
                    "url": full_url,
                    "thumbnail": thumb_url or full_url,
                    "alt": title[:100] if title else query
                })
        
        print(f"[Wikimedia Images] Returning {len(images)} images")
        return images[:per_page]
    except Exception as e:
        print(f"[Wikimedia Images] Error for '{query}': {e}")
        return []


def search_visuals_unified(query: str, per_page: int = 6) -> list[dict]:
    """Search all visual sources and combine results. Priority: Unsplash > Wikimedia > Pixabay > Pexels."""
    all_results = []
    
    print(f"[Unified Search] Starting search for: '{query}'")
    
    # Try Unsplash first (highest quality, pending approval)
    unsplash_results = search_unsplash(query, per_page=2)
    all_results.extend(unsplash_results)
    print(f"[Unified Search] Unsplash: {len(unsplash_results)} results")
    
    # Try Wikimedia Commons (best for documentary/historical)
    if len(all_results) < per_page:
        wiki_results = search_wikimedia_images(query, per_page=3)
        all_results.extend(wiki_results)
        print(f"[Unified Search] Wikimedia: {len(wiki_results)} results")
    
    # Try Pixabay
    if len(all_results) < per_page:
        pixabay_results = search_pixabay(query, per_page=2)
        all_results.extend(pixabay_results)
        print(f"[Unified Search] Pixabay: {len(pixabay_results)} results")
    
    # Fallback to Pexels if still need more
    if len(all_results) < per_page:
        pexels_results = search_pexels(query, per_page=per_page - len(all_results))
        all_results.extend(pexels_results)
        print(f"[Unified Search] Pexels: {len(pexels_results)} results")
    
    print(f"[Unified Search] Total: {len(all_results)} results for '{query}'")
    return all_results[:per_page]


def search_pexels_safe(query: str, per_page: int = 6) -> list[dict]:
    """Alias for unified search for backward compatibility."""
    return search_visuals_unified(query, per_page)


def build_post_from_script(user_script: str) -> dict:
    """Full pipeline: take user's script idea, extract keywords, find stock footage, build post."""
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
    """
    Extract the core thesis from any content - ideas, transcripts, or scripts.
    The thesis is the single central idea that everything else must serve.
    """
    # If user has already provided clarification, be more aggressive about proceeding
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
    """
    COMBINED function: Extract thesis AND generate script in ONE AI call.
    Saves ~10 seconds by eliminating a separate API call.
    """
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
    """
    Identify anchor points in a script - key statements that structure the argument.
    Anchors are the pillars that hold up the thesis.
    """
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
    """
    Detect thought transitions in content - potential clip points.
    Only marks as clip-worthy if cutting improves clarity/retention.
    """
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
    """
    Classify content as informative, comedic, or inspiring.
    This determines the visual composition approach.
    """
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
    
    # Validate and normalize content_type to valid values
    valid_types = ["informative", "comedic", "inspiring"]
    content_type = result.get("content_type", "informative").lower()
    if content_type not in valid_types:
        content_type = "informative"
    result["content_type"] = content_type
    
    return result


def build_visual_layers(script: str, content_classification: dict, anchors: list = None) -> dict:
    """
    Build a multi-layer visual composition based on content type.
    Returns layer definitions that FFmpeg can composite.
    """
    content_type = content_classification.get("content_type", "informative")
    visual_style = content_classification.get("visual_style", {})
    
    # Base layer templates per content type
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
    
    # Build actual layers with timing based on script structure
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
    
    # Add overlays based on anchors if available
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
    
    # Add composition hints from classification
    layers["composition_hints"] = content_classification.get("composition_hints", [])
    layers["suggested_overlays"] = visual_style.get("suggested_overlays", [])
    
    return layers


def generate_visual_plan(script: str, thesis: str, anchors: list = None) -> dict:
    """
    Generate a complete visual plan for a script.
    Combines content classification with layer building.
    """
    # Step 1: Classify content type
    classification = classify_content_type(script, thesis)
    
    # Step 2: Build layer structure
    layers = build_visual_layers(script, classification, anchors)
    
    # Step 3: Generate specific visual suggestions
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
    """
    Generate a script that serves a specific thesis.
    Every line must trace back to proving/exploring the core claim.
    """
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


def process_source_for_clipping(transcript: str, source_url: str = None) -> dict:
    """
    Process source material for intelligent clipping.
    Extracts thesis, finds anchors, detects thought changes, suggests clips.
    """
    thesis = extract_thesis(transcript, "transcript")
    
    if thesis.get('requires_clarification', False):
        return {
            "status": "needs_clarification",
            "thesis": thesis,
            "question": thesis.get('clarification_question', 'What is the main point you want to make?')
        }
    
    prompt = f"""Analyze this transcript for CLIPPING.

TRANSCRIPT:
{transcript[:10000]}

EXTRACTED THESIS: {thesis.get('thesis_statement', '')}

Find the BEST CLIP-WORTHY MOMENTS that:
1. Most powerfully express the thesis
2. Stand alone as complete thoughts
3. Would grab attention in first 3 seconds
4. Have natural start/end points

For each potential clip:
- Extract the exact text
- Note timestamp position (percentage)
- Rate how well it serves the thesis
- Suggest any cuts that improve clarity

Output JSON:
{{
    "thesis": {{thesis details}},
    "recommended_clips": [
        {{
            "clip_text": "Exact text of recommended clip",
            "start_position": 0.0-1.0,
            "end_position": 0.0-1.0,
            "thesis_alignment": 0.0-1.0,
            "hook_potential": 0.0-1.0,
            "standalone_quality": 0.0-1.0,
            "suggested_cuts": ["positions where internal cuts help"],
            "visual_suggestion": "What visuals would enhance this"
        }}
    ],
    "overall_quality": 0.0-1.0,
    "total_potential_clips": number
}}"""

    result = call_ai(prompt, SYSTEM_GUARDRAILS, json_output=True, max_tokens=3000)
    if result:
        result['status'] = 'ready'
        return result
    return {"status": "error", "thesis": thesis, "recommended_clips": []}


def learn_from_source_content(transcript: str, clips_extracted: list, user_feedback: dict = None) -> dict:
    """
    Learn from clipped content to improve future generation.
    Extracts hooks, pacing, structure, and style patterns.
    """
    prompt = f"""Analyze this SOURCE CONTENT for LEARNING.

ORIGINAL TRANSCRIPT:
{transcript[:6000]}

CLIPS THAT WERE EXTRACTED:
{json.dumps(clips_extracted, indent=2)[:3000]}

USER FEEDBACK (if any):
{json.dumps(user_feedback) if user_feedback else 'None provided'}

Extract PATTERNS that should inform future content generation:

1. HOOK PATTERNS: What makes the openings work?
2. PACING PATTERNS: Sentence length, rhythm, pauses
3. STRUCTURE PATTERNS: How arguments are built
4. STYLE PATTERNS: Tone, word choice, personality

Output JSON:
{{
    "learned_hooks": [
        {{"pattern": "description", "example": "from content", "effectiveness": 0.0-1.0}}
    ],
    "learned_pacing": {{
        "avg_sentence_length": "short/medium/long",
        "rhythm_style": "punchy/flowing/varied",
        "pause_usage": "frequent/occasional/rare"
    }},
    "learned_structure": {{
        "opening_style": "description",
        "argument_flow": "description",
        "closing_style": "description"
    }},
    "learned_style": {{
        "tone": "description",
        "personality_markers": ["list of voice traits"],
        "word_choice": "simple/complex/technical"
    }},
    "key_insights": ["What makes this content effective"],
    "apply_to_generation": ["Specific guidance for future scripts"]
}}"""

    return call_ai(prompt, SYSTEM_GUARDRAILS, json_output=True, max_tokens=2048)


def get_source_learning_context(user_id: str) -> str:
    """
    Get accumulated learnings from all source content the user has clipped.
    This feeds into generation to make scripts match their preferred style.
    """
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
    """
    Get global learned patterns that benefit ALL users.
    These patterns are learned from the collective success of all content.
    """
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


def unified_content_engine(user_input: str, user_id: str, mode: str = "auto", has_media: bool = False, clarification_count: int = 0, force_generate: bool = False) -> dict:
    """
    Unified engine that handles content creation with optional media integration.
    
    Workflow:
    1. Always start with script creation from user's idea/thesis
    2. After script confirmation, proceed to visual curation
    3. If video/audio provided: transcribe and offer two options
       - "Inspire my visuals": Use clip content to inform visual curation
       - "Clip this video": Extract segments using anchor system
    
    Modes:
    - auto: Determine intent (greeting vs creating)
    - create: Force script creation mode
    - clip_video: Direct video clipping using anchors
    - inspire_visuals: Use provided media to inform visual curation
    
    Clarification Rules:
    - Max 3 clarifying questions allowed
    - After 3 clarifications, force script generation using AI's own knowledge
    - AI should research and fill gaps rather than asking endless questions
    """
    # OPTIMIZATION: Skip intent detection for substantial content (saves ~5 seconds)
    # Only do intent detection for short ambiguous inputs
    if has_media and mode == "auto":
        mode = "media_options"
        detection = {"mode": "media_options"}
    elif mode == "auto":
        input_lower = user_input.lower().strip()
        # Check for refine keywords first
        refine_keywords = ['edit', 'rewrite', 'adjust', 'change', 'modify', 'update the script', 'fix']
        is_refine = any(kw in input_lower for kw in refine_keywords)
        
        if is_refine:
            mode = 'refine'
            detection = {"mode": "refine"}
        elif len(user_input.strip()) > 50:
            # Substantial content - skip AI call, assume create mode
            mode = 'create'
            detection = {"mode": "create"}
        elif len(user_input.strip()) < 20:
            # Very short - likely a greeting
            mode = 'greeting'
            detection = {"mode": "greeting"}
        else:
            # Only call AI for ambiguous mid-length inputs
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
    
    # Build full context with user-specific AND global learnings
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
    
    # Handle media-based modes (when user provides video/audio)
    if mode == "clip_video":
        # Direct video clipping using anchor system
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
        # Use provided media content to inform visual curation for existing script
        # This is called after script is confirmed, with media transcript
        return {
            "mode": "inspire_visuals",
            "status": "ready",
            "message": "Media analyzed. Visual curation will incorporate insights from your reference.",
            "source_analyzed": True
        }
    
    if mode == "media_options":
        # User provided media - offer them the choice
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
    
    # Default: Create mode - generate script from user's idea/content
    # OPTIMIZATION: Use combined thesis+script function (saves ~10 seconds)
    has_clarification = clarification_count > 0 or force_generate
    
    # Get learned patterns for the combined call
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
    
    # COMBINED CALL: Extract thesis AND generate script in one AI call
    combined_result = extract_thesis_and_generate_script(
        user_input, 
        full_context, 
        learned_patterns, 
        has_clarification=has_clarification
    )
    
    # Handle clarification if needed (max 2 clarifications)
    if combined_result.get('requires_clarification', False) and not force_generate and clarification_count < 2:
        question = combined_result.get('clarification_question', 'What is the main point you want to make?')
        options = combined_result.get('clarification_options', [])
        
        # Clean up options
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
    
    # Extract thesis and script from combined result
    thesis = combined_result.get('thesis', {})
    script = combined_result.get('script', {})
    
    # FORCE-GENERATE FALLBACK: If still requiring clarification after max attempts, force generate
    if (force_generate or clarification_count >= 2) and (not thesis.get('thesis_statement') or combined_result.get('requires_clarification')):
        # Use AI to research and force generate thesis+script
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
    
    # Final fallback if combined result is incomplete
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
        # Fallback to separate script generation if combined failed
        script = generate_thesis_driven_script(thesis, full_context, learned_patterns)
    
    # Identify anchors from the script
    anchors = identify_anchors(script.get('full_script', ''), thesis.get('thesis_statement', ''))
    thought_changes = detect_thought_changes(script.get('full_script', ''))
    
    # Classify content type and generate visual plan
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
    """
    Analyze editing patterns from video content for global AI learning.
    Called when videos are processed or clips are uploaded.
    Returns patterns that should be stored globally.
    """
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
    """
    Store analyzed patterns in the GlobalPattern table for all users to benefit.
    Should be called with a database session.
    """
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
    """
    Retrieve top-performing global patterns to inject into AI prompts.
    Returns patterns that have proven successful across all users.
    """
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
    """
    AI self-critique system. Runs AFTER user accepts/downloads a video.
    Analyzes what the AI did well and what it didn't do well.
    Stores learnings for future improvement.
    
    Args:
        project_data: Dict containing script, visual_plan, template, user_feedback
        user_accepted: Whether the user accepted this output
    
    Returns:
        Dict with critique analysis and learnings
    """
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
    """
    Store AI self-critique learnings in the database for future reference.
    """
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


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        video_file = sys.argv[1]
        print(f"Processing: {video_file}")
        results = process_video(video_file)
        print(json.dumps(results, indent=2))
    else:
        print("Usage: python context_engine.py <video_file>")
