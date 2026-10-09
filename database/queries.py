from typing import Any, Dict, List, Optional
from database.db import get_db

# ------------------------------------------------------------------------------
# User Operations
# ------------------------------------------------------------------------------

async def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    """Retrieve user record by Telegram user_id."""
    async with get_db() as conn:
        async with conn.execute(
            "SELECT * FROM users WHERE user_id = ?;", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def upsert_user(user_id: int, preferred_font: str = "default") -> Dict[str, Any]:
    """Fetch existing user profile or initialize on first interaction (race-safe)."""
    async with get_db() as conn:
        await conn.execute(
            """
            INSERT INTO users (
                user_id, preferred_font, watermark_file_id, watermark_text,
                watermark_enabled, watermark_position, watermark_scale, watermark_opacity
            )
            VALUES (?, ?, NULL, NULL, 0, 'bottom_right', 1.0, 0.8)
            ON CONFLICT(user_id) DO NOTHING;
            """,
            (user_id, preferred_font),
        )
        await conn.commit()

    return await get_user(user_id) or {
        "user_id": user_id,
        "preferred_font": preferred_font,
        "watermark_file_id": None,
        "watermark_text": None,
        "watermark_enabled": 0,
        "watermark_position": "bottom_right",
        "watermark_scale": 1.0,
        "watermark_opacity": 0.8,
    }

async def update_user_font(user_id: int, font_key: str) -> None:
    """Update user's preferred font selection."""
    await upsert_user(user_id)
    async with get_db() as conn:
        await conn.execute(
            "UPDATE users SET preferred_font = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?;",
            (font_key, user_id),
        )
        await conn.commit()

async def update_user_watermark(
    user_id: int, file_id: str, enabled: int = 1, position: str = "bottom_right"
) -> None:
    """Save or update user custom watermark file_id, position, and status."""
    await upsert_user(user_id)
    async with get_db() as conn:
        await conn.execute(
            """
            UPDATE users
            SET watermark_file_id = ?, watermark_enabled = ?, watermark_position = ?, updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?;
            """,
            (file_id, enabled, position, user_id),
        )
        await conn.commit()

async def toggle_user_watermark(user_id: int) -> bool:
    """Toggle user watermark ON/OFF. Returns the new enabled state."""
    user = await upsert_user(user_id)
    current_status = user.get("watermark_enabled", 0)
    new_status = 0 if current_status else 1

    async with get_db() as conn:
        await conn.execute(
            "UPDATE users SET watermark_enabled = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?;",
            (new_status, user_id),
        )
        await conn.commit()

    return bool(new_status)

async def set_watermark_position(user_id: int, position: str) -> None:
    """Update user's chosen watermark anchor point."""
    await upsert_user(user_id)
    async with get_db() as conn:
        await conn.execute(
            "UPDATE users SET watermark_position = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?;",
            (position, user_id),
        )
        await conn.commit()

async def update_user_watermark_settings(
    user_id: int,
    scale: Optional[float] = None,
    opacity: Optional[float] = None,
    position: Optional[str] = None,
    text: Optional[str] = None,
    enabled: Optional[int] = None,
) -> None:
    """Update advanced watermark settings for a user."""
    await upsert_user(user_id)
    fields = []
    params = []
    if scale is not None:
        fields.append("watermark_scale = ?")
        params.append(scale)
    if opacity is not None:
        fields.append("watermark_opacity = ?")
        params.append(opacity)
    if position is not None:
        fields.append("watermark_position = ?")
        params.append(position)
    if text is not None:
        fields.append("watermark_text = ?")
        params.append(text)
    if enabled is not None:
        fields.append("watermark_enabled = ?")
        params.append(enabled)

    if fields:
        fields.append("updated_at = CURRENT_TIMESTAMP")
        params.append(user_id)
        sql = f"UPDATE users SET {', '.join(fields)} WHERE user_id = ?;"
        async with get_db() as conn:
            await conn.execute(sql, tuple(params))
            await conn.commit()

# ------------------------------------------------------------------------------
# Template Operations
# ------------------------------------------------------------------------------

def _format_template_dict(row: Any) -> Dict[str, Any]:
    """Ensure both 'title' and 'name' as well as 'media_type' are present in dictionary."""
    if not row:
        return {}
    d = dict(row)
    title = d.get("title") or d.get("name") or "Template"
    d["title"] = title
    d["name"] = title
    d["media_type"] = d.get("media_type") or "photo"
    return d

async def add_template(
    file_id: str,
    name: Optional[str] = None,
    tags: str = "",
    is_trending: int = 0,
    file_unique_id: Optional[str] = None,
    media_type: str = "photo",
    title: Optional[str] = None,
    added_by: Optional[int] = None,
    source_channel_id: Optional[str] = None,
    source_channel_title: Optional[str] = None,
) -> int:
    """Register or update a meme template with its Telegram file_id, media_type, tags, and creator."""
    clean_file_id = file_id.strip()
    resolved_title = (title or name or "Template").strip()
    resolved_name = (name or title or "Template").strip()
    tags_str = (tags or "").strip()

    async with get_db() as conn:
        # Atomic upsert on UNIQUE(file_id): avoids SELECT-then-INSERT race under concurrency.
        await conn.execute(
            """
            INSERT INTO templates (
                file_id, file_unique_id, media_type, title, name, tags,
                added_by, source_channel_id, source_channel_title, is_trending, usage_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(file_id) DO UPDATE SET
                title = excluded.title,
                name = excluded.name,
                tags = excluded.tags,
                file_unique_id = COALESCE(excluded.file_unique_id, file_unique_id),
                media_type = excluded.media_type,
                added_by = COALESCE(excluded.added_by, added_by),
                source_channel_id = COALESCE(excluded.source_channel_id, source_channel_id),
                source_channel_title = COALESCE(excluded.source_channel_title, source_channel_title);
            """,
            (
                clean_file_id,
                file_unique_id,
                media_type,
                resolved_title,
                resolved_name,
                tags_str,
                added_by,
                source_channel_id,
                source_channel_title,
                1 if is_trending else 0,
            ),
        )
        await conn.commit()
        # lastrowid is unreliable on conflict; re-select the row id.
        async with conn.execute(
            "SELECT id FROM templates WHERE file_id = ?;", (clean_file_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row["id"] if row else 0

async def get_template_by_id(template_id: int) -> Optional[Dict[str, Any]]:
    """Retrieve template record by its primary key ID."""
    async with get_db() as conn:
        async with conn.execute(
            "SELECT * FROM templates WHERE id = ?;", (template_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return _format_template_dict(row) if row else None

async def get_all_templates(limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    """Fetch paginated list of meme templates ordered by popularity."""
    async with get_db() as conn:
        async with conn.execute(
            """
            SELECT * FROM templates
            ORDER BY is_trending DESC, usage_count DESC, id DESC
            LIMIT ? OFFSET ?;
            """,
            (limit, offset),
        ) as cursor:
            rows = await cursor.fetchall()
            return [_format_template_dict(r) for r in rows]

async def get_templates_paginated(limit: int = 6, offset: int = 0) -> List[Dict[str, Any]]:
    """Fetch paginated batch of templates for multi-column grid rendering."""
    return await get_all_templates(limit=limit, offset=offset)

async def get_templates_count() -> int:
    """Return total number of registered meme templates."""
    async with get_db() as conn:
        async with conn.execute("SELECT COUNT(*) as cnt FROM templates;") as cursor:
            row = await cursor.fetchone()
            return row["cnt"] if row else 0

async def get_trending_templates(limit: int = 10) -> List[Dict[str, Any]]:
    """Fetch high-scoring trending meme templates."""
    async with get_db() as conn:
        async with conn.execute(
            """
            SELECT * FROM templates
            WHERE is_trending = 1 OR usage_count > 0
            ORDER BY is_trending DESC, usage_count DESC
            LIMIT ?;
            """,
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [_format_template_dict(r) for r in rows]

async def get_random_template() -> Optional[Dict[str, Any]]:
    """Fetch a random meme template from the catalog."""
    async with get_db() as conn:
        async with conn.execute(
            "SELECT * FROM templates ORDER BY RANDOM() LIMIT 1;"
        ) as cursor:
            row = await cursor.fetchone()
            return _format_template_dict(row) if row else None

async def increment_template_usage(template_id: int) -> None:
    """Increment popularity usage count when a meme is rendered."""
    async with get_db() as conn:
        await conn.execute(
            "UPDATE templates SET usage_count = usage_count + 1 WHERE id = ?;",
            (template_id,),
        )
        await conn.commit()

async def delete_template(template_id: int) -> bool:
    """Hard delete a meme template from the library."""
    async with get_db() as conn:
        await conn.execute("DELETE FROM templates WHERE id = ?;", (template_id,))
        await conn.commit()
        return True

# ------------------------------------------------------------------------------
# Banner Operations (Strictly Opt-In)
# ------------------------------------------------------------------------------

async def add_banner(name: str, file_id: str, is_default: int = 0) -> int:
    """Add a promotional banner to the database."""
    async with get_db() as conn:
        if is_default:
            await conn.execute("UPDATE banners SET is_default = 0;")
        cursor = await conn.execute(
            "INSERT INTO banners (name, file_id, is_default) VALUES (?, ?, ?);",
            (name.strip(), file_id.strip(), 1 if is_default else 0),
        )
        await conn.commit()
        return cursor.lastrowid

async def get_banner_by_id(banner_id: int) -> Optional[Dict[str, Any]]:
    """Retrieve banner by ID."""
    async with get_db() as conn:
        async with conn.execute(
            "SELECT * FROM banners WHERE id = ?;", (banner_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def get_default_banner() -> Optional[Dict[str, Any]]:
    """Retrieve configured default banner, or latest banner if any."""
    async with get_db() as conn:
        async with conn.execute(
            "SELECT * FROM banners ORDER BY is_default DESC, id DESC LIMIT 1;"
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def get_all_banners() -> List[Dict[str, Any]]:
    """Retrieve all available promotional banners."""
    async with get_db() as conn:
        async with conn.execute("SELECT * FROM banners ORDER BY is_default DESC, id DESC;") as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

async def set_default_banner(banner_id: int) -> None:
    """Designate a specific banner as the default."""
    async with get_db() as conn:
        await conn.execute("UPDATE banners SET is_default = 0;")
        await conn.execute("UPDATE banners SET is_default = 1 WHERE id = ?;", (banner_id,))
        await conn.commit()

async def delete_banner(banner_id: int) -> bool:
    """Delete a promotional banner."""
    async with get_db() as conn:
        await conn.execute("DELETE FROM banners WHERE id = ?;", (banner_id,))
        await conn.commit()
        return True

# ------------------------------------------------------------------------------
# Alias Operations (High-Precision Search)
# ------------------------------------------------------------------------------

async def resolve_alias(term: str) -> Optional[str]:
    """Check if an informal/colloquial search term maps to a canonical name."""
    clean_term = term.strip().lower()
    async with get_db() as conn:
        async with conn.execute(
            "SELECT canonical_name FROM aliases WHERE LOWER(alias_term) = ?;",
            (clean_term,),
        ) as cursor:
            row = await cursor.fetchone()
            return row["canonical_name"] if row else None

async def add_alias(term: str, canonical: str) -> None:
    """Register a new alias mapping (stored in normalized form so lookups match)."""
    # Local import: search_engine imports database.queries, so a top-level import would be circular.
    from services.search_engine import normalize_query
    clean_term = normalize_query(term) or term.strip().lower()
    async with get_db() as conn:
        await conn.execute(
            """
            INSERT INTO aliases (alias_term, canonical_name)
            VALUES (?, ?)
            ON CONFLICT(alias_term) DO UPDATE SET canonical_name = excluded.canonical_name;
            """,
            (clean_term, canonical.strip()),
        )
        await conn.commit()

async def get_all_aliases() -> List[Dict[str, Any]]:
    """Retrieve all alias pairs."""
    async with get_db() as conn:
        async with conn.execute("SELECT * FROM aliases ORDER BY alias_term ASC;") as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

async def delete_alias(term: str) -> bool:
    """Delete an alias mapping by term (normalized form); returns True if a row was removed."""
    from services.search_engine import normalize_query
    clean_term = normalize_query(term) or term.strip().lower()
    async with get_db() as conn:
        cursor = await conn.execute("DELETE FROM aliases WHERE alias_term = ?;", (clean_term,))
        await conn.commit()
        return cursor.rowcount > 0

# ------------------------------------------------------------------------------
# External Source Operations (Admin-Managed Meme Repositories)
# ------------------------------------------------------------------------------

async def add_source(url: str, name: Optional[str] = None) -> int:
    """Register or update an external meme API / feed source."""
    clean_url = url.strip()
    source_name = (name or clean_url).strip()
    async with get_db() as conn:
        cursor = await conn.execute(
            """
            INSERT INTO sources (url, name, is_active)
            VALUES (?, ?, 1)
            ON CONFLICT(url) DO UPDATE SET name = excluded.name, is_active = 1;
            """,
            (clean_url, source_name),
        )
        await conn.commit()
        return cursor.lastrowid

async def get_all_sources() -> List[Dict[str, Any]]:
    """Retrieve all configured external sources."""
    async with get_db() as conn:
        async with conn.execute("SELECT * FROM sources ORDER BY id DESC;") as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

async def remove_source(source_id: int) -> bool:
    """Remove an external meme feed source."""
    async with get_db() as conn:
        await conn.execute("DELETE FROM sources WHERE id = ?;", (source_id,))
        await conn.commit()
        return True

async def is_url_scraped(url: str) -> bool:
    """Check whether a source URL was already scraped (dedupe for the background scraper)."""
    async with get_db() as conn:
        async with conn.execute(
            "SELECT 1 FROM scraped_urls WHERE url = ?;", (url.strip(),)
        ) as cursor:
            return await cursor.fetchone() is not None

async def mark_url_scraped(url: str, source_id: Optional[int]) -> None:
    """Record a source URL as scraped (idempotent)."""
    async with get_db() as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO scraped_urls (url, source_id) VALUES (?, ?);",
            (url.strip(), source_id),
        )
        await conn.commit()

# ------------------------------------------------------------------------------
# Favorites (user bookmarked templates)
# ------------------------------------------------------------------------------

async def add_favorite(user_id: int, template_id: int) -> None:
    """Bookmark a template for a user (idempotent)."""
    async with get_db() as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO favorites (user_id, template_id) VALUES (?, ?);",
            (user_id, template_id),
        )
        await conn.commit()

async def remove_favorite(user_id: int, template_id: int) -> bool:
    """Remove a bookmark; returns True if a row was deleted."""
    async with get_db() as conn:
        cursor = await conn.execute(
            "DELETE FROM favorites WHERE user_id = ? AND template_id = ?;",
            (user_id, template_id),
        )
        await conn.commit()
        return cursor.rowcount > 0

async def is_favorite(user_id: int, template_id: int) -> bool:
    """Check whether a template is bookmarked by a user."""
    async with get_db() as conn:
        async with conn.execute(
            "SELECT 1 FROM favorites WHERE user_id = ? AND template_id = ?;",
            (user_id, template_id),
        ) as cursor:
            return await cursor.fetchone() is not None

async def get_favorites(user_id: int) -> List[Dict[str, Any]]:
    """Return the user's bookmarked templates, most recently added first."""
    async with get_db() as conn:
        async with conn.execute(
            """
            SELECT t.* FROM templates t
            JOIN favorites f ON f.template_id = t.id
            WHERE f.user_id = ?
            ORDER BY f.created_at DESC, f.rowid DESC;
            """,
            (user_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [_format_template_dict(r) for r in rows]

# ------------------------------------------------------------------------------
# Drafts (one saved meme draft per user)
# ------------------------------------------------------------------------------

async def save_draft(user_id: int, data: dict) -> None:
    """Persist one draft per user (REPLACE semantics via atomic upsert).

    Extracts editor FSM keys with table defaults; booleans normalized to int.
    file_id is required — without it nothing is written.
    """
    raw_file_id = data.get("file_id")
    file_id = raw_file_id.strip() if isinstance(raw_file_id, str) else raw_file_id
    if not file_id:
        return

    def _d(key: str, default: Any) -> Any:
        v = data.get(key)
        return default if v is None else v

    def _b(key: str, default: int) -> int:
        v = data.get(key)
        return default if v is None else int(bool(v))

    vals = {
        "template_id": _d("template_id", None),
        "file_id": file_id,
        "text": _d("text", None),
        "variant": _d("variant", "overlay"),
        "text_color": _d("text_color", "white"),
        "stroke_width": _d("stroke_width", 4),
        "case_mode": _d("case_mode", "raw"),
        "filter_name": _d("filter", None) or _d("filter_name", "none"),
        "font_key": _d("font_key", None),
        "watermark_enabled": _b("watermark_enabled", 1),
        "watermark_pos": _d("watermark_pos", "bottom_right"),
        "watermark_scale": _d("watermark_scale", 1.0),
        "watermark_file_id": _d("watermark_file_id", None),
        "watermark_text": _d("watermark_text", None),
        "watermark_opacity": _d("watermark_opacity", 0.8),
        "banner_id": _d("banner_id", None),
        "is_clean": _b("is_clean", 0),
    }
    cols = ["user_id"] + list(vals.keys())
    placeholders = ", ".join(["?"] * len(cols))
    updates = ", ".join(f"{c} = excluded.{c}" for c in vals.keys())
    async with get_db() as conn:
        await conn.execute(
            f"INSERT INTO drafts ({', '.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(user_id) DO UPDATE SET {updates}, updated_at = CURRENT_TIMESTAMP;",
            (user_id, *[vals[c] for c in vals.keys()]),
        )
        await conn.commit()

async def get_draft(user_id: int) -> Optional[Dict[str, Any]]:
    """Return the user's saved draft as a dict, or None if there is none."""
    async with get_db() as conn:
        async with conn.execute(
            "SELECT * FROM drafts WHERE user_id = ?;", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def delete_draft(user_id: int) -> bool:
    """Delete the user's saved draft; returns True if a row was deleted."""
    async with get_db() as conn:
        cursor = await conn.execute(
            "DELETE FROM drafts WHERE user_id = ?;", (user_id,)
        )
        await conn.commit()
        return cursor.rowcount > 0
