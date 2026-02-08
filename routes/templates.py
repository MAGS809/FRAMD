"""
Template/Creative DNA routes blueprint.
Handles video analysis, template extraction, creative DNA, reskinning, and personalization.
"""
import os
import json
import re
import time
import logging
import requests
from datetime import datetime
from flask import Blueprint, request, jsonify, session, current_app, Response
from flask_login import current_user
from concurrent.futures import ThreadPoolExecutor, as_completed

from extensions import db
from models import (
    VideoTemplate, TemplateElement, Subscription, User,
    ReskinFeedback, VisualMatch
)
from video_renderer import (
    create_whisper_synced_captions,
    create_dynamic_captions_ass,
    create_word_synced_subtitles,
    generate_video_description,
    CAPTION_TEMPLATES,
)

template_bp = Blueprint('template', __name__)


def validate_safe_path(file_path):
    """Validate file path is safe and within allowed directories."""
    if not file_path:
        return None
    normalized = os.path.normpath(file_path)
    if normalized.startswith('/') and not normalized.startswith('/home/runner'):
        if not normalized.startswith('uploads/') and not normalized.startswith('output/'):
            return None
    if '..' in normalized:
        return None
    allowed_dirs = ['uploads', 'output', 'tmp']
    path_parts = normalized.replace('\\', '/').split('/')
    if path_parts[0] not in allowed_dirs and not normalized.startswith('/home/runner'):
        for allowed in allowed_dirs:
            if allowed in path_parts:
                return normalized
        return None
    return normalized


def _rate_limit(limit=30, window=60):
    """Rate limiting decorator for template routes."""
    from app import rate_limit
    return rate_limit(limit, window)


@template_bp.route('/analyze-video', methods=['POST'])
def analyze_video():
    """Analyze an uploaded video by extracting frames and transcribing audio."""
    import base64
    import subprocess
    from openai import OpenAI

    data = request.get_json()
    file_path = data.get('file_path')

    if not file_path or not os.path.exists(file_path):
        return jsonify({'error': 'Video file not found'}), 404

    if not file_path.lower().endswith(('.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v')):
        return jsonify({'error': 'Not a video file'}), 400

    try:
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

        dur_cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', file_path]
        result = subprocess.run(dur_cmd, capture_output=True, text=True, timeout=30)
        duration = float(result.stdout.strip()) if result.stdout.strip() else 0

        frames_dir = os.path.join('uploads', 'video_frames')
        os.makedirs(frames_dir, exist_ok=True)
        frame_path = os.path.join(frames_dir, f'frame_{int(time.time())}.jpg')

        mid_point = duration / 2 if duration > 0 else 1
        extract_cmd = ['ffmpeg', '-y', '-ss', str(mid_point), '-i', file_path, '-vframes', '1', '-q:v', '2', frame_path]
        subprocess.run(extract_cmd, capture_output=True, timeout=30)

        transcript = ""
        audio_path = file_path.rsplit('.', 1)[0] + '_audio.mp3'
        audio_cmd = ['ffmpeg', '-y', '-i', file_path, '-vn', '-acodec', 'mp3', '-q:a', '4', audio_path]
        subprocess.run(audio_cmd, capture_output=True, timeout=120)

        if os.path.exists(audio_path) and os.path.getsize(audio_path) > 1000:
            try:
                with open(audio_path, 'rb') as audio_file:
                    transcription = client.audio.transcriptions.create(
                        model="whisper-1",
                        file=audio_file,
                        response_format="text"
                    )
                    transcript = transcription if isinstance(transcription, str) else str(transcription)
            except Exception as e:
                logging.warning(f"Transcription failed: {e}")
            finally:
                try:
                    os.remove(audio_path)
                except:
                    pass

        frame_analysis = None
        if os.path.exists(frame_path):
            with open(frame_path, 'rb') as f:
                frame_b64 = base64.b64encode(f.read()).decode('utf-8')

            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Describe this video frame briefly. What is shown? What's the visual style and mood?"},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{frame_b64}"}}
                    ]
                }],
                max_tokens=300
            )
            frame_analysis = response.choices[0].message.content

            try:
                os.remove(frame_path)
            except:
                pass

        description = ""
        if frame_analysis:
            description += f"Visual: {frame_analysis}"
        if transcript:
            description += f"\n\nAudio transcript: {transcript[:1000]}"

        return jsonify({
            'success': True,
            'analysis': {
                'description': description or "Video uploaded successfully",
                'duration': duration,
                'transcript': transcript[:1500] if transcript else None,
                'frame_analysis': frame_analysis,
                'mood': 'video content',
                'suggested_use': 'background',
                'content_type': 'video'
            },
            'file_path': file_path
        })

    except Exception as e:
        logging.error(f"Video analysis error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': True,
            'analysis': {
                'description': 'Video uploaded (analysis unavailable)',
                'mood': 'video',
                'suggested_use': 'background',
                'content_type': 'video'
            },
            'file_path': file_path
        })


@template_bp.route('/analyze-image', methods=['POST'])
def analyze_image():
    """Analyze an uploaded image using OpenAI GPT-4o vision."""
    import base64
    from openai import OpenAI

    data = request.get_json()
    file_path = data.get('file_path')

    if not file_path or not os.path.exists(file_path):
        return jsonify({'error': 'File not found'}), 404

    if not file_path.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp')):
        return jsonify({'error': 'Not an image file'}), 400

    try:
        with open(file_path, 'rb') as f:
            image_data = base64.b64encode(f.read()).decode('utf-8')

        ext = file_path.lower().split('.')[-1]
        mime_types = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png', 'gif': 'image/gif', 'webp': 'image/webp'}
        mime_type = mime_types.get(ext, 'image/jpeg')

        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": """Analyze this image for use in a short-form video post. Provide:
1. A brief description (1-2 sentences) of what's in the image
2. The mood/tone it conveys
3. Suggested use: 'background' (full-screen behind text) or 'popup' (overlay element)
4. Any text visible in the image

Respond in JSON format:
{
  "description": "...",
  "mood": "...",
  "suggested_use": "background" or "popup",
  "visible_text": "..." or null,
  "content_type": "photo/illustration/screenshot/graphic"
}"""
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime_type};base64,{image_data}"
                            }
                        }
                    ]
                }
            ],
            max_tokens=500
        )

        reply = response.choices[0].message.content or ""

        json_match = re.search(r'\{[^{}]*\}', reply, re.DOTALL)
        if json_match:
            analysis = json.loads(json_match.group())
        else:
            analysis = {
                "description": reply,
                "mood": "neutral",
                "suggested_use": "background",
                "visible_text": None,
                "content_type": "photo"
            }

        return jsonify({
            'success': True,
            'analysis': analysis,
            'file_path': file_path
        })

    except Exception as e:
        logging.error(f"Image analysis error: {e}")
        return jsonify({'error': str(e)}), 500


@template_bp.route('/extract-video-template', methods=['POST'])
def extract_video_template():
    """Extract template structure from an uploaded video for personalization."""
    import base64
    import subprocess
    from openai import OpenAI

    data = request.get_json()
    file_path = data.get('file_path')
    user_id = session.get('replit_user_id') or data.get('user_id')

    if not file_path or not os.path.exists(file_path):
        return jsonify({'error': 'Video file not found'}), 404

    if not file_path.lower().endswith(('.mp4', '.mov', '.avi', '.mkv', '.webm')):
        return jsonify({'error': 'Not a video file'}), 400

    try:
        dur_cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', file_path]
        result = subprocess.run(dur_cmd, capture_output=True, text=True, timeout=30)
        duration = float(result.stdout.strip()) if result.stdout.strip() else 0

        frames_dir = os.path.join('uploads', 'template_frames')
        os.makedirs(frames_dir, exist_ok=True)

        frame_timestamps = []
        num_frames = min(8, max(4, int(duration / 5)))
        for i in range(num_frames):
            ts = (duration / num_frames) * i + 0.5
            frame_timestamps.append(ts)

        frame_paths = []
        for i, ts in enumerate(frame_timestamps):
            frame_path = os.path.join(frames_dir, f'frame_{int(ts*1000)}.jpg')
            extract_cmd = [
                'ffmpeg', '-y', '-ss', str(ts), '-i', file_path,
                '-vframes', '1', '-q:v', '2', frame_path
            ]
            subprocess.run(extract_cmd, capture_output=True, timeout=30)
            if os.path.exists(frame_path):
                frame_paths.append({'path': frame_path, 'timestamp': ts})

        audio_path = file_path.replace('.mp4', '_audio.mp3').replace('.mov', '_audio.mp3')
        audio_cmd = ['ffmpeg', '-y', '-i', file_path, '-vn', '-acodec', 'mp3', '-q:a', '4', audio_path]
        subprocess.run(audio_cmd, capture_output=True, timeout=120)

        transcript = ""
        if os.path.exists(audio_path):
            try:
                client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
                with open(audio_path, 'rb') as audio_file:
                    transcription = client.audio.transcriptions.create(
                        model="whisper-1",
                        file=audio_file,
                        response_format="verbose_json"
                    )
                    transcript = transcription.text if hasattr(transcription, 'text') else str(transcription)
            except Exception as e:
                logging.warning(f"Transcription failed: {e}")

        frame_b64 = ""
        if frame_paths:
            with open(frame_paths[0]['path'], 'rb') as f:
                frame_b64 = base64.b64encode(f.read()).decode('utf-8')

        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

        analysis_prompt = f"""Analyze this video frame (first of {len(frame_paths)} frames) from a {duration:.1f} second video.

Transcript: {transcript[:1500] if transcript else 'No audio/speech detected'}

Extract the VIDEO TEMPLATE structure:
1. Visual style/aesthetic (colors, mood, energy level)
2. Estimated scene count and pacing
3. Text overlay patterns (position, style, timing)
4. Content structure (hook, body, call-to-action)
5. Transition style (cuts, fades, zooms)

Respond in JSON:
{{
  "aesthetic": {{
    "color_palette": ["primary", "secondary", "accent"],
    "mood": "energetic/calm/dramatic/playful/serious",
    "style": "minimal/bold/cinematic/social-native/professional"
  }},
  "structure": {{
    "hook_duration": 2.5,
    "total_scenes": 5,
    "pacing": "fast/medium/slow",
    "has_text_overlays": true,
    "has_call_to_action": true
  }},
  "text_patterns": {{
    "position": "center/top/bottom",
    "style": "bold/subtle/animated",
    "frequency": "every_scene/sparse/constant"
  }},
  "transitions": {{
    "type": "cut/fade/zoom/slide",
    "speed": "snappy/smooth/dramatic"
  }},
  "content_type": "ad/explainer/testimonial/meme/vlog/tutorial",
  "recommended_length": 30
}}"""

        messages = [{"role": "user", "content": [{"type": "text", "text": analysis_prompt}]}]
        if frame_b64:
            messages[0]["content"].append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{frame_b64}"}
            })

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            max_tokens=1000
        )

        reply = response.choices[0].message.content or ""

        json_match = re.search(r'\{[\s\S]*\}', reply)
        if json_match:
            template_data = json.loads(json_match.group())
        else:
            template_data = {
                "aesthetic": {"mood": "neutral", "style": "social-native"},
                "structure": {"total_scenes": num_frames, "pacing": "medium"},
                "text_patterns": {"position": "center", "style": "bold"},
                "transitions": {"type": "cut", "speed": "snappy"},
                "content_type": "general"
            }

        thumb_path = os.path.join('uploads', 'template_thumbs', f'template_{int(time.time())}.jpg')
        os.makedirs(os.path.dirname(thumb_path), exist_ok=True)
        if frame_paths:
            import shutil
            shutil.copy(frame_paths[0]['path'], thumb_path)

        scenes = []
        scene_duration = duration / max(template_data.get('structure', {}).get('total_scenes', num_frames), 1)
        for i, fp in enumerate(frame_paths):
            scenes.append({
                "index": i,
                "start_time": fp['timestamp'],
                "duration": scene_duration,
                "frame_path": fp['path'],
                "placeholder": f"[Scene {i+1} content]"
            })

        if user_id:
            template = VideoTemplate(
                user_id=user_id,
                name=f"Template {datetime.now().strftime('%m/%d %H:%M')}",
                source_video_path=file_path,
                duration=duration,
                scene_count=len(scenes),
                scenes=scenes,
                aesthetic=template_data.get('aesthetic'),
                transitions=template_data.get('transitions'),
                text_patterns=template_data.get('text_patterns'),
                audio_profile={"transcript": transcript[:2000], "has_speech": bool(transcript)},
                thumbnail_path=thumb_path
            )
            db.session.add(template)
            db.session.commit()
            template_id = template.id
        else:
            template_id = None

        for fp in frame_paths:
            try:
                os.remove(fp['path'])
            except:
                pass
        if os.path.exists(audio_path):
            try:
                os.remove(audio_path)
            except:
                pass

        return jsonify({
            'success': True,
            'template_id': template_id,
            'duration': duration,
            'scene_count': len(scenes),
            'scenes': scenes,
            'aesthetic': template_data.get('aesthetic'),
            'structure': template_data.get('structure'),
            'text_patterns': template_data.get('text_patterns'),
            'transitions': template_data.get('transitions'),
            'content_type': template_data.get('content_type'),
            'transcript': transcript[:500] if transcript else None,
            'thumbnail': thumb_path,
            'suggested_visuals': []
        })

    except Exception as e:
        logging.error(f"Video template extraction error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@template_bp.route('/extract-template-elements', methods=['POST'])
def extract_template_elements():
    """Extract template with element-level precision for frame-accurate recreation."""
    from template_engine import extract_template, ELEMENT_GROUPS
    from anthropic import Anthropic

    data = request.get_json()
    file_path = data.get('file_path')
    template_name = data.get('name', f"Template {datetime.now().strftime('%m/%d %H:%M')}")
    user_id = session.get('replit_user_id') or data.get('user_id')

    if not file_path or not os.path.exists(file_path):
        return jsonify({'error': 'Video file not found'}), 404

    try:
        anthropic_client = Anthropic()
        template_data = extract_template(
            file_path,
            template_name,
            anthropic_client=anthropic_client,
            openai_client=None
        )

        if 'error' in template_data:
            return jsonify(template_data), 400

        if user_id:
            template = VideoTemplate(
                user_id=user_id,
                name=template_name,
                source_video_path=file_path,
                duration=template_data.get('duration'),
                scene_count=len(template_data.get('transitions', [])) + 1,
                scenes=template_data.get('transitions'),
                aesthetic={'element_summary': template_data.get('element_summary')},
                transitions=template_data.get('transitions')
            )
            db.session.add(template)
            db.session.commit()

            for elem in template_data.get('elements', []):
                template_elem = TemplateElement(
                    template_id=template.id,
                    name=elem.get('name', 'unknown'),
                    display_name=elem.get('display_name'),
                    element_group=elem.get('element_group', 'visuals'),
                    element_type=elem.get('element_type', 'graphic'),
                    position_x=elem.get('position', {}).get('x', 0.5),
                    position_y=elem.get('position', {}).get('y', 0.5),
                    width=elem.get('position', {}).get('width'),
                    height=elem.get('position', {}).get('height'),
                    z_index=elem.get('z_index', 0),
                    start_time=elem.get('start_time', 0),
                    end_time=elem.get('end_time'),
                    duration=elem.get('duration'),
                    animation_in=elem.get('animation_detected'),
                    original_content=elem.get('original_content'),
                    content_description=elem.get('content_description'),
                    style_properties=elem.get('style_properties'),
                    is_swappable=elem.get('is_swappable', True),
                    swap_prompt_hint=elem.get('swap_prompt_hint')
                )
                db.session.add(template_elem)

            db.session.commit()
            template_data['template_id'] = template.id

        return jsonify({
            'success': True,
            **template_data
        })

    except Exception as e:
        logging.error(f"Template element extraction error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@template_bp.route('/generate-from-template', methods=['POST'])
def generate_from_template():
    """Generate a video by filling template element slots with AI-generated content."""
    from template_engine import generate_element_content
    from anthropic import Anthropic

    data = request.get_json()
    template_id = data.get('template_id')
    user_request = data.get('request', '')
    user_assets = data.get('assets', {})
    user_id = session.get('replit_user_id') or data.get('user_id')

    if not template_id:
        return jsonify({'error': 'Template ID required'}), 400

    try:
        anthropic_client = Anthropic()
        template = VideoTemplate.query.get(template_id)
        if not template:
            return jsonify({'error': 'Template not found'}), 404

        if template.user_id != user_id and not template.is_public:
            return jsonify({'error': 'Not authorized'}), 403

        template.usage_count = (template.usage_count or 0) + 1
        db.session.commit()

        elements = TemplateElement.query.filter_by(template_id=template_id).all()

        generated_elements = []
        for elem in elements:
            elem_dict = {
                'id': elem.id,
                'name': elem.name,
                'display_name': elem.display_name,
                'element_type': elem.element_type,
                'element_group': elem.element_group,
                'position': {
                    'x': elem.position_x,
                    'y': elem.position_y,
                    'width': elem.width,
                    'height': elem.height
                },
                'start_time': elem.start_time,
                'end_time': elem.end_time,
                'original_content': elem.original_content,
                'swap_prompt_hint': elem.swap_prompt_hint
            }

            if elem.is_swappable:
                new_content = generate_element_content(
                    elem_dict,
                    user_request,
                    user_assets=user_assets,
                    anthropic_client=anthropic_client
                )
                elem_dict['generated_content'] = new_content

            generated_elements.append(elem_dict)

        return jsonify({
            'success': True,
            'template_id': template_id,
            'template_name': template.name,
            'duration': template.duration,
            'elements': generated_elements,
            'element_count': len(generated_elements)
        })

    except Exception as e:
        logging.error(f"Template generation error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@template_bp.route('/regenerate-element', methods=['POST'])
def regenerate_element():
    """Regenerate a single element based on user feedback."""
    from template_engine import generate_element_content
    from anthropic import Anthropic

    data = request.get_json()
    element_id = data.get('element_id')
    user_instruction = data.get('instruction', '')
    user_id = session.get('replit_user_id') or data.get('user_id')

    if not element_id:
        return jsonify({'error': 'Element ID required'}), 400

    try:
        anthropic_client = Anthropic()
        elem = TemplateElement.query.get(element_id)
        if not elem:
            return jsonify({'error': 'Element not found'}), 404

        template = VideoTemplate.query.get(elem.template_id)
        if not template or (template.user_id != user_id and not template.is_public):
            return jsonify({'error': 'Not authorized'}), 403

        elem_dict = {
            'element_type': elem.element_type,
            'element_group': elem.element_group,
            'original_content': elem.original_content,
            'swap_prompt_hint': elem.swap_prompt_hint
        }

        new_content = generate_element_content(
            elem_dict,
            user_instruction,
            anthropic_client=anthropic_client
        )

        return jsonify({
            'success': True,
            'element_id': element_id,
            'element_name': elem.display_name or elem.name,
            'new_content': new_content
        })

    except Exception as e:
        logging.error(f"Element regeneration error: {e}")
        return jsonify({'error': str(e)}), 500


@template_bp.route('/get-templates', methods=['GET'])
def get_templates():
    """Get available templates for the user."""
    user_id = session.get('replit_user_id') or request.args.get('user_id')

    try:
        if user_id:
            templates = VideoTemplate.query.filter_by(user_id=user_id).order_by(VideoTemplate.created_at.desc()).limit(20).all()
        else:
            templates = VideoTemplate.query.filter_by(is_public=True).order_by(VideoTemplate.usage_count.desc()).limit(10).all()

        result = []
        for t in templates:
            element_count = TemplateElement.query.filter_by(template_id=t.id).count()
            result.append({
                'id': t.id,
                'name': t.name,
                'duration': t.duration,
                'scene_count': t.scene_count,
                'element_count': element_count,
                'thumbnail': t.thumbnail_path,
                'aesthetic': t.aesthetic,
                'usage_count': t.usage_count,
                'created_at': t.created_at.isoformat() if t.created_at else None
            })

        return jsonify({'templates': result})

    except Exception as e:
        logging.error(f"Get templates error: {e}")
        return jsonify({'error': str(e)}), 500


@template_bp.route('/get-template-elements/<int:template_id>', methods=['GET'])
def get_template_elements(template_id):
    """Get all elements for a specific template."""
    user_id = session.get('replit_user_id')

    try:
        template = VideoTemplate.query.get(template_id)
        if not template:
            return jsonify({'error': 'Template not found'}), 404

        if template.user_id != user_id and not template.is_public:
            return jsonify({'error': 'Not authorized'}), 403

        elements = TemplateElement.query.filter_by(template_id=template_id).order_by(TemplateElement.start_time).all()

        result = []
        for e in elements:
            result.append({
                'id': e.id,
                'name': e.name,
                'display_name': e.display_name,
                'element_group': e.element_group,
                'element_type': e.element_type,
                'position': {
                    'x': e.position_x,
                    'y': e.position_y,
                    'width': e.width,
                    'height': e.height
                },
                'z_index': e.z_index,
                'start_time': e.start_time,
                'end_time': e.end_time,
                'duration': e.duration,
                'animation_in': e.animation_in,
                'animation_out': e.animation_out,
                'original_content': e.original_content,
                'content_description': e.content_description,
                'style_properties': e.style_properties,
                'is_swappable': e.is_swappable
            })

        return jsonify({'elements': result})

    except Exception as e:
        logging.error(f"Get template elements error: {e}")
        return jsonify({'error': str(e)}), 500


@template_bp.route('/extract-creative-dna', methods=['POST'])
def extract_creative_dna():
    """Extract creative DNA from a video for AI Remix: preserves source video structure for reskinning.
    Uses Claude as primary vision model with OpenAI as fallback."""
    import base64
    import subprocess
    from anthropic import Anthropic
    from app import rate_limit
    from visual_director import create_visual_plan, COLOR_GRADING_PROFILES

    data = request.get_json()
    file_path = data.get('file_path')
    topic = data.get('topic', '')

    file_path = validate_safe_path(file_path)
    if not file_path or not os.path.exists(file_path):
        return jsonify({'error': 'Video file not found or invalid path'}), 404

    try:
        anthropic_client = Anthropic()

        dur_cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', file_path]
        result = subprocess.run(dur_cmd, capture_output=True, text=True, timeout=30)
        duration = float(result.stdout.strip()) if result.stdout.strip() else 30

        fps_cmd = ['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=r_frame_rate', '-of', 'csv=p=0', file_path]
        fps_result = subprocess.run(fps_cmd, capture_output=True, text=True, timeout=30)
        fps_str = fps_result.stdout.strip() if fps_result.stdout.strip() else '30/1'
        try:
            if '/' in fps_str:
                num, den = fps_str.split('/')
                fps = float(num) / float(den)
            else:
                fps = float(fps_str)
        except:
            fps = 30.0

        res_cmd = ['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height', '-of', 'csv=p=0', file_path]
        res_result = subprocess.run(res_cmd, capture_output=True, text=True, timeout=30)
        try:
            w, h = res_result.stdout.strip().split(',')
            source_width, source_height = int(w), int(h)
        except:
            source_width, source_height = 1080, 1920

        frames_dir = os.path.join('uploads', 'dna_frames')
        os.makedirs(frames_dir, exist_ok=True)

        num_scenes = max(3, min(8, int(duration / 4)))
        interval = duration / num_scenes

        frame_paths = []
        for i in range(num_scenes):
            timestamp = i * interval + (interval / 2)
            frame_path = os.path.join(frames_dir, f'dna_{int(time.time())}_{i}.jpg')
            extract_cmd = ['ffmpeg', '-y', '-ss', str(timestamp), '-i', file_path, '-vframes', '1', '-q:v', '2', frame_path]
            subprocess.run(extract_cmd, capture_output=True, timeout=30)
            if os.path.exists(frame_path):
                frame_paths.append({
                    'path': frame_path,
                    'timestamp': timestamp,
                    'start_time': i * interval,
                    'end_time': (i + 1) * interval,
                    'index': i
                })

        scenes_dna = []

        vision_prompt = """Analyze this video frame for AI Remix. The goal is to transform this video's visuals while keeping its motion and structure.

Output ONLY valid JSON:
{
    "scene_type": "talking_head/product_shot/b_roll/text_overlay/action/transition/establishing",
    "intent": "What is this scene communicating? (1 sentence)",
    "visual_description": "Detailed description of what's visually shown",
    "composition": {
        "layout": "centered/rule_of_thirds/split_screen/fullscreen",
        "subject_position": "center/left/right/top/bottom",
        "framing": "close_up/medium/wide/extreme_wide"
    },
    "colors": {
        "dominant": "#hex",
        "accent": "#hex",
        "mood": "warm/cool/neutral/vibrant/muted"
    },
    "motion_detected": "static/slow_zoom/pan/tracking/handheld/fast_motion",
    "reskin_approach": "color_grade/overlay_graphics/style_transfer/keep_with_effects",
    "reskin_reasoning": "Why this approach works for this scene",
    "has_text": true/false,
    "has_person": true/false,
    "enhancement_suggestion": "What stock/AI elements could enhance (not replace) this scene"
}"""

        def parse_vision_response(raw_text):
            """Parse JSON from vision model response."""
            cleaned = raw_text.strip()
            if cleaned.startswith('```'):
                cleaned = cleaned.split('```')[1]
                if cleaned.startswith('json'):
                    cleaned = cleaned[4:]
            return json.loads(cleaned)

        def analyze_frame_with_claude(frame_b64):
            """Analyze a single frame using Claude vision."""
            try:
                response = anthropic_client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=600,
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": frame_b64}},
                            {"type": "text", "text": vision_prompt}
                        ]
                    }]
                )
                return parse_vision_response(response.content[0].text)
            except Exception as e:
                logging.warning(f"Claude vision analysis failed: {e}")
                return None

        def analyze_frame_with_openai(frame_b64):
            """Fallback: Analyze frame using OpenAI GPT-4o vision."""
            try:
                from openai import OpenAI
                openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
                response = openai_client.chat.completions.create(
                    model="gpt-4o",
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": vision_prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{frame_b64}"}}
                        ]
                    }],
                    max_tokens=600,
                    timeout=180
                )
                return parse_vision_response(response.choices[0].message.content)
            except Exception as e:
                logging.warning(f"OpenAI vision fallback failed: {e}")
                return None

        for frame_info in frame_paths:
            with open(frame_info['path'], 'rb') as f:
                frame_b64 = base64.b64encode(f.read()).decode('utf-8')

            scene_data = analyze_frame_with_claude(frame_b64)

            if not scene_data:
                logging.info("Claude failed, trying OpenAI fallback...")
                scene_data = analyze_frame_with_openai(frame_b64)

            if not scene_data:
                logging.warning("Both vision models failed, using default scene data")
                scene_data = {
                    "scene_type": "b_roll",
                    "intent": "Visual content",
                    "visual_description": "Video content",
                    "composition": {"layout": "centered", "framing": "medium"},
                    "colors": {"dominant": "#333333", "mood": "neutral"},
                    "motion_detected": "static",
                    "reskin_approach": "color_grade",
                    "reskin_reasoning": "Default approach - apply color grading to preserve original footage",
                    "has_text": False,
                    "has_person": False,
                    "enhancement_suggestion": "Apply subtle color grading to match new topic"
                }

            scene_data['timestamp'] = frame_info['timestamp']
            scene_data['start_time'] = frame_info['start_time']
            scene_data['end_time'] = frame_info['end_time']
            scene_data['duration'] = interval
            scene_data['index'] = frame_info['index']
            scenes_dna.append(scene_data)

            try:
                os.remove(frame_info['path'])
            except:
                pass

        transcript = ""
        audio_path = file_path.rsplit('.', 1)[0] + '_dna_audio.mp3'
        audio_cmd = ['ffmpeg', '-y', '-i', file_path, '-vn', '-acodec', 'mp3', '-q:a', '4', audio_path]
        subprocess.run(audio_cmd, capture_output=True, timeout=120)

        if os.path.exists(audio_path) and os.path.getsize(audio_path) > 1000:
            try:
                from openai import OpenAI
                openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
                with open(audio_path, 'rb') as audio_file:
                    transcription = openai_client.audio.transcriptions.create(
                        model="whisper-1",
                        file=audio_file,
                        response_format="text"
                    )
                    transcript = transcription if isinstance(transcription, str) else str(transcription)
            except Exception as e:
                logging.warning(f"Transcription failed: {e}")
            finally:
                try:
                    os.remove(audio_path)
                except:
                    pass

        visual_plan = None
        if topic or transcript:
            try:
                visual_plan = create_visual_plan(
                    script=transcript or topic,
                    user_intent=topic,
                    template_type=None
                )
                logging.info(f"Visual Director plan created: content_type={visual_plan.get('content_type')}")
            except Exception as e:
                logging.warning(f"Visual Director planning failed: {e}")

        recommended_grade = 'cinematic'
        if visual_plan:
            color_mood = visual_plan.get('color_mood', 'clean_modern')
            mood_to_grade = {
                'warm_professional': 'warm',
                'clean_modern': 'cool',
                'bold_contrast': 'vibrant',
                'atmospheric': 'cinematic',
                'professional_serious': 'cool',
                'vibrant_saturated': 'vibrant',
                'brand_aligned': 'cinematic',
            }
            recommended_grade = mood_to_grade.get(color_mood, 'cinematic')

        creative_dna = {
            "total_duration": duration,
            "fps": fps,
            "source_width": source_width,
            "source_height": source_height,
            "scene_count": len(scenes_dna),
            "scenes": scenes_dna,
            "overall_style": {
                "pacing": "fast" if duration / len(scenes_dna) < 3 else "medium" if duration / len(scenes_dna) < 5 else "slow",
                "color_palette": list(set([s.get('colors', {}).get('dominant', '#333') for s in scenes_dna])),
                "dominant_motion": max(set([s.get('motion_detected', 'static') for s in scenes_dna]), key=[s.get('motion_detected', 'static') for s in scenes_dna].count) if scenes_dna else 'static'
            },
            "transcript": transcript[:2000] if transcript else None,
            "source_path": file_path,
            "visual_director_plan": visual_plan,
            "recommended_color_grade": recommended_grade,
            "remix_strategy": {
                "use_source_video": True,
                "apply_style_transfer": True,
                "overlay_graphics": True,
                "replace_scenes": False,
                "preserve_motion": True,
                "preserve_duration": True
            }
        }

        return jsonify({
            'success': True,
            'creative_dna': creative_dna
        })

    except Exception as e:
        logging.error(f"Creative DNA extraction error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@template_bp.route('/reskin-video', methods=['POST'])
def reskin_video():
    """AI Remix: Transform source video with new visual style while preserving motion and structure."""
    import subprocess
    import uuid
    import shutil
    from openai import OpenAI
    from app import rate_limit

    data = request.get_json()
    creative_dna = data.get('creative_dna', {})
    new_topic = data.get('topic', '')
    new_script = data.get('script', '')
    brand_colors = data.get('brand_colors', {})
    custom_images_raw = data.get('custom_images', [])
    voiceover_path = data.get('voiceover_path')
    caption_position = data.get('caption_position', 'bottom')
    caption_style = data.get('caption_style', 'modern')
    color_grade = data.get('color_grade', 'cinematic')

    custom_images = [validate_safe_path(p) for p in custom_images_raw if validate_safe_path(p)]
    if voiceover_path:
        voiceover_path = validate_safe_path(voiceover_path)

    if not creative_dna:
        return jsonify({'error': 'Creative DNA required'}), 400

    source_path = creative_dna.get('source_path')
    if not source_path or not os.path.exists(source_path):
        return jsonify({'error': 'Source video not found. Please re-upload the video.'}), 400

    if not new_topic and not new_script:
        return jsonify({'error': 'Topic or script required'}), 400

    try:
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        output_id = str(uuid.uuid4())[:8]
        os.makedirs('output', exist_ok=True)
        os.makedirs('uploads/remix_overlays', exist_ok=True)

        source_duration = creative_dna.get('total_duration', 30)
        source_width = creative_dna.get('source_width', 1080)
        source_height = creative_dna.get('source_height', 1920)
        scenes = creative_dna.get('scenes', [])

        logging.info(f"AI Remix starting: {len(scenes)} scenes, {source_duration:.1f}s duration")

        format_dims = {'9:16': (1080, 1920), '16:9': (1920, 1080), '1:1': (1080, 1080)}
        target_width, target_height = format_dims.get(data.get('format', '9:16'), (1080, 1920))

        color_grades = {
            'cinematic': 'eq=contrast=1.1:brightness=0.02:saturation=1.2,colorbalance=rs=0.05:gs=-0.02:bs=0.08',
            'warm': 'eq=contrast=1.05:brightness=0.03:saturation=1.1,colorbalance=rs=0.12:gs=0.05:bs=-0.05',
            'cool': 'eq=contrast=1.1:brightness=0:saturation=0.95,colorbalance=rs=-0.05:gs=0:bs=0.1',
            'vibrant': 'eq=contrast=1.15:brightness=0.02:saturation=1.4',
            'muted': 'eq=contrast=0.95:brightness=0:saturation=0.7',
            'vintage': 'eq=contrast=1.1:brightness=-0.02:saturation=0.85,colorbalance=rs=0.1:gs=0.05:bs=-0.1',
            'none': ''
        }
        grade_filter = color_grades.get(color_grade, color_grades['cinematic'])

        base_reskinned = f'output/remix_base_{output_id}.mp4'

        base_filter = f'scale={target_width}:{target_height}:force_original_aspect_ratio=increase,crop={target_width}:{target_height},setsar=1'
        if grade_filter:
            base_filter += f',{grade_filter}'

        logging.info("Step 1: Applying visual transformation to source video...")
        base_cmd = [
            'ffmpeg', '-y', '-i', source_path,
            '-vf', base_filter,
            '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
            '-c:a', 'aac', '-b:a', '192k',
            base_reskinned
        ]
        result = subprocess.run(base_cmd, capture_output=True, timeout=300)

        if result.returncode != 0:
            logging.error(f"Base transformation failed: {result.stderr.decode()}")
            return jsonify({'error': 'Failed to process source video'}), 500

        current_video = base_reskinned

        overlay_decisions = []
        pexels_key = os.environ.get("PEXELS_API_KEY")

        def generate_overlay_for_scene(scene, scene_idx):
            approach = scene.get('reskin_approach', 'color_grade')
            enhancement = scene.get('enhancement_suggestion', '')
            intent = scene.get('intent', '')
            has_person = scene.get('has_person', False)

            overlay_info = {
                'scene_index': scene_idx,
                'approach': approach,
                'overlay_path': None,
                'overlay_type': None,
                'start_time': scene.get('start_time', 0),
                'end_time': scene.get('end_time', 0),
                'duration': scene.get('duration', 3)
            }

            if approach in ['color_grade', 'keep_with_effects']:
                overlay_info['action'] = 'keep_original'
                return overlay_info

            if approach == 'overlay_graphics' and not has_person:
                try:
                    overlay_prompt = f"""Create a subtle, semi-transparent graphic overlay for: {new_topic}.
Scene context: {intent}.
Style: Modern, minimalist, suitable as video overlay.
Must be: Abstract shapes, light patterns, or decorative elements only.
No text, no faces, no solid backgrounds."""

                    dalle_response = client.images.generate(
                        model="dall-e-3",
                        prompt=overlay_prompt,
                        size="1024x1792",
                        quality="standard",
                        n=1
                    )

                    overlay_url = dalle_response.data[0].url
                    overlay_path = f'uploads/remix_overlays/overlay_{output_id}_{scene_idx}.png'
                    img_response = requests.get(overlay_url, timeout=30)
                    with open(overlay_path, 'wb') as f:
                        f.write(img_response.content)

                    overlay_info['overlay_path'] = overlay_path
                    overlay_info['overlay_type'] = 'ai_graphic'
                    overlay_info['action'] = 'blend_overlay'
                    return overlay_info

                except Exception as e:
                    logging.warning(f"Overlay generation failed for scene {scene_idx}: {e}")

            if approach == 'style_transfer' and pexels_key:
                try:
                    search_query = f"{new_topic} {enhancement or 'background'}"
                    headers = {"Authorization": pexels_key}

                    video_url = f"https://api.pexels.com/videos/search?query={search_query}&per_page=1&orientation=portrait"
                    response = requests.get(video_url, headers=headers, timeout=10)

                    if response.status_code == 200:
                        videos = response.json().get('videos', [])
                        if videos:
                            video_files = videos[0].get('video_files', [])
                            hd_files = [f for f in video_files if f.get('height', 0) >= 720]
                            if hd_files:
                                stock_url = hd_files[0].get('link')
                                stock_path = f'uploads/remix_overlays/stock_{output_id}_{scene_idx}.mp4'
                                stock_response = requests.get(stock_url, timeout=30)
                                with open(stock_path, 'wb') as f:
                                    f.write(stock_response.content)

                                overlay_info['overlay_path'] = stock_path
                                overlay_info['overlay_type'] = 'stock_video'
                                overlay_info['action'] = 'blend_video'
                                return overlay_info
                except Exception as e:
                    logging.warning(f"Stock video fetch failed: {e}")

            overlay_info['action'] = 'keep_original'
            return overlay_info

        logging.info("Step 2: Analyzing scenes for enhancements...")

        enhancement_scenes = [s for s in scenes if s.get('reskin_approach') in ['overlay_graphics', 'style_transfer']]

        if enhancement_scenes and len(enhancement_scenes) <= 3:
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = {executor.submit(generate_overlay_for_scene, scene, scene.get('index', i)): i
                          for i, scene in enumerate(enhancement_scenes)}
                for future in as_completed(futures):
                    try:
                        overlay_info = future.result()
                        if overlay_info.get('overlay_path'):
                            overlay_decisions.append(overlay_info)
                    except Exception as e:
                        logging.warning(f"Overlay generation error: {e}")

        if overlay_decisions:
            logging.info(f"Step 3: Applying {len(overlay_decisions)} enhancement overlays...")

            for overlay in overlay_decisions:
                if not overlay.get('overlay_path') or not os.path.exists(overlay['overlay_path']):
                    continue

                overlay_output = f'output/remix_overlay_{output_id}_{overlay["scene_index"]}.mp4'
                start_time = overlay.get('start_time', 0)
                end_time = overlay.get('end_time', start_time + 3)

                if overlay['overlay_type'] == 'ai_graphic':
                    overlay_filter = f"[1:v]scale={target_width}:{target_height},format=rgba,colorchannelmixer=aa=0.3[ovr];[0:v][ovr]overlay=0:0:enable='between(t,{start_time},{end_time})'"
                else:
                    overlay_filter = f"[1:v]scale={target_width}:{target_height},format=rgba,colorchannelmixer=aa=0.25[ovr];[0:v][ovr]blend=all_mode=overlay:all_opacity=0.3:enable='between(t,{start_time},{end_time})'"

                try:
                    overlay_cmd = [
                        'ffmpeg', '-y',
                        '-i', current_video,
                        '-i', overlay['overlay_path'],
                        '-filter_complex', overlay_filter,
                        '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
                        '-c:a', 'copy',
                        overlay_output
                    ]
                    result = subprocess.run(overlay_cmd, capture_output=True, timeout=120)

                    if result.returncode == 0 and os.path.exists(overlay_output):
                        if current_video != base_reskinned:
                            try:
                                os.remove(current_video)
                            except:
                                pass
                        current_video = overlay_output
                except Exception as e:
                    logging.warning(f"Overlay application failed: {e}")

        final_output = f'output/reskinned_{output_id}.mp4'

        if voiceover_path and os.path.exists(voiceover_path):
            logging.info("Step 4: Adding voiceover...")
            audio_cmd = [
                'ffmpeg', '-y',
                '-i', current_video,
                '-i', voiceover_path,
                '-c:v', 'copy',
                '-c:a', 'aac', '-b:a', '192k',
                '-map', '0:v:0', '-map', '1:a:0',
                final_output
            ]
            subprocess.run(audio_cmd, capture_output=True, timeout=300)
        else:
            shutil.copy(current_video, final_output)

        if new_script and data.get('captions_enabled', True):
            logging.info("Step 5: Adding dynamic animated captions...")
            captioned_output = f'output/reskinned_captioned_{output_id}.mp4'

            script_text = new_script
            if isinstance(new_script, dict):
                script_text = new_script.get('text', new_script.get('script', new_script.get('content', '')))
            if not isinstance(script_text, str):
                script_text = str(script_text) if script_text else ''

            if voiceover_path and os.path.exists(voiceover_path):
                dur_cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', voiceover_path]
                dur_result = subprocess.run(dur_cmd, capture_output=True, text=True, timeout=30)
                audio_duration = float(dur_result.stdout.strip()) if dur_result.stdout.strip() else source_duration
            else:
                audio_duration = source_duration

            caption_template = caption_style if caption_style in CAPTION_TEMPLATES else 'bold_pop'

            ass_path = f'output/captions_{output_id}.ass'

            whisper_success = False
            if voiceover_path and os.path.exists(voiceover_path):
                _, whisper_success = create_whisper_synced_captions(
                    voiceover_path,
                    ass_path,
                    template=caption_template,
                    position=caption_position,
                    video_width=target_width,
                    video_height=target_height
                )

            if not whisper_success:
                logging.info("Using estimated caption timing (no voiceover for Whisper sync)")
                create_dynamic_captions_ass(
                    script_text,
                    audio_duration,
                    ass_path,
                    template=caption_template,
                    position=caption_position,
                    video_width=target_width,
                    video_height=target_height
                )

            caption_cmd = [
                'ffmpeg', '-y',
                '-i', final_output,
                '-vf', f"ass={ass_path}",
                '-c:a', 'copy',
                captioned_output
            ]
            result = subprocess.run(caption_cmd, capture_output=True, timeout=600)

            if result.returncode == 0 and os.path.exists(captioned_output):
                shutil.move(captioned_output, final_output)
                logging.info(f"Captions applied with template: {caption_template} (Whisper-synced: {whisper_success})")
            else:
                logging.warning(f"ASS caption failed, falling back to SRT: {result.stderr.decode() if result.stderr else 'unknown error'}")
                srt_path = f'output/captions_{output_id}.srt'
                create_word_synced_subtitles(script_text, audio_duration, srt_path)

                fallback_cmd = [
                    'ffmpeg', '-y',
                    '-i', final_output,
                    '-vf', f"subtitles={srt_path}:force_style='FontName=Arial,FontSize=48,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=3,Shadow=2,Bold=1,MarginV=100,Alignment=2'",
                    '-c:a', 'copy',
                    captioned_output
                ]
                fallback_result = subprocess.run(fallback_cmd, capture_output=True, timeout=600)
                if fallback_result.returncode == 0 and os.path.exists(captioned_output):
                    shutil.move(captioned_output, final_output)
                if os.path.exists(srt_path):
                    os.remove(srt_path)

            if os.path.exists(ass_path):
                os.remove(ass_path)

        logging.info("Cleaning up temporary files...")
        cleanup_files = [base_reskinned]
        if current_video != base_reskinned and current_video != final_output:
            cleanup_files.append(current_video)

        for overlay in overlay_decisions:
            if overlay.get('overlay_path') and os.path.exists(overlay['overlay_path']):
                cleanup_files.append(overlay['overlay_path'])

        for f in cleanup_files:
            if f and os.path.exists(f) and f != final_output:
                try:
                    os.remove(f)
                except:
                    pass

        dur_cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', final_output]
        dur_result = subprocess.run(dur_cmd, capture_output=True, text=True, timeout=30)
        final_duration = float(dur_result.stdout.strip()) if dur_result.stdout.strip() else source_duration

        logging.info(f"AI Remix complete: {final_duration:.1f}s video created")

        return jsonify({
            'success': True,
            'video_path': '/' + final_output,
            'video_url': '/' + final_output,
            'duration': final_duration,
            'source_duration': source_duration,
            'scene_count': len(scenes),
            'color_grade_applied': color_grade,
            'overlays_applied': len([o for o in overlay_decisions if o.get('overlay_path')]),
            'approach': 'source_video_transformation',
            'creative_decisions': overlay_decisions
        })

    except Exception as e:
        logging.error(f"AI Remix error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@template_bp.route('/ai-quality-review', methods=['POST'])
def ai_quality_review():
    """AI self-reviews a generated video before showing to user."""
    import base64
    import subprocess
    from openai import OpenAI

    data = request.get_json()
    video_path = data.get('video_path')
    topic = data.get('topic', '')
    script = data.get('script', '')
    creative_dna = data.get('creative_dna', {})

    if not video_path or not os.path.exists(video_path.lstrip('/')):
        return jsonify({'error': 'Video not found'}), 404

    actual_path = video_path.lstrip('/')

    try:
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

        frames_dir = os.path.join('uploads', 'review_frames')
        os.makedirs(frames_dir, exist_ok=True)

        dur_cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', actual_path]
        result = subprocess.run(dur_cmd, capture_output=True, text=True, timeout=30)
        duration = float(result.stdout.strip()) if result.stdout.strip() else 30

        frame_paths = []
        for i, timestamp in enumerate([2, duration/2, max(duration-2, 3)]):
            frame_path = os.path.join(frames_dir, f'review_{int(time.time())}_{i}.jpg')
            extract_cmd = ['ffmpeg', '-y', '-ss', str(timestamp), '-i', actual_path, '-vframes', '1', '-q:v', '2', frame_path]
            subprocess.run(extract_cmd, capture_output=True, timeout=30)
            if os.path.exists(frame_path):
                frame_paths.append(frame_path)

        if not frame_paths:
            return jsonify({'quality_score': 0.5, 'pass': True, 'issues': ['Could not extract frames for review']})

        frame_contents = []
        for fp in frame_paths:
            with open(fp, 'rb') as f:
                frame_b64 = base64.b64encode(f.read()).decode('utf-8')
                frame_contents.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{frame_b64}"}})

        review_prompt = f"""Review this generated video for quality. The video was created for topic: "{topic}"

Script being used: {script[:500] if script else 'N/A'}

Score each aspect from 0.0 to 1.0:
1. Visual coherence - Do the scenes flow together naturally?
2. Topic alignment - Do the visuals match the topic/script?
3. Professional quality - Does it look like professional content, not stock footage slideshow?
4. Brand consistency - Is there visual consistency throughout?

Output JSON only:
{{
    "visual_coherence": 0.0-1.0,
    "topic_alignment": 0.0-1.0,
    "professional_quality": 0.0-1.0,
    "brand_consistency": 0.0-1.0,
    "overall_score": 0.0-1.0,
    "pass": true/false (true if overall >= 0.6),
    "issues": ["list of specific issues found"],
    "weak_scenes": [0, 1, 2] (indexes of scenes that need regeneration),
    "suggestions": ["how to improve"]
}}"""

        content = [{"type": "text", "text": review_prompt}] + frame_contents

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": content}],
            max_tokens=500
        )

        review_text = response.choices[0].message.content

        try:
            cleaned = review_text.strip()
            if cleaned.startswith('```'):
                cleaned = cleaned.split('```')[1]
                if cleaned.startswith('json'):
                    cleaned = cleaned[4:]
            review = json.loads(cleaned)
        except:
            review = {
                "overall_score": 0.7,
                "pass": True,
                "issues": [],
                "weak_scenes": [],
                "suggestions": []
            }

        for fp in frame_paths:
            try:
                os.remove(fp)
            except:
                pass

        return jsonify({
            'success': True,
            'review': review,
            'quality_score': review.get('overall_score', 0.7),
            'pass': review.get('pass', True),
            'issues': review.get('issues', []),
            'weak_scenes': review.get('weak_scenes', [])
        })

    except Exception as e:
        logging.error(f"AI quality review error: {e}")
        return jsonify({'quality_score': 0.6, 'pass': True, 'issues': [str(e)]})


@template_bp.route('/reskin-feedback', methods=['POST'])
def reskin_feedback():
    """Store feedback on reskinned video for global learning."""
    data = request.get_json()
    liked = data.get('liked')
    comment = data.get('comment', '')
    video_path = data.get('video_path')
    topic = data.get('topic', '')
    visual_sources = data.get('visual_sources', [])
    search_queries = data.get('search_queries', [])
    creative_dna = data.get('creative_dna', {})
    quality_scores = data.get('quality_scores', {})

    user_id = None
    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('dev_user_id')

    try:
        feedback = ReskinFeedback(
            user_id=user_id,
            source_dna=creative_dna,
            topic=topic,
            visual_sources=visual_sources,
            ai_quality_score=quality_scores.get('overall_score'),
            visual_match_score=quality_scores.get('topic_alignment'),
            brand_alignment_score=quality_scores.get('brand_consistency'),
            coherence_score=quality_scores.get('visual_coherence'),
            user_liked=liked,
            user_comment=comment,
            search_queries_used=search_queries,
            successful_visuals=[v for i, v in enumerate(visual_sources) if liked] if liked else [],
            failed_visuals=[v for i, v in enumerate(visual_sources) if not liked] if not liked else []
        )
        db.session.add(feedback)

        for i, scene in enumerate(creative_dna.get('scenes', [])):
            intent = scene.get('intent', '')
            scene_type = scene.get('scene_type', '')
            query = search_queries[i] if i < len(search_queries) else ''
            source = visual_sources[i] if i < len(visual_sources) else ''

            if intent and query:
                match = VisualMatch.query.filter_by(
                    scene_intent=intent[:500],
                    search_query=query[:500]
                ).first()

                if not match:
                    match = VisualMatch(
                        scene_intent=intent[:500],
                        scene_type=scene_type,
                        search_query=query[:500],
                        source=source
                    )
                    db.session.add(match)

                if liked:
                    match.success_count += 1
                else:
                    match.fail_count += 1

                total = match.success_count + match.fail_count
                match.success_rate = match.success_count / total if total > 0 else 0

        db.session.commit()

        return jsonify({
            'success': True,
            'message': 'Feedback recorded for global learning',
            'feedback_id': feedback.id
        })

    except Exception as e:
        logging.error(f"Reskin feedback error: {e}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@template_bp.route('/get-best-visual-match', methods=['POST'])
def get_best_visual_match():
    """Get the best visual search query for a scene intent based on global learning."""
    data = request.get_json()
    intent = data.get('intent', '')
    scene_type = data.get('scene_type', '')
    topic = data.get('topic', '')

    if not intent:
        return jsonify({'query': topic or 'professional background'})

    try:
        matches = VisualMatch.query.filter(
            VisualMatch.scene_intent.ilike(f'%{intent[:100]}%')
        ).order_by(
            VisualMatch.success_rate.desc(),
            VisualMatch.success_count.desc()
        ).limit(5).all()

        if matches and matches[0].success_rate > 0.5:
            best_query = matches[0].search_query
            if topic:
                best_query = f"{topic} {best_query}"
            return jsonify({
                'query': best_query,
                'source': 'learned',
                'confidence': matches[0].success_rate
            })

        return jsonify({
            'query': f"{topic} {scene_type}" if topic else scene_type or 'professional background',
            'source': 'default',
            'confidence': 0.0
        })

    except Exception as e:
        logging.error(f"Visual match lookup error: {e}")
        return jsonify({'query': topic or 'professional background', 'source': 'fallback'})


@template_bp.route('/render-personalized-video', methods=['POST'])
def render_personalized_video():
    """Render a personalized video: mute original audio, dub with new voiceover, add captions."""
    import subprocess
    import uuid

    user_id = None
    is_dev_mode = os.environ.get('FLASK_ENV') == 'development' or os.environ.get('DEV_MODE') == 'true'

    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('dev_user_id')

    if not is_dev_mode:
        sub = Subscription.query.filter_by(user_id=user_id).first() if user_id else None
        user = User.query.get(user_id) if user_id else None

        has_active_sub = sub and sub.is_active()
        has_free_generation = user and hasattr(user, 'free_video_generations') and (user.free_video_generations or 0) > 0

        if not has_active_sub and not has_free_generation:
            return jsonify({
                'error': 'Pro subscription required',
                'requires_subscription': True,
                'message': 'Video rendering requires a Pro subscription.'
            }), 403

        if not has_active_sub and has_free_generation:
            user.free_video_generations = max(0, (user.free_video_generations or 1) - 1)
            db.session.commit()

    data = request.get_json()
    template_path = data.get('template_path')
    template_id = data.get('template_id')
    audio_path = data.get('audio_path')
    script_text = data.get('script', '')
    captions_data = data.get('captions', {})
    video_format = data.get('format', '9:16')

    if not template_path or not os.path.exists(template_path):
        return jsonify({'error': 'Template video not found'}), 404

    if not audio_path or not os.path.exists(audio_path):
        return jsonify({'error': 'Audio file not found'}), 404

    try:
        output_id = str(uuid.uuid4())[:8]
        output_path = f'output/personalized_{output_id}.mp4'
        os.makedirs('output', exist_ok=True)

        format_dims = {
            '9:16': (1080, 1920),
            '16:9': (1920, 1080),
            '1:1': (1080, 1080),
            '4:5': (1080, 1350)
        }
        width, height = format_dims.get(video_format, (1080, 1920))

        dur_cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', template_path]
        dur_result = subprocess.run(dur_cmd, capture_output=True, text=True, timeout=30)
        video_duration = float(dur_result.stdout.strip()) if dur_result.stdout.strip() else 30

        audio_dur_cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', audio_path]
        audio_result = subprocess.run(audio_dur_cmd, capture_output=True, text=True, timeout=30)
        audio_duration = float(audio_result.stdout.strip()) if audio_result.stdout.strip() else 30

        target_duration = max(audio_duration, video_duration)

        muted_path = f'output/muted_{output_id}.mp4'
        mute_cmd = [
            'ffmpeg', '-y', '-i', template_path,
            '-an',
            '-vf', f'scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1',
            '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
            '-t', str(target_duration),
            muted_path
        ]
        subprocess.run(mute_cmd, capture_output=True, timeout=300)

        dubbed_path = f'output/dubbed_{output_id}.mp4'
        dub_cmd = [
            'ffmpeg', '-y',
            '-i', muted_path,
            '-i', audio_path,
            '-c:v', 'copy',
            '-c:a', 'aac', '-b:a', '192k',
            '-map', '0:v:0', '-map', '1:a:0',
            '-shortest',
            dubbed_path
        ]
        subprocess.run(dub_cmd, capture_output=True, timeout=300)

        captions_enabled = captions_data.get('enabled', False) if isinstance(captions_data, dict) else bool(captions_data)

        if captions_enabled and script_text:
            final_path = output_path

            full_script = script_text.get('full_script', '') if isinstance(script_text, dict) else str(script_text)

            ass_path = f'output/captions_{output_id}.ass'
            srt_path = f'output/captions_{output_id}.srt'
            whisper_success = False

            if audio_path and os.path.exists(audio_path):
                _, whisper_success = create_whisper_synced_captions(
                    audio_path,
                    ass_path,
                    template='bold_pop',
                    position='bottom',
                    video_width=width,
                    video_height=height
                )

            if not whisper_success:
                create_word_synced_subtitles(full_script, audio_duration, srt_path)

            caption_style_name = captions_data.get('style', 'modern') if isinstance(captions_data, dict) else 'modern'

            styles = {
                'modern': {'font': 'Inter-Bold', 'size': 52, 'color': 'white', 'outline': 3, 'shadow': 2},
                'minimal': {'font': 'Inter-Regular', 'size': 44, 'color': 'white', 'outline': 2, 'shadow': 0},
                'bold': {'font': 'Inter-ExtraBold', 'size': 60, 'color': 'yellow', 'outline': 4, 'shadow': 2},
                'neon': {'font': 'Inter-Bold', 'size': 54, 'color': '#00ffff', 'outline': 3, 'shadow': 4}
            }
            style = styles.get(caption_style_name, styles['modern'])

            if whisper_success and os.path.exists(ass_path):
                caption_cmd = [
                    'ffmpeg', '-y',
                    '-i', dubbed_path,
                    '-vf', f"ass={ass_path}",
                    '-c:a', 'copy',
                    final_path
                ]
                logging.info("Using Whisper-synced ASS captions")
            else:
                caption_cmd = [
                    'ffmpeg', '-y',
                    '-i', dubbed_path,
                    '-vf', f"subtitles={srt_path}:force_style='FontName={style['font']},FontSize={style['size']},PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline={style['outline']},Shadow={style['shadow']},MarginV=60'",
                    '-c:a', 'copy',
                    final_path
                ]
                logging.info("Using estimated timing SRT captions")

            result = subprocess.run(caption_cmd, capture_output=True, timeout=600)

            if result.returncode != 0:
                import shutil
                shutil.copy(dubbed_path, final_path)

            for f in [srt_path, ass_path]:
                if os.path.exists(f):
                    os.remove(f)
        else:
            import shutil
            shutil.copy(dubbed_path, output_path)

        for f in [muted_path, dubbed_path]:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except:
                    pass

        description = ""
        if script_text:
            full_script = script_text.get('full_script', '') if isinstance(script_text, dict) else str(script_text)
            description = generate_video_description(full_script[:500])

        return jsonify({
            'success': True,
            'video_path': '/' + output_path,
            'video_url': '/' + output_path,
            'url': '/' + output_path,
            'duration': target_duration,
            'format': video_format,
            'description': description,
            'template_id': template_id
        })

    except Exception as e:
        logging.error(f"Personalized video render error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@template_bp.route('/apply-template', methods=['POST'])
def apply_template():
    """Apply a video template to user's content and suggest curated visuals."""
    data = request.get_json()
    template_id = data.get('template_id')
    user_content = data.get('content', '')
    user_topic = data.get('topic', '')

    if not template_id:
        return jsonify({'error': 'Template ID required'}), 400

    template = VideoTemplate.query.get(template_id)
    if not template:
        return jsonify({'error': 'Template not found'}), 404

    try:
        from context_engine import get_ai_client

        client = get_ai_client()

        prompt = f"""You are adapting a video template to new content.

TEMPLATE INFO:
- Duration: {template.duration:.1f} seconds
- Scenes: {template.scene_count}
- Style: {template.aesthetic}
- Text patterns: {template.text_patterns}
- Original structure: {template.scenes}

USER'S NEW CONTENT:
Topic: {user_topic}
Content: {user_content[:2000]}

Create a personalized script that follows the SAME STRUCTURE and PACING as the template, but with the user's content.

For each scene, provide:
1. The adapted text/narration
2. A suggested visual concept (for AI to find a matching image)
3. Any text overlay content

Respond in JSON:
{{
  "title": "Video title",
  "scenes": [
    {{
      "index": 0,
      "narration": "Adapted narration for this scene",
      "visual_concept": "Description for image search",
      "text_overlay": "Text to show on screen",
      "duration": 3.0
    }}
  ],
  "estimated_duration": 30,
  "style_notes": "How to maintain the template's aesthetic"
}}"""

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )

        reply = response.content[0].text if response.content else ""

        json_match = re.search(r'\{[\s\S]*\}', reply)
        if json_match:
            adapted = json.loads(json_match.group())
        else:
            adapted = {"scenes": [], "error": "Could not parse response"}

        template.usage_count += 1
        db.session.commit()

        return jsonify({
            'success': True,
            'adapted_content': adapted,
            'template_aesthetic': template.aesthetic,
            'template_transitions': template.transitions
        })

    except Exception as e:
        logging.error(f"Template application error: {e}")
        return jsonify({'error': str(e)}), 500


@template_bp.route('/my-templates', methods=['GET'])
def get_my_templates():
    """Get user's saved video templates."""
    user_id = session.get('replit_user_id')
    if not user_id:
        return jsonify({'templates': []})

    templates = VideoTemplate.query.filter_by(user_id=user_id).order_by(VideoTemplate.created_at.desc()).limit(20).all()

    return jsonify({
        'templates': [{
            'id': t.id,
            'name': t.name,
            'duration': t.duration,
            'scene_count': t.scene_count,
            'aesthetic': t.aesthetic,
            'thumbnail': t.thumbnail_path,
            'usage_count': t.usage_count,
            'created_at': t.created_at.isoformat() if t.created_at else None
        } for t in templates]
    })
