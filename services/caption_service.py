import os
import logging
import assemblyai as aai

logger = logging.getLogger(__name__)


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


def _get_assemblyai_client():
    api_key = os.environ.get('ASSEMBLYAI_API_KEY')
    if not api_key:
        return None
    aai.settings.api_key = api_key
    return aai.Transcriber()


def transcribe_audio(audio_path):
    transcriber = _get_assemblyai_client()
    if not transcriber:
        logger.warning("AssemblyAI API key not set, falling back to Whisper")
        return _whisper_fallback(audio_path)

    try:
        config = aai.TranscriptionConfig(
            speech_model=aai.SpeechModel.best,
            punctuate=True,
            format_text=True,
        )

        transcript = transcriber.transcribe(audio_path, config=config)

        if transcript.status == aai.TranscriptStatus.error:
            logger.error(f"AssemblyAI transcription failed: {transcript.error}")
            return _whisper_fallback(audio_path)

        words = []
        if transcript.words:
            for word in transcript.words:
                words.append({
                    'text': word.text,
                    'start': word.start / 1000.0,
                    'end': word.end / 1000.0,
                    'confidence': word.confidence,
                })

        result = {
            'text': transcript.text,
            'words': words,
            'duration': transcript.audio_duration,
            'confidence': transcript.confidence,
            'provider': 'assemblyai',
        }

        logger.info(f"AssemblyAI returned {len(words)} words, duration={transcript.audio_duration}s, confidence={transcript.confidence}")
        return result

    except Exception as e:
        logger.error(f"AssemblyAI error: {e}")
        return _whisper_fallback(audio_path)


def _whisper_fallback(audio_path):
    try:
        from openai import OpenAI
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

        with open(audio_path, 'rb') as audio_file:
            transcription = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                response_format="verbose_json",
                timestamp_granularities=["word"]
            )

        words = []
        raw_words = []
        if hasattr(transcription, 'words') and transcription.words:
            raw_words = transcription.words
        elif hasattr(transcription, 'segments'):
            for segment in transcription.segments:
                if hasattr(segment, 'words'):
                    raw_words.extend(segment.words)

        for w in raw_words:
            if isinstance(w, dict):
                words.append({
                    'text': w.get('word', '').strip(),
                    'start': w.get('start', 0),
                    'end': w.get('end', 0),
                    'confidence': 1.0,
                })
            else:
                words.append({
                    'text': getattr(w, 'word', '').strip(),
                    'start': getattr(w, 'start', 0),
                    'end': getattr(w, 'end', 0),
                    'confidence': 1.0,
                })

        duration = getattr(transcription, 'duration', 0)
        if not duration and words:
            duration = words[-1]['end']

        logger.info(f"Whisper fallback returned {len(words)} words")
        return {
            'text': getattr(transcription, 'text', ''),
            'words': [w for w in words if w['text']],
            'duration': duration,
            'confidence': 0.95,
            'provider': 'whisper',
        }

    except Exception as e:
        logger.error(f"Whisper fallback also failed: {e}")
        return None


def validate_caption_sync(transcription_result, expected_duration=None):
    if not transcription_result or not transcription_result.get('words'):
        return False, "No words in transcription"

    words = transcription_result['words']

    if not words:
        return False, "Empty word list"

    last_word_end = words[-1]['end']
    first_word_start = words[0]['start']

    if first_word_start < 0:
        return False, f"Invalid start time: {first_word_start}"

    for i in range(1, len(words)):
        if words[i]['start'] < words[i-1]['start']:
            return False, f"Word timestamps out of order at index {i}"

    if expected_duration and last_word_end > expected_duration * 1.1:
        return False, f"Caption duration ({last_word_end}s) exceeds audio duration ({expected_duration}s) by >10%"

    return True, "OK"


def words_to_phrases(words, max_words_per_phrase=4, uppercase=False):
    phrases = []
    current_phrase = []
    current_start = None
    current_end = 0

    for word_data in words:
        text = word_data['text'].strip()
        if not text:
            continue

        if uppercase:
            text = text.upper()

        if current_start is None:
            current_start = word_data['start']

        current_phrase.append({
            'text': text,
            'start': word_data['start'],
            'end': word_data['end'],
        })
        current_end = word_data['end']

        ends_sentence = text.rstrip().endswith(('.', '!', '?', ','))
        if len(current_phrase) >= max_words_per_phrase or (len(current_phrase) >= 2 and ends_sentence):
            phrases.append({
                'text': ' '.join(w['text'] for w in current_phrase),
                'start': current_start,
                'end': current_end,
                'words': current_phrase,
            })
            current_phrase = []
            current_start = None

    if current_phrase:
        phrases.append({
            'text': ' '.join(w['text'] for w in current_phrase),
            'start': current_start,
            'end': current_end,
            'words': current_phrase,
        })

    return phrases


def _format_srt_time(seconds):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _format_vtt_time(seconds):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def _format_ass_time(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def export_srt(phrases, output_path):
    srt_lines = []
    for i, phrase in enumerate(phrases, 1):
        start = _format_srt_time(phrase['start'])
        end = _format_srt_time(phrase['end'])
        srt_lines.append(f"{i}\n{start} --> {end}\n{phrase['text']}\n")

    content = '\n'.join(srt_lines)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(content)

    return output_path


def export_vtt(phrases, output_path):
    vtt_lines = ["WEBVTT\n"]
    for i, phrase in enumerate(phrases, 1):
        start = _format_vtt_time(phrase['start'])
        end = _format_vtt_time(phrase['end'])
        vtt_lines.append(f"{i}\n{start} --> {end}\n{phrase['text']}\n")

    content = '\n'.join(vtt_lines)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(content)

    return output_path


def export_ass(phrases, output_path, template='bold_pop', position='bottom',
               video_width=1080, video_height=1920):
    style = CAPTION_TEMPLATES.get(template, CAPTION_TEMPLATES['bold_pop'])

    margin_v = {'top': 100, 'center': int(video_height/2 - 50), 'bottom': 150}.get(position, 150)
    alignment = {'top': 8, 'center': 5, 'bottom': 2}.get(position, 2)

    bold_val = -1 if style['bold'] else 0
    back_color = style.get('back_color', '&H00000000')
    border_style = 3 if template == 'boxed' else 1

    ass_header = f"""[Script Info]
Title: Synced Captions
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

    anim_map = {
        'pop': r"\fscx110\fscy110\t(0,100,\fscx100\fscy100)",
        'bounce': r"\fscx120\fscy120\t(0,80,\fscx100\fscy100)",
        'glow': r"\blur3\t(0,150,\blur0)",
        'fade': r"\alpha&HFF&\t(0,100,\alpha&H00&)",
        'slide': r"\fscx105\t(0,100,\fscx100)",
    }
    anim_effect = anim_map.get(style['animation'], '')

    events = []
    for phrase in phrases:
        phrase_words = phrase.get('words', [])
        if not phrase_words:
            start_t = _format_ass_time(phrase['start'])
            end_t = _format_ass_time(phrase['end'])
            event_line = f"Dialogue: 0,{start_t},{end_t},Default,,0,0,0,,{phrase['text']}"
            events.append(event_line)
            continue

        for i, word_data in enumerate(phrase_words):
            word_start = word_data['start']
            word_end = word_data['end']

            before_words = [w['text'] for w in phrase_words[:i]]
            after_words = [w['text'] for w in phrase_words[i+1:]]
            current_word = word_data['text']

            text_parts = []
            if before_words:
                text_parts.append("{\\rDefault}" + ' '.join(before_words) + " ")
            text_parts.append("{\\rHighlight" + anim_effect + "}" + current_word)
            if after_words:
                text_parts.append("{\\rDefault} " + ' '.join(after_words))

            full_text = ''.join(text_parts)
            event_line = f"Dialogue: 0,{_format_ass_time(word_start)},{_format_ass_time(word_end)},Default,,0,0,0,,{full_text}"
            events.append(event_line)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(ass_header)
        f.write('\n'.join(events))

    logger.info(f"Created ASS captions with {len(events)} events: {output_path}")
    return output_path


def generate_captions(audio_path, output_path, template='bold_pop',
                      position='bottom', video_width=1080, video_height=1920,
                      uppercase=False, export_format='ass'):
    transcription = transcribe_audio(audio_path)

    if not transcription or not transcription.get('words'):
        logger.error("Transcription returned no words")
        return None, False

    is_valid, msg = validate_caption_sync(transcription, transcription.get('duration'))
    if not is_valid:
        logger.warning(f"Caption sync validation: {msg}")

    phrases = words_to_phrases(
        transcription['words'],
        max_words_per_phrase=4,
        uppercase=uppercase
    )

    if not phrases:
        logger.error("No phrases generated from transcription")
        return None, False

    if export_format == 'srt':
        result_path = export_srt(phrases, output_path)
    elif export_format == 'vtt':
        result_path = export_vtt(phrases, output_path)
    else:
        result_path = export_ass(phrases, output_path, template=template,
                                  position=position, video_width=video_width,
                                  video_height=video_height)

    provider = transcription.get('provider', 'unknown')
    word_count = len(transcription['words'])
    logger.info(f"Generated {export_format.upper()} captions via {provider}: {word_count} words, {len(phrases)} phrases")
    print(f"[CaptionService] Generated {export_format.upper()} via {provider}: {word_count} words, {len(phrases)} phrases")

    return result_path, True


def generate_captions_json(audio_path, uppercase=False):
    transcription = transcribe_audio(audio_path)

    if not transcription or not transcription.get('words'):
        return None

    phrases = words_to_phrases(
        transcription['words'],
        max_words_per_phrase=4,
        uppercase=uppercase
    )

    return {
        'text': transcription['text'],
        'words': transcription['words'],
        'phrases': phrases,
        'duration': transcription.get('duration'),
        'confidence': transcription.get('confidence'),
        'provider': transcription.get('provider', 'unknown'),
    }
