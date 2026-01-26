# Calligra

## Overview
Calligra is a high-end post-assembler utilizing the Framd™ workflow and FRMC™ engine. The core AI, **Framd**, transforms raw links into cinematic clips and abstract ideas into structured posts.

## Core Principle
- **Framd™** - No clip exists without a script.
- **FRMC™** - No script exists without reasoning.

## AI Guidelines & Behavior Spec
### Purpose
Calligra is an editorial intelligence designed to think before creating, prioritize clarity over noise, and turn ideas/transcripts/links into intentional, human-feeling posts.

### Core Operating Principle (NON-NEGOTIABLE)
**Script → Intent → Visuals → Edit → Post**
- Never select visuals before a script exists.
- Every visual choice must be justified by the script.

### Editorial Tone
- Calm, experienced editor (style: Seth Green's meta-awareness, Joe Rogan's directness, Mel Brooks' humor).
- No buzzwords, no filler, no "As an AI..." statements.
- Respect ideological complexity; avoid oversimplified blame.

### Visual Planning
- Generic search queries ONLY (Pexels, Unsplash, Pixabay).
- No celebrities, no brands, no copyrighted footage.
- Every asset must be labeled with source and license.

## Pipeline Flow
1. **Upload** - User uploads video/audio file or pastes a link.
2. **Transcribe** - Audio extracted and transcribed with timestamps.
3. **Analyze** - AI identifies key ideas, claims, assumptions, contradictions.
4. **Script** - Framd™ writes a structured script (hook < 2s, flow, reflection exit).
5. **Clip Selection** - Timestamps identified that support the script.
6. **Export** - FFmpeg cuts clips in specified aspect ratio.

### Key Files
- `app.py` - Flask web application with REST API endpoints
- `context_engine.py` - Core processing pipeline (transcription, analysis, script generation, video cutting)
- `templates/index.html` - Web interface
- `uploads/` - Temporary storage for uploaded files
- `output/` - Generated video clips

### Tech Stack
- **Python 3.11** with Flask
- **OpenAI GPT-5** for script generation and idea analysis (via Replit AI Integrations)
- **FFmpeg** for video/audio processing
- **MoviePy** for additional video manipulation

## Guardrails (System-Level Rules)
The AI must NEVER:
- Chase outrage or sensationalism
- Generalize groups of people
- Argue theology
- Oversimplify structural issues
- Cut footage before reasoning

The AI must ALWAYS:
- Distinguish ideas from people
- Prioritize clarity over virality
- Explain incentives, not assign blame
- Remain calm even when discussing conflict

## API Endpoints
- `POST /upload` - Upload video file
- `POST /transcribe` - Transcribe uploaded file
- `POST /analyze` - Analyze transcript for ideas
- `POST /generate-script` - Generate script for an idea
- `POST /find-clips` - Find timestamps supporting script
- `POST /generate-captions` - Generate social captions
- `POST /cut-clip` - Cut video clip with FFmpeg
- `POST /process-full` - Full automated pipeline
- `POST /chat` - Direct chat with Grok-style AI

## Chat API Usage
Send POST to `/chat` with JSON body:
```json
{
  "message": "Your question here",
  "conversation": []  // Optional: previous messages for context
}
```

Response:
```json
{
  "success": true,
  "reply": "AI response here",
  "conversation": [...]  // Full conversation history
}
```

## AI Personality (Grok-Style)
The AI is configured to be:
- Direct and unfiltered - no corporate speak
- Witty with dry intellectual humor
- First principles reasoning
- Willing to challenge assumptions
- Honest about uncomfortable truths

## Output Formats
- 9:16 (Shorts/Reels/TikTok)
- 1:1 (Square/Carousel)
- 4:5 (Feed)
- 16:9 (Landscape)
