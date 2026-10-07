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
    """Fetch existing user profile or initialize on first interaction."""
    existing = await get_user(user_id)
    if existing:
        return existing

    async with get_db() as conn:
        await conn.execute(
            """
            INSERT INTO users (
                user_id, preferred_font, watermark_file_id, watermark_text,
                watermark_enabled, watermark_position, watermark_scale, watermark_opacity
            )
            VALUES (?, ?, NULL, NULL, 0, 'bottom_right', 1.0, 0.8);
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
        # Check if template already exists by file_id
        async with conn.execute(
            "SELECT id FROM templates WHERE file_id = ?;", (clean_file_id,)
        ) as cursor:
            existing = await cursor.fetchone()
            if existing:
                t_id = existing["id"]
                await conn.execute(
                    """
                    UPDATE templates
                    SET title = ?, name = ?, tags = ?, file_unique_id = COALESCE(?, file_unique_id),
                        media_type = ?, added_by = COALESCE(?, added_by),
                        source_channel_id = COALESCE(?, source_channel_id),
                        source_channel_title = COALESCE(?, source_channel_title)
                    WHERE id = ?;
                    """,
                    (
                        resolved_title,
                        resolved_name,
                        tags_str,
                        file_unique_id,
                        media_type,
                        added_by,
                        source_channel_id,
                        source_channel_title,
                        t_id,
                    ),
                )
                await conn.commit()
                return t_id

        cursor = await conn.execute(
            """
            INSERT INTO templates (
                file_id, file_unique_id, media_type, title, name, tags,
                added_by, source_channel_id, source_channel_title, is_trending, usage_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0);
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
        return cursor.lastrowid

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
    """Register a new alias mapping."""
    async with get_db() as conn:
        await conn.execute(
            """
            INSERT INTO aliases (alias_term, canonical_name)
            VALUES (?, ?)
            ON CONFLICT(alias_term) DO UPDATE SET canonical_name = excluded.canonical_name;
            """,
            (term.strip().lower(), canonical.strip()),
        )
        await conn.commit()

async def get_all_aliases() -> List[Dict[str, Any]]:
    """Retrieve all alias pairs."""
    async with get_db() as conn:
        async with conn.execute("SELECT * FROM aliases ORDER BY alias_term ASC;") as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

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
