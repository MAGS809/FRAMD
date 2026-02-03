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
- **Caption Template System** (GetCaptions-style):
  - 5 templates: Bold Pop, Clean Minimal, Boxed, Gradient Glow, Street Style
  - Word-by-word highlighting with pop/scale animation synced to audio
  - AI auto-selects best template based on content type
  - Refresh button cycles to new AI-generated style
  - Back/forward navigation through style history
  - Learning tracks which styles users keep vs refresh
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
- **AI Remix Mode** (formerly Re-skin Mode): Source-video-preserving transformation. User uploads any video, AI keeps the original footage as the foundation and transforms it with new visual style. Key principle: SOURCE VIDEO IS THE BASE, not replaced with static images.
  - Creative DNA extraction via GPT-4o Vision (scene intents, composition, colors, motion)
  - **Source Video Preservation**: Original footage motion and structure are kept intact
  - **Visual Transformation**: Color grading profiles applied to source video (Cinematic, Warm, Cool, Vibrant, Muted, Vintage)
  - **Intelligent Enhancement**: Stock VIDEO clips and DALL-E graphics used as overlays/enhancements, NOT replacements
  - Per-scene decision matrix: AI decides optimal approach (style_transfer, overlay_graphics, color_grade, keep_with_effects)
  - Creative decision logging: AI records what it changed and why
  - AI quality gate (self-review before showing user)
  - Global learning system tracking which creative decisions work
  - Custom voiceover upload or AI voice generation
  - Custom image integration with precise scene placement
  - Caption position controls (top/middle/bottom) with voiceover-synced timing
- **Next-gen clipper Mode**: Script-guided video creation. User provides script (or generates from template), AI clips and edits according to the script structure, influenced by the chosen template's editing style.
- **Custom Template System** (`template_engine.py`): Users upload videos to create personal templates with element-level precision:
  - 6 element groups: branding, text, visuals, motion, interactive, data
  - Frame-by-frame element detection via Claude/OpenAI vision
  - Element slots with positions, timing, animations, and style properties
  - Transition detection between scenes
  - Template matching AI auto-selects best template for user request
  - Interactive element editing: hover to see element names, click to select
  - Element-specific regeneration: change one element without affecting others
  - Database models: TemplateElement, GeneratedAsset for reusable assets
- **Visual Director AI** (`visual_director.py`): Pre-plans all visuals before generation for coherent, professional output:
  - Content type detection (podcast, explainer, hot_take, ad, story, news, meme)
  - Per-scene source selection (stock vs DALL-E vs user content)
  - Color palette and style consistency across all scenes
  - Editing DNA patterns per content type (pacing, cut style, transitions)
  - Learning system tracks which visual decisions work well
- **Preview Protection System**: Bouncing TikTok-style watermark on previews:
  - Download = accept (user is satisfied)
  - "Needs Changes" triggers revision flow (Minor Tweaks vs Start Over)
  - "Get Final Video" removes watermark (uses tokens)
  - Revision feedback recorded for AI learning
- **Intelligent Source Mixing**: AI decides optimal source per scene:
  - Stock photos for real people/places (authenticity)
  - DALL-E for abstract concepts, custom scenes
  - User content prioritized when available
- **Source Merging Engine**: Unified post-processing that blends all sources seamlessly:
  - Color grading profiles (Warm Cinematic, Cool Professional, Punchy Vibrant, Muted Film, Clean Neutral)
  - AI recommends best style per project with 2-3 alternatives
  - Film grain overlay toggle (default on) to mask source differences
  - Transition effects library (zoom, whip cut, dissolve, light leak, slide)
  - FFmpeg filter chain applies all processing in one render pass
- **Caption Template System** (GetCaptions-style):
  - 5 templates: Bold Pop, Clean Minimal, Boxed, Gradient Glow, Street Style
  - Word-by-word highlighting with pop/scale animation synced to audio
  - AI auto-selects best template based on content type
  - Refresh button cycles to new AI-generated style
  - Back/forward navigation through style history
  - Learning tracks which styles users keep vs refresh
- **Database**: PostgreSQL with 25+ tables including VisualPlan, VisualLearning, PreviewVideo, CaptionStyleHistory, UserMergingPreferences for AI learning.

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