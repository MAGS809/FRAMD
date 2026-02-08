import os
import re
import subprocess
import logging


CAPTION_TEMPLATES = {
    'bold_pop': {
        'name': 'Bold Pop',
        'font': 'Arial',
        'base_size': 52,
        'highlight_size': 62,
        'primary_color': '&H00FFFFFF',
        'highlight_color': '&H0000D4FF',
        'outline_color': '&H00000000',
        'outline': 4,
        'shadow': 3,
        'bold': True,
        'animation': 'pop'
    },
    'clean_minimal': {
        'name': 'Clean Minimal',
        'font': 'Arial',
        'base_size': 44,
        'highlight_size': 48,
        'primary_color': '&H00FFFFFF',
        'highlight_color': '&H00FFFFFF',
        'outline_color': '&H80000000',
        'outline': 2,
        'shadow': 1,
        'bold': False,
        'animation': 'fade'
    },
    'gradient_glow': {
        'name': 'Gradient Glow',
        'font': 'Arial',
        'base_size': 48,
        'highlight_size': 56,
        'primary_color': '&H00FFFFFF',
        'highlight_color': '&H00FFD700',
        'outline_color': '&H00000000',
        'outline': 3,
        'shadow': 4,
        'bold': True,
        'animation': 'glow'
    },
    'street_style': {
        'name': 'Street Style',
        'font': 'Impact',
        'base_size': 56,
        'highlight_size': 64,
        'primary_color': '&H00FFFFFF',
        'highlight_color': '&H0000FF00',
        'outline_color': '&H00000000',
        'outline': 5,
        'shadow': 2,
        'bold': True,
        'animation': 'bounce'
    },
    'boxed': {
        'name': 'Boxed',
        'font': 'Arial',
        'base_size': 42,
        'highlight_size': 46,
        'primary_color': '&H00000000',
        'highlight_color': '&H00000000',
        'back_color': '&H80FFFFFF',
        'outline_color': '&H00000000',
        'outline': 0,
        'shadow': 0,
        'bold': True,
        'animation': 'slide'
    }
}


def build_visual_fx_filter(visual_fx, width, height):
    """
    Build FFmpeg filter string based on template visual FX settings.
    Returns a filter string to apply color grading, vignette, etc.
    """
    filters = []
    
    color_grade = visual_fx.get('color_grade', 'natural')
    vignette = visual_fx.get('vignette', 0)
    
    color_grade_filters = {
        'high_contrast': 'eq=contrast=1.3:brightness=0.05:saturation=1.2',
        'clean_bright': 'eq=contrast=1.1:brightness=0.08:saturation=1.1',
        'warm_cinematic': 'colorbalance=rs=0.1:gs=0.05:bs=-0.1,eq=contrast=1.15:saturation=1.1',
        'neutral_sharp': 'eq=contrast=1.2:saturation=0.95,unsharp=5:5:1',
        'warm_intimate': 'colorbalance=rs=0.15:gs=0.08:bs=-0.05,eq=contrast=1.05:brightness=0.03',
        'saturated_pop': 'eq=saturation=1.4:contrast=1.2:brightness=0.05',
        'polished_commercial': 'eq=contrast=1.1:brightness=0.05:saturation=1.15',
        'vibrant_social': 'eq=saturation=1.35:contrast=1.15',
        'natural': 'eq=contrast=1.05:saturation=1.0'
    }
    
    if color_grade in color_grade_filters:
        filters.append(color_grade_filters[color_grade])
    
    if vignette > 0:
        vignette_angle = 3.14159 / (2 + vignette * 3)
        filters.append(f'vignette=PI/{2 + int(vignette * 3)}')
    
    if not filters:
        return ''
    
    return ','.join(filters)


def create_whisper_synced_captions(audio_path, output_path, template='bold_pop', position='bottom', video_width=1080, video_height=1920, uppercase=False):
    """
    Create ASS subtitle file with word-by-word captions synced to actual voiceover audio using Whisper.
    Returns (output_path, success) tuple.
    """
    from openai import OpenAI
    
    try:
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
        
        if not words:
            print("Whisper returned no word timestamps, falling back to estimated timing")
            return None, False
        
        print(f"Whisper returned {len(words)} word timestamps for caption sync")
        
        style = CAPTION_TEMPLATES.get(template, CAPTION_TEMPLATES['bold_pop'])
        
        margin_v = {'top': 100, 'center': int(video_height/2 - 50), 'bottom': 150}.get(position, 150)
        alignment = {'top': 8, 'center': 5, 'bottom': 2}.get(position, 2)
        
        bold_val = -1 if style['bold'] else 0
        back_color = style.get('back_color', '&H00000000')
        border_style = 3 if template == 'boxed' else 1
        
        ass_header = f"""[Script Info]
Title: Whisper-Synced Captions
ScriptType: v4.00+
PlayResX: {video_width}
PlayResY: {video_height}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{style['font']},{style['base_size']},{style['primary_color']},&H000000FF,{style['outline_color']},{back_color},{bold_val},0,0,0,100,100,0,0,{border_style},{style['outline']},{style['shadow']},{alignment},40,40,{margin_v},1
Style: Highlight,{style['font']},{style['highlight_size']},{style['highlight_color']},&H000000FF,{style['outline_color']},{back_color},{bold_val},0,0,0,100,100,0,0,{border_style},{style['outline']},{style['shadow']},{alignment},40,40,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        
        def format_ass_time(seconds):
            h = int(seconds // 3600)
            m = int((seconds % 3600) // 60)
            s = seconds % 60
            return f"{h}:{m:02d}:{s:05.2f}"
        
        if style['animation'] == 'pop':
            anim_effect = r"\fscx110\fscy110\t(0,100,\fscx100\fscy100)"
        elif style['animation'] == 'bounce':
            anim_effect = r"\fscx120\fscy120\t(0,80,\fscx100\fscy100)"
        elif style['animation'] == 'glow':
            anim_effect = r"\blur3\t(0,150,\blur0)"
        elif style['animation'] == 'fade':
            anim_effect = r"\alpha&HFF&\t(0,100,\alpha&H00&)"
        elif style['animation'] == 'slide':
            anim_effect = r"\fscx105\t(0,100,\fscx100)"
        else:
            anim_effect = ""
        
        events = []
        chunk_size = 4
        
        phrases = []
        current_phrase = []
        current_start = None
        current_end = 0
        
        for word_data in words:
            if isinstance(word_data, dict):
                word = word_data.get('word', '').strip()
                start = word_data.get('start', 0)
                end = word_data.get('end', 0)
            else:
                word = getattr(word_data, 'word', '').strip()
                start = getattr(word_data, 'start', 0)
                end = getattr(word_data, 'end', 0)
            
            if not word:
                continue
            
            if uppercase:
                word = word.upper()
            
            if current_start is None:
                current_start = start
            
            current_phrase.append({'word': word, 'start': start, 'end': end})
            current_end = end
            
            if len(current_phrase) >= chunk_size or word.rstrip().endswith(('.', '!', '?', ',')):
                phrases.append({
                    'words': current_phrase,
                    'start': current_start,
                    'end': current_end
                })
                current_phrase = []
                current_start = None
        
        if current_phrase:
            phrases.append({
                'words': current_phrase,
                'start': current_start,
                'end': current_end
            })
        
        for phrase in phrases:
            phrase_words = phrase['words']
            
            for i, word_data in enumerate(phrase_words):
                word_start = word_data['start']
                word_end = word_data['end']
                
                before_words = [w['word'] for w in phrase_words[:i]]
                after_words = [w['word'] for w in phrase_words[i+1:]]
                current_word = word_data['word']
                
                text_parts = []
                if before_words:
                    text_parts.append("{\\rDefault}" + ' '.join(before_words) + " ")
                text_parts.append("{\\rHighlight" + anim_effect + "}" + current_word)
                if after_words:
                    text_parts.append("{\\rDefault} " + ' '.join(after_words))
                
                full_text = ''.join(text_parts)
                
                event_line = f"Dialogue: 0,{format_ass_time(word_start)},{format_ass_time(word_end)},Default,,0,0,0,,{full_text}"
                events.append(event_line)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(ass_header)
            f.write('\n'.join(events))
        
        print(f"Created Whisper-synced captions with {len(events)} events: {output_path}")
        return output_path, True
        
    except Exception as e:
        print(f"Whisper caption sync failed: {e}")
        return None, False


def create_dynamic_captions_ass(script_text, audio_duration, output_path, template='bold_pop', position='bottom', video_width=1080, video_height=1920):
    """
    Create ASS subtitle file with word-by-word animated captions.
    Features pop/scale animations synced to audio timing.
    NOTE: This uses ESTIMATED timing. Use create_whisper_synced_captions for true audio sync.
    """
    style = CAPTION_TEMPLATES.get(template, CAPTION_TEMPLATES['bold_pop'])
    
    clean_text = re.sub(r'\[.*?\]', '', script_text)
    clean_text = re.sub(r'\s+', ' ', clean_text).strip()
    words = clean_text.split()
    
    margin_v = {'top': 100, 'center': int(video_height/2 - 50), 'bottom': 150}.get(position, 150)
    alignment = {'top': 8, 'center': 5, 'bottom': 2}.get(position, 2)
    
    bold_val = -1 if style['bold'] else 0
    back_color = style.get('back_color', '&H00000000')
    
    border_style = 3 if template == 'boxed' else 1
    
    ass_header = f"""[Script Info]
Title: Dynamic Captions
ScriptType: v4.00+
PlayResX: {video_width}
PlayResY: {video_height}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{style['font']},{style['base_size']},{style['primary_color']},&H000000FF,{style['outline_color']},{back_color},{bold_val},0,0,0,100,100,0,0,{border_style},{style['outline']},{style['shadow']},{alignment},40,40,{margin_v},1
Style: Highlight,{style['font']},{style['highlight_size']},{style['highlight_color']},&H000000FF,{style['outline_color']},{back_color},{bold_val},0,0,0,100,100,0,0,{border_style},{style['outline']},{style['shadow']},{alignment},40,40,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    
    if not words:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(ass_header)
        return output_path
    
    seconds_per_word = audio_duration / len(words)
    
    def format_ass_time(seconds):
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = seconds % 60
        return f"{h}:{m:02d}:{s:05.2f}"
    
    if style['animation'] == 'pop':
        anim_effect = r"\fscx110\fscy110\t(0,100,\fscx100\fscy100)"
    elif style['animation'] == 'bounce':
        anim_effect = r"\fscx120\fscy120\t(0,80,\fscx100\fscy100)"
    elif style['animation'] == 'glow':
        anim_effect = r"\blur3\t(0,150,\blur0)"
    elif style['animation'] == 'fade':
        anim_effect = r"\alpha&HFF&\t(0,100,\alpha&H00&)"
    elif style['animation'] == 'slide':
        anim_effect = r"\fscx105\t(0,100,\fscx100)"
    else:
        anim_effect = ""
    
    events = []
    chunk_size = 4
    
    for chunk_idx in range(0, len(words), chunk_size):
        chunk_words = words[chunk_idx:chunk_idx + chunk_size]
        chunk_start = chunk_idx * seconds_per_word
        chunk_end = min((chunk_idx + len(chunk_words)) * seconds_per_word, audio_duration)
        
        for word_offset, word in enumerate(chunk_words):
            word_start = chunk_start + (word_offset * seconds_per_word)
            word_end = min(word_start + seconds_per_word, chunk_end)
            
            before_words = chunk_words[:word_offset]
            after_words = chunk_words[word_offset + 1:]
            
            text_parts = []
            if before_words:
                text_parts.append("{\\rDefault}" + ' '.join(before_words) + " ")
            text_parts.append("{\\rHighlight" + anim_effect + "}" + word)
            if after_words:
                text_parts.append("{\\rDefault} " + ' '.join(after_words))
            
            full_text = ''.join(text_parts)
            
            event_line = f"Dialogue: 0,{format_ass_time(word_start)},{format_ass_time(word_end)},Default,,0,0,0,,{full_text}"
            events.append(event_line)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(ass_header)
        f.write('\n'.join(events))
    
    return output_path


def create_word_synced_subtitles(script_text, audio_duration, output_path):
    """
    Create SRT subtitle file with word-level timing based on audio duration.
    Distributes words evenly across the audio duration.
    """
    clean_text = re.sub(r'\[.*?\]', '', script_text)
    clean_text = re.sub(r'\s+', ' ', clean_text).strip()
    words = clean_text.split()
    
    if not words:
        return output_path
    
    words_per_second = len(words) / max(audio_duration, 1)
    seconds_per_word = 1 / max(words_per_second, 0.5)
    
    chunk_size = 4
    chunks = []
    for i in range(0, len(words), chunk_size):
        chunks.append(' '.join(words[i:i+chunk_size]))
    
    with open(output_path, 'w', encoding='utf-8') as f:
        for i, chunk in enumerate(chunks):
            start_time = i * chunk_size * seconds_per_word
            end_time = min((i + 1) * chunk_size * seconds_per_word, audio_duration)
            
            def format_time(seconds):
                h = int(seconds // 3600)
                m = int((seconds % 3600) // 60)
                s = int(seconds % 60)
                ms = int((seconds % 1) * 1000)
                return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
            
            f.write(f"{i+1}\n")
            f.write(f"{format_time(start_time)} --> {format_time(end_time)}\n")
            f.write(f"{chunk}\n\n")
    
    return output_path


def generate_video_description(script_text, max_length=280):
    """
    Generate a social media description from script text.
    Returns a concise, engaging caption with hashtags.
    """
    clean_text = re.sub(r'\[.*?\]', '', script_text)
    clean_text = re.sub(r'\s+', ' ', clean_text).strip()
    
    sentences = re.split(r'[.!?]', clean_text)
    hook = sentences[0].strip() if sentences else clean_text[:100]
    
    if len(hook) > max_length - 50:
        hook = hook[:max_length - 50] + '...'
    
    hashtags = '#Framd #ContentCreation #VideoMaker'
    
    description = f"{hook}\n\n{hashtags}"
    
    return description[:max_length * 2]


def send_render_complete_email(user_id, video_url, project_name):
    """Send email notification when video rendering is complete."""
    try:
        from models import User, EmailNotification
        from flask import current_app
        
        with current_app.app_context():
            user = User.query.get(user_id)
            if not user or not user.email:
                return
            
            email_pref = EmailNotification.query.filter_by(
                user_id=user_id, 
                notification_type='video_ready'
            ).first()
            
            if email_pref and not email_pref.enabled:
                return
            
            domain = os.environ.get('REPLIT_DEV_DOMAIN', 'framd.app')
            full_video_url = f"https://{domain}{video_url}"
            
            print(f"[Email] Would send render complete email to {user.email} for {project_name}")
            print(f"[Email] Video URL: {full_video_url}")
            
    except Exception as e:
        print(f"[Email] Error sending notification: {e}")


def background_render_task(job_id, render_params, user_id, app_context):
    """
    Execute video render in background thread.
    
    Scope: Processes scenes, concatenates clips, adds audio, and applies template visual FX.
    Note: Captions overlay is handled by the main render pipeline for real-time preview.
    Background renders focus on quick assembly with FX for users who close their tab.
    """
    import uuid
    from app import background_render_jobs
    
    try:
        background_render_jobs[job_id]['status'] = 'rendering'
        background_render_jobs[job_id]['progress'] = 5
        
        with app_context:
            scenes = render_params.get('scenes', [])
            audio_path = render_params.get('audio_path', '')
            video_format = render_params.get('format', '9:16')
            project_name = render_params.get('project_name', 'Untitled')
            template = render_params.get('template', 'start_from_scratch')
            
            output_id = str(uuid.uuid4())[:8]
            output_path = f'output/background_{output_id}.mp4'
            os.makedirs('output', exist_ok=True)
            
            format_dims = {
                '9:16': (1080, 1920),
                '16:9': (1920, 1080),
                '1:1': (1080, 1080),
                '4:5': (1080, 1350)
            }
            width, height = format_dims.get(video_format, (1080, 1920))
            
            background_render_jobs[job_id]['progress'] = 20
            background_render_jobs[job_id]['status'] = 'processing_scenes'
            
            clip_paths = []
            for i, scene in enumerate(scenes):
                scene_url = scene.get('url', '')
                scene_duration = scene.get('duration', 4)
                scene_path = scene_url.lstrip('/') if scene_url.startswith('/') else scene_url
                
                if scene_path and os.path.exists(scene_path):
                    clip_path = f'output/bg_clip_{output_id}_{i}.mp4'
                    cmd = [
                        'ffmpeg', '-y', '-loop', '1', '-i', scene_path,
                        '-t', str(scene_duration), '-c:v', 'libx264', '-preset', 'fast',
                        '-vf', f'scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}',
                        clip_path
                    ]
                    result = subprocess.run(cmd, capture_output=True, timeout=120)
                    if result.returncode == 0 and os.path.exists(clip_path):
                        clip_paths.append(clip_path)
            
            if not clip_paths:
                background_render_jobs[job_id]['status'] = 'error'
                background_render_jobs[job_id]['error'] = 'No valid scenes to render'
                return
            
            background_render_jobs[job_id]['progress'] = 40
            background_render_jobs[job_id]['status'] = 'concatenating'
            
            concat_file = f'output/bg_concat_{output_id}.txt'
            with open(concat_file, 'w') as f:
                for clip in clip_paths:
                    f.write(f"file '{os.path.abspath(clip)}'\n")
            
            concat_output = f'output/bg_concat_{output_id}.mp4'
            concat_cmd = [
                'ffmpeg', '-y', '-f', 'concat', '-safe', '0',
                '-i', concat_file, '-c', 'copy', concat_output
            ]
            subprocess.run(concat_cmd, capture_output=True, timeout=120)
            
            background_render_jobs[job_id]['progress'] = 60
            background_render_jobs[job_id]['status'] = 'adding_audio'
            
            video_with_audio = concat_output
            if audio_path and os.path.exists(audio_path):
                video_with_audio = f'output/bg_audio_{output_id}.mp4'
                audio_cmd = [
                    'ffmpeg', '-y', '-i', concat_output, '-i', audio_path,
                    '-c:v', 'copy', '-c:a', 'aac', '-shortest', video_with_audio
                ]
                result = subprocess.run(audio_cmd, capture_output=True, timeout=120)
                if result.returncode != 0:
                    video_with_audio = concat_output
            
            background_render_jobs[job_id]['progress'] = 80
            background_render_jobs[job_id]['status'] = 'applying_fx'
            
            try:
                from context_engine import build_visual_fx_filter
                from visual_director import apply_merging_to_ffmpeg_command
                
                base_filter = build_visual_fx_filter(template)
                
                content_type_map = {
                    'hot_take': 'hot_take',
                    'explainer': 'explainer',
                    'meme_funny': 'meme',
                    'make_an_ad': 'ad',
                    'story': 'story'
                }
                content_type = content_type_map.get(template, 'general')
                
                vf_filter = apply_merging_to_ffmpeg_command(
                    base_filter,
                    content_type=content_type,
                    color_style=None,
                    film_grain=True
                )
                
                if vf_filter and vf_filter != '':
                    fx_cmd = [
                        'ffmpeg', '-y', '-i', video_with_audio,
                        '-vf', vf_filter, '-c:a', 'copy', output_path
                    ]
                    result = subprocess.run(fx_cmd, capture_output=True, timeout=300)
                    if result.returncode != 0:
                        import shutil
                        shutil.copy(video_with_audio, output_path)
                else:
                    import shutil
                    shutil.copy(video_with_audio, output_path)
            except Exception as fx_error:
                print(f"[Background Render] FX error: {fx_error}")
                import shutil
                shutil.copy(video_with_audio, output_path)
            
            for clip in clip_paths:
                if os.path.exists(clip):
                    os.remove(clip)
            if os.path.exists(concat_file):
                os.remove(concat_file)
            if os.path.exists(concat_output) and concat_output != output_path:
                os.remove(concat_output)
            if video_with_audio != concat_output and video_with_audio != output_path:
                if os.path.exists(video_with_audio):
                    os.remove(video_with_audio)
            
            background_render_jobs[job_id]['progress'] = 100
            
            if os.path.exists(output_path):
                background_render_jobs[job_id]['status'] = 'complete'
                background_render_jobs[job_id]['video_url'] = '/' + output_path
                
                send_render_complete_email(user_id, '/' + output_path, project_name)
            else:
                background_render_jobs[job_id]['status'] = 'error'
                background_render_jobs[job_id]['error'] = 'Final render failed'
                
    except Exception as e:
        background_render_jobs[job_id]['status'] = 'error'
        background_render_jobs[job_id]['error'] = str(e)
        print(f"[Background Render] Error: {e}")
        import traceback
        traceback.print_exc()
