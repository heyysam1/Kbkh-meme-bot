import aiosqlite
from contextlib import asynccontextmanager
from typing import AsyncGenerator
import config
from database.schema import auto_migrate

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
    Initialize SQLite database tables, run auto-migration,
    and create indexes for high-concurrency operations.
    """
    async with get_db() as conn:
        await auto_migrate(conn)
        await conn.commit()
