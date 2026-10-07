"""
Database schema definitions and auto-migration routines for KBKH Meme Engine.
Ensures zero-downtime updates and backwards compatibility for existing SQLite databases.
"""

import aiosqlite

CREATE_TABLES_SQL = """
-- Users table (preferences, font selection, and custom watermark settings)
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    preferred_font TEXT NOT NULL DEFAULT 'default',
    watermark_file_id TEXT,
    watermark_text TEXT,
    watermark_enabled INTEGER NOT NULL DEFAULT 0,
    watermark_position TEXT NOT NULL DEFAULT 'bottom_right',
    watermark_scale REAL NOT NULL DEFAULT 1.0,
    watermark_opacity REAL NOT NULL DEFAULT 0.8,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Templates catalog (Zero local disk: Telegram file_id & multi-mime support)
CREATE TABLE IF NOT EXISTS templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id TEXT UNIQUE NOT NULL,
    file_unique_id TEXT,
    media_type TEXT NOT NULL DEFAULT 'photo',
    title TEXT,
    name TEXT,
    tags TEXT DEFAULT '',
    added_by INTEGER,
    source_channel_id TEXT,
    source_channel_title TEXT,
    is_trending INTEGER NOT NULL DEFAULT 0,
    usage_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Banners table (Admin-managed promotional sponsor banners)
CREATE TABLE IF NOT EXISTS banners (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    file_id TEXT NOT NULL,
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Aliases table (Meme colloquial alias mapping for high-precision search)
CREATE TABLE IF NOT EXISTS aliases (
    alias_term TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL
);

-- External sources table (Admin-managed public meme sources & RSS/APIs)
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT UNIQUE NOT NULL,
    name TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

async def auto_migrate(conn: aiosqlite.Connection) -> None:
    """
    Safely inspect and patch database schema on startup without data loss.
    Adds media_type, file_unique_id, source_channel_id, title, added_by, and sources table if missing.
    """
    # 1. Ensure all base tables exist
    await conn.executescript(CREATE_TABLES_SQL)

    # 2. Inspect templates table schema
    async with conn.execute("PRAGMA table_info(templates);") as cursor:
        rows = await cursor.fetchall()
        existing_cols = {row["name"] if isinstance(row, dict) or hasattr(row, "keys") else row[1] for row in rows}

    # 3. Add any missing columns dynamically to templates
    required_columns = {
        "file_unique_id": "TEXT",
        "media_type": "TEXT NOT NULL DEFAULT 'photo'",
        "title": "TEXT",
        "name": "TEXT",
        "added_by": "INTEGER",
        "source_channel_id": "TEXT",
        "source_channel_title": "TEXT",
        "is_trending": "INTEGER NOT NULL DEFAULT 0",
        "usage_count": "INTEGER NOT NULL DEFAULT 0",
    }

    for col_name, col_def in required_columns.items():
        if col_name not in existing_cols:
            await conn.execute(f"ALTER TABLE templates ADD COLUMN {col_name} {col_def};")

    # 4. Inspect users table schema for watermark options
    async with conn.execute("PRAGMA table_info(users);") as cursor:
        user_rows = await cursor.fetchall()
        user_cols = {row["name"] if isinstance(row, dict) or hasattr(row, "keys") else row[1] for row in user_rows}

    user_required = {
        "watermark_text": "TEXT",
        "watermark_scale": "REAL NOT NULL DEFAULT 1.0",
        "watermark_opacity": "REAL NOT NULL DEFAULT 0.8",
    }
    for col_name, col_def in user_required.items():
        if col_name not in user_cols:
            await conn.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_def};")

    # 5. Synchronize title <-> name columns for backwards compatibility
    await conn.execute(
        "UPDATE templates SET title = name WHERE (title IS NULL OR title = '') AND name IS NOT NULL;"
    )
    await conn.execute(
        "UPDATE templates SET name = title WHERE (name IS NULL OR name = '') AND title IS NOT NULL;"
    )

    # 6. Create performant indexes
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_templates_search ON templates(name, tags);")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_templates_title ON templates(title);")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_templates_media_type ON templates(media_type);")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_templates_ranking ON templates(is_trending DESC, usage_count DESC);")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_templates_added_by ON templates(added_by);")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_banners_default ON banners(is_default DESC);")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_aliases_term ON aliases(alias_term);")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_sources_active ON sources(is_active);")
