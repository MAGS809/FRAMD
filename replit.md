# Framd

## Overview
Framd, powered by Echo Engine, is a sophisticated post-assembler designed to convert diverse inputs like links, ideas, and transcripts into cinematic clips and structured posts. Its core purpose is to generate clear, honest, and impactful short-form content that resonates deeply with human feeling and respects complexity. The project prioritizes clarity, integrity, and resonance over mere virality or spectacle, aiming to establish a platform for creating thought-provoking content with significant market potential.

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
- **Script Card UI**: Features hook preview, duration estimate, scene count, quality score, and inline editing.
- **Style**: Minimal, modern LLM-style UI with glassmorphism and Apple-inspired elements.
- **Color Scheme**: Deep forest green (#0a1f14) with golden yellow (#ffd60a) accents.
- **Layout**: Centered 720px max-width container, pill-style navigation, and smooth animations.
- **Workflow**: Guided 8-step process via an "Echo Engine" chat interface; chat-driven stage transitions.
- **Template System**: 9 templates (e.g., Hot Take, Explainer, Meme/Funny) to guide AI content creation.
- **Discover Feed**: Tinder-style swipeable cards for AI-generated content, enabling user feedback and personalization.

**Technical Implementations:**
- **Backend**: Flask web application (`app.py`) for REST API endpoints.
- **Core Processing**: `context_engine.py` manages AI processing pipeline and conversation memory.
- **Unified Content Engine**: A single AI for content creation, handling text to script conversion and video/audio processing.
- **Thesis-Driven Architecture**: Content is structured around a single core thesis with specific anchor points (HOOK, CLAIM, EVIDENCE, PIVOT, COUNTER, CLOSER).
- **AI Reasoning**: Utilizes a 4-question framework for content generation.
- **Trend Intelligence**: AI researches social media platforms for current trends to inform script generation, visual curation, and output descriptions, with caching and optional citations.
- **Auto-Generated Descriptions**: Videos include auto-generated social media descriptions with hashtags.
- **Visual Content Sourcing**: Prioritizes AI-generated visuals (DALL-E) for uniqueness, with stock (Wikimedia/Pexels) as last-resort fallback only.
- **Voice System**: 8 distinct character personas with multi-character script support.
- **Caption System**: Word-synced captions rendered with FFmpeg, displayed with dynamic phone-frame preview.
- **Output Formats**: Supports 9:16, 1:1, 4:5, 16:9 aspect ratios.
- **Scene Composer**: Enables background picking and character layering with Pillow-based background removal.
- **Stage Directions**: AI-generated audio direction layer with effects.
- **Sound FX System**: 10 synthesized effects auto-mixed into videos using tags.
- **Multi-Platform Export**: Export to TikTok, Instagram Reels, YouTube Shorts, and Twitter with platform-optimized formats and progress tracking.
- **Promo Pack Generator**: AI analyzes scripts to extract quotes, detect humor, and generate shareable content like quote cards and infographics.
- **Token Cost System**: Token-based pricing with a per-video cost structure.
- **Character Generation**: DALL-E integration for AI-generated characters.
- **AI Learning System**: Tracks successful projects and user style to enable auto-generation.
- **Video Feedback System**: Like/Dislike functionality with comments for AI refinement, supporting revisions.
- **AI Self-Improvement**: Tracks feedback patterns and success rates to inform future prompt generation.
- **Auto-Generator System**: AI-powered content draft generation integrated with trend research, offering multiple drafts with unique angles, vibes, and hooks. Includes configurable daily limits and enhanced feedback learning.
- **Subscription Model**: Three tiers (Free, Creator, Pro) with varying token allowances and features.
- **NSFW Content Filter**: Blocks inappropriate images from visual sources.
- **Video Re-skinning System (Re-skin Mode)**: Clip-guided video regeneration. User uploads any video, AI extracts "creative DNA" (rhythm, structure, pacing, scene timing) as the guide. AI then generates NEW visuals (DALL-E primary) matching the user's topic while preserving the original video's feel. Features:
  - Creative DNA extraction via GPT-4o Vision (scene intents, composition, colors, motion)
  - Adjustable elements (AI has creative leeway): colors, angles, visual content
  - Fixed elements (preserved from original): rhythm, structure, transitions, motion patterns
  - AI-generated visuals (DALL-E primary) - stock fallback only when generation fails
  - Creative decision logging: AI records what it changed and why
  - AI quality gate (self-review before showing user)
  - Global learning system tracking which creative decisions work
  - Custom voiceover upload or AI voice generation
  - Custom image integration with precise scene placement
  - Caption position controls (top/center/bottom)
- **Next Gen Clipper Mode**: Script-guided video creation. User provides script (or generates from template), AI clips and edits according to the script structure, influenced by the chosen template's editing style.
- **Custom Template System**: Users upload videos to create personal templates. AI learns the editing style (pacing, cuts, rhythm) for reuse across future projects.
- **Database**: PostgreSQL with 20+ tables and indexed foreign keys for performance.

## External Dependencies
- **Python 3.11**
- **Claude (Anthropic)**: Primary AI for script generation, visual curation, and chat via Replit AI Integrations.
- **xAI (grok-3)**: Fallback AI.
- **ElevenLabs**: Primary Text-to-Speech (TTS) engine.
- **OpenAI**: For audio transcription (Whisper), voiceover generation, and DALL-E character images.
- **FFmpeg**: For video/audio processing and caption rendering.
- **Stripe**: For subscription management and payment processing.
- **Wikimedia Commons**: Primary source for visual content.
- **Pexels**: Fallback source for visual content.