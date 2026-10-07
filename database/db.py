import aiosqlite
from contextlib import asynccontextmanager
from typing import AsyncGenerator
import config

@asynccontextmanager
async def get_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    """
    Asynchronous database connection context manager.
    Ensures parent directory exists, uses dictionary-like aiosqlite.Row factory,
    and enables WAL mode and foreign keys for high concurrency and data integrity.
    """
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(config.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL;")
        await conn.execute("PRAGMA foreign_keys=ON;")
        yield conn

async def init_db() -> None:
    """
    Initialize SQLite database tables and indexes strictly matching
    the system architecture blueprint.
    """
    async with get_db() as conn:
        # 1. Users table (preferences and custom watermark state)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                preferred_font TEXT NOT NULL DEFAULT 'default',
                watermark_file_id TEXT,
                watermark_enabled INTEGER NOT NULL DEFAULT 0,
                watermark_position TEXT NOT NULL DEFAULT 'bottom_right',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 2. Templates catalog (Zero local disk: Telegram file_id only)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_id TEXT NOT NULL,
                name TEXT NOT NULL,
                tags TEXT DEFAULT '',
                is_trending INTEGER NOT NULL DEFAULT 0,
                usage_count INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_templates_search ON templates(name, tags);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_templates_ranking ON templates(is_trending DESC, usage_count DESC);")

        # 3. Banners table (Admin-managed promotional banners)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS banners (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                file_id TEXT NOT NULL,
                is_default INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_banners_default ON banners(is_default DESC);")

        # 4. Aliases table (Meme alias mapping for high-precision search)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS aliases (
                alias_term TEXT PRIMARY KEY,
                canonical_name TEXT NOT NULL
            );
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_aliases_term ON aliases(alias_term);")

        await conn.commit()
