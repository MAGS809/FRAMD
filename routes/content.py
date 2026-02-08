from flask import Blueprint, request, jsonify, session, current_app
from flask_login import current_user
from extensions import db
import os
import re
import logging

content_bp = Blueprint('content_bp', __name__)


@content_bp.route('/detect-characters', methods=['POST'])
def detect_characters():
    """AI detects characters in the script for casting."""
    from context_engine import call_ai

    data = request.get_json()
    script = data.get('script', '')
    
    if not script:
        return jsonify({'success': False, 'error': 'No script provided'}), 400
    
    system_prompt = """Analyze the script and list all speaking characters.
For each character, provide:
1. Their name (as used in the script)
2. A very brief personality description (2-3 words)
3. One sample line they speak in this script

OUTPUT FORMAT (JSON):
{
  "characters": [
    {
      "name": "NARRATOR",
      "personality": "Calm, authoritative",
      "sample_line": "The world is changing faster than we think."
    }
  ]
}"""

    try:
        prompt = f"Detect characters in this script:\n\n{script}"
        result = call_ai(prompt, system_prompt, json_output=True, max_tokens=1024)
        
        if not result:
            result = {"characters": [{"name": "NARRATOR", "personality": "Calm, clear", "sample_line": "Narration..."}]}
        
        characters = result.get('characters', [])
        
        if not characters:
            characters = [{"name": "NARRATOR", "personality": "Calm, authoritative", "sample_line": "Let me tell you a story..."}]
        
        for char in characters:
            if 'sample_line' in char:
                char['sample_line'] = char['sample_line'].replace("'", "\\'").replace('"', '\\"')[:100]
        
        return jsonify({'success': True, 'characters': characters})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@content_bp.route('/generate-stage-directions', methods=['POST'])
def generate_stage_directions():
    """Generate stage directions from a script using AI."""
    from context_engine import call_ai

    data = request.get_json()
    script = data.get('script', '')
    
    if not script:
        return jsonify({'success': False, 'error': 'No script provided'}), 400
    
    prompt = f"""Analyze this script and generate stage directions (audio effects, pauses, transitions).

SCRIPT:
{script}

Generate stage directions using these formats:

TIMING:
- [PAUSE 1s] - silence/pause for specified duration
- [BEAT] - short dramatic pause (0.5s)
- [SILENCE 2s] - extended silence
- [TRANSITION] - scene change (1s pause)

SOUND EFFECTS (auto-generated and mixed into final video):
- [SOUND: whoosh] - transition swoosh
- [SOUND: impact] - deep bass hit for emphasis
- [SOUND: tension] - suspenseful rising drone
- [SOUND: reveal] - bright discovery chime
- [SOUND: alarm] - alert/warning tone
- [SOUND: heartbeat] - rhythmic pulse
- [SOUND: static] - radio interference
- [SOUND: beep] - notification ping
- [SOUND: rumble] - low rumble/thunder
- [SOUND: wind] - ambient atmosphere

Add duration: [SOUND: tension 2s] for longer effects.

Rules:
1. Place SFX at key emotional moments (reveals, transitions, emphasis)
2. 3-6 sound effects per script is ideal - don't overdo it
3. Match the script's tone (tension vs reveal, impact vs whoosh)
4. SFX are automatically generated and mixed into the final video

Output ONLY the stage directions, one per line, in order of appearance.
Include a brief note about where each should occur."""
    
    try:
        system = "You are an audio director for short-form video content."
        result = call_ai(prompt, system, json_output=False, max_tokens=1024)
        directions = result.get('text', '') if result else ""
        return jsonify({'success': True, 'directions': directions})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@content_bp.route('/build-post', methods=['POST'])
def build_post():
    """Build a complete post from a user's script/pitch idea."""
    from context_engine import build_post_from_script
    
    data = request.get_json()
    user_script = data.get('script')
    
    if not user_script:
        return jsonify({'error': 'No script provided'}), 400
    
    try:
        result = build_post_from_script(user_script)
        return jsonify({
            'success': True,
            'result': result
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@content_bp.route('/chat', methods=['POST'])
def chat():
    """Direct chat with Krakd AI with conversation memory and unified content engine."""
    from openai import OpenAI
    from context_engine import (
        save_conversation, build_personalized_prompt,
        get_source_learning_context, extract_audio, transcribe_audio,
        unified_content_engine
    )
    
    data = request.get_json()
    message = data.get('message')
    conversation = data.get('conversation', [])
    use_unified_engine = data.get('use_unified_engine', False)
    mode = data.get('mode', 'auto')
    
    user_id = current_user.id if current_user.is_authenticated else 'dev_user'
    
    if not message:
        return jsonify({'error': 'No message provided'}), 400
    
    video_context = ""
    video_patterns = [
        r'📎\s*([^\s]+\.mp4)',
        r'([^\s]+\.mp4)',
        r'uploads/[^\s]+\.mp4',
        r'output/[^\s]+\.mp4'
    ]
    
    video_file = None
    for pattern in video_patterns:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            potential_file = match.group(1) if '(' in pattern else match.group(0)
            potential_file = potential_file.strip()
            if os.path.exists(potential_file):
                video_file = potential_file
                break
            clean_path = potential_file.lstrip('📎').strip()
            if os.path.exists(clean_path):
                video_file = clean_path
                break
            for prefix in ['uploads/', 'output/', '']:
                test_path = prefix + os.path.basename(clean_path)
                if os.path.exists(test_path):
                    video_file = test_path
                    break
    
    if video_file and os.path.exists(video_file):
        try:
            audio_path = video_file.rsplit('.', 1)[0] + '_audio.mp3'
            if extract_audio(video_file, audio_path):
                transcript_data = transcribe_audio(audio_path)
                if transcript_data and transcript_data.get('text'):
                    video_context = f"\n\n[VIDEO TRANSCRIPTION from {os.path.basename(video_file)}]:\n{transcript_data['text']}\n\n[Use this transcription to understand the video's content, style, and message.]"
                try:
                    os.remove(audio_path)
                except:
                    pass
        except Exception as e:
            logging.warning(f"Could not transcribe video {video_file}: {e}")
    
    if use_unified_engine:
        try:
            result = unified_content_engine(message, user_id, mode)
            return jsonify({
                'success': True,
                'unified_result': result,
                'mode': result.get('mode', 'create')
            })
        except Exception as e:
            logging.error(f"Unified engine error in chat: {e}")
    
    client = OpenAI(
        api_key=os.environ.get("XAI_API_KEY"),
        base_url="https://api.x.ai/v1"
    )
    
    source_learning = get_source_learning_context(user_id)
    
    system_prompt = """You are Krakd — a unified thinking and clipping system.

PURPOSE:
Turn ideas, transcripts, or source material into clear, honest, human-feeling content.
You can BOTH create from ideas AND clip from source material.

THESIS-DRIVEN ARCHITECTURE:
Every piece of content you create must serve ONE CORE THESIS.
Before generating anything, identify or confirm the thesis.
If the user's input is unclear, ask ONE clarifying question about their core claim.

ANCHOR-BASED SCRIPTS:
Structure arguments around ANCHOR POINTS:
- HOOK: First statement that grabs attention
- CLAIM: Direct assertions supporting thesis
- EVIDENCE: Facts or examples proving claims
- PIVOT: Transitions to new supporting points
- CLOSER: Final statement reinforcing thesis

THOUGHT-CHANGE CLIPPING:
When analyzing content for clips:
- Identify where ideas shift
- Only recommend cuts that IMPROVE clarity or retention
- If continuous flow works better, keep it continuous

MODES:
1. CREATE MODE: User gives idea → You extract thesis → Generate anchor-based script
2. CLIP MODE: User gives transcript/source → You find thesis → Suggest clips at thought-changes

CORE PHILOSOPHY:
1. Language matters more than volume — say the right thing, not more things
2. Ideas fail when ignored, not when challenged — explain resistance precisely
3. Coexistence is logic, not sentiment — durable outcomes from shared stakes

TONE (STRICT):
- Calm, clear, grounded, subtly witty when appropriate, confident without arrogance
- NEVER: sarcastic, smug, preachy, outraged, juvenile, crude, sexual, graphic

SCRIPT FORMAT:
- INT./EXT. scene headings, CHARACTER NAMES in caps, no markdown
- Include [VISUAL: description] notes for B-roll throughout
- Every line serves the thesis
- Ending closes the loop

OUTPUT STANDARD:
- Intentional — every line has a reason
- Restrained — no excess, no padding
- Human-written — natural flow
- Punchy — clarity without dilution

Never explain what you're doing. Just write."""

    if source_learning:
        system_prompt += f"\n\n{source_learning}"

    personalized_prompt = build_personalized_prompt(user_id, system_prompt)
    
    messages = [{"role": "system", "content": personalized_prompt}]
    messages.extend(conversation)
    
    user_message_with_context = message + video_context if video_context else message
    messages.append({"role": "user", "content": user_message_with_context})
    
    save_conversation(user_id, 'user', message)
    
    try:
        response = client.chat.completions.create(
            model="grok-3",
            messages=messages,
            max_tokens=2048
        )
        
        reply = response.choices[0].message.content or ""
        
        save_conversation(user_id, 'assistant', reply)
        
        return jsonify({
            'success': True,
            'reply': reply,
            'conversation': messages + [{"role": "assistant", "content": reply}]
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@content_bp.route('/unified-engine', methods=['POST'])
def unified_engine():
    """
    Unified content engine - handles both creation and clipping in one interface.
    Automatically detects mode from input, or accepts explicit mode parameter.
    
    When has_media=True, shows options: "Inspire my visuals" or "Clip this video"
    """
    from models import SourceContent, ProjectThesis, ScriptAnchor, ThoughtChange, Project
    from context_engine import unified_content_engine
    
    data = request.get_json()
    user_input = data.get('input', '')
    mode = data.get('mode', 'auto')
    project_id = data.get('project_id')
    has_media = data.get('has_media', False)
    clarification_count = data.get('clarification_count', 0)
    force_generate = data.get('force_generate', False)
    
    user_id = current_user.id if current_user.is_authenticated else 'dev_user'
    
    if not user_input:
        return jsonify({'error': 'No input provided'}), 400
    
    try:
        result = unified_content_engine(user_input, user_id, mode, has_media, clarification_count, force_generate)
        
        if result.get('mode') == 'greeting':
            return jsonify({
                'mode': 'greeting',
                'status': 'conversational',
                'reply': result.get('reply', "What's on your mind the world should get to know?"),
                'needs_content': True
            })
        
        if result.get('mode') == 'media_options':
            return jsonify({
                'mode': 'media_options',
                'status': 'needs_choice',
                'options': result.get('options', []),
                'question': result.get('question', 'What would you like to do with this video?')
            })
        
        if result.get('status') == 'ready':
            if result.get('mode') == 'clip_video':
                source = SourceContent(
                    user_id=user_id,
                    content_type='transcript',
                    transcript=user_input[:10000],
                    extracted_thesis=result.get('result', {}).get('thesis', {}).get('thesis_statement'),
                    extracted_anchors=result.get('result', {}).get('recommended_clips', []),
                    learned_hooks=result.get('result', {}).get('learnings', {}).get('learned_hooks'),
                    learned_pacing=result.get('result', {}).get('learnings', {}).get('learned_pacing'),
                    learned_structure=result.get('result', {}).get('learnings', {}).get('learned_structure'),
                    learned_style=result.get('result', {}).get('learnings', {}).get('learned_style')
                )
                db.session.add(source)
                db.session.commit()
                result['source_id'] = source.id
            
            elif result.get('mode') == 'create' and project_id:
                thesis_data = result.get('thesis', {})
                thesis = ProjectThesis(
                    project_id=project_id,
                    user_id=user_id,
                    thesis_statement=thesis_data.get('thesis_statement', ''),
                    thesis_type=thesis_data.get('thesis_type'),
                    core_claim=thesis_data.get('core_claim'),
                    target_audience=thesis_data.get('target_audience'),
                    intended_impact=thesis_data.get('intended_impact'),
                    confidence_score=thesis_data.get('confidence', 1.0)
                )
                db.session.add(thesis)
                
                for i, anchor in enumerate(result.get('anchors', [])):
                    if isinstance(anchor, dict):
                        anchor_obj = ScriptAnchor(
                            project_id=project_id,
                            anchor_text=anchor.get('anchor_text', ''),
                            anchor_type=anchor.get('anchor_type', 'CLAIM'),
                            position=anchor.get('position', i),
                            supports_thesis=anchor.get('supports_thesis', True),
                            is_hook=anchor.get('is_hook', False),
                            is_closer=anchor.get('is_closer', False),
                            visual_intent=anchor.get('visual_intent'),
                            emotional_beat=anchor.get('emotional_beat')
                        )
                        db.session.add(anchor_obj)
                
                for tc in result.get('thought_changes', []):
                    if isinstance(tc, dict):
                        tc_obj = ThoughtChange(
                            project_id=project_id,
                            position=tc.get('position', 0),
                            from_idea=tc.get('from_idea'),
                            to_idea=tc.get('to_idea'),
                            transition_type=tc.get('transition_type', 'pivot'),
                            should_clip=tc.get('should_clip', False),
                            clip_reasoning=tc.get('clip_reasoning'),
                            clarity_improvement=tc.get('clarity_improvement'),
                            retention_improvement=tc.get('retention_improvement')
                        )
                        db.session.add(tc_obj)
                
                db.session.commit()
        
        return jsonify({
            'success': True,
            'result': result
        })
    except Exception as e:
        db.session.rollback()
        logging.error(f"Unified engine error: {e}")
        return jsonify({'error': str(e)}), 500


@content_bp.route('/extract-thesis', methods=['POST'])
def api_extract_thesis():
    """Extract thesis from content."""
    from context_engine import extract_thesis

    data = request.get_json()
    content = data.get('content', '')
    content_type = data.get('content_type', 'idea')
    
    if not content:
        return jsonify({'error': 'No content provided'}), 400
    
    try:
        thesis = extract_thesis(content, content_type)
        return jsonify({
            'success': True,
            'thesis': thesis
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@content_bp.route('/identify-anchors', methods=['POST'])
def api_identify_anchors():
    """Identify anchor points in a script."""
    from context_engine import identify_anchors

    data = request.get_json()
    script = data.get('script', '')
    thesis = data.get('thesis', '')
    
    if not script or not thesis:
        return jsonify({'error': 'Script and thesis required'}), 400
    
    try:
        anchors = identify_anchors(script, thesis)
        return jsonify({
            'success': True,
            'anchors': anchors
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@content_bp.route('/detect-thought-changes', methods=['POST'])
def api_detect_thought_changes():
    """Detect thought transitions in content."""
    from context_engine import detect_thought_changes

    data = request.get_json()
    content = data.get('content', '')
    content_type = data.get('content_type', 'script')
    
    if not content:
        return jsonify({'error': 'No content provided'}), 400
    
    try:
        changes = detect_thought_changes(content, content_type)
        return jsonify({
            'success': True,
            'thought_changes': changes
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@content_bp.route('/clip-source', methods=['POST'])
def clip_source():
    """Process source material for intelligent clipping."""
    from models import SourceContent
    from context_engine import (
        process_source_for_clipping, learn_from_source_content,
        analyze_editing_patterns_global, store_global_patterns
    )
    
    data = request.get_json()
    transcript = data.get('transcript', '')
    source_url = data.get('source_url')
    
    user_id = current_user.id if current_user.is_authenticated else 'dev_user'
    
    if not transcript:
        return jsonify({'error': 'No transcript provided'}), 400
    
    try:
        result = process_source_for_clipping(transcript, source_url)
        
        if result.get('status') == 'ready':
            learnings = learn_from_source_content(transcript, result.get('recommended_clips', []))
            
            try:
                global_analysis = analyze_editing_patterns_global(
                    {'transcript': transcript},
                    result.get('recommended_clips', [])
                )
                if global_analysis.get('success') and global_analysis.get('patterns'):
                    store_global_patterns(global_analysis['patterns'], db.session)
                    print(f"[Global Learning] Stored {len(global_analysis['patterns'])} patterns from clip source")
            except Exception as ge:
                print(f"[Global Learning] Error: {ge}")
            
            source = SourceContent(
                user_id=user_id,
                content_type='transcript',
                source_url=source_url,
                transcript=transcript[:10000],
                extracted_thesis=result.get('thesis', {}).get('thesis_statement'),
                extracted_anchors=result.get('recommended_clips', []),
                extracted_thought_changes=result.get('thought_changes', []),
                learned_hooks=learnings.get('learned_hooks'),
                learned_pacing=learnings.get('learned_pacing'),
                learned_structure=learnings.get('learned_structure'),
                learned_style=learnings.get('learned_style'),
                clips_generated=len(result.get('recommended_clips', [])),
                quality_score=result.get('overall_quality')
            )
            db.session.add(source)
            db.session.commit()
            
            result['source_id'] = source.id
            result['learnings'] = learnings
        
        return jsonify({
            'success': True,
            'result': result
        })
    except Exception as e:
        db.session.rollback()
        logging.error(f"Clip source error: {e}")
        return jsonify({'error': str(e)}), 500


@content_bp.route('/generate-thesis-script', methods=['POST'])
def api_generate_thesis_script():
    """Generate a thesis-driven script."""
    from context_engine import (
        get_user_context, get_source_learning_context,
        generate_thesis_driven_script, identify_anchors, detect_thought_changes
    )
    from models import SourceContent
    
    data = request.get_json()
    thesis = data.get('thesis', {})
    
    user_id = current_user.id if current_user.is_authenticated else 'dev_user'
    
    if not thesis or not thesis.get('thesis_statement'):
        return jsonify({'error': 'Thesis statement required'}), 400
    
    try:
        user_context = get_user_context(user_id)
        source_learning = get_source_learning_context(user_id)
        full_context = f"{user_context}\n\n{source_learning}" if source_learning else user_context
        
        learned_patterns = {}
        try:
            sources = SourceContent.query.filter_by(user_id=user_id).limit(5).all()
            for src in sources:
                if src.learned_hooks:
                    learned_patterns['hooks'] = src.learned_hooks
                if src.learned_pacing:
                    learned_patterns['pacing'] = src.learned_pacing
                if src.learned_structure:
                    learned_patterns['structure'] = src.learned_structure
                if src.learned_style:
                    learned_patterns['style'] = src.learned_style
        except:
            pass
        
        script = generate_thesis_driven_script(thesis, full_context, learned_patterns)
        anchors = identify_anchors(script.get('full_script', ''), thesis.get('thesis_statement', ''))
        thought_changes = detect_thought_changes(script.get('full_script', ''))
        
        return jsonify({
            'success': True,
            'script': script,
            'anchors': anchors,
            'thought_changes': thought_changes,
            'learned_patterns_applied': bool(learned_patterns)
        })
    except Exception as e:
        logging.error(f"Generate thesis script error: {e}")
        return jsonify({'error': str(e)}), 500


@content_bp.route('/get-source-learnings', methods=['GET'])
def get_source_learnings():
    """Get accumulated learnings from all clipped content."""
    from models import SourceContent
    
    user_id = current_user.id if current_user.is_authenticated else 'dev_user'
    
    try:
        sources = SourceContent.query.filter_by(user_id=user_id).order_by(
            SourceContent.created_at.desc()
        ).limit(20).all()
        
        learnings = {
            'total_sources': len(sources),
            'total_clips_generated': sum(s.clips_generated or 0 for s in sources),
            'hooks': [],
            'pacing': None,
            'structure': None,
            'style': None
        }
        
        for src in sources:
            if src.learned_hooks:
                if isinstance(src.learned_hooks, list):
                    learnings['hooks'].extend(src.learned_hooks)
                else:
                    learnings['hooks'].append(src.learned_hooks)
            if src.learned_pacing and not learnings['pacing']:
                learnings['pacing'] = src.learned_pacing
            if src.learned_structure and not learnings['structure']:
                learnings['structure'] = src.learned_structure
            if src.learned_style and not learnings['style']:
                learnings['style'] = src.learned_style
        
        if learnings['hooks']:
            learnings['hooks'] = sorted(
                [h for h in learnings['hooks'] if isinstance(h, dict)],
                key=lambda x: x.get('effectiveness', 0),
                reverse=True
            )[:5]
        
        return jsonify({
            'success': True,
            'learnings': learnings
        })
    except Exception as e:
        logging.error(f"Get source learnings error: {e}")
        return jsonify({'error': str(e)}), 500


@content_bp.route('/classify-content', methods=['POST'])
def classify_content():
    """Classify content type and generate visual plan."""
    from context_engine import classify_content_type, generate_visual_plan, identify_anchors
    
    data = request.get_json()
    script = data.get('script', '')
    thesis = data.get('thesis', '')
    
    if not script:
        return jsonify({'error': 'No script provided'}), 400
    
    try:
        anchors = identify_anchors(script, thesis)
        visual_plan = generate_visual_plan(script, thesis, anchors)
        
        return jsonify({
            'success': True,
            'classification': visual_plan.get('classification', {}),
            'content_type': visual_plan.get('classification', {}).get('content_type', 'informative'),
            'layers': visual_plan.get('layers', {}),
            'assets': visual_plan.get('assets', {}),
            'anchors': anchors
        })
    except Exception as e:
        logging.error(f"Content classification error: {e}")
        return jsonify({'error': str(e)}), 500


@content_bp.route('/process-full', methods=['POST'])
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
            current_app.config['OUTPUT_FOLDER'],
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


@content_bp.route('/auto-assign-voices', methods=['POST'])
def auto_assign_voices():
    """Auto-assign voices to characters based on script context."""
    data = request.get_json()
    script = data.get('script', {})
    
    characters = set()
    anchors = script.get('anchors', [])
    for anchor in anchors:
        char = anchor.get('character', 'Narrator')
        characters.add(char)
    
    if not characters:
        characters.add('Narrator')
    
    voice_pool = {
        'male': ['Adam', 'Antoni', 'Arnold', 'Josh', 'Sam'],
        'female': ['Bella', 'Domi', 'Elli', 'Rachel'],
        'neutral': ['Adam', 'Rachel']
    }
    
    voice_assignments = {}
    male_idx = 0
    female_idx = 0
    
    for char in sorted(characters):
        char_lower = char.lower()
        
        if any(name in char_lower for name in ['narrator', 'host', 'adam', 'john', 'mike', 'david', 'james']):
            voice_assignments[char] = voice_pool['male'][male_idx % len(voice_pool['male'])]
            male_idx += 1
        elif any(name in char_lower for name in ['sarah', 'rachel', 'bella', 'emma', 'lisa', 'amy']):
            voice_assignments[char] = voice_pool['female'][female_idx % len(voice_pool['female'])]
            female_idx += 1
        else:
            if male_idx <= female_idx:
                voice_assignments[char] = voice_pool['male'][male_idx % len(voice_pool['male'])]
                male_idx += 1
            else:
                voice_assignments[char] = voice_pool['female'][female_idx % len(voice_pool['female'])]
                female_idx += 1
    
    return jsonify({
        'success': True,
        'voice_assignments': voice_assignments
    })
