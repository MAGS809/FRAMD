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

### Tone & Voice (Strict)
The AI **is**: calm, clear, grounded, subtly witty when appropriate, confident without arrogance.

The AI **is never**: sarcastic, smug, preachy, outraged, juvenile, crude, sexual, graphic, meme-brained.

If humor appears, it is sly, intelligent, and brief — never the point of the piece.

If content becomes graphic: "The story gets graphic here — we're skipping that part."

### Script & Content Rules
- Hooks must be direct, not clickbait
- Metaphors allowed only if brief and clarifying
- Sentences should flow — not clipped, not robotic
- Every line logically leads to the next
- Ending must close the loop (return to core idea or implication)
- No filler. No buzzwords. No trend-chasing language.

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
- **Discover Feed**: Tinder-style swipeable cards for browsing AI-generated content, allowing users to like, skip, and provide feedback for AI personalization.

**Technical Implementations:**
- **Flask Web Application**: `app.py` serves as the backend for REST API endpoints.
- **Context Engine**: `context_engine.py` manages the core processing pipeline and conversation memory.
- **Unified Content Engine**: A single AI brain handles both content creation and clipping modes.
- **Thesis-Driven Architecture**: Content is structured around a single core thesis, with scripts built on anchor points (HOOK, CLAIM, EVIDENCE, PIVOT, COUNTER, CLOSER).
- **AI Reasoning**: A 4-question framework guides content generation.
- **Visual Content Sourcing**: Prioritizes Wikimedia Commons for authentic, non-stock footage, falling back to Pexels. Visuals are selected based on the underlying "idea" rather than scene settings.
- **Legal Media Asset Library**: Stores links with full licensing metadata.
- **Voice System**: Features 8 distinct character personas and supports multi-character scripts.
- **Caption System**: Word-synced captions rendered via FFmpeg drawtext filters with dynamic phone-frame preview.
- **Output Formats**: Supports 9:16, 1:1, 4:5, 16:9 aspect ratios.
- **Scene Composer**: Enables background picking and character layering with Pillow-based background removal.
- **Stage Directions**: AI-generated audio direction layer with effects like [PAUSE], [BEAT], [SILENCE], [TRANSITION].
- **Sound FX System**: 10 synthesized effects (e.g., whoosh, impact) are auto-mixed into videos using [SOUND: type] tags.
- **Token Cost System**: Displays per-feature token costs, with a session-based spending tracker.
- **Character Generation**: DALL-E integration for AI-generated characters.
- **AI Learning System**: Tracks successful projects to learn user style, unlocking auto-generation after sufficient progress.
- **Video Feedback System**: Like/Dislike buttons on generated videos. If disliked, users can add comments and send back to AI for refinement. Free users get 3 revisions, Pro gets unlimited.
- **AI Self-Improvement**: Tracks feedback patterns, success rates, and injects learned patterns into future prompts. Stores feedback in VideoFeedback table and tracks global patterns in GlobalPattern table.
- **Auto-Generator System**: Fully configurable content auto-generation with user-controlled settings (tone, format, length, voice style, topics). Locked until 5 liked projects (faded button shows "X/5 liked to unlock"), then unlocked for AI-powered content creation using learned patterns.
- **Subscription Model**: Free tier for script writing and basic features; Pro tier ($10/month) for unlimited video generation, hosting, and unlimited revisions via Stripe.
- **NSFW Content Filter**: Blocks inappropriate images from visual sources.

## External Dependencies
- **Python 3.11**
- **Krakd AI**: (powered by xAI grok-3) for chat, script generation, and visual curation.
- **ElevenLabs**: Primary Text-to-Speech (TTS) engine for voiceovers.
- **OpenAI**: Fallback for audio transcription and voiceover.
- **FFmpeg**: For video/audio processing and caption rendering.
- **Stripe**: For subscription management and payment processing.
- **Wikimedia Commons**: Primary source for visual content.
- **Pexels**: Fallback source for visual content.
- **DALL-E**: For AI-generated character images.