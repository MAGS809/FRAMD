# Calligra

## Overview
Calligra is a high-end post-assembler utilizing the Krakd workflow. The core AI, **Krakd**, transforms raw links into cinematic clips and abstract ideas into structured posts.

## UI Design (Apple-Inspired)
- **Typography**: Inter font family with refined weights (300-700)
- **Color Scheme**: Deep forest green (#0a1f14) with golden yellow (#ffd60a) accents
- **Glassmorphism**: Panels use backdrop-filter blur with subtle transparency
- **Animations**: Smooth cubic-bezier transitions (0.4, 0, 0.2, 1)
- **Components**: Apple-style segmented controls, floating token panel
- **Layout**: Centered 720px max-width container with generous spacing

## Core Principle
- **Krakd™** - No clip exists without a script.
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
- **Humor**: Wide range allowed - from intelligent observational to absurdist dark comedy.
- **Adult Humor OK**: Profanity, dark jokes, edgy meme-style content, absurdist comedy all permitted.
- **Style Examples**: SpongeBob reaction memes, Sesame Street dark edits, South Park irreverence, deadpan absurdism.

### Hard Boundaries
- **Sexual/Graphic Policy**: No sexualized visuals (Hard Ban on bikinis, lingerie, erotic poses). Adult language/jokes are fine; explicit imagery is not.
- **Still Avoid**: Forced metaphors, preachy tone, corporate speak.

### Visual Content
- **Primary Source**: Wikimedia Commons (documentary/archival - authentic, non-stock)
- **Fallback Source**: Pexels (only if <6 results from Wikimedia)
- **Criteria**: No celebrities, no brands, non-sexual.
- **Search Ladder**: Wikimedia → Pexels fallback → Query expansion

### Political & Social
- No ragebait, slogans, or demonization. Expose contradictions calmly.
- Recognize power imbalances - don't flatten dynamics with "both sides" framing
- Critique state policy and dominance structures without demonizing individuals
- A solution is invalid if affected peoples do not accept it

### Self-Correction Framework (Learn From Mistakes)
When output fails to match user intent, Krakd must:

**Step 1: Identify Errors**
- ERROR A: Generic peace-commercial tone instead of sharp argument
- ERROR B: Flattened power dynamics (treating unequal actors as equal)
- ERROR C: Missing the core logical strike the user intended
- ERROR D: Wrong framing (drifting to secular unity language when spiritual was needed)
- ERROR E: Unrealistic jumps without acknowledging difficulty and sacrifice

**Step 2: Upgrade Reasoning**
- Replace "safe" templates with user's actual structure
- Recognize power imbalances and greater responsibility
- Tie holiness/meaning to moral obligation, not propaganda
- Ending should be philosophical challenge, not motivational poster

**Step 3: Hard Constraints**
- Maintain distinct character voices (curious vs authoritative)
- Keep scene structure, timing, and visual/cut directions
- Avoid propaganda imagery and sentimental unity slogans
- No "all sides equally responsible" framing
- Include specific points user requested

**Step 4: Self-Check Before Output**
"If slipping into generic unity language or equal-blame framing, stop and rewrite before output."

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
- **Krakd AI** (powered by xAI grok-3) for text generation - chat, script generation, idea analysis, visual curation
- **OpenAI** for audio only - transcription (gpt-4o-mini-transcribe) and voiceover (tts-1-hd)
- **FFmpeg** for video/audio processing
- **MoviePy** for additional video manipulation

### Token Pricing (Updated for Krakd)
- 100 tokens = $2.00 (60% cheaper with Krakd)
- 500 tokens = $8.00 (60% cheaper with Krakd)
- 2000 tokens = $25.00 (58% cheaper with Krakd)

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
Assets are stored with full licensing metadata (LINKS ONLY - downloaded on-demand):
- **Allowed Licenses**: CC0, Public Domain, CC BY, CC BY-SA, CC BY 4.0, CC BY-SA 4.0, Pexels License
- **Required Fields**: source_page, download_url, license, license_url, commercial_use_allowed, attribution_text
- **Safe Flags**: no_sexual, no_brands, no_celeb
- **Sources**: Pexels (integrated), Wikimedia Commons (integrated)
- **License Validation**: HARD REJECT NC/ND/Editorial FIRST, then whitelist check. Uses `validate_license()` function.
- **Keyword Cache**: Stores keyword → asset associations for faster future curation
- **Compliance Statement**: "This app only downloads media from sources with explicit reuse permissions. Each asset is stored with license metadata and attribution requirements. If licensing is unclear, the asset is rejected."

### Asset Library Endpoints
- `POST /ingest` - Crawl and save verified legal asset LINKS with rejection logging
- `GET /assets` - Query cached assets by tags and content type
- `POST /save-to-cache` - Save selected asset to cache with keywords (also increments use_count)
- `POST /download-asset` - Download asset on-demand for final render (SSRF-protected)
- `POST /source/preview` - Generate verified source document preview (3-tier fallback)

## Source Document Preview System
For educational reels, users can add verified source citations that display as overlays.

### 3-Tier Fallback Logic
1. **Tier 1 - Official Preview**: For PDFs, render page 1 to PNG. For articles with og:image, use that
2. **Tier 2 - Rendered Snapshot**: Generate a clean document-style image with title, author, publisher, date, and 2-4 short excerpts (≤25 words each)
3. **Tier 3 - Title Card**: Simple fallback with source name, headline, date, and URL

### Source Preview Response
```json
{
  "ok": true,
  "method": "official_preview|rendered_snapshot|title_card",
  "image_url": "/output/source_preview_abc123.png",
  "meta": { "title": "...", "source": "...", "author": "...", "date": "...", "excerpts": [...] }
}
```

## Asset Popularity Tracking
- Each MediaAsset has a `use_count` field tracking how often it's selected
- Assets with use_count >= 3 are marked as "Popular" with a badge in the visual curation UI
- Popular assets help users quickly find proven, quality footage

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

## AI Personality (Krakd)
The AI is configured to be:
- Direct and unfiltered - no corporate speak
- Witty with dry intellectual humor
- First principles reasoning
- Willing to challenge assumptions
- Honest about uncomfortable truths

## Floating AI Assistant
- **"Tell Krakd your vision"** - Fixed golden tab at bottom of screen
- **Popup Chat** - Slides up from bottom when clicked, with dark overlay
- **Workflow Control** - Can change voice, toggle captions, adjust formats via natural language
- **Quick Suggestions** - Chip buttons for common actions (deeper voice, add captions, TikTok format)
- **Contextual Awareness** - Knows current script, selected formats, voice, and caption settings
- **Defensive Checks** - All global variable references protected against undefined errors

## Voice System
- **6 Standard Voices**: Alloy (neutral), Echo (deep male), Fable (British), Onyx (authoritative male), Nova (warm female), Shimmer (clear female)
- **6 Goofy Characters**: Cartoon (silly animated), Drama King (theatrical), Robot (monotone), Surfer Dude (chill bro), Villain (evil maniacal), Grandma (sweet rambling)
- **Voice Previews**: Click "Preview" on any voice card to hear a sample
- **Multi-Character Support**: Click "Detect Characters" to analyze script for multiple speakers and assign different voices to each

## Output Formats
- 9:16 (Shorts/Reels/TikTok)
- 1:1 (Square/Carousel)
- 4:5 (Feed)
- 16:9 (Landscape)

## Visual Curation Flow
1. **Content Type Selection**: Educational (B-roll) or Skit/Podcast (characters + scenes)
2. **Visual Guidance**: Optional input to direct visual search (e.g., "dark moody visuals", "warm natural lighting")
3. **Context Extraction**: AI analyzes script for setting, mood, and visual intent
4. **Cache-First Search**: Checks cached keyword→asset associations before external APIs
5. **Scene-by-Scene Picker**: Each scene shows:
   - Visual Context banner with setting/mood tags
   - Scene description with script segment
   - Rationale explaining why this visual matters
   - 4 video options with yellow border on selection
   - Source badge (Pexels/Wikimedia) and license badge
   - CACHED badge for previously-used assets
6. **Character Picker** (Skits only): Voice-only or assign visual models per character
7. **Auto-Selection**: First option auto-selected per scene for minimal-input flow
8. **Cache Learning**: Selected assets saved to cache with keywords for faster future curation

## Voice System
- **6 Voice Options**: Alloy (neutral), Echo (deep male), Fable (British), Onyx (authoritative male), Nova (warm female), Shimmer (clear female)
- **Voice Previews**: Click "Preview" on any voice card to hear a sample
- **Multi-Character Support**: Click "Detect Characters" to analyze script for multiple speakers and assign different voices to each

## Caption System
- **Toggle**: Enable/disable captions for final video
- **Word-Synced**: Captions display 3-4 words at a time, timed to match voiceover duration
- **Fonts**: Inter, Roboto, Poppins, Montserrat, Open Sans, Lato
- **Colors**: White, Yellow, Cyan, Lime, Magenta, Orange
- **Positions**: Top, Center, Bottom
- **Effects**: Outline, Shadow, Background box, Uppercase
- **Rendering**: Multiple FFmpeg drawtext filters with enable='between(t,start,end)' for word timing

## Timeline Developer (Unified View)
- **AI Chat First**: Dedicated AI chat section with robot emoji under Timeline header - "Chat with Krakd anytime to change visuals, voice, or captions"
- **Format First Selection**: Pick output format(s) before building visuals - supports multi-select for batch rendering
- **Multi-Format Toggle**: Preview tabs appear when 2+ formats selected to switch between previews
- **Carousel Options**: Two carousel styles - "News & Facts" (viral sharing) or "Comic Recap" (visual story panels)
- **Visual Timeline Bar**: Horizontal bar showing clip thumbnails with duration markers, clickable to select segments
- **Live Preview Panel**: Right side panel shows selected visual, caption preview, format aspect ratio, and voice indicator
- **Accordion Settings**: Collapsible sections for Voice and Captions (format moved to top)
- **Color Wheel Picker**: Native color inputs for caption text/highlight colors with preset color buttons
- **Personalized AI Note**: "Krakd learns your style over time - the more you create, the better it understands your vision"

## Export Hub
- **Social Media Buttons**: One-click export to Instagram, TikTok, YouTube, X (Twitter), Facebook, LinkedIn
- **Smart Export Flow**: Opens platform in new tab + auto-triggers download so user can upload immediately
- **Download Options**: Direct video download + "Download All Formats" when multiple formats rendered

## Chat Interface
- **File Upload**: Paperclip button allows attaching video, audio, images, PDFs, or documents
- **Auto-Transcription**: Video/audio files are automatically transcribed and included in AI context
- **Smart Upload**: File preview shows attached file name with option to remove before sending
- **Error Handling**: Guards against sending empty messages when upload fails

## Script Formatting
- AI formats final scripts with `CHARACTER_NAME: dialogue` for voice acting
- No meta-narration - just the lines to be spoken aloud
- Multi-character scripts get separate voice generation and audio combining
