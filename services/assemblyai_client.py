"""
AssemblyAI client for word-level captioning.
Provides top-tier speech-to-text with word-level timestamps for caption overlay rendering.
"""
import os
import logging
import assemblyai as aai

logger = logging.getLogger(__name__)


def get_client():
    api_key = os.environ.get('ASSEMBLYAI_API_KEY')
    if not api_key:
        raise ValueError("ASSEMBLYAI_API_KEY not set")
    aai.settings.api_key = api_key
    return aai.Transcriber()


def transcribe_with_timestamps(audio_path):
    transcriber = get_client()

    config = aai.TranscriptionConfig(
        speech_model=aai.SpeechModel.best,
        punctuate=True,
        format_text=True,
    )

    transcript = transcriber.transcribe(audio_path, config=config)

    if transcript.status == aai.TranscriptStatus.error:
        logger.error(f"AssemblyAI transcription failed: {transcript.error}")
        return None

    words = []
    if transcript.words:
        for word in transcript.words:
            words.append({
                'text': word.text,
                'start': word.start / 1000.0,
                'end': word.end / 1000.0,
                'confidence': word.confidence,
            })

    return {
        'text': transcript.text,
        'words': words,
        'duration': transcript.audio_duration,
        'confidence': transcript.confidence,
    }


def transcribe_from_url(audio_url):
    transcriber = get_client()

    config = aai.TranscriptionConfig(
        speech_model=aai.SpeechModel.best,
        punctuate=True,
        format_text=True,
    )

    transcript = transcriber.transcribe(audio_url, config=config)

    if transcript.status == aai.TranscriptStatus.error:
        logger.error(f"AssemblyAI transcription failed: {transcript.error}")
        return None

    words = []
    if transcript.words:
        for word in transcript.words:
            words.append({
                'text': word.text,
                'start': word.start / 1000.0,
                'end': word.end / 1000.0,
                'confidence': word.confidence,
            })

    return {
        'text': transcript.text,
        'words': words,
        'duration': transcript.audio_duration,
        'confidence': transcript.confidence,
    }


def extract_caption_segments(transcription_result, max_words_per_segment=5):
    if not transcription_result or not transcription_result.get('words'):
        return []

    words = transcription_result['words']
    segments = []
    current_segment = []

    for word in words:
        current_segment.append(word)
        if len(current_segment) >= max_words_per_segment:
            segments.append({
                'text': ' '.join(w['text'] for w in current_segment),
                'start': current_segment[0]['start'],
                'end': current_segment[-1]['end'],
                'words': current_segment,
            })
            current_segment = []

    if current_segment:
        segments.append({
            'text': ' '.join(w['text'] for w in current_segment),
            'start': current_segment[0]['start'],
            'end': current_segment[-1]['end'],
            'words': current_segment,
        })

    return segments
