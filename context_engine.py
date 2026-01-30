import os
import json
import subprocess
import tempfile
import requests
from typing import Optional, List
from openai import OpenAI

# Krakd AI - powered by xAI
XAI_API_KEY = os.environ.get("XAI_API_KEY")
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY")

# Krakd client for text generation (xAI backend)
client = OpenAI(
    api_key=XAI_API_KEY,
    base_url="https://api.x.ai/v1"
)

# OpenAI client for audio transcription (Krakd doesn't support audio yet)
openai_client = OpenAI(
    api_key=os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY"),
    base_url=os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL")
)

SYSTEM_GUARDRAILS = """You are Calligra - a thinking engine, not a content factory. Your purpose is to turn ideas into clear, honest posts while respecting the audience's intelligence.

CORE OPERATING PRINCIPLE:
Script → Visual Intent → Safe Assets → Edit → Post
- NEVER select visuals before a script exists.
- EVERY visual choice must serve the script.

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
- Pexels, Unsplash, Pixabay, Mixkit, Coverr, Wikimedia Commons ONLY.
- Generic search queries only. No celebrities, no brands.
- Store source and license for every asset.

POLITICAL/SOCIAL:
- No ragebait, slogans, or demonization. 
- Expose contradictions calmly; let conclusions emerge naturally.

FORMATTING RULES:
- NEVER use hyphens or dashes in any generated content. Use colons, commas, or restructure sentences instead.
- Keep punctuation clean and simple.

"Clarity over noise. Meaning over metrics. Thought before output." """


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

    # the newest OpenAI model is "gpt-5" which was released August 7, 2025.
    # do not change this unless explicitly requested by the user
    response = client.chat.completions.create(
        model="grok-3",
        messages=[
            {"role": "system", "content": SYSTEM_GUARDRAILS},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"},
        max_completion_tokens=4096
    )
    
    try:
        content = response.choices[0].message.content or "{}"
        result = json.loads(content)
        return result.get('ideas', result) if isinstance(result, dict) else result
    except json.JSONDecodeError:
        return []


def generate_script(idea: dict, transcript: str, duration: int = 30) -> dict:
    """Generate a script for a specific idea. Script-first approach."""
    prompt = f"""Write a {duration}-second video script based on this idea:

IDEA: {idea['idea']}
TYPE: {idea['type']}
CONTEXT: {idea.get('context', 'N/A')}

FULL TRANSCRIPT FOR REFERENCE:
{transcript[:8000]}

The script must contain:
1. HOOK: An opening that creates clarity, not shock (1-2 sentences)
2. CORE_CLAIM: The central argument or observation (2-3 sentences)
3. GROUNDING: Explanation that provides context and nuance (2-3 sentences)
4. CLOSING: A line that reinforces meaning without being preachy (1 sentence)

Also specify:
- TONE: One of [calm, dry, ironic, reflective, urgent, analytical]
- VISUAL_INTENT: One of [supportive, neutral, contextual, contrasting]

Output as JSON with keys: hook, core_claim, grounding, closing, tone, visual_intent, full_script"""

    # the newest OpenAI model is "gpt-5" which was released August 7, 2025.
    # do not change this unless explicitly requested by the user
    response = client.chat.completions.create(
        model="grok-3",
        messages=[
            {"role": "system", "content": SYSTEM_GUARDRAILS},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"},
        max_completion_tokens=2048
    )
    
    try:
        content = response.choices[0].message.content or "{}"
        return json.loads(content)
    except json.JSONDecodeError:
        return {}


def validate_loop_score(thesis: str, script: dict) -> dict:
    """Validate how well the script closes back to the thesis. Returns loop score and fix suggestions."""
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

    response = client.chat.completions.create(
        model="grok-3",
        messages=[
            {"role": "system", "content": SYSTEM_GUARDRAILS},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"},
        max_completion_tokens=1024
    )
    
    try:
        content = response.choices[0].message.content or "{}"
        return json.loads(content)
    except json.JSONDecodeError:
        return {
            "loop_score": 0.5,
            "loop_strength": "moderate",
            "analysis": "Unable to analyze loop closure",
            "issues": [],
            "suggested_fix": None,
            "fix_type": None
        }


def get_scene_visuals(scene_text: str, scene_type: str, keywords: list = None) -> dict:
    """Get AI-curated visual suggestions for a specific scene/anchor."""
    keywords_str = ", ".join(keywords) if keywords else ""
    
    prompt = f"""Suggest visual content for this scene in a video script.

SCENE TYPE: {scene_type}
SCENE TEXT: {scene_text}
KEYWORDS: {keywords_str}

Think about what visual would best support this moment in the script.
Consider: documentary footage, archival imagery, maps, diagrams, or atmospheric shots.

Output JSON with:
- "visual_concept": One sentence describing the ideal visual
- "search_queries": Array of 3 specific search queries for Pexels/Wikimedia
- "visual_style": "documentary" | "atmospheric" | "archival" | "diagram" | "portrait" | "b-roll"
- "motion": "static" | "slow_pan" | "zoom" | "dynamic"
- "mood": Brief mood description"""

    response = client.chat.completions.create(
        model="grok-3",
        messages=[
            {"role": "system", "content": SYSTEM_GUARDRAILS},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"},
        max_completion_tokens=512
    )
    
    try:
        content = response.choices[0].message.content or "{}"
        return json.loads(content)
    except json.JSONDecodeError:
        return {
            "visual_concept": "Supportive visual for this scene",
            "search_queries": ["abstract background", "documentary footage"],
            "visual_style": "atmospheric",
            "motion": "static",
            "mood": "neutral"
        }


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

    # the newest OpenAI model is "gpt-5" which was released August 7, 2025.
    # do not change this unless explicitly requested by the user
    response = client.chat.completions.create(
        model="grok-3",
        messages=[
            {"role": "system", "content": SYSTEM_GUARDRAILS},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"},
        max_completion_tokens=2048
    )
    
    try:
        content = response.choices[0].message.content or "{}"
        return json.loads(content)
    except json.JSONDecodeError:
        return {"clips": [], "total_duration": 0, "notes": ""}


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

    # the newest OpenAI model is "gpt-5" which was released August 7, 2025.
    # do not change this unless explicitly requested by the user
    response = client.chat.completions.create(
        model="grok-3",
        messages=[
            {"role": "system", "content": SYSTEM_GUARDRAILS},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"},
        max_completion_tokens=512
    )
    
    try:
        content = response.choices[0].message.content or "{}"
        return json.loads(content)
    except json.JSONDecodeError:
        return {}


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

    response = client.chat.completions.create(
        model="grok-3",
        messages=[
            {"role": "system", "content": SYSTEM_GUARDRAILS},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"}
    )
    
    try:
        content = response.choices[0].message.content or "{}"
        return json.loads(content)
    except json.JSONDecodeError:
        return {"approved": False, "reasoning": "Approval engine error", "required_changes": []}


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

    response = client.chat.completions.create(
        model="grok-3",
        messages=[
            {"role": "system", "content": SYSTEM_GUARDRAILS},
            {"role": "user", "content": prompt}
        ],
        max_completion_tokens=1024
    )
    
    content = response.choices[0].message.content or "{}"
    content = content.strip()
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]
    
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {
            "primary_keywords": [],
            "mood_keywords": [],
            "visual_suggestions": [],
            "tone": "neutral",
            "hook_summary": script[:100]
        }


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
    try:
        response = client.chat.completions.create(
            model="grok-3-fast",
            max_completion_tokens=512,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "Analyze text and identify any people, characters, or figures mentioned."},
                {"role": "user", "content": f"""Analyze this scene text and identify any characters or people mentioned:

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
If no people/characters mentioned, return empty array."""}
            ]
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        print(f"Character detection error: {e}")
        return {"characters": [], "has_people": False}


def search_pexels_safe(query: str, per_page: int = 6) -> list[dict]:
    """Search Pexels for copyright-free stock images matching query."""
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
                    "id": photo.get("id"),
                    "url": photo.get("src", {}).get("large2x") or photo.get("src", {}).get("large"),
                    "thumbnail": photo.get("src", {}).get("medium"),
                    "photographer": photo.get("photographer", "Unknown"),
                    "pexels_url": photo.get("url"),
                    "alt": photo.get("alt", query),
                    "attribution": f"Photo by {photo.get('photographer', 'Unknown')} on Pexels"
                })
            return images
    except Exception as e:
        print(f"Error searching Pexels for '{query}': {e}")
    
    return []


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

    response = client.chat.completions.create(
        model="grok-3",
        messages=[
            {"role": "system", "content": SYSTEM_GUARDRAILS},
            {"role": "user", "content": refined_script_prompt}
        ],
        max_completion_tokens=1024
    )
    
    content = response.choices[0].message.content or "{}"
    content = content.strip()
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]
    
    try:
        refined_script = json.loads(content)
    except json.JSONDecodeError:
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


def extract_thesis(content: str, content_type: str = "idea") -> dict:
    """
    Extract the core thesis from any content - ideas, transcripts, or scripts.
    The thesis is the single central idea that everything else must serve.
    """
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

Output JSON:
{{
    "thesis_statement": "One clear sentence stating the core claim",
    "thesis_type": "one of [argument, observation, revelation, challenge, question]",
    "core_claim": "The underlying truth being asserted",
    "target_audience": "Who needs to hear this and why",
    "intended_impact": "What should change in the viewer's mind",
    "confidence": 0.0-1.0 confidence score,
    "requires_clarification": true/false,
    "clarification_question": "If unclear, what ONE question would clarify the thesis"
}}"""

    response = client.chat.completions.create(
        model="grok-3",
        messages=[
            {"role": "system", "content": SYSTEM_GUARDRAILS},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"},
        max_completion_tokens=1024
    )
    
    try:
        content = response.choices[0].message.content or "{}"
        return json.loads(content)
    except json.JSONDecodeError:
        return {"thesis_statement": "", "confidence": 0.0, "requires_clarification": True}


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

    response = client.chat.completions.create(
        model="grok-3",
        messages=[
            {"role": "system", "content": SYSTEM_GUARDRAILS},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"},
        max_completion_tokens=2048
    )
    
    try:
        content = response.choices[0].message.content or "{}"
        result = json.loads(content)
        # Handle both {"anchors": [...]} and direct array formats
        if isinstance(result, dict):
            anchors = result.get('anchors', [])
            return anchors if isinstance(anchors, list) else []
        elif isinstance(result, list):
            return result
        return []
    except json.JSONDecodeError:
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

    response = client.chat.completions.create(
        model="grok-3",
        messages=[
            {"role": "system", "content": SYSTEM_GUARDRAILS},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"},
        max_completion_tokens=2048
    )
    
    try:
        content = response.choices[0].message.content or "{}"
        result = json.loads(content)
        return result.get('thought_changes', result) if isinstance(result, dict) else result
    except json.JSONDecodeError:
        return []


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

    response = client.chat.completions.create(
        model="grok-3-fast",
        messages=[
            {"role": "system", "content": SYSTEM_GUARDRAILS},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"},
        max_completion_tokens=1024
    )
    
    try:
        content = response.choices[0].message.content or "{}"
        result = json.loads(content)
        
        # Validate and normalize content_type to valid values
        valid_types = ["informative", "comedic", "inspiring"]
        content_type = result.get("content_type", "informative").lower()
        if content_type not in valid_types:
            # Default to informative for unknown types
            content_type = "informative"
        result["content_type"] = content_type
        
        return result
    except json.JSONDecodeError:
        return {"content_type": "informative", "confidence": 0.5}


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

    response = client.chat.completions.create(
        model="grok-3",
        messages=[
            {"role": "system", "content": SYSTEM_GUARDRAILS},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"},
        max_completion_tokens=2048
    )
    
    try:
        content = response.choices[0].message.content or "{}"
        visual_assets = json.loads(content)
    except json.JSONDecodeError:
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

    response = client.chat.completions.create(
        model="grok-3",
        messages=[
            {"role": "system", "content": SYSTEM_GUARDRAILS},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"},
        max_completion_tokens=2048
    )
    
    try:
        content = response.choices[0].message.content or "{}"
        return json.loads(content)
    except json.JSONDecodeError:
        return {}


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

    response = client.chat.completions.create(
        model="grok-3",
        messages=[
            {"role": "system", "content": SYSTEM_GUARDRAILS},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"},
        max_completion_tokens=3000
    )
    
    try:
        content = response.choices[0].message.content or "{}"
        result = json.loads(content)
        result['status'] = 'ready'
        return result
    except json.JSONDecodeError:
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

    response = client.chat.completions.create(
        model="grok-3",
        messages=[
            {"role": "system", "content": SYSTEM_GUARDRAILS},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"},
        max_completion_tokens=2048
    )
    
    try:
        content = response.choices[0].message.content or "{}"
        return json.loads(content)
    except json.JSONDecodeError:
        return {}


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


def unified_content_engine(user_input: str, user_id: str, mode: str = "auto") -> dict:
    """
    Unified engine that handles both creation and clipping through one interface.
    
    Modes:
    - auto: AI determines if this is creation or clipping based on input
    - create: Force script creation mode
    - clip: Force clipping mode (expects transcript/link)
    """
    detection_prompt = f"""Analyze this user input and determine what they're trying to do.

INPUT:
{user_input[:2000]}

Are they:
1. CREATING: Starting from an idea, asking for a script
2. CLIPPING: Providing source material (transcript, link, video) to extract clips from
3. REFINING: Adjusting existing content

Output JSON:
{{
    "mode": "create/clip/refine",
    "detected_thesis": "If thesis is clear, state it. Otherwise null",
    "source_type": "If clipping: transcript/link/idea. If creating: null",
    "needs_clarification": true/false,
    "clarification_question": "If unclear, what to ask"
}}"""

    if mode == "auto":
        response = client.chat.completions.create(
            model="grok-3-fast",
            messages=[
                {"role": "system", "content": "You analyze user intent for a video content system."},
                {"role": "user", "content": detection_prompt}
            ],
            response_format={"type": "json_object"},
            max_completion_tokens=512
        )
        
        try:
            content = response.choices[0].message.content or "{}"
            detection = json.loads(content)
            mode = detection.get('mode', 'create')
        except:
            mode = 'create'
            detection = {}
    else:
        detection = {"mode": mode}
    
    user_context = get_user_context(user_id)
    source_learning = get_source_learning_context(user_id)
    full_context = f"{user_context}\n\n{source_learning}" if source_learning else user_context
    
    if mode == "clip":
        result = process_source_for_clipping(user_input)
        if result.get('status') == 'ready':
            learnings = learn_from_source_content(user_input, result.get('recommended_clips', []))
            result['learnings'] = learnings
            
            # Add content classification for clips too
            thesis_statement = result.get('thesis', {}).get('thesis_statement', '')
            if thesis_statement:
                classification = classify_content_type(user_input[:1500], thesis_statement)
                result['content_type'] = classification.get('content_type', 'informative')
                result['visual_style'] = classification.get('visual_style', {})
        
        return {"mode": "clip", "result": result, "status": "ready"}
    
    else:
        thesis = extract_thesis(user_input, "idea")
        
        if thesis.get('requires_clarification', False):
            return {
                "mode": "create",
                "status": "needs_clarification", 
                "thesis": thesis,
                "question": thesis.get('clarification_question', 'What is the main point you want to make?')
            }
        
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
        
        script = generate_thesis_driven_script(thesis, full_context, learned_patterns)
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


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        video_file = sys.argv[1]
        print(f"Processing: {video_file}")
        results = process_video(video_file)
        print(json.dumps(results, indent=2))
    else:
        print("Usage: python context_engine.py <video_file>")
