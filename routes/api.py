"""
API routes blueprint.
Handles /api/jobs endpoints for video generation job queue,
background render jobs, and export/promo functionality.
"""
import os
import json
import logging
from flask import Blueprint, request, jsonify, session
from flask_login import current_user

from models import Project
from job_queue import JOB_QUEUE
from routes.utils import get_user_id, format_user_error

api_bp = Blueprint('api', __name__)


@api_bp.route('/api/jobs', methods=['POST'])
def api_create_job():
    """Create a new video generation job."""
    user_id = get_user_id()
    if not user_id:
        return jsonify({'ok': False, 'error': 'Not authenticated'}), 401
    
    data = request.get_json()
    project_id = data.get('project_id')
    quality_tier = data.get('quality_tier', 'good')
    job_data = data.get('job_data', {})
    
    if not project_id:
        return jsonify({'ok': False, 'error': 'Project ID required'}), 400
    
    project = Project.query.filter_by(id=project_id, user_id=user_id).first()
    if not project:
        return jsonify({'ok': False, 'error': 'Project not found'}), 404
    
    job_id = JOB_QUEUE.add_job(
        user_id=user_id,
        project_id=project_id,
        quality_tier=quality_tier,
        job_data=job_data
    )
    
    job = JOB_QUEUE.get_job(job_id)
    
    return jsonify({
        'ok': True,
        'job': JOB_QUEUE.to_dict(job)
    })


@api_bp.route('/api/jobs/<int:job_id>', methods=['GET'])
def api_get_job(job_id):
    """Get job status and progress."""
    user_id = get_user_id()
    if not user_id:
        return jsonify({'ok': False, 'error': 'Not authenticated'}), 401
    
    job = JOB_QUEUE.get_job(job_id)
    if not job:
        return jsonify({'ok': False, 'error': 'Job not found'}), 404
    
    if job.user_id != user_id:
        return jsonify({'ok': False, 'error': 'Not authorized'}), 403
    
    position = JOB_QUEUE.get_queue_position(job_id) if job.status == 'pending' else 0
    
    return jsonify({
        'ok': True,
        'job': JOB_QUEUE.to_dict(job),
        'queue_position': position
    })


@api_bp.route('/api/jobs', methods=['GET'])
def api_get_user_jobs():
    """Get all jobs for the current user."""
    user_id = get_user_id()
    if not user_id:
        return jsonify({'ok': False, 'error': 'Not authenticated'}), 401
    
    active_only = request.args.get('active', 'false').lower() == 'true'
    
    if active_only:
        jobs = JOB_QUEUE.get_active_jobs(user_id)
    else:
        jobs = JOB_QUEUE.get_user_jobs(user_id, limit=20)
    
    return jsonify({
        'ok': True,
        'jobs': [JOB_QUEUE.to_dict(job) for job in jobs]
    })


@api_bp.route('/api/jobs/<int:job_id>/cancel', methods=['POST'])
def api_cancel_job(job_id):
    """Cancel a pending job."""
    user_id = get_user_id()
    if not user_id:
        return jsonify({'ok': False, 'error': 'Not authenticated'}), 401
    
    success = JOB_QUEUE.cancel_job(job_id, user_id)
    
    if success:
        return jsonify({'ok': True, 'message': 'Job cancelled'})
    else:
        return jsonify({'ok': False, 'error': 'Cannot cancel job (may already be processing)'}), 400


@api_bp.route('/api/jobs/stats', methods=['GET'])
def api_queue_stats():
    """Get overall queue statistics (admin)."""
    stats = JOB_QUEUE.get_queue_stats()
    
    return jsonify({
        'ok': True,
        'stats': stats
    })


@api_bp.route('/api/projects', methods=['GET'])
def api_get_projects():
    user_id = get_user_id()
    if not user_id:
        return jsonify({'ok': False, 'error': 'Not authenticated'}), 401
    
    projects = Project.query.filter_by(user_id=user_id).order_by(Project.updated_at.desc()).limit(50).all()
    
    return jsonify({
        'ok': True,
        'projects': [{
            'id': p.id,
            'name': p.name,
            'mode': p.template_type,
            'status': p.status,
            'duration': 0,
            'thumbnail': None,
            'created_at': p.created_at.isoformat() if p.created_at else None,
            'updated_at': p.updated_at.isoformat() if p.updated_at else None
        } for p in projects]
    })


@api_bp.route('/start-background-render', methods=['POST'])
def start_background_render():
    """Start a video render in the background using database-backed job queue."""
    import uuid
    
    user_id = None
    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('dev_user_id')
    
    data = request.get_json() or {}
    
    project_id = data.get('project_id')
    quality_tier = data.get('quality_tier', 'good')
    
    job_data = {
        'scenes': data.get('scenes', []),
        'audio_path': data.get('audio_path', ''),
        'format': data.get('format', '9:16'),
        'captions': data.get('captions', {}),
        'script': data.get('script', ''),
        'project_name': data.get('project_name', 'Untitled'),
        'template': data.get('template', 'start_from_scratch')
    }
    
    job = JOB_QUEUE.add_job(
        user_id=user_id,
        project_id=project_id,
        quality_tier=quality_tier,
        job_data=job_data
    )
    
    if job:
        return jsonify({
            'success': True,
            'ok': True,
            'job_id': job['id'],
            'job': job,
            'message': 'Video rendering started. You can continue working while it processes.'
        })
    else:
        return jsonify({
            'success': False,
            'ok': False,
            'error': 'Failed to create job'
        }), 500


@api_bp.route('/render-status/<job_id>', methods=['GET'])
def get_render_status(job_id):
    """Check the status of a background render job."""
    from app import background_render_jobs
    
    job = JOB_QUEUE.get_job(job_id)
    
    if not job:
        if job_id in background_render_jobs:
            old_job = background_render_jobs[job_id]
            return jsonify({
                'status': old_job['status'],
                'progress': old_job['progress'],
                'video_url': old_job['video_url'],
                'error': old_job['error']
            })
        return jsonify({'error': 'Job not found'}), 404
    
    return jsonify({
        'status': job['status'],
        'progress': job.get('progress', {}).get('percent', 0),
        'video_url': job.get('result_url'),
        'error': job.get('error_message'),
        'job': job
    })


@api_bp.route('/my-render-jobs', methods=['GET'])
def get_my_render_jobs():
    """Get all render jobs for the current user."""
    from app import background_render_jobs
    
    user_id = None
    if current_user.is_authenticated:
        user_id = current_user.id
    else:
        user_id = session.get('dev_user_id')
    
    if not user_id:
        return jsonify([])
    
    user_jobs = []
    for job_id, job in background_render_jobs.items():
        if job.get('user_id') == user_id:
            user_jobs.append({
                'job_id': job_id,
                'status': job['status'],
                'progress': job['progress'],
                'video_url': job['video_url'],
                'created_at': job.get('created_at')
            })
    
    user_jobs.sort(key=lambda x: x.get('created_at', 0), reverse=True)
    
    return jsonify(user_jobs[:10])


@api_bp.route('/api/job/<job_id>/status', methods=['GET'])
def api_job_status(job_id):
    from app import background_render_jobs
    
    if job_id in background_render_jobs:
        job = background_render_jobs[job_id]
        return jsonify({
            'ok': True,
            'status': job.get('status', 'unknown'),
            'progress': job.get('progress', 0),
            'message': job.get('status', 'Processing...').replace('_', ' ').title(),
            'video_url': job.get('video_url'),
            'error': job.get('error')
        })
    return jsonify({'ok': False, 'error': 'Job not found'}), 404


@api_bp.route('/export-platform-format', methods=['POST'])
def export_platform_format():
    """Export video in platform-specific format with caption styles and post optimization."""
    import subprocess
    import uuid
    from context_engine import call_ai
    from PIL import Image, ImageDraw, ImageFont
    
    data = request.get_json() or {}
    video_url = data.get('video_url', '')
    platform = data.get('platform', 'tiktok')
    caption_style = data.get('caption_style', 'bold_centered')
    is_post_platform = data.get('is_post_platform', False)
    carousel_count = data.get('carousel_count', 5)
    script_text = data.get('script_text', '')
    project_id = data.get('project_id')
    
    if not video_url:
        return jsonify({'success': False, 'error': 'No video URL provided', 'platform': platform}), 400
    
    source_path = video_url.lstrip('/')
    possible_paths = [
        source_path,
        os.path.join('output', os.path.basename(source_path)),
        source_path.replace('/output/', 'output/')
    ]
    
    actual_path = None
    for path in possible_paths:
        if os.path.exists(path):
            actual_path = path
            break
    
    if not actual_path:
        return jsonify({'success': False, 'error': f'Video not found for {platform}', 'platform': platform}), 404
    
    platform_configs = {
        'tiktok': {'width': 1080, 'height': 1920, 'ratio': '9:16', 'caption_y': 0.35},
        'ig_reels': {'width': 1080, 'height': 1920, 'ratio': '9:16', 'caption_y': 0.40},
        'yt_shorts': {'width': 1080, 'height': 1920, 'ratio': '9:16', 'caption_y': 0.45},
        'ig_feed': {'width': 1080, 'height': 1350, 'ratio': '4:5', 'caption_y': 0.50},
        'ig_carousel': {'width': 1080, 'height': 1350, 'ratio': '4:5'},
        'twitter': {'width': 1920, 'height': 1080, 'ratio': '16:9', 'caption_y': 0.80},
        'instagram': {'width': 1080, 'height': 1920, 'ratio': '9:16', 'caption_y': 0.40},
        'youtube': {'width': 1080, 'height': 1920, 'ratio': '9:16', 'caption_y': 0.45}
    }
    
    config = platform_configs.get(platform, platform_configs['tiktok'])
    output_id = str(uuid.uuid4())[:8]
    
    try:
        if platform == 'ig_carousel':
            images = generate_carousel_images(actual_path, carousel_count, script_text, output_id)
            return jsonify({
                'success': True,
                'images': images,
                'platform': platform,
                'format': config['ratio']
            })
        
        output_path = f'output/{platform}_{output_id}.mp4'
        
        vf_filters = [f"scale={config['width']}:{config['height']}:force_original_aspect_ratio=decrease",
                      f"pad={config['width']}:{config['height']}:(ow-iw)/2:(oh-ih)/2"]
        
        cmd = [
            'ffmpeg', '-y', '-i', actual_path,
            '-vf', ','.join(vf_filters),
            '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
            '-c:a', 'aac', '-b:a', '128k',
            output_path
        ]
        
        result = subprocess.run(cmd, capture_output=True, timeout=300)
        
        if result.returncode == 0 and os.path.exists(output_path):
            response_data = {
                'success': True,
                'video_path': '/' + output_path,
                'platform': platform,
                'format': config['ratio']
            }
            
            if is_post_platform and script_text:
                try:
                    platform_name = {'ig_feed': 'Instagram Feed', 'twitter': 'Twitter/X'}.get(platform, platform)
                    caption_prompt = f"""Generate an optimized caption for {platform_name} based on this video script:

{script_text}

Research what works on {platform_name} right now and create:
1. A hook that grabs attention
2. The main message (concise)
3. A call-to-action
4. 3-5 relevant hashtags

Respond with ONLY the caption text ready to post (include hashtags at the end)."""
                    
                    ai_caption = call_ai(caption_prompt, max_tokens=300)
                    response_data['suggested_caption'] = ai_caption.strip()
                except Exception as e:
                    print(f"Caption generation failed: {e}")
            
            return jsonify(response_data)
        else:
            error_msg = result.stderr.decode()[:200] if result.stderr else 'Unknown error'
            print(f"FFmpeg error for {platform}: {error_msg}")
            return jsonify({'success': False, 'error': f'Export failed for {platform}', 'platform': platform}), 500
            
    except Exception as e:
        print(f"Platform export error for {platform}: {e}")
        return jsonify({'success': False, 'error': format_user_error(str(e)), 'platform': platform}), 500


def generate_carousel_images(video_path, count, script_text, output_id):
    """Generate carousel images from video frames with text overlays."""
    import subprocess
    from PIL import Image, ImageDraw, ImageFont
    from context_engine import call_ai
    
    count = max(2, min(10, int(count or 5)))
    
    os.makedirs('output/carousel', exist_ok=True)
    images = []
    
    try:
        probe_cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                     '-of', 'default=noprint_wrappers=1:nokey=1', video_path]
        duration = float(subprocess.run(probe_cmd, capture_output=True, text=True).stdout.strip() or '10')
    except:
        duration = 10
    
    if not script_text or len(script_text.strip()) < 10:
        slides = [{"text": f"Slide {i+1}", "timestamp": (i + 0.5) / count} for i in range(count)]
    else:
        try:
            slide_prompt = f"""Create {count} Instagram carousel slides from this script:

{script_text}

For each slide, provide:
- "text": Short, impactful text for the slide (max 80 chars)
- "timestamp": Approximate position in video (0.0 to 1.0) for the frame

Return JSON array only:
[{{"text": "...", "timestamp": 0.1}}, ...]"""
            
            ai_response = call_ai(slide_prompt, max_tokens=800)
            ai_response = ai_response.strip()
            if '```' in ai_response:
                ai_response = ai_response.split('```')[1].replace('json', '').strip()
            slides = json.loads(ai_response)
        except Exception as e:
            print(f"AI slide generation failed: {e}")
            slides = [{"text": f"Slide {i+1}", "timestamp": (i + 0.5) / count} for i in range(count)]
    
    for i, slide in enumerate(slides[:count]):
        raw_timestamp = slide.get('timestamp', (i + 0.5) / count)
        clamped_timestamp = max(0.0, min(1.0, float(raw_timestamp)))
        timestamp = clamped_timestamp * duration
        text = slide.get('text', f'Slide {i+1}')[:100]
        frame_path = f'output/carousel/frame_{output_id}_{i}.png'
        output_path = f'output/carousel/slide_{output_id}_{i}.png'
        
        try:
            extract_cmd = ['ffmpeg', '-y', '-ss', str(timestamp), '-i', video_path,
                          '-vframes', '1', '-s', '1080x1350', frame_path]
            subprocess.run(extract_cmd, capture_output=True, timeout=30)
            
            if os.path.exists(frame_path):
                img = Image.open(frame_path)
                draw = ImageDraw.Draw(img)
                
                try:
                    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 48)
                except:
                    font = ImageFont.load_default()
                
                bbox = draw.textbbox((0, 0), text, font=font)
                text_width = bbox[2] - bbox[0]
                text_height = bbox[3] - bbox[1]
                x = (1080 - text_width) // 2
                y = 1350 - text_height - 100
                
                for dx, dy in [(-2, -2), (-2, 2), (2, -2), (2, 2)]:
                    draw.text((x + dx, y + dy), text, font=font, fill='black')
                draw.text((x, y), text, font=font, fill='white')
                
                img.save(output_path)
                images.append('/' + output_path)
                
                if os.path.exists(frame_path):
                    os.remove(frame_path)
        except Exception as e:
            print(f"Carousel slide {i} failed: {e}")
    
    return images


@api_bp.route('/generate-promo-pack', methods=['POST'])
def generate_promo_pack():
    """Generate promotional content from video script."""
    from context_engine import call_ai
    
    data = request.get_json() or {}
    script = data.get('script', '')
    
    if not script:
        return jsonify({'error': 'No script provided'}), 400
    
    try:
        prompt = f"""Analyze this video script and generate promotional content:

Script:
{script}

Generate a JSON response with:
1. "quote_cards": Array of 3-4 powerful standalone quotes from the script. Each has:
   - "quote": The exact quote (max 100 chars)
   - "bg_color": A hex color for background
   - "accent_color": A complementary hex color

2. "has_humor": Boolean - is this content funny/memeable?

3. "memes": If has_humor is true, array of 2-3 meme ideas with:
   - "top_text": Top meme text
   - "bottom_text": Bottom meme text
   - "format": Meme format name (e.g., "Drake", "Distracted Boyfriend", "Change My Mind")

4. "infographics": Array of 2-3 key statistics or facts with:
   - "stat": The number or key stat (e.g., "73%", "2.5x")
   - "label": Brief description (max 50 chars)

Only include memes array if the content genuinely has humor potential.
Respond with ONLY valid JSON, no markdown."""

        response = call_ai(prompt, max_tokens=1500)
        
        try:
            response_text = response.strip()
            if response_text.startswith('```'):
                response_text = response_text.split('```')[1]
                if response_text.startswith('json'):
                    response_text = response_text[4:]
            
            promo_data = json.loads(response_text)
            
            return jsonify({
                'success': True,
                'quote_cards': promo_data.get('quote_cards', []),
                'memes': promo_data.get('memes', []) if promo_data.get('has_humor') else [],
                'infographics': promo_data.get('infographics', []),
                'has_humor': promo_data.get('has_humor', False)
            })
            
        except json.JSONDecodeError:
            lines = [l.strip() for l in script.split('\n') if l.strip() and not l.startswith('[')]
            quotes = lines[:3] if len(lines) >= 3 else lines
            
            return jsonify({
                'success': True,
                'quote_cards': [{'quote': q[:100], 'bg_color': '#1a1a2e', 'accent_color': '#16213e'} for q in quotes],
                'memes': [],
                'infographics': [{'stat': str(len(lines)), 'label': 'Key points covered'}],
                'has_humor': False
            })
            
    except Exception as e:
        print(f"Promo pack error: {e}")
        return jsonify({'error': format_user_error(str(e))}), 500


@api_bp.route('/download-promo-pack', methods=['POST'])
def download_promo_pack():
    """Generate downloadable promo assets."""
    import zipfile
    import uuid
    from PIL import Image, ImageDraw, ImageFont
    
    data = request.get_json() or {}
    approved_items = data.get('approved_items', [])
    promo_data = data.get('promo_data', {})
    
    if not approved_items:
        return jsonify({'error': 'No items selected'}), 400
    
    try:
        pack_id = str(uuid.uuid4())[:8]
        pack_dir = f'output/promo_pack_{pack_id}'
        os.makedirs(pack_dir, exist_ok=True)
        
        generated_files = []
        
        def hex_to_rgb(hex_color):
            hex_color = hex_color.lstrip('#')
            return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
        
        def create_gradient(size, color1, color2):
            img = Image.new('RGB', size)
            for y in range(size[1]):
                r = int(color1[0] + (color2[0] - color1[0]) * y / size[1])
                g = int(color1[1] + (color2[1] - color1[1]) * y / size[1])
                b = int(color1[2] + (color2[2] - color1[2]) * y / size[1])
                for x in range(size[0]):
                    img.putpixel((x, y), (r, g, b))
            return img
        
        for item_key in approved_items:
            item_type, idx = item_key.split('-')
            idx = int(idx)
            
            try:
                font_large = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 56)
                font_med = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 40)
                font_small = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 28)
            except:
                font_large = ImageFont.load_default()
                font_med = font_large
                font_small = font_large
            
            if item_type == 'quote' and idx < len(promo_data.get('quote_cards', [])):
                card = promo_data['quote_cards'][idx]
                bg_color = hex_to_rgb(card.get('bg_color', '#1a1a2e'))
                accent_color = hex_to_rgb(card.get('accent_color', '#16213e'))
                img = create_gradient((1080, 1080), bg_color, accent_color)
                draw = ImageDraw.Draw(img)
                quote_text = f'"{card.get("quote", "")}"'
                words = quote_text.split()
                lines = []
                current_line = ""
                for word in words:
                    test_line = current_line + " " + word if current_line else word
                    if len(test_line) > 30:
                        lines.append(current_line)
                        current_line = word
                    else:
                        current_line = test_line
                if current_line:
                    lines.append(current_line)
                y_offset = 540 - (len(lines) * 35)
                for line in lines:
                    draw.text((540, y_offset), line, fill='white', font=font_med, anchor='mm')
                    y_offset += 70
                draw.text((540, 1000), "framd.io", fill=(255, 255, 255, 128), font=font_small, anchor='mm')
                
            elif item_type == 'meme' and idx < len(promo_data.get('memes', [])):
                meme = promo_data['memes'][idx]
                img = Image.new('RGB', (1080, 1080), color='#000000')
                draw = ImageDraw.Draw(img)
                top = meme.get('top_text', '').upper()
                bottom = meme.get('bottom_text', '').upper()
                for offset in [(-3,-3), (-3,3), (3,-3), (3,3), (-3,0), (3,0), (0,-3), (0,3)]:
                    draw.text((540+offset[0], 80+offset[1]), top, fill='black', font=font_large, anchor='mm')
                    draw.text((540+offset[0], 1000+offset[1]), bottom, fill='black', font=font_large, anchor='mm')
                draw.text((540, 80), top, fill='white', font=font_large, anchor='mm')
                draw.text((540, 1000), bottom, fill='white', font=font_large, anchor='mm')
                draw.text((540, 540), f"[{meme.get('format', 'Meme')}]", fill='#666666', font=font_small, anchor='mm')
                
            elif item_type == 'info' and idx < len(promo_data.get('infographics', [])):
                info = promo_data['infographics'][idx]
                img = create_gradient((1080, 1080), (10, 31, 20), (26, 61, 42))
                draw = ImageDraw.Draw(img)
                draw.text((540, 400), info.get('stat', ''), fill='#ffd60a', font=font_large, anchor='mm')
                draw.text((540, 520), info.get('label', ''), fill='white', font=font_med, anchor='mm')
                draw.text((540, 1000), "framd.io", fill=(255, 255, 255, 128), font=font_small, anchor='mm')
            else:
                continue
            
            img_path = f'{pack_dir}/{item_type}_{idx}.png'
            img.save(img_path)
            generated_files.append(img_path)
        
        zip_path = f'output/promo_pack_{pack_id}.zip'
        with zipfile.ZipFile(zip_path, 'w') as zipf:
            for file_path in generated_files:
                zipf.write(file_path, os.path.basename(file_path))
        
        import shutil
        shutil.rmtree(pack_dir, ignore_errors=True)
        
        return jsonify({
            'success': True,
            'download_url': '/' + zip_path
        })
        
    except Exception as e:
        print(f"Promo pack download error: {e}")
        return jsonify({'error': format_user_error(str(e))}), 500


@api_bp.route('/download-reference', methods=['POST'])
def download_reference():
    """Download a video from URL and optionally analyze it as a reference."""
    import subprocess
    import uuid
    from flask import current_app
    from context_engine import extract_audio, transcribe_audio
    
    data = request.get_json()
    url = data.get('url', '').strip()
    
    if not url:
        return jsonify({'error': 'No URL provided'}), 400
    
    try:
        job_id = str(uuid.uuid4())[:8]
        output_path = os.path.join(current_app.config['UPLOAD_FOLDER'], f'reference_{job_id}.mp4')
        
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
            direct_path = os.path.join(current_app.config['UPLOAD_FOLDER'], f'reference_{job_id}_direct.mp4')
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
