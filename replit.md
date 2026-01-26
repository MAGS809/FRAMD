# Calligra

## Overview
Calligra is a high-end post-assembler utilizing the Framd workflow. The core AI, **Framd**, transforms raw links into cinematic clips and abstract ideas into structured posts.

## UI Design (Apple-Inspired)
- **Typography**: Inter font family with refined weights (300-700)
- **Color Scheme**: Deep forest green (#0a1f14) with golden yellow (#ffd60a) accents
- **Glassmorphism**: Panels use backdrop-filter blur with subtle transparency
- **Animations**: Smooth cubic-bezier transitions (0.4, 0, 0.2, 1)
- **Components**: Apple-style segmented controls, floating token panel
- **Layout**: Centered 720px max-width container with generous spacing

## Core Principle
- **Framd™** - No clip exists without a script.
- **Reasoning** - No script exists without thinking.

## AI Constitution & Operating Principles

### Purpose
Calligra is a thinking engine, not a content factory. Its purpose is to turn ideas into clear, honest posts, respect the audience's intelligence, and prioritize meaning over flash.

### Core Workflow (DO NOT BREAK)
**Script → Visual Intent → Safe Assets → Edit → Post**
- Every visual exists to serve the script.
- No "cut first, think later".

### Tone & Voice
- **Primary**: Calm, confident, clear, restrained, thoughtful.
- **Humor**: Intelligent, subtle, observational.timing-based. Rule: If humor can be removed and message works, it's correct.

### Hard Boundaries
- **No Juvenile Humor**: No bathroom, sex, or shock value.
- **Sexual/Graphic Policy**: Default to silence. No sexualized visuals (Hard Ban on bikinis, lingerie, erotic poses).
- **Red Flags**: No internet slang, memes, forced metaphors, or preachy tone.

### Visual Content
- **Allowed Sources**: Pexels, Unsplash, Pixabay, Mixkit, Coverr, Wikimedia Commons.
- **Criteria**: No celebrities, no brands, non-sexual.

### Political & Social
- No ragebait, slogans, or demonization. Expose contradictions calmly.

## Final Brand Principle
**Clarity over noise. Meaning over metrics. Thought before output.**

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
- `POST /refine-script` - Conversational script refinement with AI
- `POST /curate-visuals` - AI creates visual board with Pexels videos based on script
- `POST /search-pexels-videos` - Search Pexels for legally licensed videos
- `POST /save-asset` - Save verified legal asset to library
- `POST /search-assets` - Search asset library by tags
- `POST /create-checkout-session` - Create Stripe checkout for token purchase
- `POST /add-tokens` - Add tokens after successful payment

## Legal Media Asset Library
Assets are stored with full licensing metadata:
- **Allowed Licenses**: CC0, Public Domain, CC BY, CC BY-SA, Pexels License
- **Required Fields**: source_page, download_url, license, license_url
- **Safe Flags**: no_sexual, no_brands, no_celeb
- **Sources**: Pexels (integrated), Wikimedia Commons (planned)

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

## Voice System
- **6 Voice Options**: Alloy (neutral), Echo (deep male), Fable (British), Onyx (authoritative male), Nova (warm female), Shimmer (clear female)
- **Voice Previews**: Click "Preview" on any voice card to hear a sample
- **Multi-Character Support**: Click "Detect Characters" to analyze script for multiple speakers and assign different voices to each

## Caption System
- **Toggle**: Enable/disable captions for final video
- **Styles**: Bold Center (TikTok style), Typewriter (reveal effect), Highlight (key words pop), Minimal (clean subtitles)
- **Rendering**: FFmpeg drawtext filter with text sanitization

## Script Formatting
- AI formats final scripts with `CHARACTER_NAME: dialogue` for voice acting
- No meta-narration - just the lines to be spoken aloud
- Multi-character scripts get separate voice generation and audio combining
