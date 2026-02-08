import os
import re
import subprocess
import uuid
import shutil


def extract_dialogue_only(script_text):
    """
    Extract ONLY spoken dialogue from script - bare minimum for voice generation.
    Keeps lines formatted as [CHARACTER]: dialogue or CHARACTER: dialogue.
    Filters AI commentary that appears BEFORE script starts.
    """
    dialogue_lines = []
    in_script = False
    
    ai_meta_patterns = [
        r'^Understood', r'^I\'ll create', r'^Here\'s', r'^Let me create',
        r'^This script', r'^The script', r'^I\'ve', r'^I can create',
        r'^Let me know', r'^Would you like', r'^The message',
        r'^exaggerated personas', r'^With voices', r'^I hope this',
        r'^This uses a', r'^The humor comes',
    ]
    
    for line in script_text.split('\n'):
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
            dialogue = match1.group(2).strip()
            dialogue = re.sub(r'\([^)]*\)', '', dialogue).strip()
            if dialogue:
                dialogue_lines.append(dialogue)
            continue
        
        match2 = re.match(r'^([A-Za-z][A-Za-z0-9\-\.\'\s]{0,25}):\s*(.+)$', line)
        if match2:
            char_name = match2.group(1).strip().upper()
            dialogue = match2.group(2).strip()
            if char_name in ['SCENE', 'VISUAL', 'CUT', 'FADE', 'INT', 'EXT', 'TITLE', 'CHARACTERS', 'VOICES']:
                continue
            dialogue = re.sub(r'\([^)]*\)', '', dialogue).strip()
            if dialogue:
                dialogue_lines.append(dialogue)
            continue
    
    return ' '.join(dialogue_lines)


def generate_sound_effect_elevenlabs(effect_description, output_path, duration=2.0):
    """
    Generate a sound effect using ElevenLabs Sound Effects API.
    Falls back to FFmpeg synthesis if ElevenLabs is unavailable.
    """
    elevenlabs_key = os.environ.get("ELEVENLABS_API_KEY")
    
    if elevenlabs_key:
        try:
            from elevenlabs.client import ElevenLabs
            
            client = ElevenLabs(api_key=elevenlabs_key)
            
            result = client.text_to_sound_effects.convert(
                text=effect_description,
                duration_seconds=min(duration, 22.0),
                prompt_influence=0.3
            )
            
            os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else 'output', exist_ok=True)
            
            with open(output_path, 'wb') as f:
                for chunk in result:
                    f.write(chunk)
            
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                print(f"Generated ElevenLabs SFX: {effect_description[:30]}...")
                return output_path
        except Exception as e:
            print(f"ElevenLabs SFX error: {e}, falling back to FFmpeg")
    
    return None


def generate_sound_effect(effect_type, output_path, duration=1.0):
    """
    Generate a sound effect using ElevenLabs (preferred) or FFmpeg synthesis (fallback).
    Returns the path to the generated audio file.
    
    Supported effect types:
    - whoosh: Quick transition swoosh
    - impact: Deep bass hit
    - tension: Rising drone
    - reveal: Bright chime/sting
    - alarm: Alert/warning tone
    - heartbeat: Rhythmic pulse
    - static: Radio/TV static
    - beep: Simple notification
    - rumble: Low rumble/earthquake
    - wind: Ambient wind
    """
    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else 'output', exist_ok=True)
    
    effect_descriptions = {
        'whoosh': 'quick cinematic swoosh transition sound effect',
        'impact': 'deep bass impact hit sound effect for emphasis',
        'tension': 'rising tension drone suspenseful atmosphere',
        'reveal': 'bright reveal sting chime sound effect',
        'alarm': 'alert warning notification tone',
        'heartbeat': 'rhythmic heartbeat pulse sound',
        'static': 'radio TV static interference noise',
        'beep': 'simple digital beep notification',
        'rumble': 'low deep rumble earthquake bass',
        'wind': 'ambient wind atmospheric whoosh'
    }
    
    description = effect_descriptions.get(effect_type.lower(), effect_type)
    elevenlabs_result = generate_sound_effect_elevenlabs(description, output_path, duration)
    if elevenlabs_result:
        return elevenlabs_result
    
    effect_commands = {
        'whoosh': [
            'ffmpeg', '-y', '-f', 'lavfi',
            '-i', f'anoisesrc=d={duration}:color=pink:amplitude=0.3,afade=t=in:d=0.05,afade=t=out:d={duration*0.8}:st={duration*0.2},highpass=f=800,lowpass=f=4000',
            '-c:a', 'aac', '-b:a', '128k', output_path
        ],
        'impact': [
            'ffmpeg', '-y', '-f', 'lavfi',
            '-i', f'sine=f=60:d={duration},afade=t=out:d={duration*0.9}:st=0.1,volume=2',
            '-c:a', 'aac', '-b:a', '128k', output_path
        ],
        'tension': [
            'ffmpeg', '-y', '-f', 'lavfi',
            '-i', f'sine=f=80:d={duration},tremolo=f=5:d=0.5,afade=t=in:d={duration*0.3},afade=t=out:d={duration*0.3}:st={duration*0.7}',
            '-c:a', 'aac', '-b:a', '128k', output_path
        ],
        'reveal': [
            'ffmpeg', '-y', '-f', 'lavfi',
            '-i', f'sine=f=880:d={duration},afade=t=in:d=0.05,afade=t=out:d={duration*0.5}:st={duration*0.5},volume=0.5',
            '-c:a', 'aac', '-b:a', '128k', output_path
        ],
        'alarm': [
            'ffmpeg', '-y', '-f', 'lavfi',
            '-i', f'sine=f=800:d={duration},tremolo=f=8:d=0.9,afade=t=out:d=0.1:st={duration-0.1}',
            '-c:a', 'aac', '-b:a', '128k', output_path
        ],
        'heartbeat': [
            'ffmpeg', '-y', '-f', 'lavfi',
            '-i', f'sine=f=50:d={duration},tremolo=f=1.5:d=0.9,afade=t=in:d=0.1,afade=t=out:d=0.2:st={max(0.1, duration-0.2)}',
            '-c:a', 'aac', '-b:a', '128k', output_path
        ],
        'static': [
            'ffmpeg', '-y', '-f', 'lavfi',
            '-i', f'anoisesrc=d={duration}:color=white:amplitude=0.2,bandpass=f=2000:width_type=h:w=1000',
            '-c:a', 'aac', '-b:a', '128k', output_path
        ],
        'beep': [
            'ffmpeg', '-y', '-f', 'lavfi',
            '-i', f'sine=f=1000:d={min(duration, 0.3)},afade=t=in:d=0.01,afade=t=out:d=0.05:st={max(0.01, min(duration, 0.3)-0.05)}',
            '-c:a', 'aac', '-b:a', '128k', output_path
        ],
        'rumble': [
            'ffmpeg', '-y', '-f', 'lavfi',
            '-i', f'anoisesrc=d={duration}:color=brown:amplitude=0.4,lowpass=f=120,afade=t=in:d={duration*0.2},afade=t=out:d={duration*0.3}:st={duration*0.7}',
            '-c:a', 'aac', '-b:a', '128k', output_path
        ],
        'wind': [
            'ffmpeg', '-y', '-f', 'lavfi',
            '-i', f'anoisesrc=d={duration}:color=pink:amplitude=0.15,lowpass=f=600,afade=t=in:d={duration*0.3},afade=t=out:d={duration*0.3}:st={duration*0.7}',
            '-c:a', 'aac', '-b:a', '128k', output_path
        ],
    }
    
    cmd = effect_commands.get(effect_type.lower(), effect_commands['whoosh'])
    
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        if result.returncode == 0 and os.path.exists(output_path):
            return output_path
        else:
            print(f"SFX generation failed for {effect_type}: {result.stderr.decode()[:200]}")
            return None
    except Exception as e:
        print(f"SFX generation error: {e}")
        return None


def parse_sfx_from_directions(script_text, stage_directions=''):
    """
    Parse [SOUND: description] tags from script and stage directions.
    Returns a list of sound effect requests with estimated timing.
    """
    sfx_requests = []
    combined_text = f"{script_text}\n{stage_directions}"
    
    description_to_effect = {
        'whoosh': 'whoosh',
        'swoosh': 'whoosh',
        'transition': 'whoosh',
        'swipe': 'whoosh',
        'impact': 'impact',
        'hit': 'impact',
        'boom': 'impact',
        'thud': 'impact',
        'punch': 'impact',
        'tension': 'tension',
        'suspense': 'tension',
        'drone': 'tension',
        'rising': 'tension',
        'reveal': 'reveal',
        'sting': 'reveal',
        'chime': 'reveal',
        'discovery': 'reveal',
        'alarm': 'alarm',
        'alert': 'alarm',
        'warning': 'alarm',
        'siren': 'alarm',
        'heartbeat': 'heartbeat',
        'heart': 'heartbeat',
        'pulse': 'heartbeat',
        'static': 'static',
        'noise': 'static',
        'interference': 'static',
        'beep': 'beep',
        'notification': 'beep',
        'ping': 'beep',
        'rumble': 'rumble',
        'earthquake': 'rumble',
        'thunder': 'rumble',
        'bass': 'rumble',
        'wind': 'wind',
        'breeze': 'wind',
        'atmosphere': 'wind',
    }
    
    sound_pattern = re.compile(r'\[SOUND:\s*([^\]]+)\]', re.IGNORECASE)
    
    lines = combined_text.split('\n')
    line_position = 0
    
    for line in lines:
        matches = sound_pattern.findall(line)
        for description in matches:
            description_lower = description.lower().strip()
            
            effect_type = 'whoosh'
            for keyword, effect in description_to_effect.items():
                if keyword in description_lower:
                    effect_type = effect
                    break
            
            duration = 1.0
            duration_match = re.search(r'(\d+(?:\.\d+)?)\s*s', description_lower)
            if duration_match:
                duration = float(duration_match.group(1))
            
            sfx_requests.append({
                'description': description.strip(),
                'effect_type': effect_type,
                'duration': duration,
                'position': line_position
            })
        
        line_position += 1
    
    return sfx_requests


def mix_sfx_into_audio(voiceover_path, sfx_requests, output_path, total_script_lines=None):
    """
    Generate sound effects and mix them into the voiceover audio.
    SFX are placed based on their relative position in the script.
    """
    from pydub import AudioSegment
    
    if not sfx_requests or not os.path.exists(voiceover_path):
        if os.path.exists(voiceover_path):
            shutil.copy(voiceover_path, output_path)
        return output_path
    
    try:
        voiceover = AudioSegment.from_file(voiceover_path)
        total_duration_ms = len(voiceover)
        
        print(f"Mixing {len(sfx_requests)} sound effects into {total_duration_ms/1000:.1f}s audio")
        
        max_position = max(sfx['position'] for sfx in sfx_requests) if sfx_requests else 1
        if total_script_lines and total_script_lines > max_position:
            max_position = total_script_lines
        max_position = max(1, max_position)
        
        for i, sfx in enumerate(sfx_requests):
            position_ratio = sfx['position'] / max_position
            sfx_duration_ms = sfx['duration'] * 1000
            start_ms = int(position_ratio * max(0, total_duration_ms - sfx_duration_ms))
            
            sfx_path = f"output/sfx_temp_{i}_{uuid.uuid4().hex[:6]}.m4a"
            generated_path = generate_sound_effect(sfx['effect_type'], sfx_path, sfx['duration'])
            
            if generated_path and os.path.exists(generated_path):
                try:
                    sfx_audio = AudioSegment.from_file(generated_path)
                    sfx_audio = sfx_audio - 6
                    
                    voiceover = voiceover.overlay(sfx_audio, position=start_ms)
                    print(f"  Added {sfx['effect_type']} at {start_ms/1000:.1f}s")
                except Exception as e:
                    print(f"  Failed to overlay SFX {i}: {e}")
                finally:
                    try:
                        os.remove(generated_path)
                    except:
                        pass
        
        voiceover.export(output_path, format='mp3', bitrate='192k')
        print(f"SFX mixed audio saved to {output_path}")
        return output_path
        
    except Exception as e:
        print(f"SFX mixing failed: {e}")
        if os.path.exists(voiceover_path):
            shutil.copy(voiceover_path, output_path)
        return output_path


def extract_voice_actor_script(script_text, character_filter=None):
    """
    Extract a clean voice actor script from the full screenplay.
    If character_filter is provided, only include lines for that character.
    
    Returns clean dialogue only - what the voice actor reads.
    """
    lines = script_text.split('\n')
    voice_lines = []
    
    for line in lines:
        stripped = line.strip()
        
        if not stripped:
            continue
        
        if re.match(r'^[=_\-]{3,}$', stripped):
            continue
        
        if stripped.startswith('VISUAL:') or stripped.startswith('CUT:'):
            continue
        
        if stripped.startswith('SCENE ') or stripped.startswith('INT.') or stripped.startswith('EXT.'):
            continue
        
        if stripped.startswith('CUT TO'):
            continue
        
        if stripped.startswith('CHARACTERS:') or stripped.startswith('VOICES?'):
            continue
        
        if stripped.startswith('===') or stripped.endswith('==='):
            continue
        
        direction_keywords = {'VISUAL', 'CUT', 'FADE', 'SCENE', 'INT', 'EXT', 'TITLE'}
        
        bracket_match = re.match(r'^\[([A-Za-z][A-Za-z0-9\s_\.\-\']+)\]:\s*(.+)$', stripped)
        if bracket_match:
            character = bracket_match.group(1).strip().upper()
            dialogue = bracket_match.group(2).strip()
            
            if character in direction_keywords:
                continue
            
            if character_filter:
                if character == character_filter.upper():
                    voice_lines.append(dialogue)
            else:
                voice_lines.append(dialogue)
            continue
        
        colon_match = re.match(r'^([A-Za-z][A-Za-z0-9\s_\.\-\']+):\s*(.+)$', stripped)
        if colon_match:
            character = colon_match.group(1).strip().upper()
            dialogue = colon_match.group(2).strip()
            if character not in direction_keywords:
                if character_filter:
                    if character == character_filter.upper():
                        voice_lines.append(dialogue)
                else:
                    voice_lines.append(dialogue)
            continue
        
        if re.match(r'^\[', stripped):
            continue
    
    result = []
    prev_empty = False
    for line in voice_lines:
        if not line.strip():
            if not prev_empty:
                result.append('')
            prev_empty = True
        else:
            result.append(line)
            prev_empty = False
    
    return '\n'.join(result).strip()


def parse_character_lines(script_text):
    """
    Parse a multi-character script and extract lines per character.
    Expected format: [CHARACTER NAME]: dialogue text
    Also handles mixed case and punctuation in character names.
    
    Returns list of dicts with order preserved:
    [{'character': 'NEWS ANCHOR', 'line': 'The market crashed.', 'order': 0}, ...]
    """
    character_lines = []
    order = 0
    
    direction_keywords = {'VISUAL', 'CUT', 'FADE', 'SCENE', 'INT', 'EXT', 'TITLE', 'CUT TO', 'FADE TO'}
    
    for line in script_text.split('\n'):
        line = line.strip()
        if not line:
            continue
        
        if re.match(r'^[=_\-]{3,}$', line):
            continue
        if line.upper().startswith('[VISUAL') or line.upper().startswith('[CUT') or line.upper().startswith('[FADE'):
            continue
        if line.upper().startswith('VISUAL:') or line.upper().startswith('CUT:'):
            continue
        if line.upper().startswith('SCENE ') or line.upper().startswith('INT.') or line.upper().startswith('EXT.'):
            continue
        if line.upper().startswith('CHARACTERS:') or line.upper().startswith('VOICES?'):
            continue
        
        bracket_match = re.match(r'^\[([A-Za-z][A-Za-z0-9\s_\.\-\']+)\]:\s*(.+)$', line)
        if bracket_match:
            character = bracket_match.group(1).strip().upper()
            dialogue = bracket_match.group(2).strip()
            if dialogue and character not in direction_keywords:
                character_lines.append({
                    'character': character,
                    'line': dialogue,
                    'order': order
                })
                order += 1
            continue
        
        colon_match = re.match(r'^([A-Za-z][A-Za-z0-9\s_\.\-\']+):\s*(.+)$', line)
        if colon_match:
            character = colon_match.group(1).strip().upper()
            dialogue = colon_match.group(2).strip()
            if character not in direction_keywords:
                if dialogue:
                    character_lines.append({
                        'character': character,
                        'line': dialogue,
                        'order': order
                    })
                    order += 1
    
    return character_lines


def get_character_voice_map(voice_assignments):
    """
    Map character names to their assigned voice personas.
    voice_assignments is a dict like {'NEWS ANCHOR': 'news_anchor', 'WOLF': 'wolf_businessman'}
    """
    return voice_assignments if voice_assignments else {}


def assemble_audio_clips(clip_paths, output_path):
    """
    Assemble multiple audio clips into a single file in order.
    Uses FFmpeg filter_complex for reliable MP3 concatenation with re-encoding.
    """
    if not clip_paths:
        return None
    
    if len(clip_paths) == 1:
        shutil.copy(clip_paths[0], output_path)
        return output_path
    
    try:
        inputs = []
        filter_parts = []
        
        for i, clip in enumerate(clip_paths):
            inputs.extend(['-i', clip])
            filter_parts.append(f'[{i}:a]')
        
        filter_str = ''.join(filter_parts) + f'concat=n={len(clip_paths)}:v=0:a=1[out]'
        
        cmd = [
            'ffmpeg', '-y',
            *inputs,
            '-filter_complex', filter_str,
            '-map', '[out]',
            '-c:a', 'libmp3lame', '-q:a', '2',
            output_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            print(f"FFmpeg concat error: {result.stderr}")
            return None
        
        return output_path
    except Exception as e:
        print(f"Audio assembly error: {e}")
        return None
