import os
import json
import subprocess
import tempfile
import requests
from typing import Optional
from openai import OpenAI

AI_INTEGRATIONS_OPENAI_API_KEY = os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY")
AI_INTEGRATIONS_OPENAI_BASE_URL = os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL")
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY")

client = OpenAI(
    api_key=AI_INTEGRATIONS_OPENAI_API_KEY,
    base_url=AI_INTEGRATIONS_OPENAI_BASE_URL
)

SYSTEM_GUARDRAILS = """You are Calligra - a thinking engine, not a content factory. Your purpose is to turn ideas into clear, honest posts while respecting the audience's intelligence.

CORE OPERATING PRINCIPLE:
Script → Visual Intent → Safe Assets → Edit → Post
- NEVER select visuals before a script exists.
- EVERY visual choice must serve the script.

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

"Clarity over noise. Meaning over metrics. Thought before output." """


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
        response = client.audio.transcriptions.create(
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
        model="gpt-5",
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
        model="gpt-5",
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
        model="gpt-5",
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
        model="gpt-5",
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
        model="gpt-4o",
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
        model="gpt-5",
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
        model="gpt-5",
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


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        video_file = sys.argv[1]
        print(f"Processing: {video_file}")
        results = process_video(video_file)
        print(json.dumps(results, indent=2))
    else:
        print("Usage: python context_engine.py <video_file>")
