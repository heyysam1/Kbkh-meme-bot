import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeDefault

import config
from database.db import init_db
from handlers import (
    start,
    catalog,
    editor,
    submission,
    search_flow,
    settings,
    admin,
    channel,
    meme_flow,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("kbkh_meme_bot")

async def set_bot_commands(bot: Bot) -> None:
    """Register official Bot Commands for native Telegram Menu and autocomplete (Strict Zero Emoji)."""
    commands = [
        BotCommand(command="start", description="Start the bot and view dashboard"),
        BotCommand(command="template", description="Browse meme template catalog grid"),
        BotCommand(command="add_template", description="Upload a new meme template to catalog"),
        BotCommand(command="search", description="Search templates and public memes"),
        BotCommand(command="admin", description="Access admin configuration panel"),
        BotCommand(command="help", description="View usage guide and shortcuts"),
    ]
    await bot.set_my_commands(commands, scope=BotCommandScopeDefault())
    logger.info("[OK] Telegram official bot commands registered successfully.")

async def on_startup(bot: Bot) -> None:
    """Execute initialization routines before polling starts."""
    logger.info("[INIT] Initializing KBKH Meme Bot subsystems...")
    # Initialize SQLite database, schema, and auto-migrations
    await init_db()
    logger.info("[OK] SQLite database, schema, and migrations initialized successfully.")

    # Validate asset paths
    if not config.WHITE_LOGO_PATH.exists() or not config.BLACK_LOGO_PATH.exists():
        logger.warning("[WARN] One or more KBKH brand logos were not found in assets/logos/.")
    else:
        logger.info("[OK] KBKH brand logo assets verified.")

    # Register Bot command menu
    await set_bot_commands(bot)

    bot_info = await bot.get_me()
    logger.info(f"[OK] Bot started successfully as @{bot_info.username} (ID: {bot_info.id})")

async def main() -> None:
    """Bootstrap and start bot polling."""
    # Ensure token is set
    if not config.BOT_TOKEN:
        logger.error("[CRITICAL] BOT_TOKEN is not configured! Please set BOT_TOKEN in .env file.")
        sys.exit(1)

    # Initialize Bot instance with HTML default formatting
    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode="HTML"),
    )

    # Initialize Dispatcher with in-memory FSM storage
    dp = Dispatcher(storage=MemoryStorage())

    # Register modular routers in strict hierarchical priority
    dp.include_router(admin.router)          # Admin overrides
    dp.include_router(start.router)          # /start, /help, /cancel
    dp.include_router(catalog.router)        # /template, grid browsing, /random
    dp.include_router(submission.router)     # /add_template user submission FSM
    dp.include_router(search_flow.router)    # /search & hybrid engine
    dp.include_router(editor.router)         # Editing FSM & all edit:* callbacks
    dp.include_router(settings.router)       # User & watermark settings
    dp.include_router(channel.router)        # Unrestricted channel_post listeners

    # Register startup hook
    dp.startup.register(on_startup)

    try:
        logger.info("[LOOP] Starting long-polling event loop...")
        allowed_updates = ["message", "callback_query", "channel_post", "edited_channel_post"]
        await dp.start_polling(bot, allowed_updates=allowed_updates)
    finally:
        await bot.session.close()
        logger.info("[CLOSE] Bot session closed cleanly.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("[EXIT] Bot execution terminated by user.")
