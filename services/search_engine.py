import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional
from database.queries import get_all_templates, resolve_alias

def normalize_query(query: str) -> str:
    """
    Clean, lowercase, and strip punctuation from a user search term.
    Preserves Bengali characters (\u0980-\u09FF) and standard alphanumeric text.
    """
    if not query:
        return ""
    # Lowercase
    text = query.lower().strip()
    # Remove punctuation, keeping alphanumeric, whitespace, and Bengali characters
    text = re.sub(r"[^\w\s\u0980-\u09ff]", " ", text)
    # Collapse multiple whitespace
    return re.sub(r"\s+", " ", text).strip()

def calculate_token_similarity(query_norm: str, target_norm: str) -> float:
    """
    Compute a combined similarity score using sequence matcher and token overlap.
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

    # 3. Substring containment check
    if query_norm in target_norm:
        return max(seq_ratio, 0.85, overlap_ratio)

    return max(seq_ratio, overlap_ratio * 0.8)

async def search_templates(query: str, limit: int = 4) -> List[Dict[str, Any]]:
    """
    High-precision meme template finder (95%+ target accuracy):
    1. Query normalization and punctuation stripping.
    2. Alias mapping lookup (e.g., colloquial terms to official titles).
    3. Token-based and fuzzy sequence matching against template names and tags.
    4. Score weighting incorporating is_trending and usage_count.
    5. Returns top 3-4 ranked templates for visual confirmation.
    """
    clean_query = normalize_query(query)
    if not clean_query:
        return []

    # Check for direct colloquial alias resolution
    canonical_alias: Optional[str] = await resolve_alias(clean_query)
    search_targets = [clean_query]
    if canonical_alias:
        search_targets.append(normalize_query(canonical_alias))

    # Retrieve all available templates from SQLite
    all_templates = await get_all_templates(limit=500, offset=0)
    if not all_templates:
        return []

    scored_candidates = []

    for template in all_templates:
        t_name = normalize_query(template.get("name", ""))
        t_tags = normalize_query(template.get("tags", ""))
        is_trending = template.get("is_trending", 0)
        usage_count = template.get("usage_count", 0)

        best_match_ratio = 0.0

        for target in search_targets:
            # Check match against title
            name_score = calculate_token_similarity(target, t_name)
            # Check match against tags
            tag_score = calculate_token_similarity(target, t_tags) * 0.9

            match_score = max(name_score, tag_score)

            # Extra weight if canonical alias directly matches title
            if canonical_alias and normalize_query(canonical_alias) in t_name:
                match_score = max(match_score, 1.0)

            if match_score > best_match_ratio:
                best_match_ratio = match_score

        # Discard low-confidence noise
        if best_match_ratio < 0.28:
            continue

        # Score calculation formula from Blueprint:
        # Rank Score = Match Ratio * (1 + (0.2 * is_trending)) + usage popularity bonus
        popularity_bonus = min(usage_count * 0.01, 0.20)
        final_rank_score = best_match_ratio * (1.0 + (0.2 * is_trending)) + popularity_bonus

        candidate = dict(template)
        candidate["search_score"] = round(final_rank_score, 4)
        scored_candidates.append(candidate)

    # Sort candidates by final score in descending order
    scored_candidates.sort(key=lambda x: x["search_score"], reverse=True)

    # Return top 3-4 candidates
    return scored_candidates[:limit]
