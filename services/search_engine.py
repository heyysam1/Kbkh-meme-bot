import asyncio
import logging
import re
import time
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional
import aiohttp
from database.queries import get_all_templates, resolve_alias

logger = logging.getLogger("kbkh_meme_bot.search_engine")

# In-memory cache for external meme catalog (Imgflip)
_EXTERNAL_MEMES_CACHE: List[Dict[str, Any]] = []
_EXTERNAL_CACHE_TIMESTAMP: float = 0.0
_EXTERNAL_CACHE_TTL: float = 3600.0  # 1 hour cache

# Registry of discovered external templates for instant callback resolution
_EXTERNAL_REGISTRY: Dict[str, Dict[str, Any]] = {}

def normalize_query(query: str) -> str:
    """
    Clean, lowercase, and strip punctuation from a user search term.
    Preserves Bengali characters (\u0980-\u09FF) and standard alphanumeric text.
    """
    if not query:
        return ""
    text = query.lower().strip()
    text = re.sub(r"[^\w\s\u0980-\u09ff]", " ", text)
    return re.sub(r"\s+", " ", text).strip()

def calculate_token_similarity(query_norm: str, target_norm: str) -> float:
    """
    Compute a combined similarity score using sequence matcher and token overlap.
    Includes case-insensitive substring and prefix matching boosts.
    """
    if not query_norm or not target_norm:
        return 0.0

    # 1. Exact string ratio
    seq_ratio = SequenceMatcher(None, query_norm, target_norm).ratio()

    # 2. Token overlap ratio
    q_tokens = set(query_norm.split())
    t_tokens = set(target_norm.split())

    if not q_tokens or not t_tokens:
        return seq_ratio

    common_tokens = q_tokens.intersection(t_tokens)
    overlap_ratio = len(common_tokens) / len(q_tokens)

    # 3. Substring containment check with strong boost
    if query_norm in target_norm:
        return max(seq_ratio, 0.90, overlap_ratio)

    # 4. Any token substring check
    if any(qt in tt for qt in q_tokens for tt in t_tokens if len(qt) > 2):
        return max(seq_ratio, 0.70, overlap_ratio)

    return max(seq_ratio, overlap_ratio * 0.8)

async def search_templates(query: str, limit: int = 4) -> List[Dict[str, Any]]:
    """
    High-precision local meme template finder:
    1. Query normalization and punctuation stripping.
    2. Alias mapping lookup (e.g., colloquial terms to official titles).
    3. Token-based and fuzzy sequence matching against template names, titles, and tags.
    4. Score weighting incorporating is_trending and usage_count.
    """
    clean_query = normalize_query(query)
    if not clean_query:
        return []

    canonical_alias: Optional[str] = await resolve_alias(clean_query)
    search_targets = [clean_query]
    if canonical_alias:
        search_targets.append(normalize_query(canonical_alias))

    all_templates = await get_all_templates(limit=500, offset=0)
    if not all_templates:
        return []

    scored_candidates = []

    for template in all_templates:
        t_title = normalize_query(template.get("title") or template.get("name") or "")
        t_tags = normalize_query(template.get("tags") or "")
        is_trending = template.get("is_trending", 0)
        usage_count = template.get("usage_count", 0)

        best_match_ratio = 0.0

        for target in search_targets:
            title_score = calculate_token_similarity(target, t_title)
            tag_score = calculate_token_similarity(target, t_tags) * 0.9
            match_score = max(title_score, tag_score)

            if canonical_alias and normalize_query(canonical_alias) in t_title:
                match_score = max(match_score, 1.0)

            if match_score > best_match_ratio:
                best_match_ratio = match_score

        if best_match_ratio < 0.25:
            continue

        popularity_bonus = min(usage_count * 0.01, 0.20)
        final_rank_score = best_match_ratio * (1.0 + (0.2 * is_trending)) + popularity_bonus

        candidate = dict(template)
        candidate["search_score"] = round(final_rank_score, 4)
        scored_candidates.append(candidate)

    scored_candidates.sort(key=lambda x: x["search_score"], reverse=True)
    return scored_candidates[:limit]

async def _fetch_imgflip_memes() -> List[Dict[str, Any]]:
    """Fetch top 100 popular blank meme templates from Imgflip API."""
    global _EXTERNAL_MEMES_CACHE, _EXTERNAL_CACHE_TIMESTAMP
    now = time.time()
    if _EXTERNAL_MEMES_CACHE and (now - _EXTERNAL_CACHE_TIMESTAMP) < _EXTERNAL_CACHE_TTL:
        return _EXTERNAL_MEMES_CACHE

    url = "https://api.imgflip.com/get_memes"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5.0)) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    memes = data.get("data", {}).get("memes", [])
                    if memes:
                        _EXTERNAL_MEMES_CACHE = memes
                        _EXTERNAL_CACHE_TIMESTAMP = now
                        return memes
    except Exception as e:
        logger.warning("Imgflip API fetch error: %s", e)

    return _EXTERNAL_MEMES_CACHE

async def _fetch_reddit_memes(query: str, count: int = 5) -> List[Dict[str, Any]]:
    """Query Reddit meme API as alternative fallback."""
    clean_q = re.sub(r"[^\w]", "", query)
    endpoint = f"https://meme-api.com/gimme/{clean_q}/{count}" if clean_q else f"https://meme-api.com/gimme/{count}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    results = []

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5.0)) as session:
            async with session.get(endpoint, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    memes = data.get("memes", [])
                    for m in memes:
                        post_url = m.get("url")
                        title = m.get("title") or "Reddit Meme"
                        if post_url and (post_url.endswith(".jpg") or post_url.endswith(".png") or post_url.endswith(".jpeg")):
                            item = {
                                "id": f"rd_{abs(hash(post_url)) % 10000000}",
                                "name": title,
                                "url": post_url,
                                "source": "Reddit",
                            }
                            results.append(item)
    except Exception as e:
        logger.warning("Reddit meme API fallback error: %s", e)

    return results

async def search_external_memes(query: str, limit: int = 3) -> List[Dict[str, Any]]:
    """
    Search external public meme repositories (Imgflip & Reddit) for blank templates.
    """
    clean_query = normalize_query(query)
    if not clean_query:
        return []

    q_tokens = set(clean_query.split())
    imgflip_memes = await _fetch_imgflip_memes()
    matches = []

    for meme in imgflip_memes:
        m_name = normalize_query(meme.get("name", ""))
        score = calculate_token_similarity(clean_query, m_name)
        if clean_query in m_name:
            score = max(score, 0.95)
        elif any(qt in m_name for qt in q_tokens if len(qt) > 2):
            score = max(score, 0.75)

        if score >= 0.40:
            ext_id = f"ext_if_{meme['id']}"
            item = {
                "id": ext_id,
                "title": meme.get("name"),
                "name": meme.get("name"),
                "url": meme.get("url"),
                "is_external": True,
                "source": "Imgflip",
                "search_score": round(score, 4),
            }
            _EXTERNAL_REGISTRY[ext_id] = item
            matches.append(item)

    matches.sort(key=lambda x: x["search_score"], reverse=True)

    # If Imgflip has few matches, check Reddit
    if len(matches) < limit:
        reddit_memes = await _fetch_reddit_memes(clean_query, count=limit - len(matches))
        for rm in reddit_memes:
            ext_id = f"ext_{rm['id']}"
            rm_item = {
                "id": ext_id,
                "title": rm["name"],
                "name": rm["name"],
                "url": rm["url"],
                "is_external": True,
                "source": rm.get("source", "Reddit"),
                "search_score": 0.65,
            }
            _EXTERNAL_REGISTRY[ext_id] = rm_item
            matches.append(rm_item)

    return matches[:limit]

async def hybrid_search_templates(query: str, limit: int = 4) -> Dict[str, Any]:
    """
    Hybrid Search Engine:
    1. Search local SQLite catalog first.
    2. If fewer than 3 results found, query external public meme repositories.
    3. Return combined bundle containing local and external candidate lists.
    """
    local_results = await search_templates(query, limit=limit)
    external_results = []

    # If local search has fewer than 3 results, activate external fallback
    if len(local_results) < 3:
        needed = max(2, limit - len(local_results))
        external_results = await search_external_memes(query, limit=needed)

    return {
        "local": local_results,
        "external": external_results,
        "total": len(local_results) + len(external_results),
    }

def get_external_template(external_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve cached external template by its ID."""
    return _EXTERNAL_REGISTRY.get(external_id)

async def fetch_external_image_bytes(url: str) -> Optional[bytes]:
    """Download image bytes from external URL directly into memory."""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10.0)) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    return await resp.read()
    except Exception as e:
        logger.error("Failed to download external template image from %s: %s", url, e)
    return None
