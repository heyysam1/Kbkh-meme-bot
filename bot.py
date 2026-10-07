import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

import config
from database.db import init_db
from handlers import start, meme_flow, search_flow, settings, admin

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("kbkh_meme_bot")

async def on_startup(bot: Bot) -> None:
    """Execute initialization routines before polling starts."""
    logger.info("Initializing KBKH Meme Bot subsystems...")
    # Initialize SQLite database and tables
    await init_db()
    logger.info("✓ SQLite database and schema initialized successfully.")

    # Validate asset paths
    if not config.WHITE_LOGO_PATH.exists() or not config.BLACK_LOGO_PATH.exists():
        logger.warning("⚠️ Warning: One or more KBKH brand logos were not found in assets/logos/.")
    else:
        logger.info("✓ KBKH brand logo assets verified.")

    bot_info = await bot.get_me()
    logger.info(f"✓ Bot started successfully as @{bot_info.username} (ID: {bot_info.id})")

async def main() -> None:
    """Bootstrap and start bot polling."""
    # Ensure token is set
    if not config.BOT_TOKEN:
        logger.error("CRITICAL: BOT_TOKEN is not configured! Please set BOT_TOKEN in .env file.")
        sys.exit(1)

    # Initialize Bot instance with HTML default formatting
    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode="HTML"),
    )

    # Initialize Dispatcher with in-memory FSM storage
    dp = Dispatcher(storage=MemoryStorage())

    # Register modular routers
    dp.include_router(admin.router)       # Admin commands & channel listener
    dp.include_router(start.router)       # /start, /help, main menu
    dp.include_router(meme_flow.router)   # FSM meme generation & post-edit actions
    dp.include_router(search_flow.router) # Search & template discovery
    dp.include_router(settings.router)    # Watermark & font preferences

    # Register startup hook
    dp.startup.register(on_startup)

    try:
        logger.info("Starting long-polling event loop...")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
        logger.info("Bot session closed cleanly.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot execution terminated by user.")
