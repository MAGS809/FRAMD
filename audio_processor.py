import os
import json
import subprocess
import tempfile
from ai_client import call_ai, SYSTEM_GUARDRAILS, openai_client


def extract_audio(video_path: str, output_path: str) -> bool:
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


def find_clip_timestamps(script: dict, transcript_segments: list) -> list:
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


def process_source_for_clipping(transcript: str, source_url: str = None) -> dict:
    from script_generator import extract_thesis
    
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
