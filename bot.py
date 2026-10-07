import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeDefault

import config
from database.db import init_db
from handlers import start, meme_flow, search_flow, settings, admin, channel

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("kbkh_meme_bot")

async def set_bot_commands(bot: Bot) -> None:
    """Register official Bot Commands for native Telegram Menu and autocomplete."""
    commands = [
        BotCommand(command="start", description="🚀 বট শুরু করুন ও ড্যাশবোর্ড দেখুন"),
        BotCommand(command="template", description="📂 সব মিম টেমপ্লেট ব্রাউজ করুন"),
        BotCommand(command="search", description="🔍 লোকাল ও ট্রেন্ডিং মিম খুঁজুন"),
        BotCommand(command="random", description="🎲 র্যান্ডম মিম টেমপ্লেট পান"),
        BotCommand(command="help", description="ℹ️ ব্যবহারের নিয়ম ও গাইড"),
    ]
    await bot.set_my_commands(commands, scope=BotCommandScopeDefault())
    logger.info("✓ Telegram official bot commands registered successfully.")

async def on_startup(bot: Bot) -> None:
    """Execute initialization routines before polling starts."""
    logger.info("Initializing KBKH Meme Bot subsystems...")
    # Initialize SQLite database, schema, and auto-migrations
    await init_db()
    logger.info("✓ SQLite database, schema, and migrations initialized successfully.")

    # Validate asset paths
    if not config.WHITE_LOGO_PATH.exists() or not config.BLACK_LOGO_PATH.exists():
        logger.warning("⚠️ Warning: One or more KBKH brand logos were not found in assets/logos/.")
    else:
        logger.info("✓ KBKH brand logo assets verified.")

    # Register Bot command menu
    await set_bot_commands(bot)

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

    # Register modular routers (channel ingestion first, then admin, start, flows)
    dp.include_router(channel.router)     # Multi-channel unrestricted ingestion
    dp.include_router(admin.router)       # Admin commands & retroactive bulk ingestion
    dp.include_router(start.router)       # /start, /help, main menu
    dp.include_router(meme_flow.router)   # FSM meme generation & post-edit actions
    dp.include_router(search_flow.router) # Search & template discovery
    dp.include_router(settings.router)    # Watermark & font preferences

    # Register startup hook
    dp.startup.register(on_startup)

    try:
        logger.info("Starting long-polling event loop...")
        allowed_updates = ["message", "callback_query", "channel_post", "edited_channel_post"]
        await dp.start_polling(bot, allowed_updates=allowed_updates)
    finally:
        await bot.session.close()
        logger.info("Bot session closed cleanly.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot execution terminated by user.")
