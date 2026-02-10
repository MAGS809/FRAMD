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
- **Style**: Minimal, modern LLM-style UI with dark theme (#0d0d0d bg, #ffd60a gold accents).
- **Workflow**: Brief-first chat interface — user describes idea, optionally uploads videos, AI builds scene plan.
- **Entry Point**: "What do you want to make?" with text input + upload button. Mode cards (Remix, Clipper, Stock) are optional presets below.
- **Scene Plan UI**: Interactive scene-by-scene display with source types, containers, costs, and approve/revise buttons.
- **Community Templates**: Matched when no uploads provided; 3-tier system (exact → close → AI-generated).

**Unified Pipeline Architecture:**
- **Brief-First Flow**: User describes idea → optionally uploads videos (each can be clip OR remix) → AI builds scene plan → cost estimate → approve → generate.
- **Modes became presets**: Remix, Clipper, Stock are shortcuts that pre-fill context, not rigid gates. Users never locked into one capability.
- **Per-video processing**: Each uploaded video gets its own clip/remix toggle. Clip extracts best moments; remix extracts skeleton/vibe for AI transformation.
- **Visual Director** (`services/visual_director.py`): AI establishes visual structure BEFORE sourcing. Determines layout_type, container_style, color_palette, motion_style, transitions, grain, contrast. Stock footage is NEVER raw — always placed INSIDE visual containers (cards, frames, split screens).
- **Scene Composer** (`services/scene_composer.py`): Orders scenes by narrative anchor structure (HOOK → CLAIM → EVIDENCE → PIVOT → COUNTER → CLOSER). Identifies and fills timeline gaps. Builds unified timeline with consistent post-processing.
- **Community Template System** (`routes/community.py`): 3-tier template matching (exact topic+tone → broadened → AI-generated with trend research). Watermark rules: F/Echo for AI-generated (removed with any edit), F/creatorUsername for community (requires AI-evaluated meaningful structural change).

**Technical Implementations:**
- **Backend**: Flask web application (`app.py`) with modular Blueprint architecture:
  - `routes/chat.py` (chat_bp) - Chat/conversation endpoints (/api/chat) with unified pipeline awareness
  - `routes/pipeline.py` (pipeline_bp) - Unified pipeline endpoints (upload-source, process-source, build-scene-plan, estimate-cost)
  - `routes/community.py` (community_bp) - Community template matching, creation, watermark removal evaluation
  - `routes/api.py` (api_bp) - Jobs API endpoints (/api/jobs, /api/projects)
  - `routes/pages.py` (pages_bp) - Page templates (/, /pricing, /terms, /privacy, /faq, /dev, /chat)
  - `routes/auth.py` (auth_bp) - Authentication under /v2 prefix
  - `routes/payments.py` (payments_bp) - Stripe webhooks under /v2 prefix
- **Core Processing**: `context_engine.py` manages AI processing pipeline and conversation memory.
- **Database Models**: ProjectSource (per-video with clip/remix mode), CommunityTemplate (with watermark logic), ScenePlan (per-scene with source_type, visual_container, cost). Project model extended with brief, visual_structure, pipeline fields.
- **Thesis-Driven Architecture**: Content structured around core thesis with anchor points (HOOK, CLAIM, EVIDENCE, PIVOT, COUNTER, CLOSER).
- **Trend Intelligence**: AI researches social media platforms for current trends.
- **Visual Content Sourcing**: Priority: user content → AI-generated (DALL-E/Runway) → stock (Wikimedia/Pexels). Stock always inside visual containers.
- **Voice System**: 8 distinct character personas with multi-character script support.
- **Caption Template System**: 5 templates (Bold Pop, Clean Minimal, Gradient Glow, Street Style, Boxed) with word-by-word highlighting synced to audio via AssemblyAI. Captions are bundled free with every video. Centralized in `services/caption_service.py`.
- **Output Formats**: Supports 9:16, 1:1, 4:5, 16:9 aspect ratios.
- **Overlay System**: 7 overlay types with two-tier save (Recent + Saved Templates). Volume cap at $29.99/month.
- **Integrated Preview Pipeline** (`services/preview_service.py`): Preview IS the pipeline. When scene plan is built, ALL scenes render immediately by source type: clip (extract from source), remix (frame → Runway API), stock (Pexels search + download), DALL-E (image generation → video conversion). Each scene saves its rendered clip to `ScenePlan.rendered_path`. Frontend shows rendering progress first (per-scene status), then displays editable scene plan with video thumbnails once all scenes are ready. Scene descriptions are editable — changing triggers re-render of that single scene via `/api/project/{id}/scene/{scene_id}/re-render`. On approval, worker stitches pre-rendered clips with transitions (no regeneration). Scene 1→2 transition preview auto-generated after all scenes render. Sidebar shows spinner during generation; toast notification when complete.
- **Preview-First Flow**: Rendering → Editable Scene Plan → Approve → Final Assembly. User can edit descriptions, re-render individual scenes, then approve. "Come back later" messaging with sidebar spinner and notification.
- **Source Merging Engine**: Unified post-processing with color grading, grain, transitions via FFmpeg.
- **Database**: PostgreSQL with 28+ tables including community_templates, project_sources, scene_plans.

## External Dependencies
- **Python 3.11**
- **Claude (Anthropic)**: Primary AI orchestrator for script generation, visual curation, chat, and multi-source video orchestration.
- **xAI (grok-3)**: Fallback AI.
- **ElevenLabs**: Primary Text-to-Speech (TTS) engine.
- **OpenAI**: For audio transcription (Whisper), voiceover generation, and DALL-E character images.
- **AssemblyAI**: Primary caption engine for all video captions ($0.15/hr, best-in-class accuracy). Integrated via `services/caption_service.py` with OpenAI Whisper as automatic fallback. Supports SRT, VTT, and ASS export formats. API endpoint: `/transcribe-captions`.
- **Runway API**: AI video generation for Remix mode. Uses image learning to build templates, generates video transformations.
- **Video Editor API** (Shotstack recommended): JSON-based video editing API that follows AI instructions to merge Runway + stock + user files. Chosen for: developer-first architecture, built-in AI features, enterprise-grade infrastructure, consistent per-minute pricing ($0.25-0.40/min). Cost integrated into per-video pricing.
- **FFmpeg**: For video/audio processing and caption rendering.
- **Stripe**: For subscription management and payment processing.
- **Wikimedia Commons**: Primary source for visual content.
- **Pexels**: Fallback source for visual content.

## Mode Pricing (per 30 seconds)
- **Remix**: $4.00 - $8.00 depending on quality tier (see below)
- **Clipper**: $0.49 per clip base, up to $1.49 with overlays. $29.99/month cap (free clips after cap)
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