import os
import requests
from ai_client import call_ai, SYSTEM_GUARDRAILS

UNSPLASH_ACCESS_KEY = os.environ.get("UNSPLASH_ACCESS_KEY")
PIXABAY_API_KEY = os.environ.get("PIXABAY_API_KEY")
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY")


def extract_keywords_from_script(script: str) -> dict:
    prompt = f"""Analyze this script/pitch and extract keywords that capture the NUANCE of what they're trying to say.

SCRIPT/PITCH:
{script}

Extract keywords that would help find supporting video footage. Think about:
- The TONE (funny, serious, dramatic, absurd, ironic)
- The VISUAL MOOD (dark, bright, chaotic, calm, intimate)
- KEY CONCEPTS (the actual subjects being discussed)
- EMOTIONAL BEATS (tension, relief, surprise, realization)
- METAPHORS or ANALOGIES implied

Output JSON with:
{{
    "primary_keywords": ["list of 3-5 main search terms for stock footage"],
    "mood_keywords": ["list of 2-3 mood/atmosphere words"],
    "visual_suggestions": ["list of 2-3 specific visual ideas that would support this script"],
    "tone": "one word describing overall tone",
    "hook_summary": "one sentence capturing the core message"
}}"""

    result = call_ai(prompt, SYSTEM_GUARDRAILS, json_output=True, max_tokens=1024)
    if not result:
        return {
            "primary_keywords": [],
            "mood_keywords": [],
            "visual_suggestions": [],
            "tone": "neutral",
            "hook_summary": script[:100]
        }
    return result


def search_stock_videos(keywords: list[str], per_page: int = 5) -> list[dict]:
    if not PEXELS_API_KEY:
        return []
    
    all_videos = []
    headers = {"Authorization": PEXELS_API_KEY}
    
    for keyword in keywords[:3]:
        url = "https://api.pexels.com/videos/search"
        params = {
            "query": keyword,
            "per_page": per_page,
            "orientation": "landscape"
        }
        
        try:
            response = requests.get(url, headers=headers, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                for video in data.get("videos", []):
                    video_files = video.get("video_files", [])
                    best_file = None
                    for vf in video_files:
                        if vf.get("quality") == "hd" or not best_file:
                            best_file = vf
                    
                    all_videos.append({
                        "id": video.get("id"),
                        "keyword": keyword,
                        "duration": video.get("duration"),
                        "preview_url": video.get("image"),
                        "video_url": best_file.get("link") if best_file else None,
                        "pexels_url": video.get("url"),
                        "photographer": video.get("user", {}).get("name", "Unknown"),
                        "attribution": f"Video by {video.get('user', {}).get('name', 'Unknown')} on Pexels"
                    })
        except Exception as e:
            print(f"Error searching Pexels for '{keyword}': {e}")
    
    return all_videos


def detect_characters_in_scene(scene_text: str) -> dict:
    prompt = f"""Analyze this scene text and identify any characters or people mentioned:

"{scene_text}"

For each character found, determine:
1. Name (if mentioned)
2. Type: "historical" (real historical figure), "celebrity" (modern public figure), or "generic" (unnamed person reference)
3. Search query to find representative imagery (generic terms, no copyrighted names)

Output JSON:
{{
    "characters": [
        {{"name": "character name or description", "type": "historical/celebrity/generic", "search_query": "safe search terms for imagery"}}
    ],
    "has_people": true/false
}}

For historical figures like Einstein, use "scientist portrait" not the name.
For generic references like "a leader", use "leader silhouette professional".
If no people/characters mentioned, return empty array."""
    
    system = "Analyze text and identify any people, characters, or figures mentioned."
    result = call_ai(prompt, system, json_output=True, max_tokens=512)
    return result if result else {"characters": [], "has_people": False}


def search_unsplash(query: str, per_page: int = 6) -> list[dict]:
    if not UNSPLASH_ACCESS_KEY:
        return []
    
    url = "https://api.unsplash.com/search/photos"
    params = {
        "query": query,
        "per_page": per_page,
        "orientation": "landscape"
    }
    headers = {"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}"}
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            images = []
            for photo in data.get("results", []):
                images.append({
                    "id": f"unsplash_{photo.get('id')}",
                    "url": photo.get("urls", {}).get("regular"),
                    "thumbnail": photo.get("urls", {}).get("small"),
                    "alt": photo.get("alt_description") or query
                })
            return images
    except Exception as e:
        print(f"Error searching Unsplash for '{query}': {e}")
    
    return []


def search_pixabay(query: str, per_page: int = 6) -> list[dict]:
    if not PIXABAY_API_KEY:
        return []
    
    url = "https://pixabay.com/api/"
    params = {
        "key": PIXABAY_API_KEY,
        "q": query,
        "per_page": per_page,
        "orientation": "horizontal",
        "safesearch": "true",
        "image_type": "photo"
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            images = []
            for hit in data.get("hits", []):
                images.append({
                    "id": f"pixabay_{hit.get('id')}",
                    "url": hit.get("largeImageURL") or hit.get("webformatURL"),
                    "thumbnail": hit.get("previewURL"),
                    "alt": query
                })
            return images
    except Exception as e:
        print(f"Error searching Pixabay for '{query}': {e}")
    
    return []


def search_pixabay_videos(query: str, per_page: int = 4) -> list[dict]:
    if not PIXABAY_API_KEY:
        return []
    
    url = "https://pixabay.com/api/videos/"
    params = {
        "key": PIXABAY_API_KEY,
        "q": query,
        "per_page": per_page,
        "safesearch": "true"
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            videos = []
            for hit in data.get("hits", []):
                video_files = hit.get("videos", {})
                medium = video_files.get("medium", {})
                videos.append({
                    "id": f"pixabay_v_{hit.get('id')}",
                    "download_url": medium.get("url"),
                    "thumbnail": f"https://i.vimeocdn.com/video/{hit.get('picture_id')}_640x360.jpg",
                    "title": query,
                    "duration": hit.get("duration", 0)
                })
            return videos
    except Exception as e:
        print(f"Error searching Pixabay videos for '{query}': {e}")
    
    return []


def search_pexels(query: str, per_page: int = 6) -> list[dict]:
    if not PEXELS_API_KEY:
        return []
    
    headers = {"Authorization": PEXELS_API_KEY}
    url = "https://api.pexels.com/v1/search"
    params = {
        "query": query,
        "per_page": per_page,
        "orientation": "landscape"
    }
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            images = []
            for photo in data.get("photos", []):
                images.append({
                    "id": f"pexels_{photo.get('id')}",
                    "url": photo.get("src", {}).get("large2x") or photo.get("src", {}).get("large"),
                    "thumbnail": photo.get("src", {}).get("medium"),
                    "alt": photo.get("alt", query)
                })
            return images
    except Exception as e:
        print(f"Error searching Pexels for '{query}': {e}")
    
    return []


def search_wikimedia_images(query: str, per_page: int = 4) -> list[dict]:
    try:
        search_url = 'https://commons.wikimedia.org/w/api.php'
        headers = {'User-Agent': 'EchoEngine/1.0 (content creation tool)'}
        
        search_params = {
            'action': 'query',
            'format': 'json',
            'generator': 'search',
            'gsrnamespace': 6,
            'gsrsearch': f'{query}',
            'gsrlimit': per_page * 2,
            'prop': 'imageinfo',
            'iiprop': 'url|extmetadata',
            'iiurlwidth': 800
        }
        
        response = requests.get(search_url, params=search_params, headers=headers, timeout=10)
        print(f"[Wikimedia Images] Query: '{query}', Status: {response.status_code}")
        
        if response.status_code != 200:
            return []
        
        data = response.json()
        pages = data.get('query', {}).get('pages', {})
        print(f"[Wikimedia Images] Found {len(pages)} pages")
        
        images = []
        for page_id, page in pages.items():
            if page_id == '-1':
                continue
            
            imageinfo = page.get('imageinfo', [{}])[0]
            thumb_url = imageinfo.get('thumburl') or imageinfo.get('url')
            full_url = imageinfo.get('url')
            
            if not thumb_url and full_url:
                thumb_url = full_url
            
            if full_url:
                title = page.get('title', '').replace('File:', '').replace('_', ' ')
                images.append({
                    "id": f"wikimedia_{page.get('pageid')}",
                    "url": full_url,
                    "thumbnail": thumb_url or full_url,
                    "alt": title[:100] if title else query
                })
        
        print(f"[Wikimedia Images] Returning {len(images)} images")
        return images[:per_page]
    except Exception as e:
        print(f"[Wikimedia Images] Error for '{query}': {e}")
        return []


def search_visuals_unified(query: str, per_page: int = 6) -> list[dict]:
    all_results = []
    
    print(f"[Unified Search] Starting search for: '{query}'")
    
    unsplash_results = search_unsplash(query, per_page=2)
    all_results.extend(unsplash_results)
    print(f"[Unified Search] Unsplash: {len(unsplash_results)} results")
    
    if len(all_results) < per_page:
        wiki_results = search_wikimedia_images(query, per_page=3)
        all_results.extend(wiki_results)
        print(f"[Unified Search] Wikimedia: {len(wiki_results)} results")
    
    if len(all_results) < per_page:
        pixabay_results = search_pixabay(query, per_page=2)
        all_results.extend(pixabay_results)
        print(f"[Unified Search] Pixabay: {len(pixabay_results)} results")
    
    if len(all_results) < per_page:
        pexels_results = search_pexels(query, per_page=per_page - len(all_results))
        all_results.extend(pexels_results)
        print(f"[Unified Search] Pexels: {len(pexels_results)} results")
    
    print(f"[Unified Search] Total: {len(all_results)} results for '{query}'")
    return all_results[:per_page]


def search_pexels_safe(query: str, per_page: int = 6) -> list[dict]:
    return search_visuals_unified(query, per_page)


def get_scene_visuals(scene_text: str, scene_type: str, keywords: list = None, topic_trends: dict = None) -> dict:
    keywords_str = ", ".join(keywords) if keywords else ""
    
    trend_visual_context = ""
    if topic_trends and topic_trends.get('patterns', {}).get('visuals'):
        trend_visuals = topic_trends['patterns']['visuals']
        trend_visual_context = f"""
TREND INTELLIGENCE - Visual styles working for this topic:
{', '.join(trend_visuals[:4])}
Use these visual patterns to inform your search queries.
"""
    
    prompt = f"""You are a visual researcher who thinks LATERALLY. Your job is to find stock imagery that REPRESENTS the idea, not matches literal words.

SCENE TYPE: {scene_type}
SCENE TEXT: "{scene_text}"
KEYWORDS: {keywords_str}
{trend_visual_context}

## LATERAL THINKING METHOD
Ask yourself: "What image would a viewer ASSOCIATE with this message?" — NOT "What words are in this sentence?"

Stock sites have common imagery. Search for what EXISTS, not what you wish existed.

## EXAMPLES BY CONTENT TYPE

TECH/SOFTWARE:
- "Clip tools make you scrub timelines" → "filmmaker editing computer", "video production workspace", "creative professional laptop", "digital timeline interface"
- "AI does the heavy lifting" → "robot arm assembly", "automation machinery", "hands-free workflow", "futuristic technology"
- "Stop wasting hours on editing" → "clock time lapse", "frustrated person desk", "hourglass sand falling", "productive workflow"

BUSINESS/STARTUP:
- "Most startups fail in year one" → "empty office chairs", "closed business sign", "entrepreneur stressed", "financial charts declining"
- "Scale your revenue" → "growth chart upward", "team celebrating success", "money stacks", "expanding cityscape"

LIFESTYLE/SELF-IMPROVEMENT:
- "Break free from the 9-5" → "person leaving office building", "laptop beach view", "sunrise freedom", "open road driving"
- "Build habits that stick" → "morning routine coffee", "gym workout", "journal writing", "calendar checkmarks"

DOCUMENTARY/NEWS:
- "Political corruption exposed" → "courthouse steps", "politician podium", "gavel courtroom", "redacted documents"
- "The truth they hide" → "shredded paper", "closed door meeting", "surveillance camera", "locked filing cabinet"

CREATIVE/ARTISTIC:
- "Your story deserves to be heard" → "microphone spotlight", "audience listening", "storyteller stage", "emotional performance"
- "Create content that resonates" → "creator studio setup", "audience engagement", "viral social media", "authentic moment"

## BAD QUERIES (will return garbage):
- Abstract nouns: "truth", "success", "implications"
- Full sentences: "what really happened"  
- Brand names without context: "Adobe Premiere"
- Overly specific tech: "timeline scrubbing feature"

## GOOD QUERIES use:
- People doing actions: "filmmaker editing", "entrepreneur working"
- Objects with context: "laptop creative workspace", "camera studio setup"
- Emotional scenes: "frustrated person computer", "celebrating team office"
- Universal visuals: "clock spinning", "growth chart", "sunrise city"

Output JSON:
{{
    "visual_concept": "One sentence describing what visual REPRESENTS this idea",
    "search_queries": ["lateral visual 1", "lateral visual 2", "lateral visual 3", "lateral visual 4"],
    "background_queries": ["atmospheric setting 1", "cinematic backdrop 2"],
    "visual_style": "tech | lifestyle | documentary | creative | business | atmospheric",
    "motion": "static | slow_pan | zoom | dynamic",
    "mood": "inspiring | tense | hopeful | dramatic | calm | energetic"
}}

Think like a music video director: What B-ROLL represents this feeling?"""

    result = call_ai(prompt, SYSTEM_GUARDRAILS, json_output=True, max_tokens=512)
    if not result:
        return {
            "visual_concept": "Supportive visual for this scene",
            "search_queries": ["documentary footage", "news archive", "dramatic lighting"],
            "background_queries": ["dark cinematic background", "dramatic atmosphere"],
            "visual_style": "atmospheric",
            "motion": "static",
            "mood": "neutral"
        }
    return result
