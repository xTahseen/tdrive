import asyncio
import logging
import os

from pyrogram import Client, idle
from pyrogram.types import BotCommand

from config import Config
from database import Database
from gdrive import GoogleDriveManager
from handlers import register_handlers

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")


async def start_webui(db: Database, gdrive: GoogleDriveManager, bot_app: Client):
    """Start the aiohttp WebUI server."""
    from aiohttp import web as aiohttp_web
    from webui.app import create_app

    web_app = create_app(db, gdrive, bot=bot_app)
    runner = aiohttp_web.AppRunner(web_app)
    await runner.setup()
    site = aiohttp_web.TCPSite(runner, Config.WEBUI_HOST, Config.WEBUI_PORT)
    await site.start()
    logger.info(
        f"WebUI started at http://{Config.WEBUI_HOST}:{Config.WEBUI_PORT} "
        f"(public: {Config.WEBUI_BASE_URL})"
    )
    return runner


async def main():
    Config.validate()

    db = Database(Config.MONGO_URI, Config.DB_NAME)
    await db.connect()
    logger.info("MongoDB connected successfully.")

    await db.migrate_legacy_tokens()

    app = Client(
        "gdrive_bot",
        api_id=Config.API_ID,
        api_hash=Config.API_HASH,
        bot_token=Config.BOT_TOKEN,
        workers=64,
        max_concurrent_transmissions=10,
    )

    app.db = db
    app.gdrive = GoogleDriveManager(db)

    register_handlers(app)

    logger.info("Starting bot...")
    await app.start()

    # Build bot commands list
    commands = [
        BotCommand("start",   "Start the bot"),
        BotCommand("drives",  "Manage Drive accounts & connect new ones"),
        BotCommand("storage", "View storage usage for all drives"),
        BotCommand("search",  "Search files in Google Drive"),
        BotCommand("logout",  "Disconnect active Google Drive"),
        BotCommand("help",    "Show help"),
    ]
    if Config.WEBUI_ENABLED:
        commands.append(BotCommand("setpassword", "Set WebUI login password"))

    await app.set_bot_commands(commands)
    logger.info("Bot commands synced with Telegram.")

    # Start WebUI if enabled
    webui_runner = None
    if Config.WEBUI_ENABLED:
        try:
            webui_runner = await start_webui(db, app.gdrive, app)
        except Exception as e:
            logger.error(f"Failed to start WebUI: {e}", exc_info=True)
    else:
        logger.info("WebUI is disabled (set WEBUI_ENABLED=true to enable).")

    logger.info("Bot is running! Press Ctrl+C to stop.")
    await idle()

    await app.stop()
    if webui_runner:
        await webui_runner.cleanup()
        logger.info("WebUI stopped.")
    await db.close()
    logger.info("Bot stopped.")


if __name__ == "__main__":
    try:
        import uvloop
        logger.info("uvloop detected — using fast event loop.")
        uvloop.run(main())
    except ImportError:
        asyncio.run(main())
