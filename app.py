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
