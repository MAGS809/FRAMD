"""
Content pipeline routes blueprint.
Handles transcription, analysis, trend research, script generation, scene visuals,
caption generation, format generation, and video rendering.
"""
import os
import json
import re
import logging
import uuid
import subprocess
from flask import Blueprint, request, jsonify, session, current_app, Response
from flask_login import current_user

from extensions import db
from context_engine import (
    extract_audio,
    transcribe_audio,
    analyze_ideas,
    generate_script,
    find_clip_timestamps,
    generate_captions,
)
from audio_engine import extract_voice_actor_script, parse_character_lines

pipeline_bp = Blueprint('pipeline', __name__)


@pipeline_bp.route('/transcribe', methods=['POST'])
def transcribe():
    data = request.get_json()
    file_path = data.get('file_path') or data.get('filename')

    if not file_path:
        return jsonify({'error': 'No file specified'}), 400

    possible_paths = [
        file_path,
        f'uploads/{file_path}',
        f'uploads/{os.path.basename(file_path)}',
        os.path.basename(file_path)
    ]

    resolved_path = None
    for path in possible_paths:
        if os.path.exists(path):
            resolved_path = path
            break

    if not resolved_path:
        return jsonify({'error': f'File not found: {file_path}'}), 404

    file_path = resolved_path

    audio_path = file_path.rsplit('.', 1)[0] + '_audio.wav'

    if file_path.lower().endswith(('.mp3', '.wav', '.m4a')):
        audio_path = file_path
    else:
        if not extract_audio(file_path, audio_path):
            return jsonify({'error': 'Failed to extract audio'}), 500

    try:
        transcript_data = transcribe_audio(audio_path)
        return jsonify({
            'success': True,
            'transcript': transcript_data['full_text'],
            'segments': transcript_data['segments']
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@pipeline_bp.route('/analyze', methods=['POST'])
def analyze():
    data = request.get_json()
    transcript = data.get('transcript')

    if not transcript:
        return jsonify({'error': 'No transcript provided'}), 400

    try:
        ideas = analyze_ideas(transcript)
        return jsonify({
            'success': True,
            'ideas': ideas
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@pipeline_bp.route('/research-trends', methods=['POST'])
def research_trends_endpoint():
    """Research how a topic is being discussed across platforms - Trend Intelligence feature."""
    from context_engine import research_topic_trends

    data = request.get_json()
    topic = data.get('topic')
    platform = data.get('platform', 'all')

    if not topic:
        return jsonify({'error': 'Missing topic'}), 400

    try:
        trends = research_topic_trends(topic, platform)
        return jsonify({
            'success': True,
            'trends': trends
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@pipeline_bp.route('/generate-script', methods=['POST'])
def generate_script_endpoint():
    data = request.get_json()
    idea = data.get('idea')
    transcript = data.get('transcript')
    duration = data.get('duration', 30)
    template_type = data.get('template_type', 'start_from_scratch')

    if not idea or not transcript:
        return jsonify({'error': 'Missing idea or transcript'}), 400

    try:
        script = generate_script(idea, transcript, duration, use_trends=True, template_type=template_type)

        if script and script.get('trend_intel', {}).get('sources'):
            session['last_trend_sources'] = script['trend_intel']['sources']

        return jsonify({
            'success': True,
            'script': script
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@pipeline_bp.route('/validate-loop', methods=['POST'])
def validate_loop_endpoint():
    """Validate how well a script closes back to its thesis."""
    from context_engine import validate_loop_score

    data = request.get_json()
    thesis = data.get('thesis')
    script = data.get('script')

    if not thesis or not script:
        return jsonify({'error': 'Missing thesis or script'}), 400

    try:
        result = validate_loop_score(thesis, script)
        return jsonify({
            'success': True,
            **result
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@pipeline_bp.route('/scene-visuals', methods=['POST'])
def get_scene_visuals_endpoint():
    """Get AI-curated visual suggestions for a specific scene with 3 categories."""
    from context_engine import get_scene_visuals, search_visuals_unified, detect_characters_in_scene

    data = request.get_json()
    scene_text = data.get('scene_text')
    scene_type = data.get('scene_type', 'CLAIM')
    keywords = data.get('keywords', [])

    if not scene_text:
        return jsonify({'error': 'Missing scene_text'}), 400

    try:
        visual_suggestions = get_scene_visuals(scene_text, scene_type, keywords)

        characters = []
        try:
            char_data = detect_characters_in_scene(scene_text)
            for char in char_data.get('characters', [])[:3]:
                char_name = char.get('name', '')
                char_type = char.get('type', 'generic')
                search_query = char.get('search_query', char_name)

                if char_type == 'historical' and search_query:
                    results = search_visuals_unified(search_query, per_page=2)
                    for r in results:
                        r['character_name'] = char_name
                        r['category'] = 'character'
                    characters.extend(results)
                elif char_type == 'generic':
                    results = search_visuals_unified(search_query or 'person silhouette', per_page=2)
                    for r in results:
                        r['character_name'] = char_name or 'Character'
                        r['category'] = 'character'
                    characters.extend(results)
        except:
            pass

        curated = []
        for query in visual_suggestions.get('search_queries', [])[:2]:
            try:
                results = search_visuals_unified(query, per_page=3)
                for r in results:
                    r['category'] = 'curated'
                curated.extend(results)
            except:
                pass

        backgrounds = []
        bg_queries = visual_suggestions.get('background_queries', [])
        if not bg_queries:
            bg_queries = ['cinematic background', 'dramatic atmosphere']
        for query in bg_queries[:2]:
            try:
                results = search_visuals_unified(query, per_page=2)
                for r in results:
                    r['category'] = 'background'
                backgrounds.extend(results)
            except:
                pass

        return jsonify({
            'success': True,
            'suggestions': visual_suggestions,
            'characters': characters[:4],
            'curated': curated[:4],
            'backgrounds': backgrounds[:4],
            'images': characters[:2] + curated[:2] + backgrounds[:2]
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@pipeline_bp.route('/generate-scene-direction', methods=['POST'])
def generate_scene_direction():
    """Generate AI suggestion for scene camera direction based on content using Claude."""
    from context_engine import call_ai

    data = request.get_json()
    scene_text = data.get('scene_text', '')
    scene_type = data.get('scene_type', 'SCENE')
    visual_description = data.get('visual_description', '')

    if not scene_text:
        return jsonify({'direction': 'static'})

    prompt = f"""Based on this scene content, suggest ONE camera direction that best matches the emotional and narrative tone.

Scene type: {scene_type}
Scene text: "{scene_text}"
{f'Visual description: {visual_description}' if visual_description else ''}

Available directions:
- "zoom in slowly" - for reveals, emphasis, drawing viewer in, intimate moments
- "zoom out" - for big picture moments, conclusions, pulling back to show context
- "pan left" - for transitions, showing progression, scanning across a scene
- "pan right" - for returning to something, contrast, counter-movement
- "static" - for direct statements, stable moments, letting content speak

Consider:
1. The emotional arc of the text
2. Whether this is building tension or releasing it
3. What movement would enhance rather than distract from the message

Respond with ONLY the direction (e.g. "zoom in slowly" or "static"). No explanation."""

    try:
        response = call_ai(prompt, max_tokens=20)
        direction = response.strip().lower().strip('"\'')

        valid_directions = ['zoom in slowly', 'zoom out', 'pan left', 'pan right', 'static', 'zoom in', 'slow zoom']
        if not any(d in direction for d in valid_directions):
            direction = 'static'

        if 'zoom in' in direction:
            direction = 'zoom in slowly'
        elif 'zoom out' in direction:
            direction = 'zoom out'
        elif 'pan left' in direction:
            direction = 'pan left'
        elif 'pan right' in direction:
            direction = 'pan right'
        else:
            direction = 'static'

        return jsonify({'direction': direction})

    except Exception as e:
        print(f"[Scene Direction AI] Error: {e}")
        type_defaults = {
            'HOOK': 'zoom in slowly',
            'CLAIM': 'static',
            'EVIDENCE': 'pan left',
            'PIVOT': 'zoom out',
            'COUNTER': 'pan right',
            'CLOSER': 'zoom in slowly'
        }
        return jsonify({'direction': type_defaults.get(scene_type.upper(), 'static')})


@pipeline_bp.route('/find-clips', methods=['POST'])
def find_clips():
    data = request.get_json()
    script = data.get('script')
    segments = data.get('segments')

    if not script or not segments:
        return jsonify({'error': 'Missing script or segments'}), 400

    try:
        clips = find_clip_timestamps(script, segments)
        return jsonify({
            'success': True,
            'clips': clips
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@pipeline_bp.route('/generate-captions', methods=['POST'])
def generate_captions_endpoint():
    data = request.get_json()
    script = data.get('script')
    idea = data.get('idea')

    if not script or not idea:
        return jsonify({'error': 'Missing script or idea'}), 400

    try:
        captions = generate_captions(script, idea)
        return jsonify({
            'success': True,
            'captions': captions
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@pipeline_bp.route('/refine-script', methods=['POST'])
def refine_script():
    """Conversational script refinement - asks clarifying questions."""
    from openai import OpenAI

    data = request.get_json()
    message = data.get('message', '')
    conversation = data.get('conversation', [])
    question_count = data.get('question_count', 0)
    reference = data.get('reference')

    url_pattern = r'(https?://[^\s]+(?:youtube|youtu\.be|tiktok|vimeo|twitter|x\.com|instagram|facebook|twitch)[^\s]*)'
    urls = re.findall(url_pattern, message, re.IGNORECASE)

    video_transcript = None
    video_path = None

    if urls:
        url = urls[0]
        try:
            job_id = str(uuid.uuid4())[:8]
            output_path = os.path.join(current_app.config['UPLOAD_FOLDER'], f'chat_video_{job_id}.mp4')

            cmd = [
                'yt-dlp',
                '-f', 'best[ext=mp4]/best',
                '--no-playlist',
                '--max-filesize', '100M',
                '-o', output_path,
                url
            ]
            subprocess.run(cmd, capture_output=True, text=True, timeout=120)

            if os.path.exists(output_path):
                video_path = f'/uploads/{os.path.basename(output_path)}'
                try:
                    audio_path = extract_audio(output_path)
                    if audio_path:
                        video_transcript = transcribe_audio(audio_path)
                except:
                    pass
        except:
            pass

    if reference and reference.get('transcript'):
        video_transcript = reference.get('transcript')

    client = OpenAI(
        api_key=os.environ.get("XAI_API_KEY"),
        base_url="https://api.x.ai/v1"
    )

    system_prompt = """You are Krakd — a thinking system that produces post-ready content.

PURPOSE:
Turn ideas into clear, honest, human-feeling video scripts.
Optimize for clarity, integrity, and resonance — never outrage or spectacle.

CORE PHILOSOPHY:
1. Language matters more than volume — say the right thing, not more things
2. Ideas fail when ignored, not when challenged — explain resistance precisely
3. Stability without legitimacy does not last
4. Coexistence is logic, not sentiment — durable outcomes from shared stakes
5. Discourse ≠ politics — reason and explain, don't perform identity theater

BEFORE WRITING (MANDATORY):
1. What is the core claim being made?
2. What is being misunderstood or ignored?
3. Who needs to understand this — and why might they resist?
4. What wording would reduce resistance instead of escalating it?
If unclear, ask ONE concise clarifying question. Then write.

TONE (STRICT):
- Calm, clear, grounded, subtly witty when appropriate, confident without arrogance
- NEVER: sarcastic, smug, preachy, outraged, juvenile, crude, sexual, graphic, meme-brained
- If humor appears, it is sly, intelligent, and brief — never the point
- If content gets graphic: "The story gets graphic here — we're skipping that part."

VIDEO DROPS:
Pull the gold. Skip the filler.
- [CLIP: 00:30-01:15] "money quote here"
- Max 4 clips. State the angle.

SCRIPT FORMAT (PLAIN TEXT SCREENPLAY):

================================================
                    TITLE HERE
================================================

SCENE 1 [3-4s]
EXT. LOCATION - TIME
________________________________________________

[CHARACTER NAME]: Dialogue line goes here. Keep it punchy.

VISUAL: keyword keyword keyword
CUT: wide establishing shot, slow zoom

SCENE 2 [4-5s]
INT. LOCATION - TIME
________________________________________________

[SECOND CHARACTER]: Next dialogue line here.

[CHARACTER NAME]: Response dialogue here.

VISUAL: keyword keyword keyword
CUT: medium shot, static hold


================================================
CHARACTERS: Name1, Name2
VOICES?
================================================

DIALOGUE FORMAT (CRITICAL):
- ALWAYS use [CHARACTER NAME]: dialogue format
- Character names in CAPS inside square brackets
- Dialogue follows the colon on the same line
- This enables automatic voice detection and assignment
- Example: [NEWS ANCHOR]: The market crashed today.
- Example: [WOLF]: Time to buy the dip!

SCENE EDITING RULES:
- Each scene: [Xs] = suggested duration in seconds
- CUT line: shot type (wide/medium/close-up) and motion (static/pan/zoom)
- Action scenes: 2-3s cuts. Emotional scenes: 5-7s holds.
- Total video: 35-75s for shorts format (target 35-45s, max 1:15)

FORMATTING RULES:
- ======= for title/footer bars, _______ under scene headers
- CENTER character names and dialogue
- VISUAL tags centered below dialogue
- NO markdown (no **, no >, no ---)

POLITICAL/SOCIAL RULES:
- Recognize power imbalances — don't flatten dynamics with "both sides" framing
- Critique state policy and dominance structures without demonizing individuals
- A solution is invalid if affected peoples do not accept it
- Ending should be philosophical challenge, not motivational poster

SELF-CORRECTION:
- ERROR A: Generic peace-commercial tone instead of sharp argument
- ERROR B: Flattened power dynamics (treating unequal actors as equal)
- ERROR C: Missing the core logical strike the user intended
- ERROR D: Wrong framing (drifting to secular when spiritual was needed)
- ERROR E: Unrealistic jumps without acknowledging difficulty

If slipping into generic unity language or equal-blame framing, STOP and rewrite.

OUTPUT STANDARD:
- Intentional — every line has a reason
- Restrained — no excess, no padding
- Human-written — natural flow, not model-shaped
- Punchy — clarity without dilution

FAIL CONDITION:
If output could be mistaken for generic social media commentary, activist slogans, empty neutrality, or AI filler — redo it.

Never explain what you're doing. Just write."""

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(conversation)

    user_content = message
    if video_transcript:
        user_content = f"{message}\n\n[VIDEO TRANSCRIPT]:\n{video_transcript[:4000]}"

    messages.append({"role": "user", "content": user_content})

    try:
        response = client.chat.completions.create(
            model="grok-3",
            messages=messages,
            max_tokens=1024
        )

        reply = response.choices[0].message.content or ""

        script_ready = "SCRIPT READY:" in reply.upper() or question_count >= 2
        has_question = "?" in reply and not script_ready

        refined_script = None
        voice_actor_script = None
        if script_ready:
            refined_script = reply
            if "SCRIPT READY:" in reply.upper():
                parts = reply.upper().split("SCRIPT READY:")
                if len(parts) > 1:
                    refined_script = reply[reply.upper().find("SCRIPT READY:") + 13:].strip()

            voice_actor_script = extract_voice_actor_script(refined_script or reply)

        character_lines = []
        characters_detected = []
        if refined_script or reply:
            character_lines = parse_character_lines(refined_script or reply)
            characters_detected = list(set(entry['character'] for entry in character_lines))

        return jsonify({
            'success': True,
            'reply': reply,
            'has_question': has_question,
            'script_ready': script_ready,
            'refined_script': refined_script or reply,
            'voice_actor_script': voice_actor_script,
            'character_lines': character_lines,
            'characters_detected': characters_detected,
            'video_path': video_path,
            'video_downloaded': video_path is not None
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@pipeline_bp.route('/generate-formats', methods=['POST'])
def generate_formats():
    """Generate content for multiple formats from a refined script."""
    from openai import OpenAI
    from context_engine import search_stock_videos

    data = request.get_json()
    script = data.get('script', '')
    formats = data.get('formats', [])
    conversation = data.get('conversation', [])

    client = OpenAI(
        api_key=os.environ.get("XAI_API_KEY"),
        base_url="https://api.x.ai/v1"
    )

    context = "\n".join([f"{m['role']}: {m['content']}" for m in conversation[-6:]])

    results = {}

    for fmt in formats:
        try:
            if fmt == 'reel':
                prompt = f"""Based on this script concept:
{script}

Context from conversation:
{context}

Write a 35-75 second Reel/TikTok script with:
- HOOK: First 3-5 seconds to grab attention (punchy, provocative, or surprising)
- BODY: The main content (25-55 seconds)
- PAYOFF: The ending that makes them think/share (5-10 seconds)

Output as JSON:
{{"hook": "...", "body": "...", "payoff": "...", "duration": "45 seconds", "keywords": ["keyword1", "keyword2", "keyword3"]}}"""

            elif fmt == 'carousel':
                prompt = f"""Based on this script concept:
{script}

Context from conversation:
{context}

Create an Instagram carousel post with 5-7 slides:
- Slide 1: Hook/title that stops scrolling
- Slides 2-5: Key points, claims, or evidence
- Final slide: Call to action

Also write a caption.

Output as JSON:
{{"slides": ["Slide 1 text", "Slide 2 text", ...], "caption": "...", "keywords": ["keyword1", "keyword2"]}}"""

            elif fmt == 'post':
                prompt = f"""Based on this script concept:
{script}

Context from conversation:
{context}

Write an Instagram/social media post caption that:
- Hooks in the first line
- Delivers the key insight
- Ends with a question or CTA

Also suggest relevant hashtags.

Output as JSON:
{{"caption": "...", "hashtags": "#tag1 #tag2 #tag3", "keywords": ["keyword1", "keyword2"]}}"""

            elif fmt == 'thread':
                prompt = f"""Based on this script concept:
{script}

Context from conversation:
{context}

Write a Twitter/X thread with 5-8 tweets:
- Tweet 1: Hook that makes people want to read more
- Middle tweets: Build the argument/story
- Final tweet: Payoff + CTA

Each tweet must be under 280 characters.

Output as JSON:
{{"tweets": ["Tweet 1", "Tweet 2", ...], "keywords": ["keyword1", "keyword2"]}}"""

            else:
                continue

            response = client.chat.completions.create(
                model="grok-3",
                messages=[
                    {"role": "system", "content": "You are a content creation expert. Output valid JSON only."},
                    {"role": "user", "content": prompt}
                ],
                max_completion_tokens=2048
            )

            content = response.choices[0].message.content or "{}"
            content = content.strip()
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]

            try:
                result = json.loads(content)
            except:
                result = {"error": "Failed to parse", "raw": content[:500]}

            keywords = result.get('keywords', [])
            if keywords:
                stock = search_stock_videos(keywords, per_page=3)
                result['stock_footage'] = stock

            results[fmt] = result

        except Exception as e:
            results[fmt] = {"error": str(e)}

    return jsonify({
        'success': True,
        'results': results
    })


TOKEN_COSTS = {
    'base_video': 25,
    'per_character': 3,
    'per_sfx': 1
}

@pipeline_bp.route('/generate-video', methods=['POST'])
def generate_video():
    """Generate a video mockup combining stock footage with voiceover."""
    import requests
    from models import Subscription, User
    from routes.utils import rate_limit

    user_id = None
    is_dev_mode = os.environ.get('FLASK_ENV') == 'development' or os.environ.get('DEV_MODE') == 'true'

    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('dev_user_id')

    data = request.get_json()
    extra_characters = max(0, len(data.get('character_layers', [])) - 1)
    sfx_count = len(data.get('sound_effects', []))
    token_cost = TOKEN_COSTS['base_video'] + (extra_characters * TOKEN_COSTS['per_character']) + (sfx_count * TOKEN_COSTS['per_sfx'])

    if is_dev_mode:
        print(f"[generate-video] Dev mode - free access (would cost {token_cost} tokens)")
    else:
        sub = Subscription.query.filter_by(user_id=user_id).first() if user_id else None
        user = User.query.get(user_id) if user_id else None

        if sub:
            if sub.token_balance is None:
                tier_tokens = {'free': 50, 'creator': 300, 'pro': 1000}
                sub.token_balance = tier_tokens.get(sub.tier, 50)
                db.session.commit()

            if sub.tier == 'free':
                return jsonify({
                    'error': 'Video export requires Creator or Pro subscription',
                    'requires_subscription': True,
                    'message': 'Upgrade to Creator ($10/mo) or Pro ($25/mo) to export videos.'
                }), 403

            if sub.token_balance < token_cost:
                return jsonify({
                    'error': 'Not enough tokens',
                    'token_balance': sub.token_balance,
                    'token_cost': token_cost,
                    'message': f'You need {token_cost} tokens but only have {sub.token_balance}. Tokens refresh monthly or upgrade your plan.'
                }), 403

            sub.token_balance -= token_cost
            db.session.commit()
            print(f"[generate-video] Deducted {token_cost} tokens. New balance: {sub.token_balance}")
        else:
            return jsonify({
                'error': 'Subscription required',
                'requires_subscription': True,
                'message': 'Video generation requires a Creator or Pro subscription.'
            }), 403

    voiceover_url = data.get('voiceover_url')
    stock_videos = data.get('stock_videos', [])
    script = data.get('script', '')
    format_type = data.get('format', 'reel')
    captions = data.get('captions', {'enabled': False, 'style': 'bold-center'})

    if not voiceover_url and not script:
        return jsonify({'error': 'Need voiceover or script'}), 400

    try:
        output_id = uuid.uuid4().hex[:8]
        output_dir = current_app.config['OUTPUT_FOLDER']

        if format_type == 'reel':
            width, height = 1080, 1920
            aspect = '9:16'
        else:
            width, height = 1080, 1080
            aspect = '1:1'

        temp_files = []

        allowed_domains = ['wikimedia.org', 'upload.wikimedia.org', 'commons.wikimedia.org', 'archive.org']

        if stock_videos and len(stock_videos) > 0:
            for i, video in enumerate(stock_videos[:5]):
                video_url = video.get('download_url') or video.get('url') or video.get('video_url')
                if video_url:
                    from urllib.parse import urlparse
                    parsed = urlparse(video_url)
                    if not any(domain in parsed.netloc for domain in allowed_domains):
                        print(f"Skipping untrusted video URL: {video_url}")
                        continue

                    try:
                        resp = requests.get(video_url, timeout=60)
                        if resp.status_code == 200:
                            temp_path = os.path.join(output_dir, f'temp_{output_id}_{i}.mp4')
                            with open(temp_path, 'wb') as f:
                                f.write(resp.content)
                            temp_files.append(temp_path)
                    except Exception as e:
                        print(f"Error downloading video {i}: {e}")

        final_video = os.path.join(output_dir, f'echo_video_{output_id}.mp4')

        if temp_files:
            concat_file = os.path.join(output_dir, f'concat_{output_id}.txt')
            with open(concat_file, 'w') as f:
                for tf in temp_files:
                    f.write(f"file '{os.path.basename(tf)}'\n")

            cmd = [
                'ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', concat_file,
                '-vf', f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2',
                '-c:v', 'libx264', '-preset', 'fast', '-t', '30',
                final_video
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=120)
            if result.returncode != 0:
                print(f"[FFmpeg concat] Error: {result.stderr.decode()}")

            for tf in temp_files:
                if os.path.exists(tf):
                    os.unlink(tf)
            if os.path.exists(concat_file):
                os.unlink(concat_file)
        else:
            print(f"[generate-video] No temp files downloaded, creating placeholder")
            cmd = [
                'ffmpeg', '-y', '-f', 'lavfi', '-i', f'color=c=black:s={width}x{height}:d=30',
                '-vf', f"drawtext=fontsize=40:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2:text='Echo Engine':font=sans",
                '-c:v', 'libx264', '-preset', 'fast', '-t', '30',
                final_video
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=60)
            if result.returncode != 0:
                print(f"[FFmpeg placeholder] Error: {result.stderr.decode()}")

        if captions.get('enabled') and script:
            caption_video = os.path.join(output_dir, f'captioned_{output_id}.mp4')

            from visual_director import apply_caption_template
            captions = apply_caption_template(captions)

            caption_font = captions.get('font', 'inter')
            caption_position = captions.get('position', 'center')
            caption_color = captions.get('textColor', captions.get('color', '#FFFFFF')).replace('#', '')
            caption_size = captions.get('size', 'medium')
            caption_weight = captions.get('weight', 'bold')
            caption_outline = captions.get('outline', True)
            caption_shadow = captions.get('shadow', True)
            caption_background = captions.get('background', False)
            caption_uppercase = captions.get('uppercase', False)
            caption_animation = captions.get('animation', 'highlight')
            caption_highlight_color = captions.get('highlightColor', '#FFD60A').replace('#', '')

            font_map = {
                'inter': 'Sans',
                'bebas': 'Sans-Bold',
                'montserrat': 'Sans-Bold',
                'oswald': 'Sans',
                'poppins': 'Sans',
                'roboto': 'Sans'
            }
            font_name = font_map.get(caption_font, 'Sans')

            size_map = {
                'small': 32,
                'medium': 48,
                'large': 64,
                'xlarge': 80
            }
            font_size = size_map.get(caption_size, 48)

            position_map = {
                'top': 80,
                'center': '(h-text_h)/2',
                'bottom': 'h-150'
            }
            y_pos = position_map.get(caption_position, 'h-150')

            duration_cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', final_video]
            duration_result = subprocess.run(duration_cmd, capture_output=True, text=True, timeout=10)
            try:
                video_duration = float(duration_result.stdout.strip())
            except:
                video_duration = 30.0

            clean_script = re.sub(r'[\n\r]+', ' ', script)
            clean_script = re.sub(r'\s+', ' ', clean_script).strip()

            if caption_uppercase:
                clean_script = clean_script.upper()

            words = clean_script.split()

            words_per_group = 4 if caption_animation == 'none' else 3
            word_groups = []
            for i in range(0, len(words), words_per_group):
                group = words[i:i + words_per_group]
                word_groups.append(group)

            if len(word_groups) > 0:
                time_per_group = video_duration / len(word_groups)
            else:
                time_per_group = video_duration

            filter_chain = []

            for idx, group_words in enumerate(word_groups):
                start_time = idx * time_per_group
                end_time = (idx + 1) * time_per_group

                if caption_animation in ['highlight', 'bounce', 'karaoke'] and len(group_words) > 1:
                    word_duration = time_per_group / len(group_words)

                    char_width = font_size * 0.5
                    space_width = font_size * 0.25

                    word_widths = [len(w) * char_width for w in group_words]
                    total_width = sum(word_widths) + (len(group_words) - 1) * space_width

                    for word_idx, word in enumerate(group_words):
                        word_start = start_time + (word_idx * word_duration)
                        word_end = (idx + 1) * time_per_group

                        safe_word = word.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:").replace("%", "\\%")

                        x_offset = sum(word_widths[:word_idx]) + word_idx * space_width

                        word_highlight_start = start_time + (word_idx * word_duration)
                        word_highlight_end = start_time + ((word_idx + 1) * word_duration)

                        def add_common_styling(parts_list):
                            if caption_outline:
                                parts_list.extend(["borderw=3", "bordercolor=black"])
                            if caption_shadow:
                                parts_list.extend(["shadowcolor=black@0.7", "shadowx=2", "shadowy=2"])
                            if caption_background:
                                parts_list.extend(["box=1", "boxcolor=black@0.6", "boxborderw=5"])

                        if word_idx > 0:
                            parts_before = [
                                f"drawtext=text='{safe_word}'",
                                f"fontsize={font_size}",
                                f"fontcolor=#{caption_color}",
                                f"font={font_name}",
                                f"x=(w-{total_width:.0f})/2+{x_offset:.0f}",
                                f"y={y_pos}",
                                f"enable='between(t,{start_time:.2f},{word_highlight_start:.2f})'"
                            ]
                            add_common_styling(parts_before)
                            filter_chain.append(":".join(parts_before))

                        parts_highlight = [
                            f"drawtext=text='{safe_word}'",
                            f"fontsize={font_size}",
                            f"fontcolor=#{caption_highlight_color}",
                            f"font={font_name}",
                            f"x=(w-{total_width:.0f})/2+{x_offset:.0f}",
                            f"y={y_pos}",
                            f"enable='between(t,{word_highlight_start:.2f},{word_highlight_end:.2f})'"
                        ]
                        add_common_styling(parts_highlight)
                        filter_chain.append(":".join(parts_highlight))

                        if word_highlight_end < end_time:
                            parts_after = [
                                f"drawtext=text='{safe_word}'",
                                f"fontsize={font_size}",
                                f"fontcolor=#{caption_color}",
                                f"font={font_name}",
                                f"x=(w-{total_width:.0f})/2+{x_offset:.0f}",
                                f"y={y_pos}",
                                f"enable='between(t,{word_highlight_end:.2f},{end_time:.2f})'"
                            ]
                            add_common_styling(parts_after)
                            filter_chain.append(":".join(parts_after))
                else:
                    group_text = ' '.join(group_words)
                    safe_text = group_text.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:").replace("%", "\\%")

                    parts = [
                        f"drawtext=text='{safe_text}'",
                        f"fontsize={font_size}",
                        f"fontcolor=#{caption_color}",
                        f"font={font_name}",
                        f"x=(w-text_w)/2",
                        f"y={y_pos}",
                        f"enable='between(t,{start_time:.2f},{end_time:.2f})'"
                    ]

                    if caption_outline:
                        parts.append("borderw=3")
                        parts.append("bordercolor=black")

                    if caption_shadow:
                        parts.append("shadowcolor=black@0.7")
                        parts.append("shadowx=2")
                        parts.append("shadowy=2")

                    if caption_background:
                        parts.append("box=1")
                        parts.append("boxcolor=black@0.6")
                        parts.append("boxborderw=10")

                    filter_chain.append(":".join(parts))

            font_filter = ",".join(filter_chain) if filter_chain else f"drawtext=text='':fontsize={font_size}"

            probe_cmd = ['ffprobe', '-v', 'error', '-select_streams', 'a', '-show_entries', 'stream=codec_type', '-of', 'csv=p=0', final_video]
            probe_result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=10)
            has_audio = 'audio' in probe_result.stdout

            if has_audio:
                cmd = [
                    'ffmpeg', '-y', '-i', final_video,
                    '-vf', font_filter,
                    '-c:v', 'libx264', '-preset', 'fast', '-c:a', 'copy',
                    caption_video
                ]
            else:
                cmd = [
                    'ffmpeg', '-y', '-i', final_video,
                    '-vf', font_filter,
                    '-c:v', 'libx264', '-preset', 'fast', '-an',
                    caption_video
                ]

            result = subprocess.run(cmd, capture_output=True, timeout=120)
            if os.path.exists(caption_video):
                os.unlink(final_video)
                final_video = caption_video

        if voiceover_url:
            audio_path = os.path.join(output_dir, voiceover_url.split('/')[-1])
            if os.path.exists(audio_path):
                final_with_audio = os.path.join(output_dir, f'echo_final_{output_id}.mp4')
                cmd = [
                    'ffmpeg', '-y', '-i', final_video, '-i', audio_path,
                    '-c:v', 'copy', '-c:a', 'aac', '-shortest',
                    final_with_audio
                ]
                subprocess.run(cmd, capture_output=True, timeout=60)
                if os.path.exists(final_with_audio):
                    os.unlink(final_video)
                    final_video = final_with_audio

        if os.path.exists(final_video):
            return jsonify({
                'success': True,
                'video_url': f'/output/{os.path.basename(final_video)}',
                'format': format_type
            })
        else:
            print(f"[generate-video] Final video not created: {final_video}")
            return jsonify({'error': 'Video generation failed - no output file created'}), 500

    except Exception as e:
        import traceback
        print(f"[generate-video] Error: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


COST_CLIP_SCENE = 0.10
COST_STOCK_SCENE = 0.15
COST_DALLE_SCENE = 0.30
COST_REMIX_GOOD = 0.20
COST_REMIX_BETTER = 0.30
COST_REMIX_BEST = 0.40


@pipeline_bp.route('/api/pipeline/upload-source', methods=['POST'])
def upload_source():
    from models import Project, ProjectSource
    from routes.utils import get_user_id

    try:
        user_id = get_user_id()
        if not user_id:
            return jsonify({'error': 'Not authenticated'}), 401

        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400

        file = request.files['file']
        project_id = request.form.get('project_id')
        processing_mode = request.form.get('processing_mode', 'clip')

        if not project_id:
            return jsonify({'error': 'project_id is required'}), 400

        if processing_mode not in ('clip', 'remix'):
            return jsonify({'error': 'processing_mode must be clip or remix'}), 400

        project = Project.query.filter_by(id=int(project_id), user_id=user_id).first()
        if not project:
            return jsonify({'error': 'Project not found'}), 404

        if not file.filename:
            return jsonify({'error': 'Empty filename'}), 400

        ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
        file_type = 'video' if ext in ('mp4', 'mov', 'avi', 'webm', 'mkv') else 'audio' if ext in ('mp3', 'wav', 'm4a', 'aac') else 'other'

        unique_name = f"{uuid.uuid4()}_{file.filename}"
        save_path = os.path.join('uploads', unique_name)
        file.save(save_path)

        duration = None
        try:
            probe = subprocess.run(
                ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration', '-of', 'json', save_path],
                capture_output=True, text=True, timeout=30
            )
            probe_data = json.loads(probe.stdout)
            duration = float(probe_data.get('format', {}).get('duration', 0))
        except Exception:
            pass

        existing_count = ProjectSource.query.filter_by(project_id=project.id).count()

        source = ProjectSource(
            project_id=project.id,
            user_id=user_id,
            file_path=save_path,
            file_name=file.filename,
            file_type=file_type,
            duration=duration,
            processing_mode=processing_mode,
            processing_status='pending',
            sort_order=existing_count,
        )
        db.session.add(source)
        db.session.commit()

        return jsonify({'success': True, 'source': source.to_dict()})

    except Exception as e:
        logging.error(f"[upload-source] Error: {e}")
        return jsonify({'error': str(e)}), 500


@pipeline_bp.route('/api/pipeline/process-source', methods=['POST'])
def process_source():
    from models import ProjectSource
    from context_engine import extract_audio, transcribe_audio
    from routes.utils import get_user_id

    try:
        user_id = get_user_id()
        if not user_id:
            return jsonify({'error': 'Not authenticated'}), 401

        data = request.get_json()
        source_id = data.get('source_id')
        if not source_id:
            return jsonify({'error': 'source_id is required'}), 400

        source = ProjectSource.query.filter_by(id=source_id, user_id=user_id).first()
        if not source:
            return jsonify({'error': 'Source not found'}), 404

        source.processing_status = 'processing'
        db.session.commit()

        if source.processing_mode == 'clip':
            audio_path = source.file_path.rsplit('.', 1)[0] + '_audio.wav'
            if source.file_type == 'audio':
                audio_path = source.file_path
            else:
                if not extract_audio(source.file_path, audio_path):
                    source.processing_status = 'error'
                    source.processing_error = 'Failed to extract audio'
                    db.session.commit()
                    return jsonify({'error': 'Failed to extract audio from source'}), 500

            transcript_data = transcribe_audio(audio_path)
            source.transcript = transcript_data.get('full_text', '')
            source.transcript_segments = transcript_data.get('segments', [])
            source.processing_status = 'completed'
            db.session.commit()

            return jsonify({
                'success': True,
                'source': source.to_dict(),
                'transcript': source.transcript,
                'segments': source.transcript_segments,
            })

        elif source.processing_mode == 'remix':
            source.skeleton_data = {
                'status': 'placeholder',
                'note': 'Runway integration pending — skeleton extraction will be available soon',
                'file_path': source.file_path,
                'duration': source.duration,
            }
            source.processing_status = 'completed'
            db.session.commit()

            return jsonify({
                'success': True,
                'source': source.to_dict(),
                'skeleton_data': source.skeleton_data,
            })

        else:
            return jsonify({'error': f'Unknown processing mode: {source.processing_mode}'}), 400

    except Exception as e:
        logging.error(f"[process-source] Error: {e}")
        try:
            source.processing_status = 'error'
            source.processing_error = str(e)
            db.session.commit()
        except Exception:
            pass
        return jsonify({'error': str(e)}), 500


@pipeline_bp.route('/api/pipeline/build-scene-plan', methods=['POST'])
def build_scene_plan():
    from models import Project, ProjectSource, ScenePlan
    from context_engine import call_ai, SYSTEM_GUARDRAILS
    from routes.utils import get_user_id

    try:
        user_id = get_user_id()
        if not user_id:
            return jsonify({'error': 'Not authenticated'}), 401

        data = request.get_json()
        project_id = data.get('project_id')
        if not project_id:
            return jsonify({'error': 'project_id is required'}), 400

        project = Project.query.filter_by(id=int(project_id), user_id=user_id).first()
        if not project:
            return jsonify({'error': 'Project not found'}), 404

        sources = ProjectSource.query.filter_by(project_id=project.id).order_by(ProjectSource.sort_order).all()

        source_descriptions = []
        for s in sources:
            desc = f"Source #{s.sort_order + 1}: '{s.file_name}' ({s.processing_mode} mode, {s.file_type}, {s.duration or 'unknown'}s)"
            if s.transcript:
                desc += f"\n  Transcript excerpt: {s.transcript[:300]}..."
            if s.skeleton_data:
                desc += f"\n  Skeleton data available: {s.skeleton_data.get('status', 'unknown')}"
            source_descriptions.append(desc)

        sources_text = "\n".join(source_descriptions) if source_descriptions else "No uploaded sources — use stock footage and DALL-E generated visuals."

        brief_text = project.brief or project.description or project.name or "No brief provided"

        prompt = f"""Analyze this video project and create a detailed scene-by-scene plan.

PROJECT BRIEF:
{brief_text}

AVAILABLE SOURCES:
{sources_text}

COST REFERENCE:
- clip scene (from uploaded video): $0.10/scene
- stock scene (stock footage): $0.15/scene
- dalle scene (AI generated): $0.30/scene
- remix/runway scene: $0.20/second (good), $0.30/second (better), $0.40/second (best)

Create a scene plan as a JSON array. Each scene object must have:
- "scene_index": integer starting at 0
- "source_type": one of "clip", "remix", "stock", "dalle"
- "source_id": integer ID of the ProjectSource to use (null if stock/dalle)
- "visual_container": one of "fullscreen", "split_screen", "pip", "overlay"
- "anchor_type": one of "hook", "claim", "evidence", "transition", "cta"
- "script_text": what the narrator/text says during this scene
- "duration": estimated seconds for this scene
- "transition_in": one of "cut", "fade", "slide", "zoom" or null
- "transition_out": one of "cut", "fade", "slide", "zoom" or null
- "estimated_cost": float cost based on the cost reference above
- "source_config": object with any additional config (e.g. {{"quality": "good"}}, {{"start_time": 5.0, "end_time": 10.0}})

Return ONLY a JSON object: {{"scenes": [...]}}"""

        ai_result = call_ai(
            prompt=prompt,
            system_prompt=SYSTEM_GUARDRAILS,
            json_output=True,
            max_tokens=2000
        )

        scenes_data = []
        if isinstance(ai_result, dict):
            scenes_data = ai_result.get('scenes', [])
        elif isinstance(ai_result, list):
            scenes_data = ai_result

        if not scenes_data:
            scenes_data = [{
                'scene_index': 0,
                'source_type': 'stock',
                'source_id': None,
                'visual_container': 'fullscreen',
                'anchor_type': 'hook',
                'script_text': brief_text[:200],
                'duration': 5.0,
                'transition_in': 'fade',
                'transition_out': 'cut',
                'estimated_cost': COST_STOCK_SCENE,
                'source_config': {},
            }]

        ScenePlan.query.filter_by(project_id=project.id).delete()

        created_scenes = []
        for scene_data in scenes_data:
            source_type = scene_data.get('source_type', 'stock')
            duration = float(scene_data.get('duration', 5.0))

            if 'estimated_cost' in scene_data:
                est_cost = float(scene_data['estimated_cost'])
            else:
                if source_type == 'clip':
                    est_cost = COST_CLIP_SCENE
                elif source_type == 'stock':
                    est_cost = COST_STOCK_SCENE
                elif source_type == 'dalle':
                    est_cost = COST_DALLE_SCENE
                elif source_type == 'remix':
                    quality = (scene_data.get('source_config') or {}).get('quality', 'good')
                    rate = {'good': COST_REMIX_GOOD, 'better': COST_REMIX_BETTER, 'best': COST_REMIX_BEST}.get(quality, COST_REMIX_GOOD)
                    est_cost = round(rate * duration, 2)
                else:
                    est_cost = COST_STOCK_SCENE

            scene = ScenePlan(
                project_id=project.id,
                scene_index=scene_data.get('scene_index', 0),
                source_type=source_type,
                source_id=scene_data.get('source_id'),
                source_config=scene_data.get('source_config'),
                visual_container=scene_data.get('visual_container', 'fullscreen'),
                anchor_type=scene_data.get('anchor_type'),
                script_text=scene_data.get('script_text'),
                duration=duration,
                start_time=scene_data.get('start_time', 0.0),
                end_time=scene_data.get('end_time'),
                transition_in=scene_data.get('transition_in'),
                transition_out=scene_data.get('transition_out'),
                estimated_cost=est_cost,
                render_status='planned',
            )
            db.session.add(scene)
            created_scenes.append(scene)

        db.session.commit()

        return jsonify({
            'success': True,
            'project_id': project.id,
            'scene_count': len(created_scenes),
            'scenes': [s.to_dict() for s in created_scenes],
        })

    except Exception as e:
        logging.error(f"[build-scene-plan] Error: {e}")
        return jsonify({'error': str(e)}), 500


@pipeline_bp.route('/api/pipeline/scene-plan/<int:project_id>', methods=['GET'])
def get_scene_plan(project_id):
    from models import Project, ScenePlan
    from routes.utils import get_user_id

    try:
        user_id = get_user_id()
        if not user_id:
            return jsonify({'error': 'Not authenticated'}), 401

        project = Project.query.filter_by(id=project_id, user_id=user_id).first()
        if not project:
            return jsonify({'error': 'Project not found'}), 404

        scenes = ScenePlan.query.filter_by(project_id=project.id).order_by(ScenePlan.scene_index).all()

        return jsonify({
            'success': True,
            'project_id': project.id,
            'scene_count': len(scenes),
            'scenes': [s.to_dict() for s in scenes],
        })

    except Exception as e:
        logging.error(f"[get-scene-plan] Error: {e}")
        return jsonify({'error': str(e)}), 500


@pipeline_bp.route('/api/pipeline/estimate-cost', methods=['POST'])
def estimate_cost():
    from models import Project, ScenePlan
    from routes.utils import get_user_id

    try:
        user_id = get_user_id()
        if not user_id:
            return jsonify({'error': 'Not authenticated'}), 401

        data = request.get_json()
        project_id = data.get('project_id')
        if not project_id:
            return jsonify({'error': 'project_id is required'}), 400

        project = Project.query.filter_by(id=int(project_id), user_id=user_id).first()
        if not project:
            return jsonify({'error': 'Project not found'}), 404

        scenes = ScenePlan.query.filter_by(project_id=project.id).order_by(ScenePlan.scene_index).all()

        breakdown = {}
        total = 0.0
        for scene in scenes:
            stype = scene.source_type or 'stock'
            cost = scene.estimated_cost or 0.0
            total += cost
            if stype not in breakdown:
                breakdown[stype] = {'count': 0, 'cost': 0.0}
            breakdown[stype]['count'] += 1
            breakdown[stype]['cost'] = round(breakdown[stype]['cost'] + cost, 2)

        total = round(total, 2)
        project.total_estimated_cost = total
        db.session.commit()

        return jsonify({
            'success': True,
            'project_id': project.id,
            'total_estimated_cost': total,
            'scene_count': len(scenes),
            'breakdown': breakdown,
        })

    except Exception as e:
        logging.error(f"[estimate-cost] Error: {e}")
        return jsonify({'error': str(e)}), 500
