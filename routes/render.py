from flask import Blueprint, request, jsonify, session, current_app
from flask_login import current_user
from extensions import db
from audio_engine import parse_sfx_from_directions, mix_sfx_into_audio
from video_renderer import create_whisper_synced_captions
import os
import re
import uuid
import subprocess
import shutil
import logging

render_bp = Blueprint('render_bp', __name__)


@render_bp.route('/generate-voiceover-multi', methods=['POST'])
def generate_voiceover_multi():
    """Generate voiceover with multiple character voices and stage directions."""
    from openai import OpenAI
    import base64
    from pydub import AudioSegment
    import io
    import re as regex
    from routes.voice import get_voice_config, ELEVENLABS_VOICE_SETTINGS
    
    data = request.get_json()
    script = data.get('script', '')
    character_voices = data.get('character_voices', {})
    stage_directions = data.get('stage_directions', '')
    
    if isinstance(script, dict):
        script = script.get('text', '') or script.get('content', '') or script.get('script', '') or str(script)
    
    if not script:
        return jsonify({'error': 'No script provided'}), 400
    
    client = OpenAI(
        api_key=os.environ.get("OPENAI_API_KEY")
    )
    
    try:
        lines = []
        in_script = False
        
        ai_meta_patterns = [
            r'^Understood', r'^I\'ll create', r'^Here\'s', r'^Let me create',
            r'^This script', r'^The script', r'^I\'ve', r'^I can create',
            r'^Let me know', r'^Would you like', r'^The message',
            r'^exaggerated personas', r'^With voices', r'^I hope this',
        ]
        
        for line in script.split('\n'):
            line = line.strip()
            if not line:
                continue
            
            if re.match(r'^SCENE\s+\d+', line, re.IGNORECASE):
                in_script = True
                continue
            if re.match(r'^\[.+\]:', line) or re.match(r'^[A-Z][A-Z\-]+:', line):
                in_script = True
            
            if not in_script:
                if any(re.match(p, line, re.IGNORECASE) for p in ai_meta_patterns):
                    continue
                if len(line) > 80:
                    continue
            
            if line.startswith('[VISUAL') or line.startswith('[CUT') or line.startswith('[FADE'):
                continue
            if line.startswith('VISUAL:') or line.startswith('CUT:'):
                continue
            if re.match(r'^(INT\.|EXT\.|TITLE:|CUT TO)', line):
                continue
            
            if re.match(r'^[A-Z\s\-]+$', line) and len(line) < 50 and ':' not in line:
                continue
            
            match1 = re.match(r'^\[([^\]]+)\]:\s*(.+)$', line)
            if match1:
                char_name = match1.group(1).strip().upper()
                dialogue = match1.group(2).strip()
                dialogue = re.sub(r'\([^)]*\)', '', dialogue).strip()
                if dialogue:
                    lines.append({'character': char_name, 'text': dialogue})
                continue
            
            match2 = re.match(r'^([A-Za-z][A-Za-z0-9\-\.\'\s]{0,25}):\s*(.+)$', line)
            if match2:
                char_name = match2.group(1).strip().upper()
                dialogue = match2.group(2).strip()
                if char_name in ['SCENE', 'VISUAL', 'CUT', 'FADE', 'INT', 'EXT', 'TITLE', 'CHARACTERS', 'VOICES']:
                    continue
                dialogue = re.sub(r'\([^)]*\)', '', dialogue).strip()
                if dialogue:
                    lines.append({'character': char_name, 'text': dialogue})
                continue
        
        if not lines:
            clean_script = script.strip()
            clean_lines = []
            
            meta_patterns = [
                r'^Understood', r'^I\'ll create', r'^Here\'s', r'^Let me create',
                r'^This script', r'^The script', r'^I\'ve', r'^I can create',
                r'^Let me know', r'^Would you like', r'^The message',
                r'^exaggerated personas', r'^With voices', r'^I hope this',
                r'^This uses a', r'^The humor comes', r'^Here is',
                r'^I\'ve crafted', r'^This ad', r'^The ad', r'^Below is',
                r'^Note:', r'^---', r'^\*\*', r'^Script:', r'^Title:',
            ]
            
            for line in clean_script.split('\n'):
                line = line.strip()
                if not line:
                    continue
                if line.startswith('HOOK:') or line.startswith('BODY:') or line.startswith('CLOSER:'):
                    continue
                if line.startswith('[') and line.endswith(']') and ':' not in line:
                    continue
                if any(re.match(p, line, re.IGNORECASE) for p in meta_patterns):
                    continue
                if re.match(r'^[A-Z\s]+$', line) and len(line) < 30:
                    continue
                clean_lines.append(line)
            
            if clean_lines:
                narration_text = ' '.join(clean_lines)
                lines.append({'character': 'NARRATOR', 'text': narration_text})
        
        audio_segments = []
        
        for segment in lines:
            char_name = segment['character']
            text = segment['text']
            
            voice = 'alloy'
            for key, val in character_voices.items():
                if key.upper() == char_name or char_name in key.upper():
                    voice = val
                    break
            
            base_voice, elevenlabs_voice_id, _ = get_voice_config(voice)
            
            elevenlabs_key = os.environ.get("ELEVENLABS_API_KEY")
            audio_bytes = None
            
            if elevenlabs_key:
                try:
                    from elevenlabs.client import ElevenLabs as ElevenLabsClient
                    
                    el_client = ElevenLabsClient(api_key=elevenlabs_key)
                    audio = el_client.text_to_speech.convert(
                        text=text,
                        voice_id=elevenlabs_voice_id,
                        model_id="eleven_multilingual_v2",
                        output_format="mp3_44100_128",
                        voice_settings={
                            "stability": ELEVENLABS_VOICE_SETTINGS['stability'],
                            "similarity_boost": ELEVENLABS_VOICE_SETTINGS['similarity_boost'],
                            "style": ELEVENLABS_VOICE_SETTINGS['style'],
                            "use_speaker_boost": ELEVENLABS_VOICE_SETTINGS['use_speaker_boost']
                        }
                    )
                    
                    audio_bytes = b''
                    for chunk in audio:
                        if isinstance(chunk, bytes):
                            audio_bytes += chunk
                except Exception as e:
                    print(f"ElevenLabs multi error: {e}")
            
            if not audio_bytes:
                response = client.audio.speech.create(
                    model="tts-1-hd",
                    voice=base_voice,
                    input=text,
                    speed=1.25
                )
                audio_bytes = response.content
            
            audio_segments.append(audio_bytes)
        
        def parse_stage_directions(directions_text):
            """Parse stage directions into actionable effects."""
            effects = []
            if not directions_text:
                return effects
            
            for line in directions_text.split('\n'):
                line = line.strip()
                if not line:
                    continue
                
                pause_match = regex.search(r'\[PAUSE\s*(\d+(?:\.\d+)?)\s*s?\]', line, regex.IGNORECASE)
                if pause_match:
                    effects.append({'type': 'pause', 'duration': float(pause_match.group(1)) * 1000})
                    continue
                
                if '[BEAT]' in line.upper():
                    effects.append({'type': 'pause', 'duration': 500})
                    continue
                
                silence_match = regex.search(r'\[SILENCE\s*(\d+(?:\.\d+)?)\s*s?\]', line, regex.IGNORECASE)
                if silence_match:
                    effects.append({'type': 'pause', 'duration': float(silence_match.group(1)) * 1000})
                    continue
                
                if '[TRANSITION]' in line.upper():
                    effects.append({'type': 'pause', 'duration': 1000})
                    continue
            
            return effects
        
        direction_effects = parse_stage_directions(stage_directions)
        
        if audio_segments:
            combined = AudioSegment.empty()
            effect_index = 0
            
            for i, seg_bytes in enumerate(audio_segments):
                seg = AudioSegment.from_mp3(io.BytesIO(seg_bytes))
                combined += seg
                
                pause_duration = 300
                
                if effect_index < len(direction_effects):
                    effect = direction_effects[effect_index]
                    if effect['type'] == 'pause':
                        pause_duration = max(pause_duration, int(effect['duration']))
                    effect_index += 1
                
                combined += AudioSegment.silent(duration=pause_duration)
            
            combined = combined + 6
            
            filename = f"voiceover_multi_{uuid.uuid4().hex[:8]}.mp3"
            filepath = os.path.join(current_app.config['OUTPUT_FOLDER'], filename)
            combined.export(filepath, format='mp3', bitrate='192k')
            
            return jsonify({
                'success': True,
                'audio_url': f'/output/{filename}',
                'audio_path': filepath,
                'segments': len(audio_segments),
                'effects_applied': len(direction_effects)
            })
        else:
            return jsonify({'error': 'No audio segments generated'}), 500
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@render_bp.route('/render-video', methods=['POST'])
def render_video():
    """Render final video from selected scenes and voiceover."""
    import urllib.request
    from models import Subscription, User
    from app import rate_limit, format_user_error
    from context_engine import get_template_visual_fx
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    user_id = None
    is_dev_mode = os.environ.get('FLASK_ENV') == 'development' or os.environ.get('DEV_MODE') == 'true'
    
    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('dev_user_id')
    
    if is_dev_mode:
        print("[render-video] Dev mode - free access")
    else:
        sub = Subscription.query.filter_by(user_id=user_id).first() if user_id else None
        user = User.query.get(user_id) if user_id else None
        
        has_active_sub = sub and sub.is_active()
        has_free_generation = user and hasattr(user, 'free_video_generations') and (user.free_video_generations or 0) > 0
        
        if not has_active_sub and not has_free_generation:
            return jsonify({
                'error': 'Pro subscription required',
                'requires_subscription': True,
                'message': 'Video rendering requires a Pro subscription ($10/month). Your free generation has been used.'
            }), 403
        
        if not has_active_sub and has_free_generation:
            user.free_video_generations = max(0, (user.free_video_generations or 1) - 1)
            db.session.commit()
            print(f"[render-video] Used free generation for user {user_id}, remaining: {user.free_video_generations}")
    
    data = request.get_json()
    scenes = data.get('scenes', [])
    audio_path = data.get('audio_path', '')
    video_format = data.get('format', '9:16')
    captions_data = data.get('captions', {})
    captions_enabled = captions_data.get('enabled', False) if isinstance(captions_data, dict) else bool(captions_data)
    caption_settings = captions_data if isinstance(captions_data, dict) else {}
    script_text = data.get('script', '')
    stage_directions = data.get('stage_directions', '')
    preview_mode = data.get('preview', False)
    template_type = data.get('template', 'start_from_scratch')
    
    visual_fx = get_template_visual_fx(template_type)
    
    if not scenes:
        return jsonify({'error': 'No scenes provided'}), 400
    
    output_id = str(uuid.uuid4())[:8]
    output_path = f'output/{"preview" if preview_mode else "final"}_{output_id}.mp4'
    
    os.makedirs('output', exist_ok=True)
    
    try:
        sfx_requests = parse_sfx_from_directions(script_text, stage_directions)
        
        if sfx_requests and audio_path and os.path.exists(audio_path):
            print(f"[render-video] Found {len(sfx_requests)} sound effects to mix")
            total_lines = len((script_text + '\n' + stage_directions).split('\n'))
            mixed_audio_path = f'output/audio_with_sfx_{output_id}.mp3'
            audio_path = mix_sfx_into_audio(audio_path, sfx_requests, mixed_audio_path, total_lines)
            print(f"[render-video] SFX mixed into: {audio_path}")
        
        audio_duration = None
        if audio_path and os.path.exists(audio_path):
            try:
                probe_cmd = [
                    'ffprobe', '-v', 'error',
                    '-show_entries', 'format=duration',
                    '-of', 'default=noprint_wrappers=1:nokey=1',
                    audio_path
                ]
                probe_result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=10)
                if probe_result.returncode == 0:
                    audio_duration = float(probe_result.stdout.strip())
                    print(f"Voiceover duration: {audio_duration:.2f}s")
            except Exception as e:
                print(f"Could not get audio duration: {e}")
        
        num_scenes = len([s for s in scenes if s.get('video_url') or s.get('image_url') or s.get('visual') or s.get('thumbnail')])
        if audio_duration and num_scenes > 0:
            base_clip_duration = audio_duration / num_scenes
            print(f"Audio-driven clips: {base_clip_duration:.2f}s each for {num_scenes} scenes")
        else:
            base_clip_duration = None
        
        def download_and_trim_clip(args):
            """Download and trim a single clip - runs in parallel. Supports both video and image URLs."""
            i, scene, duration, output_id = args
            video_url = scene.get('video_url', '')
            image_url = scene.get('image_url', '') or scene.get('visual', '') or scene.get('thumbnail', '')
            
            if not video_url and not image_url:
                print(f"Clip {i}: No video_url or image_url found")
                return None, i, duration
            
            raw_path = f'output/raw_{output_id}_{i}.mp4'
            clip_path = f'output/clip_{output_id}_{i}.mp4'
            
            try:
                if video_url:
                    req = urllib.request.Request(video_url, headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req, timeout=20) as response:
                        with open(raw_path, 'wb') as f:
                            f.write(response.read())
                    
                    trim_cmd = [
                        'ffmpeg', '-y',
                        '-ss', '0',
                        '-i', os.path.abspath(raw_path),
                        '-t', str(duration),
                        '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '26',
                        '-an',
                        os.path.abspath(clip_path)
                    ]
                    result = subprocess.run(trim_cmd, capture_output=True, timeout=45)
                    
                    if result.returncode != 0:
                        import shutil as _shutil
                        _shutil.copy(raw_path, clip_path)
                    
                    if os.path.exists(raw_path):
                        os.remove(raw_path)
                else:
                    img_path = f'output/img_{output_id}_{i}.jpg'
                    direction = scene.get('direction', 'static')
                    print(f"Clip {i}: Converting image to video - direction: {direction}")
                    
                    req = urllib.request.Request(image_url, headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req, timeout=20) as response:
                        with open(img_path, 'wb') as f:
                            f.write(response.read())
                    
                    format_sizes = {
                        '9:16': (1080, 1920),
                        '1:1': (1080, 1080),
                        '4:5': (1080, 1350),
                        '16:9': (1920, 1080)
                    }
                    target_w, target_h = format_sizes.get(video_format, (1080, 1920))
                    
                    base_filter = f'scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black'
                    
                    if direction and direction.lower() not in ['static', '']:
                        if 'zoom in' in direction.lower():
                            motion_filter = f'scale={int(target_w*1.2)}:{int(target_h*1.2)}:force_original_aspect_ratio=decrease,zoompan=z=\'min(zoom+0.0015,1.1)\':x=\'iw/2-(iw/zoom/2)\':y=\'ih/2-(ih/zoom/2)\':d={int(duration*30)}:s={target_w}x{target_h}'
                        elif 'zoom out' in direction.lower():
                            motion_filter = f'scale={int(target_w*1.2)}:{int(target_h*1.2)}:force_original_aspect_ratio=decrease,zoompan=z=\'if(lte(zoom,1.0),1.1,max(zoom-0.0015,1.0))\':x=\'iw/2-(iw/zoom/2)\':y=\'ih/2-(ih/zoom/2)\':d={int(duration*30)}:s={target_w}x{target_h}'
                        elif 'pan left' in direction.lower():
                            motion_filter = f'scale={int(target_w*1.3)}:-1,crop={target_w}:{target_h}:x=\'(iw-{target_w})*t/{duration}\':y=0'
                        elif 'pan right' in direction.lower():
                            motion_filter = f'scale={int(target_w*1.3)}:-1,crop={target_w}:{target_h}:x=\'(iw-{target_w})*(1-t/{duration})\':y=0'
                        else:
                            motion_filter = base_filter
                        vf = motion_filter
                    else:
                        vf = base_filter
                    
                    img_to_vid_cmd = [
                        'ffmpeg', '-y',
                        '-loop', '1',
                        '-i', os.path.abspath(img_path),
                        '-t', str(duration),
                        '-vf', vf,
                        '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '23',
                        '-pix_fmt', 'yuv420p',
                        '-an',
                        os.path.abspath(clip_path)
                    ]
                    result = subprocess.run(img_to_vid_cmd, capture_output=True, timeout=60)
                    
                    if result.returncode != 0:
                        print(f"Clip {i}: FFmpeg error - {result.stderr.decode()[:200]}")
                        fallback_cmd = [
                            'ffmpeg', '-y', '-loop', '1',
                            '-i', os.path.abspath(img_path),
                            '-t', str(duration),
                            '-vf', base_filter,
                            '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '23',
                            '-pix_fmt', 'yuv420p', '-an',
                            os.path.abspath(clip_path)
                        ]
                        subprocess.run(fallback_cmd, capture_output=True, timeout=60)
                    
                    if os.path.exists(img_path):
                        os.remove(img_path)
                
                if os.path.exists(clip_path):
                    print(f"Clip {i}: Success - {duration:.1f}s")
                    return clip_path, i, duration
                return None, i, duration
            except Exception as e:
                print(f"Clip {i} error: {e}")
                for f in [raw_path, clip_path, f'output/img_{output_id}_{i}.jpg']:
                    if os.path.exists(f):
                        os.remove(f)
                return None, i, duration
        
        download_tasks = []
        for i, scene in enumerate(scenes):
            if base_clip_duration:
                duration = base_clip_duration
            else:
                duration = scene.get('duration_seconds', scene.get('duration', 4))
                try:
                    duration = float(duration)
                    if duration <= 0 or duration > 30:
                        duration = 4
                except:
                    duration = 4
            download_tasks.append((i, scene, duration, output_id))
        
        clip_results = {}
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(download_and_trim_clip, task): task[0] for task in download_tasks}
            for future in as_completed(futures):
                clip_path, idx, duration = future.result()
                if clip_path:
                    clip_results[idx] = (clip_path, duration)
        
        sorted_indices = sorted(clip_results.keys())
        clip_paths = [clip_results[i][0] for i in sorted_indices]
        clip_durations = [clip_results[i][1] for i in sorted_indices]
        print(f"Downloaded and trimmed {len(clip_paths)} clips in parallel")
        
        if not clip_paths:
            return jsonify({'error': 'Failed to download any video clips'}), 500
        
        if preview_mode:
            format_sizes = {
                '9:16': (360, 640),
                '1:1': (360, 360),
                '4:5': (360, 450),
                '16:9': (640, 360)
            }
        else:
            format_sizes = {
                '9:16': (1080, 1920),
                '1:1': (1080, 1080),
                '4:5': (1080, 1350),
                '16:9': (1920, 1080)
            }
        width, height = format_sizes.get(video_format, (360, 640) if preview_mode else (1080, 1920))
        
        list_path = os.path.abspath(f'output/clips_{output_id}.txt')
        with open(list_path, 'w') as f:
            for clip in clip_paths:
                f.write(f"file '{os.path.abspath(clip)}'\n")
        
        print(f"Using {len(clip_durations)} clip durations from parallel processing")
        
        concat_path = os.path.abspath(f'output/concat_{output_id}.mp4')
        
        if len(clip_paths) > 1:
            transition_duration = 0.5
            
            inputs = []
            for i, clip in enumerate(clip_paths):
                inputs.extend(['-i', os.path.abspath(clip)])
            
            filter_parts = []
            
            for i in range(len(clip_paths)):
                filter_parts.append(f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1,fps=30[s{i}]")
            
            transition_duration = min(transition_duration, min(clip_durations) * 0.8) if min(clip_durations) < 1 else transition_duration
            
            if len(clip_paths) == 2:
                offset = max(0.1, clip_durations[0] - transition_duration)
                filter_parts.append(f"[s0][s1]xfade=transition=fade:duration={transition_duration}:offset={offset:.2f}[v]")
            else:
                cumulative_duration = 0
                for i in range(len(clip_paths) - 1):
                    if i == 0:
                        cumulative_duration = max(0.1, clip_durations[0] - transition_duration)
                        filter_parts.append(f"[s0][s1]xfade=transition=fade:duration={transition_duration}:offset={cumulative_duration:.2f}[v1]")
                    elif i == len(clip_paths) - 2:
                        cumulative_duration += max(0.1, clip_durations[i] - transition_duration)
                        filter_parts.append(f"[v{i}][s{i+1}]xfade=transition=fade:duration={transition_duration}:offset={cumulative_duration:.2f}[v]")
                    else:
                        cumulative_duration += max(0.1, clip_durations[i] - transition_duration)
                        filter_parts.append(f"[v{i}][s{i+1}]xfade=transition=fade:duration={transition_duration}:offset={cumulative_duration:.2f}[v{i+1}]")
            
            xfade_filter = ";".join(filter_parts)
            
            concat_cmd = ['ffmpeg', '-y'] + inputs + [
                '-filter_complex', xfade_filter,
                '-map', '[v]',
                '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '28', '-threads', '0',
                concat_path
            ]
            result = subprocess.run(concat_cmd, capture_output=True, timeout=180)
            
            if result.returncode != 0:
                print(f"Xfade error: {result.stderr.decode()[:500]}")
                concat_cmd = [
                    'ffmpeg', '-y', '-f', 'concat', '-safe', '0',
                    '-i', list_path,
                    '-c', 'copy',
                    concat_path
                ]
                result = subprocess.run(concat_cmd, capture_output=True, timeout=120)
                if result.returncode != 0:
                    print(f"Concat fallback error: {result.stderr.decode()}")
                else:
                    print("Used simple concat (xfade failed)")
            else:
                print(f"Added fade transitions between {len(clip_paths)} clips")
        else:
            concat_cmd = [
                'ffmpeg', '-y', '-f', 'concat', '-safe', '0',
                '-i', list_path,
                '-c', 'copy',
                concat_path
            ]
            result = subprocess.run(concat_cmd, capture_output=True, timeout=120)
            if result.returncode != 0:
                print(f"Concat error: {result.stderr.decode()}")
        
        has_audio = audio_path and os.path.exists(audio_path)
        temp_combined = os.path.abspath(f'output/temp_combined_{output_id}.mp4')
        
        audio_duration = None
        if has_audio:
            dur_cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', audio_path]
            dur_result = subprocess.run(dur_cmd, capture_output=True, text=True, timeout=10)
            try:
                audio_duration = float(dur_result.stdout.strip())
                print(f"Audio duration: {audio_duration:.1f}s")
            except:
                audio_duration = None
        
        pass1_cmd = ['ffmpeg', '-y']
        
        if has_audio and audio_duration:
            pass1_cmd.extend(['-stream_loop', '-1', '-i', concat_path])
            pass1_cmd.extend(['-i', audio_path])
            pass1_cmd.extend(['-t', str(audio_duration)])
        else:
            pass1_cmd.extend(['-i', concat_path])
            if has_audio:
                pass1_cmd.extend(['-i', audio_path])
        
        pass1_cmd.extend([
            '-vf', f'scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}',
            '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '26', '-threads', '0',
        ])
        
        if has_audio:
            pass1_cmd.extend(['-c:a', 'aac', '-b:a', '128k'])
        else:
            pass1_cmd.extend(['-an'])
        
        pass1_cmd.append(temp_combined)
        
        print(f"Pass 1: Combining video + audio...")
        pass1_result = subprocess.run(pass1_cmd, capture_output=True, timeout=180)
        
        if pass1_result.returncode != 0:
            print(f"Pass 1 failed: {pass1_result.stderr.decode()[:500]}")
            if os.path.exists(concat_path):
                shutil.copy(concat_path, temp_combined)
        
        caption_srt_path = None
        caption_style_settings = None
        
        if captions_enabled and audio_path and os.path.exists(audio_path):
            try:
                from openai import OpenAI
                whisper_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
                
                with open(audio_path, 'rb') as audio_file:
                    transcription = whisper_client.audio.transcriptions.create(
                        model="whisper-1",
                        file=audio_file,
                        response_format="verbose_json",
                        timestamp_granularities=["word"]
                    )
                
                words = []
                if hasattr(transcription, 'words') and transcription.words:
                    words = transcription.words
                elif hasattr(transcription, 'segments'):
                    for segment in transcription.segments:
                        if hasattr(segment, 'words'):
                            words.extend(segment.words)
                
                print(f"Whisper returned {len(words)} word timestamps")
                
                if words:
                    from visual_director import apply_caption_template
                    caption_settings = apply_caption_template(caption_settings)
                    
                    caption_color = caption_settings.get('textColor', caption_settings.get('color', '#FFFFFF')).lstrip('#')
                    caption_position = caption_settings.get('position', 'bottom')
                    caption_uppercase = caption_settings.get('uppercase', False)
                    caption_outline = caption_settings.get('outline', True)
                    caption_shadow = caption_settings.get('shadow', True)
                    
                    if caption_position == 'top':
                        y_pos = 'h*0.12'
                    elif caption_position == 'bottom':
                        y_pos = 'h*0.82'
                    else:
                        y_pos = '(h-text_h)/2'
                    
                    fontsize = 24 if not preview_mode else 14
                    
                    phrases = []
                    current_phrase = []
                    current_start = None
                    current_end = 0
                    
                    for word_data in words:
                        if isinstance(word_data, dict):
                            word = word_data.get('word', '')
                            start = word_data.get('start', 0)
                            end = word_data.get('end', 0)
                        else:
                            word = getattr(word_data, 'word', '')
                            start = getattr(word_data, 'start', 0)
                            end = getattr(word_data, 'end', 0)
                        
                        word = word.strip()
                        if not word:
                            continue
                        
                        if current_start is None:
                            current_start = start
                        
                        current_phrase.append(word)
                        current_end = end
                        
                        word_stripped = word.rstrip()
                        if len(current_phrase) >= 4 or (len(current_phrase) >= 2 and word_stripped.endswith(('.', '!', '?', ','))):
                            phrases.append({
                                'text': ' '.join(current_phrase),
                                'start': current_start,
                                'end': current_end
                            })
                            current_phrase = []
                            current_start = None
                    
                    if current_phrase:
                        phrases.append({
                            'text': ' '.join(current_phrase),
                            'start': current_start,
                            'end': current_end
                        })
                    
                    if phrases and audio_duration:
                        phrases[-1]['end'] = audio_duration
                    
                    def format_srt_time(seconds):
                        """Convert seconds to SRT timestamp format (HH:MM:SS,mmm)"""
                        hours = int(seconds // 3600)
                        minutes = int((seconds % 3600) // 60)
                        secs = int(seconds % 60)
                        millis = int((seconds % 1) * 1000)
                        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
                    
                    srt_content = []
                    for i, phrase in enumerate(phrases, 1):
                        text = phrase['text']
                        if caption_uppercase:
                            text = text.upper()
                        start_srt = format_srt_time(phrase['start'])
                        end_srt = format_srt_time(phrase['end'])
                        srt_content.append(f"{i}\n{start_srt} --> {end_srt}\n{text}\n")
                    
                    srt_path = f"output/captions_{uuid.uuid4().hex[:8]}.srt"
                    with open(srt_path, 'w', encoding='utf-8') as f:
                        f.write('\n'.join(srt_content))
                    
                    caption_srt_path = srt_path
                    caption_style_settings = {
                        'fontsize': fontsize,
                        'color': caption_color,
                        'outline': caption_outline,
                        'shadow': caption_shadow,
                        'y_pos': y_pos
                    }
                    
                    print(f"Generated SRT with {len(phrases)} caption phrases: {srt_path}")
                else:
                    print("No word timestamps returned from Whisper")
                    
            except Exception as e:
                print(f"Whisper transcription failed, skipping captions: {e}")
        
        if caption_srt_path and os.path.exists(caption_srt_path) and os.path.exists(temp_combined):
            print(f"Pass 2: Adding captions from SRT file...")
            
            style = caption_style_settings
            font_size = style['fontsize']
            hex_color = style['color'].lstrip('#')
            if len(hex_color) == 6:
                r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
                bgr_color = f"&H{b:02X}{g:02X}{r:02X}&"
            else:
                bgr_color = "&HFFFFFF&"
            
            outline_width = 3 if style['outline'] else 0
            shadow_depth = 2 if style['shadow'] else 0
            
            escaped_srt = caption_srt_path.replace('\\', '/').replace(':', r'\:')
            
            margin_v = 100
            
            subtitle_filter = (
                f"subtitles={escaped_srt}:force_style='"
                f"FontName=DejaVu Sans,FontSize={font_size},"
                f"PrimaryColour={bgr_color},OutlineColour=&H000000&,"
                f"BorderStyle=1,Outline={outline_width},Shadow={shadow_depth},"
                f"Alignment=2,MarginV={margin_v}'"
            )
            
            pass2_cmd = [
                'ffmpeg', '-y',
                '-i', temp_combined,
                '-vf', subtitle_filter,
                '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '26', '-threads', '0',
                '-c:a', 'copy',
                output_path
            ]
            
            print(f"Caption filter: {subtitle_filter}")
            pass2_result = subprocess.run(pass2_cmd, capture_output=True, timeout=300)
            
            if pass2_result.returncode != 0:
                error_msg = pass2_result.stderr.decode()[:2000]
                print(f"Pass 2 (captions) failed: {error_msg}")
                shutil.copy(temp_combined, output_path)
                print("Using video without captions as fallback")
            else:
                print("Pass 2 succeeded - captions added via SRT")
            
            try:
                os.remove(caption_srt_path)
            except:
                pass
        else:
            if os.path.exists(temp_combined):
                shutil.copy(temp_combined, output_path)
            elif os.path.exists(concat_path):
                shutil.copy(concat_path, output_path)
        
        try:
            if os.path.exists(temp_combined):
                os.remove(temp_combined)
        except:
            pass
        
        for clip in clip_paths:
            try:
                os.remove(clip)
            except:
                pass
        try:
            os.remove(list_path)
            os.remove(concat_path)
        except:
            pass
        
        try:
            mixed_audio_path = f'output/audio_with_sfx_{output_id}.mp3'
            if os.path.exists(mixed_audio_path):
                os.remove(mixed_audio_path)
        except:
            pass
        
        if os.path.exists(output_path):
            response_data = {
                'success': True,
                'video_url': '/' + output_path,
                'video_path': '/' + output_path,
                'format': video_format
            }
            
            try:
                from context_engine import generate_video_description
                
                trend_sources = session.get('last_trend_sources', [])
                
                desc_result = generate_video_description(script_text or '', trend_sources=trend_sources)
                response_data['description'] = desc_result.get('description', '')
                response_data['hashtags'] = desc_result.get('hashtags', [])
                response_data['trend_sources'] = trend_sources
            except Exception as desc_err:
                print(f"Description generation error: {desc_err}")
                response_data['description'] = ''
                response_data['trend_sources'] = []
            
            try:
                from context_engine import ai_self_critique, store_ai_learnings
                project_data = {
                    'project_id': session.get('current_project_id'),
                    'script': script_text or '',
                    'visual_plan': scenes,
                    'template': session.get('current_template', 'start_from_scratch'),
                    'original_request': session.get('original_user_request', ''),
                    'user_id': user_id
                }
                critique_result = ai_self_critique(project_data, user_accepted=True)
                if critique_result:
                    critique_result['user_id'] = user_id
                    store_ai_learnings(critique_result, db.session)
                    response_data['ai_self_score'] = critique_result.get('overall_self_score', 0)
                    print(f"[AI Self-Critique] Completed: {critique_result.get('honest_assessment', 'N/A')}")
            except Exception as critique_err:
                print(f"[AI Self-Critique] Error (non-blocking): {critique_err}")
            
            return jsonify(response_data)
        else:
            return jsonify({'error': format_user_error('Video render failed')}), 500
            
    except Exception as e:
        print(f"Render error: {e}")
        return jsonify({'error': format_user_error(str(e))}), 500


@render_bp.route('/finalize-video', methods=['POST'])
def finalize_video():
    """Remove watermark and create final video - uses tokens."""
    from models import User, Subscription, PreviewVideo
    from datetime import datetime
    
    user_id = None
    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('dev_user_id')
    
    data = request.get_json()
    preview_url = data.get('preview_url', '')
    caption_position = data.get('caption_position', 'bottom')
    
    if not preview_url:
        return jsonify({'error': 'No preview URL provided'}), 400
    
    is_dev_mode = os.environ.get('FLASK_ENV') == 'development' or os.environ.get('DEV_MODE') == 'true'
    
    if not is_dev_mode and user_id:
        sub = Subscription.query.filter_by(user_id=user_id).first()
        user = User.query.get(user_id)
        
        if not (sub and sub.is_active()):
            if user and user.tokens and user.tokens >= 10:
                user.tokens -= 10
                db.session.commit()
            else:
                return jsonify({
                    'error': 'Insufficient tokens',
                    'requires_subscription': True
                }), 403
    
    final_url = preview_url
    
    try:
        preview_record = PreviewVideo(
            user_id=user_id,
            preview_path=preview_url,
            final_path=final_url,
            is_finalized=True,
            finalized_at=datetime.utcnow()
        )
        db.session.add(preview_record)
        db.session.commit()
    except Exception as e:
        print(f"[finalize-video] Failed to record: {e}")
    
    return jsonify({
        'success': True,
        'video_url': final_url,
        'caption_position': caption_position
    })


@render_bp.route('/create-visual-plan', methods=['POST'])
def create_visual_plan():
    """Create a visual plan using the Visual Director AI."""
    from visual_director import create_visual_plan as vd_create_plan
    from models import VisualPlan
    
    user_id = None
    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('dev_user_id')
    
    data = request.get_json()
    script = data.get('script', '')
    user_intent = data.get('intent', '')
    template_type = data.get('template', None)
    user_content = data.get('user_content', [])
    
    if not script:
        return jsonify({'error': 'Script is required'}), 400
    
    try:
        plan = vd_create_plan(
            script=script,
            user_intent=user_intent,
            user_content=user_content,
            template_type=template_type
        )
        
        try:
            plan_record = VisualPlan(
                user_id=user_id,
                plan_id=plan['plan_id'],
                content_type=plan['content_type'],
                color_palette=plan['color_palette'],
                editing_dna=plan['editing_dna'],
                scenes=plan['scenes']
            )
            db.session.add(plan_record)
            db.session.commit()
        except Exception as e:
            print(f"[create-visual-plan] Failed to store plan: {e}")
        
        return jsonify({
            'success': True,
            'plan': plan
        })
    except Exception as e:
        print(f"[create-visual-plan] Error: {e}")
        return jsonify({'error': str(e)}), 500


@render_bp.route('/get-visual-plan/<plan_id>', methods=['GET'])
def get_visual_plan(plan_id):
    """Retrieve a stored visual plan."""
    from models import VisualPlan
    
    plan = VisualPlan.query.filter_by(plan_id=plan_id).first()
    
    if not plan:
        return jsonify({'error': 'Plan not found'}), 404
    
    return jsonify({
        'success': True,
        'plan': {
            'plan_id': plan.plan_id,
            'content_type': plan.content_type,
            'color_palette': plan.color_palette,
            'editing_dna': plan.editing_dna,
            'scenes': plan.scenes
        }
    })


@render_bp.route('/execute-visual-plan', methods=['POST'])
def execute_visual_plan_endpoint():
    """Execute a visual plan - fetch stock photos and prepare DALL-E prompts."""
    from visual_director import execute_visual_plan
    from models import VisualPlan
    
    data = request.get_json()
    plan_id = data.get('plan_id')
    
    if not plan_id:
        return jsonify({'error': 'Plan ID is required'}), 400
    
    plan = VisualPlan.query.filter_by(plan_id=plan_id).first()
    
    if not plan:
        return jsonify({'error': 'Plan not found'}), 404
    
    try:
        visual_plan = {
            'plan_id': plan.plan_id,
            'content_type': plan.content_type,
            'color_palette': plan.color_palette,
            'editing_dna': plan.editing_dna,
            'scenes': plan.scenes
        }
        
        executed_scenes = execute_visual_plan(visual_plan)
        
        return jsonify({
            'success': True,
            'scenes': executed_scenes,
            'content_type': plan.content_type,
            'color_palette': plan.color_palette
        })
    except Exception as e:
        print(f"[execute-visual-plan] Error: {e}")
        return jsonify({'error': str(e)}), 500


@render_bp.route('/render-with-plan', methods=['POST'])
def render_with_visual_plan():
    """Render a video using a visual plan - unified pipeline with Source Merging Engine."""
    from visual_director import execute_visual_plan, get_merging_config
    from models import VisualPlan
    import requests
    
    user_id = None
    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('dev_user_id')
    
    data = request.get_json()
    plan_id = data.get('plan_id')
    script = data.get('script', '')
    audio_path = data.get('audio_path', '')
    video_format = data.get('format', '9:16')
    caption_position = data.get('caption_position', 'bottom')
    color_style = data.get('color_style')
    film_grain = data.get('film_grain', True)
    caption_template = data.get('caption_template', 'bold_pop')
    
    plan = VisualPlan.query.filter_by(plan_id=plan_id).first() if plan_id else None
    
    try:
        scenes_to_render = []
        content_type = plan.content_type if plan else 'general'
        
        merging_config = get_merging_config(
            content_type,
            {'color_style': color_style, 'film_grain': film_grain}
        )
        
        if plan:
            visual_plan = {
                'plan_id': plan.plan_id,
                'content_type': plan.content_type,
                'color_palette': plan.color_palette,
                'editing_dna': plan.editing_dna,
                'scenes': plan.scenes
            }
            executed_scenes = execute_visual_plan(visual_plan)
            
            for scene in executed_scenes:
                if scene.get('visual'):
                    scenes_to_render.append({
                        'text': scene.get('text', ''),
                        'image_url': scene.get('visual'),
                        'source_type': scene.get('source_type', 'stock'),
                        'duration': 4
                    })
                elif scene.get('dalle_prompt'):
                    scenes_to_render.append({
                        'text': scene.get('text', ''),
                        'dalle_prompt': scene.get('dalle_prompt'),
                        'source_type': 'dalle',
                        'duration': 4
                    })
        
        if not scenes_to_render:
            return jsonify({'error': 'No scenes to render'}), 400
        
        output_id = str(uuid.uuid4())[:8]
        output_path = f'output/plan_{output_id}.mp4'
        os.makedirs('output', exist_ok=True)
        
        format_dims = {
            '9:16': (1080, 1920), '16:9': (1920, 1080),
            '1:1': (1080, 1080), '4:5': (1080, 1350)
        }
        width, height = format_dims.get(video_format, (1080, 1920))
        
        audio_duration = 0
        if audio_path and os.path.exists(audio_path):
            dur_cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', audio_path]
            dur_result = subprocess.run(dur_cmd, capture_output=True, text=True, timeout=30)
            audio_duration = float(dur_result.stdout.strip()) if dur_result.stdout.strip() else 30
        else:
            audio_duration = len(scenes_to_render) * 4
        
        scene_duration = audio_duration / max(len(scenes_to_render), 1)
        
        scene_clips = []
        temp_files = []
        
        for i, scene in enumerate(scenes_to_render):
            scene_img_path = f'output/scene_{output_id}_{i}.jpg'
            
            if scene.get('image_url'):
                try:
                    resp = requests.get(scene['image_url'], timeout=30)
                    if resp.status_code == 200:
                        with open(scene_img_path, 'wb') as f:
                            f.write(resp.content)
                        temp_files.append(scene_img_path)
                except Exception as e:
                    print(f"Failed to download scene {i} image: {e}")
                    continue
            elif scene.get('dalle_prompt'):
                try:
                    from openai import OpenAI
                    dalle_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
                    response = dalle_client.images.generate(
                        model="dall-e-3",
                        prompt=scene['dalle_prompt'],
                        size="1024x1792" if video_format == '9:16' else "1792x1024",
                        quality="standard",
                        n=1
                    )
                    img_url = response.data[0].url
                    resp = requests.get(img_url, timeout=60)
                    if resp.status_code == 200:
                        with open(scene_img_path, 'wb') as f:
                            f.write(resp.content)
                        temp_files.append(scene_img_path)
                except Exception as e:
                    print(f"Failed to generate DALL-E image for scene {i}: {e}")
                    continue
            else:
                continue
            
            if os.path.exists(scene_img_path):
                scene_clips.append({
                    'path': scene_img_path,
                    'duration': scene_duration
                })
        
        if not scene_clips:
            return jsonify({'error': 'Failed to create any scene clips'}), 400
        
        concat_file = f'output/concat_{output_id}.txt'
        temp_scene_videos = []
        
        color_filter = merging_config.get('filter_chain', '')
        
        for i, clip in enumerate(scene_clips):
            scene_video = f'output/scene_vid_{output_id}_{i}.mp4'
            
            fps = 30
            zoom_frames = int(fps * clip['duration'])
            
            vf_filters = [
                f'scale={width*2}:{height*2}:force_original_aspect_ratio=increase',
                f'crop={width*2}:{height*2}',
                f'zoompan=z=1.04:d={zoom_frames}:x=iw/2-(iw/zoom/2):y=ih/2-(ih/zoom/2):s={width}x{height}:fps={fps}'
            ]
            
            if color_filter and isinstance(color_filter, str) and color_filter.strip():
                try:
                    vf_filters.append(color_filter.strip())
                except:
                    pass
            
            img_cmd = [
                'ffmpeg', '-y',
                '-loop', '1', '-i', clip['path'],
                '-t', str(clip['duration']),
                '-vf', ','.join(vf_filters),
                '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
                '-pix_fmt', 'yuv420p',
                scene_video
            ]
            result = subprocess.run(img_cmd, capture_output=True, timeout=180)
            
            if result.returncode == 0 and os.path.exists(scene_video):
                temp_scene_videos.append(scene_video)
            else:
                stderr_msg = result.stderr.decode()[:500] if result.stderr else 'unknown error'
                print(f"[render-with-plan] Zoompan failed for scene {i}: {stderr_msg}")
                simple_filters = [
                    f'scale={width}:{height}:force_original_aspect_ratio=increase',
                    f'crop={width}:{height}',
                    'setsar=1'
                ]
                simple_cmd = [
                    'ffmpeg', '-y',
                    '-loop', '1', '-i', clip['path'],
                    '-t', str(clip['duration']),
                    '-vf', ','.join(simple_filters),
                    '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
                    '-pix_fmt', 'yuv420p',
                    '-r', str(fps),
                    scene_video
                ]
                fallback_result = subprocess.run(simple_cmd, capture_output=True, timeout=180)
                if fallback_result.returncode == 0 and os.path.exists(scene_video):
                    temp_scene_videos.append(scene_video)
        
        if not temp_scene_videos:
            return jsonify({'error': 'Failed to create scene videos'}), 400
        
        with open(concat_file, 'w') as f:
            for vid in temp_scene_videos:
                f.write(f"file '{os.path.abspath(vid)}'\n")
        temp_files.append(concat_file)
        
        concat_output = f'output/concat_out_{output_id}.mp4'
        concat_cmd = [
            'ffmpeg', '-y', '-f', 'concat', '-safe', '0',
            '-i', concat_file,
            '-c', 'copy',
            concat_output
        ]
        subprocess.run(concat_cmd, capture_output=True, timeout=300)
        temp_files.append(concat_output)
        
        if audio_path and os.path.exists(audio_path):
            audio_output = f'output/audio_{output_id}.mp4'
            audio_cmd = [
                'ffmpeg', '-y',
                '-i', concat_output,
                '-i', audio_path,
                '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
                '-map', '0:v:0', '-map', '1:a:0',
                '-shortest',
                audio_output
            ]
            subprocess.run(audio_cmd, capture_output=True, timeout=300)
            temp_files.append(audio_output)
            current_video = audio_output
        else:
            current_video = concat_output
        
        if audio_path and os.path.exists(audio_path):
            ass_path = f'output/plan_captions_{output_id}.ass'
            _, whisper_success = create_whisper_synced_captions(
                audio_path, ass_path,
                template=caption_template,
                position=caption_position,
                video_width=width, video_height=height
            )
            
            if whisper_success:
                caption_output = f'output/captioned_{output_id}.mp4'
                caption_cmd = [
                    'ffmpeg', '-y',
                    '-i', current_video,
                    '-vf', f"ass={ass_path}",
                    '-c:a', 'copy',
                    caption_output
                ]
                result = subprocess.run(caption_cmd, capture_output=True, timeout=300)
                if result.returncode == 0 and os.path.exists(caption_output):
                    current_video = caption_output
                    temp_files.append(caption_output)
                temp_files.append(ass_path)
        
        shutil.move(current_video, output_path)
        
        for f in temp_files + temp_scene_videos:
            if f and os.path.exists(f) and f != output_path:
                try:
                    os.remove(f)
                except:
                    pass
        
        print(f"[render-with-plan] Created video with {len(scene_clips)} scenes: {output_path}")
        
        return jsonify({
            'success': True,
            'video_url': f'/{output_path}',
            'video_path': output_path,
            'scenes_count': len(scenes_to_render),
            'content_type': content_type,
            'merging_config': merging_config,
            'caption_template': caption_template,
            'is_preview': True
        })
        
    except Exception as e:
        print(f"[render-with-plan] Error: {e}")
        return jsonify({'error': str(e)}), 500


@render_bp.route('/get-merging-options', methods=['POST'])
def get_merging_options():
    """Get AI-recommended color styles and caption templates for a project."""
    from visual_director import recommend_color_style, recommend_caption_style, CAPTION_TEMPLATES as VD_CAPTION_TEMPLATES
    
    data = request.get_json()
    content_type = data.get('content_type', 'general')
    
    try:
        color_rec = recommend_color_style(content_type)
        caption_rec = recommend_caption_style(content_type)
        
        return jsonify({
            'success': True,
            'color_recommendation': color_rec,
            'caption_recommendation': caption_rec,
            'caption_templates': {k: v for k, v in VD_CAPTION_TEMPLATES.items()}
        })
    except Exception as e:
        print(f"[get-merging-options] Error: {e}")
        return jsonify({'error': str(e)}), 500


@render_bp.route('/save-merging-preferences', methods=['POST'])
def save_merging_preferences():
    """Save user's Source Merging preferences."""
    from models import UserMergingPreferences
    
    user_id = None
    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('dev_user_id')
    
    if not user_id:
        return jsonify({'error': 'Not authenticated'}), 401
    
    data = request.get_json()
    
    try:
        prefs = UserMergingPreferences.query.filter_by(user_id=user_id).first()
        
        if not prefs:
            prefs = UserMergingPreferences(user_id=user_id)
            db.session.add(prefs)
        
        if 'color_style' in data:
            prefs.preferred_color_style = data['color_style']
        if 'film_grain' in data:
            prefs.film_grain_enabled = data['film_grain']
        if 'caption_template' in data:
            prefs.preferred_caption_template = data['caption_template']
        
        db.session.commit()
        
        return jsonify({'success': True})
    except Exception as e:
        print(f"[save-merging-preferences] Error: {e}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@render_bp.route('/refresh-caption-style', methods=['POST'])
def refresh_caption_style():
    """Refresh to get a new AI-curated caption style, save current to history."""
    from visual_director import recommend_caption_style, save_caption_style_choice, CAPTION_TEMPLATES as VD_CAPTION_TEMPLATES
    from models import CaptionStyleHistory
    import random
    
    user_id = None
    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('dev_user_id')
    
    data = request.get_json()
    current_style = data.get('current_style')
    content_type = data.get('content_type', 'general')
    
    try:
        if user_id and current_style:
            save_caption_style_choice(user_id, current_style, was_refresh=True)
        
        available = list(VD_CAPTION_TEMPLATES.keys())
        if current_style in available:
            available.remove(current_style)
        
        new_key = random.choice(available)
        new_template = VD_CAPTION_TEMPLATES[new_key].copy()
        new_template['key'] = new_key
        
        return jsonify({
            'success': True,
            'new_style': new_template
        })
    except Exception as e:
        print(f"[refresh-caption-style] Error: {e}")
        return jsonify({'error': str(e)}), 500


@render_bp.route('/get-caption-history', methods=['GET'])
def get_caption_history():
    """Get user's caption style history for back/forward navigation."""
    from visual_director import get_caption_style_history, CAPTION_TEMPLATES as VD_CAPTION_TEMPLATES
    
    user_id = None
    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('dev_user_id')
    
    if not user_id:
        return jsonify({'history': []})
    
    try:
        history = get_caption_style_history(user_id)
        
        for item in history:
            if item['template_key'] in VD_CAPTION_TEMPLATES:
                item['template'] = VD_CAPTION_TEMPLATES[item['template_key']]
        
        return jsonify({'success': True, 'history': history})
    except Exception as e:
        print(f"[get-caption-history] Error: {e}")
        return jsonify({'history': []})
