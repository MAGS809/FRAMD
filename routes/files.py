from flask import Blueprint, request, jsonify, send_from_directory, current_app
from werkzeug.utils import secure_filename
import os
import uuid
import requests
import logging
import traceback
from extensions import db
from PIL import Image, ImageDraw, ImageFont
from urllib.parse import urlparse
import io

files_bp = Blueprint('files_bp', __name__)


@files_bp.route('/download-asset', methods=['POST'])
def download_asset():
    """Download asset on-demand for final render. Only from allowed domains."""
    data = request.get_json()
    asset_id = data.get('asset_id')
    download_url = data.get('download_url')
    
    if not download_url:
        return jsonify({'success': False, 'error': 'No download URL'}), 400
    
    allowed_domains = ['wikimedia.org', 'upload.wikimedia.org', 'archive.org', 'commons.wikimedia.org']
    parsed = urlparse(download_url)
    if not any(domain in parsed.netloc for domain in allowed_domains):
        return jsonify({'success': False, 'error': 'Download URL not from approved source'}), 403
    
    try:
        resp = requests.get(download_url, timeout=60, stream=True)
        resp.raise_for_status()
        
        ext = 'mp4' if 'video' in resp.headers.get('content-type', '') else 'webm'
        filename = f"{asset_id or uuid.uuid4()}.{ext}"
        filepath = os.path.join('output', filename)
        
        with open(filepath, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        
        return jsonify({
            'success': True,
            'local_path': filepath,
            'filename': filename
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@files_bp.route('/remove-background', methods=['POST'])
def remove_background():
    """
    Remove background from an image using threshold-based alpha extraction.
    Returns a PNG with transparent background.
    """
    import base64
    
    data = request.get_json()
    image_url = data.get('image_url')
    image_base64 = data.get('image_base64')
    character_name = data.get('character_name', 'Subject')
    
    if not image_url and not image_base64:
        return jsonify({'error': 'No image provided'}), 400
    
    try:
        if image_base64:
            image_data = base64.b64decode(image_base64)
            img = Image.open(io.BytesIO(image_data))
        else:
            allowed_domains = ['wikimedia.org', 'upload.wikimedia.org', 'unsplash.com', 'images.unsplash.com', 'pixabay.com', 'pexels.com', 'images.pexels.com']
            parsed = urlparse(image_url)
            if not any(domain in parsed.netloc for domain in allowed_domains):
                return jsonify({'error': 'Image URL not from approved source'}), 403
            
            resp = requests.get(image_url, timeout=30)
            resp.raise_for_status()
            img = Image.open(io.BytesIO(resp.content))
        
        img = img.convert('RGBA')
        pixels = img.load()
        width, height = img.size
        
        for y in range(height):
            for x in range(width):
                r, g, b, a = pixels[x, y]
                brightness = (r + g + b) / 3
                if brightness > 240:
                    pixels[x, y] = (r, g, b, 0)
                elif brightness > 220:
                    pixels[x, y] = (r, g, b, int(a * 0.5))
        
        output_filename = f"{uuid.uuid4()}_extracted.png"
        output_path = os.path.join('output', output_filename)
        img.save(output_path, 'PNG')
        
        buffered = io.BytesIO()
        img.save(buffered, format='PNG')
        result_base64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
        
        return jsonify({
            'success': True,
            'image_base64': result_base64,
            'local_path': output_path,
            'character_name': character_name,
            'dimensions': {'width': width, 'height': height}
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@files_bp.route('/generate-character-image', methods=['POST'])
def generate_character_image():
    """
    Generate a character image using DALL-E for use in video compositions.
    """
    from openai import OpenAI
    import base64
    
    data = request.get_json()
    character_description = data.get('description', '')
    character_name = data.get('name', 'Character')
    style = data.get('style', 'realistic')
    
    if not character_description:
        return jsonify({'error': 'No character description provided'}), 400
    
    try:
        client = OpenAI()
        
        style_prompts = {
            'realistic': 'photorealistic, high quality, professional photography',
            'cartoon': 'cartoon style, vibrant colors, clean lines',
            'anime': 'anime style, Japanese animation aesthetic',
            'sketch': 'pencil sketch, black and white, artistic',
            'cinematic': 'cinematic, dramatic lighting, film quality'
        }
        
        style_modifier = style_prompts.get(style, style_prompts['realistic'])
        full_prompt = f"{character_description}, {style_modifier}, portrait, clean background suitable for background removal, high contrast"
        
        response = client.images.generate(
            model="dall-e-3",
            prompt=full_prompt,
            size="1024x1024",
            quality="standard",
            n=1
        )
        
        image_url = response.data[0].url
        
        img_response = requests.get(image_url, timeout=60)
        img_response.raise_for_status()
        
        output_filename = f"{uuid.uuid4()}_character.png"
        output_path = os.path.join('output', output_filename)
        
        with open(output_path, 'wb') as f:
            f.write(img_response.content)
        
        buffered = io.BytesIO(img_response.content)
        result_base64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
        
        return jsonify({
            'success': True,
            'image_url': image_url,
            'image_base64': result_base64,
            'local_path': output_path,
            'character_name': character_name,
            'prompt_used': full_prompt
        })
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@files_bp.route('/source/preview', methods=['POST'])
def source_preview():
    """
    Generate a preview image for a source document.
    3-tier fallback: official_preview → rendered_snapshot → title_card
    """
    from bs4 import BeautifulSoup
    from models import SourceDocument
    
    data = request.get_json()
    url = data.get('url', '').strip()
    doc_type = data.get('type', 'auto')
    
    if not url:
        return jsonify({'ok': False, 'error': 'URL required'}), 400
    
    existing = SourceDocument.query.filter_by(url=url).first()
    if existing and existing.preview_image_path and os.path.exists(existing.preview_image_path):
        return jsonify({
            'ok': True,
            'method': existing.preview_method,
            'image_url': f'/output/{os.path.basename(existing.preview_image_path)}',
            'meta': {
                'title': existing.title,
                'source': existing.publisher,
                'author': existing.author,
                'date': existing.publish_date,
                'excerpts': existing.excerpts
            }
        })
    
    parsed_url = urlparse(url)
    is_pdf = url.lower().endswith('.pdf') or doc_type == 'pdf'
    
    meta = {
        'title': None,
        'source': parsed_url.netloc.replace('www.', ''),
        'author': None,
        'date': None,
        'excerpts': []
    }
    preview_method = 'title_card'
    preview_image_path = None
    og_image = None
    
    try:
        if is_pdf:
            try:
                from pdf2image import convert_from_bytes
                resp = requests.get(url, timeout=30, headers={'User-Agent': 'Mozilla/5.0'})
                resp.raise_for_status()
                
                images = convert_from_bytes(resp.content, first_page=1, last_page=1, dpi=150)
                if images:
                    preview_method = 'official_preview'
                    preview_filename = f"source_preview_{uuid.uuid4().hex[:8]}.png"
                    preview_image_path = os.path.join('output', preview_filename)
                    images[0].save(preview_image_path, 'PNG')
                    meta['title'] = url.split('/')[-1].replace('.pdf', '').replace('_', ' ').title()
            except Exception as pdf_err:
                print(f"PDF render failed: {pdf_err}")
        
        if not is_pdf and preview_method == 'title_card':
            try:
                resp = requests.get(url, timeout=15, headers={'User-Agent': 'Mozilla/5.0'})
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, 'html.parser')
                
                og_title = soup.find('meta', property='og:title')
                if og_title:
                    meta['title'] = og_title.get('content', '')[:200]
                if not meta['title']:
                    title_tag = soup.find('title')
                    if title_tag:
                        meta['title'] = title_tag.text.strip()[:200]
                
                og_site = soup.find('meta', property='og:site_name')
                if og_site:
                    meta['source'] = og_site.get('content', meta['source'])
                
                author_meta = soup.find('meta', attrs={'name': 'author'})
                if author_meta:
                    meta['author'] = author_meta.get('content', '')[:100]
                
                date_meta = soup.find('meta', property='article:published_time')
                if date_meta:
                    meta['date'] = date_meta.get('content', '')[:30]
                if not meta['date']:
                    time_tag = soup.find('time')
                    if time_tag:
                        meta['date'] = time_tag.get('datetime', time_tag.text)[:30]
                
                paragraphs = soup.find_all('p')
                for p in paragraphs[:10]:
                    text = p.get_text().strip()
                    words = text.split()
                    if 10 <= len(words) <= 40:
                        excerpt = ' '.join(words[:25])
                        if len(words) > 25:
                            excerpt += '...'
                        meta['excerpts'].append(excerpt)
                        if len(meta['excerpts']) >= 3:
                            break
                
                og_img = soup.find('meta', property='og:image')
                if og_img:
                    og_image = og_img.get('content')
                    if og_image and og_image.startswith('http'):
                        preview_method = 'official_preview'
                        preview_filename = f"source_preview_{uuid.uuid4().hex[:8]}.jpg"
                        preview_image_path = os.path.join('output', preview_filename)
                        img_resp = requests.get(og_image, timeout=15)
                        img_resp.raise_for_status()
                        with open(preview_image_path, 'wb') as f:
                            f.write(img_resp.content)
            except Exception as article_err:
                print(f"Article metadata extraction failed: {article_err}")
        
        if preview_method == 'title_card' and meta['title']:
            try:
                preview_method = 'rendered_snapshot'
                preview_filename = f"source_snapshot_{uuid.uuid4().hex[:8]}.png"
                preview_image_path = os.path.join('output', preview_filename)
                
                width, height = 800, 600
                img = Image.new('RGB', (width, height), color='#f8f9fa')
                draw = ImageDraw.Draw(img)
                
                try:
                    title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 28)
                    meta_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
                    excerpt_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
                except:
                    title_font = ImageFont.load_default()
                    meta_font = ImageFont.load_default()
                    excerpt_font = ImageFont.load_default()
                
                draw.rectangle([20, 20, width-20, height-20], outline='#dee2e6', width=2)
                draw.rectangle([30, 30, width-30, height-30], outline='#e9ecef', width=1)
                
                draw.line([(40, 100), (width-40, 100)], fill='#0a1f14', width=2)
                
                y = 50
                title_text = meta['title'][:80] + ('...' if len(meta['title']) > 80 else '')
                draw.text((50, y), title_text, fill='#0a1f14', font=title_font)
                y = 120
                
                source_line = f"{meta['source']}"
                if meta['author']:
                    source_line += f" • {meta['author'][:40]}"
                if meta['date']:
                    source_line += f" • {meta['date'][:20]}"
                draw.text((50, y), source_line, fill='#6c757d', font=meta_font)
                y += 40
                
                for excerpt in meta['excerpts'][:3]:
                    draw.text((50, y), f'"{excerpt[:100]}"', fill='#495057', font=excerpt_font)
                    y += 60
                
                draw.line([(40, height-70), (width-40, height-70)], fill='#dee2e6', width=1)
                draw.text((50, height-55), url[:90], fill='#adb5bd', font=excerpt_font)
                
                draw.rectangle([width-150, height-60, width-40, height-35], fill='#0a1f14')
                draw.text((width-140, height-55), "VERIFIED SOURCE", fill='#ffd60a', font=excerpt_font)
                
                img.save(preview_image_path, 'PNG')
            except Exception as render_err:
                print(f"Snapshot render failed: {render_err}")
                preview_method = 'title_card'
        
        if preview_method == 'title_card' or not preview_image_path:
            preview_method = 'title_card'
            preview_filename = f"source_card_{uuid.uuid4().hex[:8]}.png"
            preview_image_path = os.path.join('output', preview_filename)
            
            width, height = 600, 300
            img = Image.new('RGB', (width, height), color='#0a1f14')
            draw = ImageDraw.Draw(img)
            
            try:
                title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 24)
                meta_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
            except:
                title_font = ImageFont.load_default()
                meta_font = ImageFont.load_default()
            
            draw.text((30, 30), meta['source'].upper(), fill='#ffd60a', font=meta_font)
            
            title_display = (meta['title'] or 'Source Document')[:60]
            draw.text((30, 70), title_display, fill='white', font=title_font)
            
            if meta['date']:
                draw.text((30, 120), meta['date'], fill='#adb5bd', font=meta_font)
            draw.text((30, height-40), url[:70], fill='#6c757d', font=meta_font)
            
            img.save(preview_image_path, 'PNG')
        
        source_doc = SourceDocument.query.filter_by(url=url).first()
        if not source_doc:
            source_doc = SourceDocument(url=url)
        
        source_doc.doc_type = 'pdf' if is_pdf else 'article'
        source_doc.title = meta['title']
        source_doc.author = meta['author']
        source_doc.publisher = meta['source']
        source_doc.publish_date = meta['date']
        source_doc.preview_method = preview_method
        source_doc.preview_image_path = preview_image_path
        source_doc.excerpts = meta['excerpts']
        source_doc.og_image = og_image
        
        db.session.add(source_doc)
        db.session.commit()
        
        return jsonify({
            'ok': True,
            'method': preview_method,
            'image_url': f'/output/{os.path.basename(preview_image_path)}',
            'meta': meta
        })
        
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@files_bp.route('/cut-clip', methods=['POST'])
def cut_clip():
    """Cut a clip from a video file based on timestamps."""
    import subprocess
    
    data = request.get_json()
    video_path = data.get('video_path', '')
    start_time = data.get('start_time', '00:00')
    end_time = data.get('end_time', '00:30')
    
    if not video_path:
        return jsonify({'error': 'No video path provided'}), 400
    
    video_path = video_path.replace('/uploads/', '')
    full_path = os.path.join(current_app.config['UPLOAD_FOLDER'], video_path)
    
    if not os.path.exists(full_path):
        return jsonify({'error': 'Video file not found'}), 404
    
    try:
        clip_id = str(uuid.uuid4())[:8]
        output_path = os.path.join(current_app.config['OUTPUT_FOLDER'], f'clip_{clip_id}.mp4')
        
        cmd = [
            'ffmpeg', '-y',
            '-ss', start_time,
            '-to', end_time,
            '-i', full_path,
            '-c:v', 'libx264',
            '-c:a', 'aac',
            '-preset', 'fast',
            output_path
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        
        if os.path.exists(output_path):
            return jsonify({
                'success': True,
                'clip_url': f'/output/clip_{clip_id}.mp4',
                'start_time': start_time,
                'end_time': end_time
            })
        else:
            return jsonify({'error': 'Failed to cut clip'}), 500
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@files_bp.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    ALLOWED_EXTENSIONS = {'mp4', 'mov', 'avi', 'mkv', 'webm', 'mp3', 'wav', 'm4a'}
    
    def allowed_file(filename):
        return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS
    
    if not allowed_file(file.filename):
        return jsonify({'error': 'File type not allowed'}), 400
    
    job_id = str(uuid.uuid4())
    filename = secure_filename(file.filename)
    file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], f"{job_id}_{filename}")
    file.save(file_path)
    
    return jsonify({
        'success': True,
        'job_id': job_id,
        'filename': filename,
        'file_path': file_path
    })


@files_bp.route('/output/<filename>')
def serve_output(filename):
    return send_from_directory(current_app.config['OUTPUT_FOLDER'], filename)


@files_bp.route('/uploads/<filename>')
def serve_uploads(filename):
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], filename)


@files_bp.route('/favicon.ico')
def favicon():
    return send_from_directory('static', 'favicon.svg', mimetype='image/svg+xml')
