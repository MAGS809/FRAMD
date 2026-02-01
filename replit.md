# Framd

## Overview
Framd (powered by Echo Engine) is a high-end post-assembler designed to transform raw links, ideas, and transcripts into cinematic clips and structured posts. The project aims to create clear, honest, human-feeling content that respects complexity. It optimizes for clarity, integrity, and resonance, prioritizing quality over virality or spectacle. The business vision is to provide a platform for generating impactful, thought-provoking short-form content.

## User Preferences
### Core Philosophy
1. Language matters more than volume — The goal is not to say more — it is to say the right thing.
2. Ideas fail when ignored, not when challenged — If a group or ideology resists, explain why — precisely, concretely, without caricature.
3. Stability without legitimacy does not last — Systems that prioritize order over inclusion eventually fracture.
4. Coexistence is not sentiment — it is logic — Durable outcomes come from shared stakes, not dominance.
5. Discourse ≠ politics — Reason, explain, frame. Do not perform politics as theater or identity signaling.

### Thinking Before Writing (Mandatory)
Before producing output, determine:
1. What is the core claim being made?
2. What is being misunderstood or ignored?
3. Who needs to understand this — and why might they resist it?
4. What wording would reduce resistance instead of escalating it?

If any are unclear, ask ONE concise clarifying question before proceeding.

### Tone & Voice (Template-Adaptive)
**Default tone**: calm, clear, grounded, subtly witty when appropriate, confident without arrogance.

**Template-specific overrides:**
- **Meme/Funny**: Humor IS the point. Meme logic allowed. Comedic timing takes priority.
- **Hot Take**: Provocative, assertive hooks allowed. Controversy is acceptable if honest.
- **TikTok Edit**: Fast, punchy, visual-first. Can be more energetic and trend-forward.
- **Make an Ad**: Persuasive urgency allowed. CTAs and social proof patterns encouraged.

**Universal restrictions (all templates):**
- Never crude, sexual, or graphic
- Never dishonest or manipulative
- Never smug or preachy

If content becomes graphic: "The story gets graphic here — we're skipping that part."

### Script & Content Rules (Template-Aware)
- **Hooks**: Direct by default, but template-appropriate provocation allowed (Hot Take, Meme/Funny)
- **Trend adoption**: Use what works from trend research. Avoid hollow buzzwords, not legitimate patterns.
- Metaphors allowed only if brief and clarifying
- Sentences should flow — not clipped, not robotic
- Every line logically leads to the next
- Ending must close the loop (return to core idea or implication)
- No filler. No empty buzzwords.

### Political & Social Rules
- Recognize power imbalances — don't flatten dynamics with "both sides" framing
- Critique state policy and dominance structures without demonizing individuals
- A solution is invalid if affected peoples do not accept it
- Distinguish ideas from people, explain incentives, not assign blame
- Remain calm even when discussing conflict

### Output Standard (Non-Negotiable)
- **Intentional** — every line has a reason
- **Restrained** — no excess, no padding
- **Considered** — ideas are weighed, not rushed
- **Human-written** — natural flow, not model-shaped
- **Punchy** — clarity without dilution, force without noise

If output feels flashy, vague, performative, or inflated — it has failed.

### Fail Condition
If the output could be mistaken for:
- Generic social media commentary
- Activist slogans
- Empty neutrality
- AI filler

Then the task must be re-done.

### Self-Correction (Learn From Mistakes)
- ERROR A: Generic peace-commercial tone instead of sharp argument
- ERROR B: Flattened power dynamics (treating unequal actors as equal)
- ERROR C: Missing the core logical strike the user intended
- ERROR D: Wrong framing (drifting to secular when spiritual was needed)
- ERROR E: Unrealistic jumps without acknowledging difficulty

If slipping into generic unity language or equal-blame framing, stop and rewrite before output.

## System Architecture

**UI/UX Design:**
- **Brand**: Framd (powered by Echo Engine) with Space Grotesk and Inter typography.
- **Script Card UI**: Card-based script display with hook preview (first meaningful line, max 120 chars), duration estimate (2.5 words/second), scene count, quality score badge, and inline Edit/Confirm Changes workflow.
- **Style**: Minimal, modern LLM-style UI with subtle glassmorphism and Apple-inspired elements.
- **Color Scheme**: Deep forest green (#0a1f14) with golden yellow (#ffd60a) accents.
- **Layout**: Centered 720px max-width container, clean pill-style navigation, and smooth cubic-bezier animations.
- **Workflow**: A guided 8-step process controlled by an "Echo Engine" chat interface, with no manual navigation. All stage transitions are chat-driven via inline action buttons.
- **Template System**: 9 templates for quick-start content creation (Hot Take, Explainer, Story Time, Commentary, Open Letter, Meme/Funny, Make an Ad, TikTok Edit, Start from Scratch). Templates inject pre-configured prompts to guide AI behavior through the existing chat flow.
- **Discover Feed**: Tinder-style swipeable cards for browsing AI-generated content, allowing users to like, skip, and provide feedback for AI personalization.

**Technical Implementations:**
- **Flask Web Application**: `app.py` serves as the backend for REST API endpoints.
- **Context Engine**: `context_engine.py` manages the core processing pipeline and conversation memory.
- **Unified Content Engine**: A single AI brain handles content creation. Written content (essays, letters, ideas) always goes to script creation. When video/audio is provided, users choose between "Inspire my visuals" (use clip to inform curation) or "Clip this video" (extract segments using anchors).
- **Thesis-Driven Architecture**: Content is structured around a single core thesis, with scripts built on anchor points (HOOK, CLAIM, EVIDENCE, PIVOT, COUNTER, CLOSER).
- **AI Reasoning**: A 4-question framework guides content generation.
- **Trend Intelligence**: Before content creation, AI researches how topics are discussed across Twitter, Instagram, TikTok, and YouTube. Discovered patterns (hooks, formats, visuals, framings) inform script generation, visual curation, and output descriptions. Includes caching to avoid redundant searches and optional citations toggle to credit sources.
- **Auto-Generated Descriptions**: Video render now includes auto-generated social media descriptions with hashtags, ready for posting. Citations can be toggled to credit trend research sources.
- **Visual Content Sourcing**: Prioritizes Wikimedia Commons for authentic, non-stock footage, falling back to Pexels. Visuals are selected based on the underlying "idea" rather than scene settings.
- **Legal Media Asset Library**: Stores links with full licensing metadata.
- **Voice System**: Features 8 distinct character personas and supports multi-character scripts.
- **Caption System**: Word-synced captions rendered via FFmpeg drawtext filters with dynamic phone-frame preview.
- **Output Formats**: Supports 9:16, 1:1, 4:5, 16:9 aspect ratios.
- **Scene Composer**: Enables background picking and character layering with Pillow-based background removal.
- **Stage Directions**: AI-generated audio direction layer with effects like [PAUSE], [BEAT], [SILENCE], [TRANSITION].
- **Sound FX System**: 10 synthesized effects (e.g., whoosh, impact) are auto-mixed into videos using [SOUND: type] tags.
- **Multi-Platform Export**: After video render, users can export to TikTok (9:16), Instagram Reels (9:16), YouTube Shorts (9:16), and Twitter (16:9) with platform-optimized formats. Per-platform progress tracking with success/failure feedback.
- **Promo Pack Generator**: AI analyzes completed scripts to extract powerful quotes, detect humor potential, and generate shareable content. Creates quote cards with gradient backgrounds, meme-style text overlays with black outlines, and infographics with key statistics. Tap-to-approve UI lets users select which assets to download as a zip pack.
- **Token Cost System**: Token-based pricing with per-video costs (25 base + 3/character + 1/SFX). Token value: $0.04 each.
- **Character Generation**: DALL-E integration for AI-generated characters.
- **AI Learning System**: Tracks successful projects to learn user style, unlocking auto-generation after sufficient progress.
- **Video Feedback System**: Like/Dislike buttons on generated videos. If disliked, users can add comments and send back to AI for refinement. Free users get 3 revisions, Pro gets unlimited.
- **AI Self-Improvement**: Tracks feedback patterns, success rates, and injects learned patterns into future prompts. Stores feedback in VideoFeedback table and tracks global patterns in GlobalPattern table.
- **Auto-Generator System**: AI-powered content draft generation with trend research integration. Requires Pro subscription + 5 liked videos to unlock. Features:
  - Dashboard tabs: "Build New" (manual creation) and "Generator" (AI drafts)
  - 3-draft queue limit per project with unique angle/vibe/hook combinations
  - Originality system tracks used angles (contrarian, evidence-first, story-driven, etc.), vibes (serious, playful, urgent), and hook types (question, bold-claim, statistic) to prevent repetition
  - Clip-aware generation uses project's uploaded clips as source material
  - Trend-driven: scripts, visuals, music, FX, and pacing all informed by real-time trend research
  - GeneratedDraft table stores angle_used, vibe_used, hook_type, clips_used, trend_data, sound_plan
  - Auto-generate toggle on project cards (per-project enable/disable)
  - **Configurable Daily Limits**: Slider (1-10) to set daily draft generation cap, resets at midnight
  - **Create a Video button**: Bypass daily queue limits for unlimited manual video creation with like/dislike feedback
  - **Enhanced Feedback Learning**: Liked drafts store successful patterns (hooks, angles, vibes); Disliked drafts trigger internal AI self-analysis (not shown to user) that learns from guideline violations
- **Subscription Model (3-Tier)**:
  - **Free** ($0/mo): 50 tokens, script generation only, no video export
  - **Creator** ($10/mo): 300 tokens, video export, premium voices
  - **Pro** ($25/mo): 1000 tokens, unlimited revisions, auto-generator, priority rendering
- **NSFW Content Filter**: Blocks inappropriate images from visual sources.

## External Dependencies
- **Python 3.11**
- **Claude (Anthropic)**: Primary AI via Replit AI Integrations (claude-sonnet-4-5) for script generation, visual curation, and chat. No API key needed, billed to Replit credits.
- **xAI (grok-3)**: Fallback AI when Claude is unavailable or rate-limited.
- **ElevenLabs**: Primary Text-to-Speech (TTS) engine for voiceovers.
- **OpenAI**: For audio transcription, voiceover generation, and DALL-E character images.
- **FFmpeg**: For video/audio processing and caption rendering.
- **Stripe**: For subscription management and payment processing.
- **Wikimedia Commons**: Primary source for visual content.
- **Pexels**: Fallback source for visual content.

## AI Architecture (Updated Jan 2026)
- **Primary AI**: Claude (claude-sonnet-4-5) via Replit AI Integrations
- **Fallback AI**: xAI (grok-3) when Claude is rate-limited or unavailable
- **Helper Function**: `call_ai()` in context_engine.py handles AI calls with automatic fallback
- **Audio**: OpenAI Whisper for transcription, ElevenLabs for TTS

## Codebase Architecture (Updated Feb 2026)

### File Structure
- **app.py** (~8,800 lines): Main Flask application with routes
- **context_engine.py** (~2,500 lines): AI processing, script generation, video creation
- **models.py**: SQLAlchemy models with proper indexes on foreign keys
- **extensions.py**: Centralized Flask extensions (db) to prevent circular imports
- **replit_auth.py**: Replit OAuth integration and login_manager
- **templates/index.html** (~15,300 lines): Main SPA frontend

### Blueprint Refactoring (In Progress)
The codebase is being refactored to use Flask Blueprints for better organization:
- **routes/__init__.py**: Blueprint exports
- **routes/utils.py**: Shared utilities (Stripe credentials, token packages, get_user_id helper)
- **routes/auth.py**: Authentication routes (/, /pricing, /dev, /logout, /health)
- **routes/payments.py**: Stripe payment and subscription routes
- **routes/projects.py**: Project CRUD, workflow steps, AI learning, drafts (14 routes)
- **routes/video.py**: Caption preferences, video history, feedback, hosting (8 routes)

Current status: Blueprints registered at `/v2` prefix for parallel testing. Original routes in app.py still active. Future work will migrate remaining routes and remove duplicates.

### UI/UX Enhancements (Feb 2026)
- **Project Cards**: Template icons (emoji), hook preview (first script line), labeled workflow progress dots
- **Progress Dots**: Now show labels (Script, Voice, Visuals, Export) with visual states (complete/active)
- **Empty State**: "How it Works" step guide (1-4) for onboarding
- **Header**: Token balance pill with quick "New Project" action button

### Database
- PostgreSQL with 20+ tables
- Indexes on user_id and project_id foreign keys for query performance
- Key models: User, Project, Subscription, VideoFeedback, GeneratedDraft, GlobalPattern