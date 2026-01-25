import os
import json
import subprocess
import tempfile
from typing import Optional
from openai import OpenAI

AI_INTEGRATIONS_OPENAI_API_KEY = os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY")
AI_INTEGRATIONS_OPENAI_BASE_URL = os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL")

client = OpenAI(
    api_key=AI_INTEGRATIONS_OPENAI_API_KEY,
    base_url=AI_INTEGRATIONS_OPENAI_BASE_URL
)

SYSTEM_GUARDRAILS = """You are the Context Engine - an AI that creates thoughtful, script-first video clips.

GUARDRAILS (System-Level Rules):
You must NEVER:
- Chase outrage or sensationalism
- Generalize groups of people
- Argue theology or take religious sides
- Oversimplify structural issues
- Cut footage before reasoning through the argument

You must ALWAYS:
- Distinguish ideas from people
- Prioritize clarity over virality
- Explain incentives, not assign blame
- Remain calm even when discussing conflict
- Write the argument FIRST, then identify supporting footage"""


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
    if hasattr(response, 'segments'):
        for seg in response.segments:
            segments.append({
                'start': seg.start,
                'end': seg.end,
                'text': seg.text
            })
    
    return {
        'full_text': response.text,
        'segments': segments
    }


def analyze_ideas(transcript: str) -> list:
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
        result = json.loads(response.choices[0].message.content)
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
        return json.loads(response.choices[0].message.content)
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
        return json.loads(response.choices[0].message.content)
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
        return json.loads(response.choices[0].message.content)
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


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        video_file = sys.argv[1]
        print(f"Processing: {video_file}")
        results = process_video(video_file)
        print(json.dumps(results, indent=2))
    else:
        print("Usage: python context_engine.py <video_file>")
