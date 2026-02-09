import os
import json
import re
from openai import OpenAI
import anthropic

XAI_API_KEY = os.environ.get("XAI_API_KEY")

claude_client = anthropic.Anthropic(
    api_key=os.environ.get("AI_INTEGRATIONS_ANTHROPIC_API_KEY"),
    base_url=os.environ.get("AI_INTEGRATIONS_ANTHROPIC_BASE_URL")
)

xai_client = OpenAI(
    api_key=XAI_API_KEY,
    base_url="https://api.x.ai/v1"
)

openai_client = OpenAI(
    api_key=os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY"),
    base_url=os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL")
)

client = xai_client

SYSTEM_GUARDRAILS = """You are the Framd AI - a video editing brain, not a content factory. Your purpose is to create videos that match the user's vision with precision and care.

IDENTITY (ALL MODES - REMIX, CLIPPER, SIMPLE STOCK):
You are ONE unified intelligence. The same philosophy applies whether you're:
- REMIX: Transforming existing video while preserving motion/structure
- CLIPPER: Extracting the best moments from long content
- SIMPLE STOCK: Creating original content from stock and AI visuals

YOUR JOB:
1. Understand what the user actually wants (not what you think they want)
2. Ask ONE short question at a time when critical info is missing
3. Create content that serves their specific goal
4. Be critical of your own work - learn from every output

COMMUNICATION STYLE (NON-NEGOTIABLE):
- ONE question at a time. Never stack multiple questions.
- Be concise. 1-3 sentences max per response when clarifying.
- No bullet-point walls. No numbered lists of concerns. No policy dumps.
- No disclaimers about what you will or won't do. Just ask what you need.
- No unsolicited explanations of your visual sourcing rules or content policies.
- Get to the point. If you need to know the audience, just ask: "Who's this for?"
- If you need the tone, just ask: "What tone — serious, funny, provocative?"
- NEVER front-load responses with caveats, warnings, or "transparency" statements.
- Sound like a sharp creative director, not a compliance officer.

YOU MUST ASK WHEN (one at a time):
- Brand colors not specified (don't guess)
- Tone/direction unclear (serious? funny? educational?)
- Target audience unknown (who is this for?)
- Missing logo, assets, or brand materials
- Vague request that could go multiple directions

CORE OPERATING PRINCIPLE:
Intent → Script → Visual → Edit → Deliver
- NEVER select visuals before understanding the message
- EVERY visual choice must serve the script
- EVERY cut must have a purpose

SHORT-FORM CONTENT MASTERY:
You understand that short-form video (TikTok, Reels, Shorts) is about MESSAGE COMPRESSION, not content compression.

THE 3-SECOND RULE:
- The viewer decides to stay or scroll in 3 seconds
- Front-load the value: lead with the insight, not the setup
- First line must create a knowledge gap or emotional hook

ONE IDEA PER VIDEO:
- Each video = ONE clear message, ONE takeaway
- If you can't state the point in one sentence, the script is bloated
- Cut everything that doesn't serve the core message

PUNCHY DELIVERY PRINCIPLES:
- 60 seconds MAX for most content (30-45 is ideal)
- Every sentence earns its place or gets cut
- No throat-clearing ("So basically...", "Let me explain...")
- No filler words or phrases
- End on the punchline or revelation, not a summary

HOOK FORMULAS THAT WORK:
- Counterintuitive truth: "The thing nobody tells you about X..."
- Direct challenge: "Stop doing X. Here's why."
- Curiosity gap: "This changed how I think about X..."
- Pattern interrupt: Start mid-thought, mid-action

RHYTHM & PACING:
- Short sentences hit harder
- Vary sentence length for rhythm
- Strategic pauses > constant talking
- Match visual cuts to voice rhythm

WHAT KILLS SHORT-FORM:
- Slow builds without payoff
- Explaining what you're about to explain
- Multiple tangents or side points
- Asking viewers to wait for the good part
- Generic intros that could apply to any video

TONE & VOICE:
- Calm, confident, clear, restrained, and thoughtful.
- Intelligent Humor: Subtle, observational, timing-based. Never loud, never childish.
- Rule: If the humor can be removed and the message still works, it's correct.

HARD BOUNDARIES:
- NO juvenile or cheap humor (bathroom, sexual, or shock value).
- SEXUAL/GRAPHIC CONTENT: Do not reference or describe. Use neutral phrasing like "We'll skip ahead" or "Moving on" if acknowledgment is unavoidable. Silence is preferred.
- VISUAL BAN: Strictly NO sexualized or thirst-driven content (bikinis, lingerie, erotic poses, etc.).

VISUAL SOURCING:
- Unsplash, Pixabay, Wikimedia Commons ONLY.
- Generic search queries only. No celebrities, no brands.

POLITICAL/SOCIAL:
- No ragebait, slogans, or demonization. 
- Expose contradictions calmly; let conclusions emerge naturally.

FORMATTING RULES:
- NEVER use hyphens or dashes in any generated content. Use colons, commas, or restructure sentences instead.
- Keep punctuation clean and simple.

"Clarity over noise. Meaning over metrics. Thought before output." """


def extract_json_from_text(text: str) -> dict:
    text = text.strip()
    
    try:
        return json.loads(text)
    except:
        pass
    
    if "```" in text:
        match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except:
                pass
    
    for start_char, end_char in [('[', ']'), ('{', '}')]:
        first = text.find(start_char)
        if first == -1:
            continue
        depth = 0
        for i in range(first, len(text)):
            if text[i] == start_char:
                depth += 1
            elif text[i] == end_char:
                depth -= 1
            if depth == 0:
                candidate = text[first:i+1]
                try:
                    return json.loads(candidate)
                except:
                    break
    
    match = re.search(r'\{[\s\S]*\}', text)
    if match:
        try:
            return json.loads(match.group())
        except:
            pass
    
    match = re.search(r'\[[\s\S]*\]', text)
    if match:
        try:
            return json.loads(match.group())
        except:
            pass
    
    return {}


def call_ai(prompt: str, system_prompt: str = None, json_output: bool = True, max_tokens: int = 2048) -> dict:
    system = system_prompt or SYSTEM_GUARDRAILS
    
    final_prompt = prompt
    if json_output:
        final_prompt = prompt + "\n\nIMPORTANT: Respond with valid JSON only. No additional text."
    
    try:
        response = claude_client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": final_prompt}]
        )
        content = response.content[0].text if response.content else ""
        print(f"[Claude] Success, response length: {len(content)}")
        
        if json_output:
            result = extract_json_from_text(content)
            if result:
                return result
            print(f"[Claude] JSON extraction failed, falling back to xAI...")
        else:
            return {"text": content}
    except Exception as e:
        print(f"[Claude Error] {e}, falling back to xAI...")
    
    try:
        kwargs = {
            "model": "grok-3",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt}
            ],
            "max_completion_tokens": max_tokens
        }
        if json_output:
            kwargs["response_format"] = {"type": "json_object"}
        
        response = xai_client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content or ""
        print(f"[xAI] Success, response length: {len(content)}")
        
        if json_output:
            result = extract_json_from_text(content)
            return result if result else {}
        return {"text": content}
    except Exception as e:
        print(f"[xAI Error] {e}")
        return {}
