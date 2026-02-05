# Framd

## Overview
Framd, powered by Echo Engine, is a post-assembler designed to convert diverse inputs (links, ideas, transcripts) into cinematic clips and structured posts. Its primary goal is to generate clear, honest, and impactful short-form content that resonates deeply with human feeling and respects complexity, prioritizing integrity and resonance over mere virality. The project aims to establish a platform for creating thought-provoking content with significant market potential.

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
- **Brand**: Framd (powered by Echo Engine) using Space Grotesk and Inter typography.
- **Style**: Minimal, modern LLM-style UI with glassmorphism and Apple-inspired elements.
- **Color Scheme**: Deep forest green (#0a1f14) with golden yellow (#ffd60a) accents.
- **Workflow**: Guided 8-step process via an "Echo Engine" chat interface; chat-driven stage transitions.
- **Template System**: 9 templates (e.g., Hot Take, Explainer, Meme/Funny) to guide AI content creation.
- **Discover Feed**: Tinder-style swipeable cards for AI-generated content, enabling user feedback and personalization.

**Technical Implementations:**
- **Backend**: Flask web application (`app.py`) with modular Blueprint architecture:
  - `routes/chat.py` (chat_bp) - Chat/conversation endpoints (/api/chat)
  - `routes/api.py` (api_bp) - Jobs API endpoints (/api/jobs, /api/projects)
  - `routes/pages.py` (pages_bp) - Page templates (/, /pricing, /terms, /privacy, /faq, /dev, /chat)
  - `routes/auth.py` (auth_bp) - Authentication under /v2 prefix
  - `routes/payments.py` (payments_bp) - Stripe webhooks under /v2 prefix
- **Core Processing**: `context_engine.py` manages AI processing pipeline and conversation memory.
- **Unified Content Engine**: A single AI for content creation, handling text to script conversion and video/audio processing.
- **Thesis-Driven Architecture**: Content is structured around a single core thesis with specific anchor points (HOOK, CLAIM, EVIDENCE, PIVOT, COUNTER, CLOSER).
- **AI Reasoning**: Utilizes a 4-question framework for content generation.
- **Trend Intelligence**: AI researches social media platforms for current trends to inform script generation, visual curation, and output descriptions.
- **Visual Content Sourcing**: Prioritizes AI-generated visuals (DALL-E), with stock (Wikimedia/Pexels) as fallback.
- **Voice System**: 8 distinct character personas with multi-character script support.
- **Caption Template System**: 5 templates (Bold Pop, Clean Minimal, Boxed, Gradient Glow, Street Style) with word-by-word highlighting synced to audio. OpenAI Whisper extracts precise word-level timestamps.
- **Output Formats**: Supports 9:16, 1:1, 4:5, 16:9 aspect ratios.
- **Scene Composer**: Enables background picking and character layering.
- **Multi-Platform Export**: Export to TikTok, Instagram Reels, YouTube Shorts, and Twitter with platform-optimized formats.
- **Promo Pack Generator**: AI analyzes scripts to extract quotes and generate shareable content like quote cards.
- **Token Cost System**: Token-based pricing with a per-video cost structure.
- **AI Learning System**: Tracks successful projects and user style for auto-generation and refinement.
- **AI Self-Critique System**: Post-export, AI analyzes its own work, identifies strengths/weaknesses, scores intent fulfillment, and stores learnings.
- **Unified AI Philosophy**: ONE AI brain (Claude) with consistent ethos across all modes (Remix, Clipper, Simple Stock).
- **Auto-Generator System**: AI-powered content draft generation integrated with trend research, offering multiple drafts.
- **Subscription Model**: Four tiers (Free, Starter, Pro, Pro Creator) with token-based pricing and varying features.
- **NSFW Content Filter**: Blocks inappropriate images.
- **AI Remix Mode**: Vibe-based video creation with surgical API orchestration. Reference files set direction (not integrated), Claude orchestrates Runway API + stock + user content files. Produces agency-quality videos based on extracted vibe + topic research.
- **Vibe Extraction System**: Analyzes reference material to extract mood/energy/pacing DIRECTION only. Reference is never integrated into final video.
- **File Type Distinction**: Reference files (vibe extraction only) vs Content files (prioritized in final video) vs Stock (gap-filling) vs Runway output (AI-generated core).
- **Surgical Orchestration Engine**: Generates precise instructions for Runway + Shotstack to work in conjunction. Each API receives exact parameters.
- **Next-gen Clipper Mode**: Script-guided video creation, where AI clips and edits according to script structure and chosen template style.
- **Custom Template System**: Users create personal templates with element-level precision using frame-by-frame element detection and interactive editing.
- **Visual Director AI**: Pre-plans all visuals for coherence, consistency (color palette, style), and editing patterns based on content type.
- **Preview Protection System**: Watermarked previews; "Download" accepts, "Needs Changes" triggers revisions, "Get Final Video" removes watermark (uses tokens).
- **Intelligent Source Mixing**: AI decides optimal visual source per scene (stock for realism, DALL-E for abstract, user content prioritized).
- **Source Merging Engine**: Unified post-processing blends all sources using color grading profiles, film grain, and transition effects via FFmpeg.
- **Database**: PostgreSQL with 25+ tables for AI learning and system data.

## External Dependencies
- **Python 3.11**
- **Claude (Anthropic)**: Primary AI orchestrator for script generation, visual curation, chat, and multi-source video orchestration.
- **xAI (grok-3)**: Fallback AI.
- **ElevenLabs**: Primary Text-to-Speech (TTS) engine.
- **OpenAI**: For audio transcription (Whisper), voiceover generation, and DALL-E character images.
- **Runway API**: AI video generation for Remix mode. Uses image learning to build templates, generates video transformations.
- **Video Editor API** (Shotstack recommended): JSON-based video editing API that follows AI instructions to merge Runway + stock + user files. Chosen for: developer-first architecture, built-in AI features, enterprise-grade infrastructure, consistent per-minute pricing ($0.25-0.40/min). Cost integrated into per-video pricing.
- **FFmpeg**: For video/audio processing and caption rendering.
- **Stripe**: For subscription management and payment processing.
- **Wikimedia Commons**: Primary source for visual content.
- **Pexels**: Fallback source for visual content.

## Mode Pricing (per 30 seconds)
- **Remix**: $4.00 - $8.00 depending on quality tier (see below)
- **Clipper**: $6.60 - $11.10 (AI scene detection + editing complexity)
- **Simple Stock**: $0.99 (Stock + DALL-E + FFmpeg only)

## Quality Tiers (Remix Mode)
- **Good** ($4/30s): Gen-3 Turbo model - fast generation, solid quality for most content
- **Better** ($6/30s): Gen-4 Turbo model - enhanced detail and motion consistency
- **Best** ($8/30s): Gen-4 Aleph model - cinema-grade output with maximum fidelity

Quality disclaimer: "Quality tier affects visual generation only. It won't change your video's direction, pacing, or message — just how sharp and polished the final output looks."

## Generation Queue System
- Sequential request processing with 2-second delay between Runway API calls
- Automatic retry with exponential backoff (5s → 10s → 20s) for rate limits
- User-friendly error messages replace technical errors
- Progress tracking: "Generating scene X of Y... ~N min remaining"
- Toast notifications when video is ready
- Loading spinner in sidebar during generation