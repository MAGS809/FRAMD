from duckduckgo_search import DDGS
from ai_client import call_ai, SYSTEM_GUARDRAILS

_trend_cache = {}


def research_topic_trends(topic: str, target_platform: str = "all") -> dict:
    global _trend_cache
    
    cache_key = f"{topic}:{target_platform}"
    if cache_key in _trend_cache:
        print(f"[TrendIntel] Using cached research for: {topic}")
        return _trend_cache[cache_key]
    
    print(f"[TrendIntel] Researching trends for: {topic}")
    
    platforms = ["Twitter", "Instagram Reels", "TikTok", "YouTube Shorts"] if target_platform == "all" else [target_platform]
    
    search_results = []
    try:
        with DDGS() as ddgs:
            for platform in platforms:
                query = f"{topic} {platform} viral video format 2025"
                results = list(ddgs.text(query, max_results=3))
                for r in results:
                    search_results.append({
                        "platform": platform,
                        "title": r.get("title", ""),
                        "snippet": r.get("body", ""),
                        "source": r.get("href", "")
                    })
                    
            general_query = f"{topic} short form video trends hooks what works"
            general_results = list(ddgs.text(general_query, max_results=5))
            for r in general_results:
                search_results.append({
                    "platform": "general",
                    "title": r.get("title", ""),
                    "snippet": r.get("body", ""),
                    "source": r.get("href", "")
                })
    except Exception as e:
        print(f"[TrendIntel] Web search error: {e}")
        search_results = []
    
    if not search_results:
        default_result = {
            "topic": topic,
            "patterns": {
                "hooks": ["Direct question hook", "Controversial statement", "Statistics lead"],
                "formats": ["Talking head with text overlay", "Documentary style", "Quick cuts with captions"],
                "visuals": ["Professional lighting", "Clean background", "Dynamic b-roll"],
                "framings": ["Educational angle", "Personal story", "News commentary"]
            },
            "platform_insights": {},
            "sources": [],
            "cached": False
        }
        return default_result
    
    search_context = "\n".join([
        f"[{r['platform']}] {r['title']}: {r['snippet']}"
        for r in search_results[:15]
    ])
    
    prompt = f"""Analyze this web research about how "{topic}" is being discussed in short-form video content.

RESEARCH FINDINGS:
{search_context}

Based on this research, extract:

1. HOOKS: What opening lines/techniques are working for this topic?
2. FORMATS: What video formats are being used? (talking head, documentary, reaction, etc.)
3. VISUALS: What imagery/b-roll styles are associated with this topic?
4. FRAMINGS: What angles/perspectives are creators taking?
5. PLATFORM SPECIFICS: Any platform-specific patterns noticed?

Output JSON:
{{
    "patterns": {{
        "hooks": ["specific hook style 1", "specific hook style 2", "specific hook style 3"],
        "formats": ["format 1", "format 2", "format 3"],
        "visuals": ["visual style 1", "visual style 2", "visual style 3"],
        "framings": ["framing angle 1", "framing angle 2", "framing angle 3"]
    }},
    "platform_insights": {{
        "Twitter": "what works on Twitter for this topic",
        "Instagram": "what works on Instagram for this topic",
        "TikTok": "what works on TikTok for this topic",
        "YouTube": "what works on YouTube Shorts for this topic"
    }},
    "successful_examples": ["brief description of a successful video format found"],
    "avoid": ["what to avoid based on research"]
}}

Focus on ACTIONABLE patterns that can inform content creation."""

    result = call_ai(prompt, SYSTEM_GUARDRAILS, json_output=True, max_tokens=1024)
    
    if result:
        result["topic"] = topic
        result["sources"] = [{"title": r["title"], "url": r["source"]} for r in search_results[:5]]
        result["cached"] = False
        _trend_cache[cache_key] = result
        print(f"[TrendIntel] Research complete for: {topic}")
        return result
    
    return {
        "topic": topic,
        "patterns": {
            "hooks": ["Direct statement", "Question hook", "Statistic lead"],
            "formats": ["Talking head", "Text overlay", "Documentary"],
            "visuals": ["Professional", "Authentic", "Dynamic"],
            "framings": ["Educational", "Commentary", "Personal"]
        },
        "platform_insights": {},
        "sources": [],
        "cached": False
    }
