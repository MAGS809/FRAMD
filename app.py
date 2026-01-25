import os
import json
import uuid
import tempfile
from flask import Flask, render_template, request, jsonify, send_from_directory, session
from werkzeug.utils import secure_filename
from context_engine import (
    extract_audio, transcribe_audio, analyze_ideas,
    generate_script, find_clip_timestamps, generate_captions,
    cut_video_clip, concatenate_clips
)

app = Flask(__name__)
app.secret_key = os.environ.get('SESSION_SECRET', 'dev-secret-key')

UPLOAD_FOLDER = 'uploads'
OUTPUT_FOLDER = 'output'
ALLOWED_EXTENSIONS = {'mp4', 'mov', 'avi', 'mkv', 'webm', 'mp3', 'wav', 'm4a'}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['OUTPUT_FOLDER'] = OUTPUT_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/download-reference', methods=['POST'])
def download_reference():
    """Download a video from URL and optionally analyze it as a reference."""
    import subprocess
    
    data = request.get_json()
    url = data.get('url', '').strip()
    
    if not url:
        return jsonify({'error': 'No URL provided'}), 400
    
    try:
        job_id = str(uuid.uuid4())[:8]
        output_path = os.path.join(app.config['UPLOAD_FOLDER'], f'reference_{job_id}.mp4')
        
        cmd = [
            'yt-dlp',
            '-f', 'best[ext=mp4]/best',
            '--no-playlist',
            '--max-filesize', '100M',
            '-o', output_path,
            url
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        
        if result.returncode != 0:
            direct_path = os.path.join(app.config['UPLOAD_FOLDER'], f'reference_{job_id}_direct.mp4')
            try:
                import requests as req
                resp = req.get(url, timeout=60, stream=True)
                if resp.status_code == 200 and 'video' in resp.headers.get('content-type', ''):
                    with open(direct_path, 'wb') as f:
                        for chunk in resp.iter_content(chunk_size=8192):
                            f.write(chunk)
                    output_path = direct_path
                else:
                    return jsonify({'error': 'Could not download video from URL'}), 400
            except Exception as e:
                return jsonify({'error': f'Download failed: {str(e)}'}), 400
        
        if os.path.exists(output_path):
            file_size = os.path.getsize(output_path)
            
            transcript = None
            try:
                audio_path = extract_audio(output_path)
                if audio_path:
                    transcript = transcribe_audio(audio_path)
            except:
                pass
            
            return jsonify({
                'success': True,
                'video_path': f'/uploads/{os.path.basename(output_path)}',
                'file_size': file_size,
                'transcript': transcript,
                'job_id': job_id
            })
        else:
            return jsonify({'error': 'Download failed - no output file'}), 400
            
    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Download timed out'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    if not allowed_file(file.filename):
        return jsonify({'error': 'File type not allowed'}), 400
    
    job_id = str(uuid.uuid4())
    filename = secure_filename(file.filename)
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{job_id}_{filename}")
    file.save(file_path)
    
    return jsonify({
        'success': True,
        'job_id': job_id,
        'filename': filename,
        'file_path': file_path
    })


@app.route('/transcribe', methods=['POST'])
def transcribe():
    data = request.get_json()
    file_path = data.get('file_path')
    
    if not file_path or not os.path.exists(file_path):
        return jsonify({'error': 'File not found'}), 404
    
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


@app.route('/analyze', methods=['POST'])
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


@app.route('/generate-script', methods=['POST'])
def generate_script_endpoint():
    data = request.get_json()
    idea = data.get('idea')
    transcript = data.get('transcript')
    duration = data.get('duration', 30)
    
    if not idea or not transcript:
        return jsonify({'error': 'Missing idea or transcript'}), 400
    
    try:
        script = generate_script(idea, transcript, duration)
        return jsonify({
            'success': True,
            'script': script
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/find-clips', methods=['POST'])
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


@app.route('/generate-captions', methods=['POST'])
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


@app.route('/cut-clip', methods=['POST'])
def cut_clip():
    data = request.get_json()
    file_path = data.get('file_path')
    start = data.get('start')
    end = data.get('end')
    aspect_ratio = data.get('aspect_ratio', '9:16')
    
    if not all([file_path, start is not None, end is not None]):
        return jsonify({'error': 'Missing required parameters'}), 400
    
    if not os.path.exists(file_path):
        return jsonify({'error': 'Source file not found'}), 404
    
    output_filename = f"clip_{uuid.uuid4().hex[:8]}.mp4"
    output_path = os.path.join(app.config['OUTPUT_FOLDER'], output_filename)
    
    try:
        if cut_video_clip(file_path, output_path, start, end, aspect_ratio):
            return jsonify({
                'success': True,
                'output_path': output_path,
                'filename': output_filename
            })
        else:
            return jsonify({'error': 'Failed to cut clip'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/output/<filename>')
def serve_output(filename):
    return send_from_directory(app.config['OUTPUT_FOLDER'], filename)


@app.route('/uploads/<filename>')
def serve_uploads(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


@app.route('/refine-script', methods=['POST'])
def refine_script():
    """Conversational script refinement - asks clarifying questions."""
    from openai import OpenAI
    import os
    
    data = request.get_json()
    message = data.get('message', '')
    conversation = data.get('conversation', [])
    question_count = data.get('question_count', 0)
    
    client = OpenAI(
        api_key=os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY"),
        base_url=os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL")
    )
    
    system_prompt = """You are the Context Engine - a sharp, witty AI that helps creators develop unique content.

YOUR JOB:
1. Listen to their content idea
2. Ask 1-3 clarifying questions (one at a time) to understand:
   - The TONE they want (funny, serious, provocative, thoughtful)
   - The UNIQUE ANGLE that makes this different
   - The TARGET AUDIENCE and what reaction they want
3. After enough clarity, summarize the refined script concept

RULES:
- Ask ONE question at a time
- Be direct, not flowery
- When you have enough info (after 1-3 questions), say "SCRIPT READY:" followed by a summary
- Make their idea sharper, not generic

If they give a rich, detailed idea upfront, you might only need 1 question.
If it's vague, ask up to 3 questions total."""

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(conversation)
    messages.append({"role": "user", "content": message})
    
    try:
        response = client.chat.completions.create(
            model="gpt-5",
            messages=messages,
            max_completion_tokens=1024
        )
        
        reply = response.choices[0].message.content or ""
        
        script_ready = "SCRIPT READY:" in reply.upper() or question_count >= 2
        has_question = "?" in reply and not script_ready
        
        refined_script = None
        if script_ready:
            refined_script = reply
            if "SCRIPT READY:" in reply.upper():
                parts = reply.upper().split("SCRIPT READY:")
                if len(parts) > 1:
                    refined_script = reply[reply.upper().find("SCRIPT READY:") + 13:].strip()
        
        return jsonify({
            'success': True,
            'reply': reply,
            'has_question': has_question,
            'script_ready': script_ready,
            'refined_script': refined_script or reply
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/generate-formats', methods=['POST'])
def generate_formats():
    """Generate content for multiple formats from a refined script."""
    from openai import OpenAI
    from context_engine import search_stock_videos
    import os
    
    data = request.get_json()
    script = data.get('script', '')
    formats = data.get('formats', [])
    conversation = data.get('conversation', [])
    
    client = OpenAI(
        api_key=os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY"),
        base_url=os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL")
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

Write a 30-60 second Reel/TikTok script with:
- HOOK: First 3-5 seconds to grab attention (punchy, provocative, or surprising)
- BODY: The main content (20-40 seconds)
- PAYOFF: The ending that makes them think/share (5-10 seconds)

Output as JSON:
{{"hook": "...", "body": "...", "payoff": "...", "duration": "30 seconds", "keywords": ["keyword1", "keyword2", "keyword3"]}}"""

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
                model="gpt-5",
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
            
            import json
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


@app.route('/generate-video', methods=['POST'])
def generate_video():
    """Generate a video mockup combining stock footage with voiceover."""
    import os
    import uuid
    import subprocess
    import requests
    
    data = request.get_json()
    voiceover_url = data.get('voiceover_url')
    stock_videos = data.get('stock_videos', [])
    script = data.get('script', '')
    format_type = data.get('format', 'reel')
    
    if not voiceover_url and not script:
        return jsonify({'error': 'Need voiceover or script'}), 400
    
    try:
        output_id = uuid.uuid4().hex[:8]
        output_dir = app.config['OUTPUT_FOLDER']
        
        if format_type == 'reel':
            width, height = 1080, 1920
            aspect = '9:16'
        else:
            width, height = 1080, 1080
            aspect = '1:1'
        
        temp_files = []
        
        if stock_videos and len(stock_videos) > 0:
            for i, video in enumerate(stock_videos[:3]):
                video_url = video.get('video_url') or video.get('pexels_url')
                if video_url and 'pexels.com' not in video_url:
                    try:
                        resp = requests.get(video_url, timeout=30)
                        if resp.status_code == 200:
                            temp_path = os.path.join(output_dir, f'temp_{output_id}_{i}.mp4')
                            with open(temp_path, 'wb') as f:
                                f.write(resp.content)
                            temp_files.append(temp_path)
                    except:
                        pass
        
        final_video = os.path.join(output_dir, f'echo_video_{output_id}.mp4')
        
        if temp_files:
            concat_file = os.path.join(output_dir, f'concat_{output_id}.txt')
            with open(concat_file, 'w') as f:
                for tf in temp_files:
                    f.write(f"file '{tf}'\n")
            
            cmd = [
                'ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', concat_file,
                '-vf', f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2',
                '-c:v', 'libx264', '-preset', 'fast', '-t', '30',
                final_video
            ]
            subprocess.run(cmd, capture_output=True, timeout=120)
            
            for tf in temp_files:
                if os.path.exists(tf):
                    os.unlink(tf)
            if os.path.exists(concat_file):
                os.unlink(concat_file)
        else:
            cmd = [
                'ffmpeg', '-y', '-f', 'lavfi', '-i', f'color=c=black:s={width}x{height}:d=30',
                '-vf', f"drawtext=fontsize=40:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2:text='Echo Engine':font=sans",
                '-c:v', 'libx264', '-preset', 'fast', '-t', '30',
                final_video
            ]
            subprocess.run(cmd, capture_output=True, timeout=60)
        
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
            return jsonify({'error': 'Video generation failed'}), 500
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/generate-voiceover', methods=['POST'])
def generate_voiceover():
    """Generate voiceover audio from script text."""
    from openai import OpenAI
    import base64
    import os
    import uuid
    
    data = request.get_json()
    text = data.get('text', '')
    voice = data.get('voice', 'alloy')
    
    if not text:
        return jsonify({'error': 'No text provided'}), 400
    
    client = OpenAI(
        api_key=os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY"),
        base_url=os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL")
    )
    
    try:
        response = client.chat.completions.create(
            model="gpt-audio",
            modalities=["text", "audio"],
            audio={"voice": voice, "format": "mp3"},
            messages=[
                {"role": "system", "content": "You are a professional voiceover artist. Read the following script naturally and engagingly."},
                {"role": "user", "content": f"Read this script: {text}"},
            ],
        )
        
        audio_data = getattr(response.choices[0].message, "audio", None)
        if audio_data and hasattr(audio_data, "data"):
            audio_bytes = base64.b64decode(audio_data.data)
            
            filename = f"voiceover_{uuid.uuid4().hex[:8]}.mp3"
            filepath = os.path.join(app.config['OUTPUT_FOLDER'], filename)
            with open(filepath, 'wb') as f:
                f.write(audio_bytes)
            
            return jsonify({
                'success': True,
                'audio_url': f'/output/{filename}',
                'duration_estimate': len(text.split()) / 2.5
            })
        else:
            return jsonify({'error': 'No audio generated'}), 500
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/build-post', methods=['POST'])
def build_post():
    """Build a complete post from a user's script/pitch idea."""
    from context_engine import build_post_from_script
    
    data = request.get_json()
    user_script = data.get('script')
    
    if not user_script:
        return jsonify({'error': 'No script provided'}), 400
    
    try:
        result = build_post_from_script(user_script)
        return jsonify({
            'success': True,
            'result': result
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/chat', methods=['POST'])
def chat():
    """Direct chat with the Grok-style AI."""
    from openai import OpenAI
    import os
    
    data = request.get_json()
    message = data.get('message')
    conversation = data.get('conversation', [])
    
    if not message:
        return jsonify({'error': 'No message provided'}), 400
    
    client = OpenAI(
        api_key=os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY"),
        base_url=os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL")
    )
    
    system_prompt = """You are an AI with a Grok-like personality: witty, direct, irreverent, and analytically sharp. You cut through noise and say what others won't.

PERSONALITY:
- Be direct and unfiltered - no corporate speak or hedging
- Use dry wit and intellectual humor when appropriate  
- Challenge assumptions and conventional thinking
- Speak like a smart friend at a bar, not a press release
- Have opinions and defend them with logic
- Be willing to say "that's a dumb argument" when it is

THINKING STYLE:
- First principles reasoning - break things down to fundamentals
- Steelman opposing views before dismantling them
- Find the hidden incentives behind stated positions
- Spot contradictions and call them out directly
- Prefer uncomfortable truths over comfortable lies"""

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(conversation)
    messages.append({"role": "user", "content": message})
    
    try:
        response = client.chat.completions.create(
            model="gpt-5",
            messages=messages,
            max_completion_tokens=2048
        )
        
        reply = response.choices[0].message.content or ""
        
        return jsonify({
            'success': True,
            'reply': reply,
            'conversation': messages + [{"role": "assistant", "content": reply}]
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/process-full', methods=['POST'])
def process_full():
    """Full pipeline: upload -> transcribe -> analyze -> script -> clips"""
    data = request.get_json()
    file_path = data.get('file_path')
    max_clips = data.get('max_clips', 3)
    clip_duration = data.get('clip_duration', 30)
    aspect_ratio = data.get('aspect_ratio', '9:16')
    
    if not file_path or not os.path.exists(file_path):
        return jsonify({'error': 'File not found'}), 404
    
    try:
        from context_engine import process_video
        results = process_video(
            file_path,
            app.config['OUTPUT_FOLDER'],
            max_clips,
            clip_duration,
            aspect_ratio
        )
        return jsonify({
            'success': True,
            'results': results
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
