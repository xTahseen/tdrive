"""
/setpassword command — sets the WebUI login password.
Only works when WEBUI_ENABLED=true.
"""
import hashlib
import logging

from pyrogram import Client, filters
from pyrogram.types import Message

from config import Config

logger = logging.getLogger(__name__)


def _hash(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()


def register(app: Client):
    @app.on_message(filters.command("setpassword") & filters.private)
    async def setpassword_handler(client: Client, message: Message):
        # Check at runtime (not import time) so Koyeb env vars are always respected
        if not Config.WEBUI_ENABLED:
            await message.reply_text(
                "⚠️ WebUI is not enabled.\n\n"
                "Set `WEBUI_ENABLED=true` in your environment variables and restart the bot."
            )
            return

        parts = message.text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            await message.reply_text(
                "🔑 **Set WebUI Password**\n\n"
                "Usage: `/setpassword <your_new_password>`\n\n"
                "⚠️ Choose a strong password — this protects access to your Google Drive files."
            )
            return

        password = parts[1].strip()
        if len(password) < 6:
            await message.reply_text("❌ Password must be at least 6 characters.")
            return

        await client.db.set_webui_password(_hash(password), message.from_user.id)

        webui_url = Config.WEBUI_BASE_URL
        await message.reply_text(
            f"✅ **WebUI password updated!**\n\n"
            f"🌐 Open your dashboard: {webui_url}\n"
            f"🔑 Sign in with your new password.\n\n"
            f"⚠️ Delete this message to keep your password safe."
        )
        logger.info(f"User {message.from_user.id} updated WebUI password")
