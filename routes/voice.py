"""
Voice/voiceover routes blueprint.
Handles voice preview, voiceover generation, multi-character voiceovers,
and character line extraction.
"""
import os
import uuid
import logging
from io import BytesIO
from flask import Blueprint, request, jsonify, session, current_app, Response
from flask_login import current_user

from audio_engine import extract_dialogue_only, parse_character_lines, assemble_audio_clips

voice_bp = Blueprint('voice', __name__)


CHARACTER_VOICE_CONFIG = {
    'the_anchor': {
        'base_voice': 'onyx',
        'elevenlabs_voice_id': 'pNInz6obpgDQGcFmaJgB',
        'prompt': "You are a professional news anchor delivering breaking news. Speak with authority, gravitas, and measured pacing. Be serious, credible, and commanding. Use the classic newsroom delivery style."
    },
    'british_authority': {
        'base_voice': 'nova',
        'elevenlabs_voice_id': 'Xb7hH8MSUJpSbSDYk0k2',
        'prompt': "You are a confident British news presenter. Speak with authority, poise, and the gravitas of a seasoned broadcaster. Your delivery is polished and commanding."
    },
    'the_storyteller': {
        'base_voice': 'onyx',
        'elevenlabs_voice_id': 'nPczCjzI2devNBz1zQrb',
        'prompt': "You are a masterful storyteller with warmth and emotional depth. Speak with perfect pacing, build tension naturally, and let moments land. Your voice makes people feel connected to the narrative."
    },
    'aussie_casual': {
        'base_voice': 'fable',
        'elevenlabs_voice_id': 'IKne3meq5aSn9XLyUdCD',
        'prompt': "You are a laid-back Australian narrator with natural charisma. Speak casually but engagingly, like you're sharing an interesting story with a friend. Keep it real and relatable."
    },
    'power_exec': {
        'base_voice': 'nova',
        'elevenlabs_voice_id': 'EXAVITQu4vr4xnSDxMaL',
        'prompt': "You are a powerful female executive - confident, sharp, no-nonsense. Speak with authority and precision. Every word is deliberate. You command respect and radiate competence."
    },
    'documentary_pro': {
        'base_voice': 'onyx',
        'elevenlabs_voice_id': 'ZQe5CZNOzWyzPSCn5a3c',
        'prompt': "You are a prestigious documentary narrator. Speak with calm authority and gravitas. Deep, measured, thoughtful. Every fact lands with weight. You educate and captivate simultaneously."
    },
    'hype_machine': {
        'base_voice': 'alloy',
        'elevenlabs_voice_id': 'TX3LPaxmHKxFdv7VOQHJ',
        'prompt': "You are an energetic hype machine! Speak with maximum energy, excitement, and urgency. Build hype! Use phrases like 'let's go', 'are you ready', 'this is gonna be huge'. Be the energy the room needs!"
    },
    'cinema_epic': {
        'base_voice': 'onyx',
        'elevenlabs_voice_id': 'JBFqnCBsd6RMkjVDRZzb',
        'prompt': "You are the epic movie trailer voice. Deep, resonant, dramatic. Build tension with pauses. Every line lands like a dramatic reveal. Be EPIC and cinematic!"
    },
    'whisper_intimate': {
        'base_voice': 'shimmer',
        'elevenlabs_voice_id': 'piTKgcLEGmPE4e6mEKli',
        'prompt': "You speak in a soft, intimate whisper. Gentle, calming, and deeply personal. Every word is like a secret shared just with the listener. Create a sense of closeness and comfort."
    },
    'zen_guide': {
        'base_voice': 'shimmer',
        'elevenlabs_voice_id': 'LcfcDJNUP1GQjkzn1xUU',
        'prompt': "You are a meditation and wellness guide. Speak with serenity, calm, and gentle wisdom. Your voice brings peace and clarity. Guide the listener to a place of inner stillness."
    },
    'warm_narrator': {
        'base_voice': 'nova',
        'elevenlabs_voice_id': 'XrExE9yKIg1WjnnlVkGX',
        'prompt': "You are a warm, inviting narrator perfect for audiobooks and heartfelt content. Speak with genuine warmth and emotional connection. Make listeners feel at home with your voice."
    },
    'countdown_king': {
        'base_voice': 'echo',
        'elevenlabs_voice_id': 'VR6AewLTigWG4xSOukaG',
        'prompt': "You are the voice of countdown and ranking videos. Build anticipation with each number. Every reveal is exciting. Keep the energy climbing as you count down. Think WatchMojo energy!"
    },
    'custom': {
        'base_voice': 'alloy',
        'elevenlabs_voice_id': 'JBFqnCBsd6RMkjVDRZzb',
        'prompt': "You are a professional voiceover artist. Read the following script naturally and engagingly with perfect pacing and clarity."
    }
}

ELEVENLABS_VOICE_SETTINGS = {
    'stability': 0.25,
    'similarity_boost': 0.85,
    'style': 0.85,
    'use_speaker_boost': True
}

def get_voice_config(voice):
    """Get base voice, ElevenLabs voice ID, and system prompt for a voice type."""
    if voice in CHARACTER_VOICE_CONFIG:
        config = CHARACTER_VOICE_CONFIG[voice]
        return config['base_voice'], config.get('elevenlabs_voice_id', 'JBFqnCBsd6RMkjVDRZzb'), config['prompt']
    return voice, 'JBFqnCBsd6RMkjVDRZzb', "You are a professional voiceover artist. Read the following script naturally and engagingly."


@voice_bp.route('/preview-voice-chars', methods=['POST'])
def preview_voice_chars():
    """Preview how many characters will be sent to voice API - helps user estimate cost."""
    data = request.get_json()
    script = data.get('script', '')

    if not script:
        return jsonify({'chars': 0, 'dialogue': ''})

    dialogue = extract_dialogue_only(script)

    return jsonify({
        'chars': len(dialogue),
        'dialogue': dialogue[:500] + ('...' if len(dialogue) > 500 else ''),
        'estimated_cost': f"~{len(dialogue)} characters for ElevenLabs"
    })


@voice_bp.route('/estimate-clip-duration', methods=['POST'])
def estimate_clip_duration():
    """Estimate video duration from script - show before visual curation."""
    data = request.get_json()
    script = data.get('script', '')

    if not script:
        return jsonify({'duration_seconds': 0, 'duration_display': '0:00', 'word_count': 0})

    dialogue = extract_dialogue_only(script)
    word_count = len(dialogue.split()) if dialogue else 0

    estimated_seconds = word_count / 2.5

    minutes = int(estimated_seconds // 60)
    seconds = int(estimated_seconds % 60)
    duration_display = f"{minutes}:{seconds:02d}"

    status = 'good'
    message = 'Duration looks good!'
    if estimated_seconds < 35:
        status = 'short'
        message = f'Script is short ({duration_display}). Target: 35s-1:15. Consider adding more content.'
    elif estimated_seconds > 75:
        status = 'long'
        message = f'Script is long ({duration_display}). Target: 35s-1:15. Consider trimming.'

    return jsonify({
        'duration_seconds': round(estimated_seconds, 1),
        'duration_display': duration_display,
        'word_count': word_count,
        'status': status,
        'message': message
    })


@voice_bp.route('/generate-voiceover', methods=['POST'])
def generate_voiceover():
    """Generate voiceover audio from script text using ElevenLabs (primary) or OpenAI (fallback)."""
    data = request.get_json()
    text = data.get('text', '')
    voice = data.get('voice', 'alloy')
    use_elevenlabs = data.get('use_elevenlabs', True)

    if not text:
        return jsonify({'error': 'No text provided'}), 400

    text = extract_dialogue_only(text)
    if not text:
        return jsonify({'error': 'No dialogue found in script'}), 400

    base_voice, elevenlabs_voice_id, system_prompt = get_voice_config(voice)

    elevenlabs_key = os.environ.get("ELEVENLABS_API_KEY")

    if use_elevenlabs and elevenlabs_key:
        try:
            from elevenlabs.client import ElevenLabs

            client = ElevenLabs(api_key=elevenlabs_key)

            audio = client.text_to_speech.convert(
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

            filename = f"voiceover_{uuid.uuid4().hex[:8]}.mp3"
            filepath = os.path.join(current_app.config['OUTPUT_FOLDER'], filename)

            audio_written = False
            with open(filepath, 'wb') as f:
                for chunk in audio:
                    if isinstance(chunk, bytes):
                        f.write(chunk)
                        audio_written = True

            if audio_written and os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                return jsonify({
                    'success': True,
                    'audio_path': filepath,
                    'audio_url': f'/output/{filename}',
                    'duration_estimate': len(text.split()) / 2.5,
                    'engine': 'elevenlabs'
                })
            else:
                print("ElevenLabs produced empty audio, falling back to OpenAI")

        except Exception as e:
            print(f"ElevenLabs error, falling back to OpenAI: {e}")

    try:
        from openai import OpenAI

        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

        response = client.audio.speech.create(
            model="tts-1-hd",
            voice=base_voice,
            input=text,
            speed=1.25
        )

        filename = f"voiceover_{uuid.uuid4().hex[:8]}.mp3"
        filepath = os.path.join(current_app.config['OUTPUT_FOLDER'], filename)
        response.stream_to_file(filepath)

        return jsonify({
            'success': True,
            'audio_path': filepath,
            'audio_url': f'/output/{filename}',
            'duration_estimate': len(text.split()) / 2.5,
            'engine': 'openai'
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@voice_bp.route('/preview-voice', methods=['POST'])
def preview_voice():
    """Generate a short voice preview sample using ElevenLabs (primary) or OpenAI (fallback)."""
    data = request.get_json()
    text = data.get('text', '')
    voice = data.get('voice', data.get('voice_name', 'alloy'))

    if not text:
        return jsonify({'error': 'No text provided'}), 400

    elevenlabs_voice_map = {
        'Adam': 'pNInz6obpgDQGcFmaJgB',
        'Antoni': 'ErXwobaYiN019PkySvjV',
        'Arnold': 'VR6AewLTigWG4xSOukaG',
        'Bella': 'EXAVITQu4vr4xnSDxMaL',
        'Domi': 'AZnzlk1XvdvUeBnXmlld',
        'Elli': 'MF3mGyEYCl7XYWbV9V6O',
        'Josh': 'TxGEqnHWrfWFTfGW9XjX',
        'Rachel': '21m00Tcm4TlvDq8ikWAM',
        'Sam': 'yoZ06aMxZJJ28mfd3POQ'
    }

    openai_voice_map = {
        'Adam': 'onyx',
        'Antoni': 'echo',
        'Arnold': 'onyx',
        'Josh': 'fable',
        'Sam': 'echo',
        'Bella': 'nova',
        'Domi': 'shimmer',
        'Elli': 'shimmer',
        'Rachel': 'nova',
        'The Analyst': 'echo',
        'The Narrator': 'onyx',
        'The Storyteller': 'fable',
        'The Teacher': 'nova',
        'The Critic': 'echo',
        'The Advocate': 'fable',
        'The Philosopher': 'onyx',
        'The Journalist': 'alloy',
    }

    if voice in elevenlabs_voice_map:
        elevenlabs_voice_id = elevenlabs_voice_map[voice]
        base_voice = openai_voice_map.get(voice, 'alloy')
    else:
        base_voice, elevenlabs_voice_id, system_prompt = get_voice_config(voice)
        if voice in openai_voice_map:
            base_voice = openai_voice_map[voice]

    elevenlabs_key = os.environ.get("ELEVENLABS_API_KEY")

    if elevenlabs_key:
        try:
            from elevenlabs.client import ElevenLabs

            client = ElevenLabs(api_key=elevenlabs_key)

            audio = client.text_to_speech.convert(
                text=text,
                voice_id=elevenlabs_voice_id,
                model_id="eleven_flash_v2_5",
                output_format="mp3_44100_128",
                voice_settings={
                    "stability": ELEVENLABS_VOICE_SETTINGS['stability'],
                    "similarity_boost": ELEVENLABS_VOICE_SETTINGS['similarity_boost'],
                    "style": ELEVENLABS_VOICE_SETTINGS['style'],
                    "use_speaker_boost": ELEVENLABS_VOICE_SETTINGS['use_speaker_boost']
                }
            )

            audio_buffer = BytesIO()
            for chunk in audio:
                if isinstance(chunk, bytes):
                    audio_buffer.write(chunk)

            audio_buffer.seek(0)

            if audio_buffer.getbuffer().nbytes > 0:
                return Response(
                    audio_buffer.getvalue(),
                    mimetype='audio/mpeg',
                    headers={'Content-Type': 'audio/mpeg'}
                )
            else:
                print("ElevenLabs preview produced empty audio, falling back to OpenAI")

        except Exception as e:
            print(f"ElevenLabs preview error, falling back to OpenAI: {e}")

    try:
        from openai import OpenAI

        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

        response = client.audio.speech.create(
            model="tts-1",
            voice=base_voice,
            input=text,
            speed=1.25
        )

        audio_buffer = BytesIO()
        for chunk in response.iter_bytes():
            audio_buffer.write(chunk)

        audio_buffer.seek(0)
        return Response(
            audio_buffer.getvalue(),
            mimetype='audio/mpeg',
            headers={'Content-Type': 'audio/mpeg'}
        )

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@voice_bp.route('/generate-multi-character-voiceover', methods=['POST'])
def generate_multi_character_voiceover():
    """
    Generate voiceover for multi-character script.
    Each character's lines are generated separately with their assigned voice,
    then assembled in script order.
    """
    from openai import OpenAI

    data = request.get_json()
    script = data.get('script', '')
    voice_assignments = data.get('voice_assignments', {})

    if not script:
        return jsonify({'error': 'No script provided'}), 400

    character_lines = parse_character_lines(script)

    if not character_lines:
        return jsonify({'error': 'No character dialogue found in script'}), 400

    client = OpenAI(
        api_key=os.environ.get("OPENAI_API_KEY")
    )

    clip_paths = []
    clip_info = []

    try:
        for entry in character_lines:
            character = entry['character']
            line = entry['line']
            order = entry['order']

            voice_key = voice_assignments.get(character) or voice_assignments.get(character.upper())
            if not voice_key:
                base_voice = 'alloy'
                elevenlabs_voice_id = 'JBFqnCBsd6RMkjVDRZzb'
            else:
                base_voice, elevenlabs_voice_id, _ = get_voice_config(voice_key)

            elevenlabs_key = os.environ.get("ELEVENLABS_API_KEY")
            generated = False

            if elevenlabs_key:
                try:
                    from elevenlabs.client import ElevenLabs as ElevenLabsClient

                    el_client = ElevenLabsClient(api_key=elevenlabs_key)
                    audio = el_client.text_to_speech.convert(
                        text=line,
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

                    clip_filename = f"clip_{order}_{uuid.uuid4().hex[:6]}.mp3"
                    clip_filepath = os.path.join(current_app.config['OUTPUT_FOLDER'], clip_filename)

                    with open(clip_filepath, 'wb') as f:
                        for chunk in audio:
                            if isinstance(chunk, bytes):
                                f.write(chunk)

                    clip_paths.append(clip_filepath)
                    clip_info.append({
                        'character': character,
                        'line': line,
                        'order': order,
                        'voice': voice_key,
                        'clip_url': f'/output/{clip_filename}',
                        'engine': 'elevenlabs'
                    })
                    generated = True
                except Exception as e:
                    print(f"ElevenLabs multi-char error: {e}")

            if not generated:
                response = client.audio.speech.create(
                    model="tts-1-hd",
                    voice=base_voice,
                    input=line,
                    speed=1.25
                )

                clip_filename = f"clip_{order}_{uuid.uuid4().hex[:6]}.mp3"
                clip_filepath = os.path.join(current_app.config['OUTPUT_FOLDER'], clip_filename)
                response.stream_to_file(clip_filepath)

                clip_paths.append(clip_filepath)
                clip_info.append({
                    'character': character,
                    'line': line,
                    'order': order,
                    'voice': voice_key,
                    'clip_url': f'/output/{clip_filename}',
                    'engine': 'openai'
                })

        final_filename = f"voiceover_multi_{uuid.uuid4().hex[:8]}.mp3"
        final_filepath = os.path.join(current_app.config['OUTPUT_FOLDER'], final_filename)

        assembled = assemble_audio_clips(clip_paths, final_filepath)

        if not assembled:
            return jsonify({'error': 'Failed to assemble audio clips'}), 500

        total_words = sum(len(entry['line'].split()) for entry in character_lines)

        return jsonify({
            'success': True,
            'audio_url': f'/output/{final_filename}',
            'audio_path': final_filepath,
            'clips': clip_info,
            'characters_detected': list(set(e['character'] for e in character_lines)),
            'duration_estimate': total_words / 2.5
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@voice_bp.route('/extract-character-lines', methods=['POST'])
def extract_character_lines_endpoint():
    """
    Extract character lines from a script for preview/editing.
    Returns parsed lines showing which character says what.
    """
    data = request.get_json()
    script = data.get('script', '')

    if not script:
        return jsonify({'error': 'No script provided'}), 400

    character_lines = parse_character_lines(script)

    by_character = {}
    for entry in character_lines:
        char = entry['character']
        if char not in by_character:
            by_character[char] = []
        by_character[char].append({
            'line': entry['line'],
            'order': entry['order']
        })

    return jsonify({
        'success': True,
        'character_lines': character_lines,
        'by_character': by_character,
        'characters': list(by_character.keys())
    })
