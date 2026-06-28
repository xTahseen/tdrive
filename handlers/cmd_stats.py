"""
/stats — admin-only command showing bot usage statistics.

Only users listed in Config.ADMIN_IDS may use this command.
"""

import logging
from datetime import datetime

from pyrogram import Client, filters
from pyrogram.types import Message

from config import Config
from logger import get_stats

logger = logging.getLogger(__name__)


def _fmt_size(size: int) -> str:
    if size < 1024:           return f"{size} B"
    elif size < 1024 ** 2:    return f"{size/1024:.1f} KB"
    elif size < 1024 ** 3:    return f"{size/1024**2:.1f} MB"
    elif size < 1024 ** 4:    return f"{size/1024**3:.2f} GB"
    return f"{size/1024**4:.2f} TB"


def _user_label(doc: dict) -> str:
    name = doc.get("first_name") or "Unknown"
    uname = f" @{doc['username']}" if doc.get("username") else ""
    return f"{name}{uname} (`{doc['_id']}`)"


def register(app: Client):

    @app.on_message(filters.command("stats") & filters.private)
    async def stats_handler(client: Client, message: Message):
        user_id = message.from_user.id

        if user_id not in Config.ADMIN_IDS:
            await message.reply_text("⛔ You are not authorised to use this command.")
            return

        loading = await message.reply_text("⏳ Fetching statistics…")

        try:
            s = await get_stats(client.db.db)
        except Exception as e:
            logger.error(f"/stats error: {e}", exc_info=True)
            await loading.edit_text(f"❌ Failed to fetch stats: {e}")
            return

        total_bytes   = s["total_bytes"]
        top           = s["top_uploaders"]

        top_lines = ""
        for i, doc in enumerate(top, 1):
            label = _user_label(doc)
            top_lines += (
                f"  {i}. {label}\n"
                f"     {doc['count']} uploads · {_fmt_size(doc['bytes'])}\n"
            )

        text = (
            "📊 **Bot Statistics**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👥 **Users**\n"
            f"  • Total registered: `{s['total_users']}`\n"
            f"  • Total /start joins: `{s['total_joins']}`\n"
            f"  • Currently blocked: `{s['currently_blocked']}`\n\n"
            f"📤 **Uploads**\n"
            f"  • Total uploads: `{s['total_uploads']}`\n"
            f"  • Successful: `{s['total_uploads'] - s['failed_uploads']}`\n"
            f"  • Failed/cancelled: `{s['failed_uploads']}`\n"
            f"  • Unique uploaders: `{s['unique_uploaders']}`\n"
            f"  • Total data uploaded: `{_fmt_size(total_bytes)}`\n\n"
        )

        if top_lines:
            text += f"🏆 **Top Uploaders**\n{top_lines}\n"

        text += f"_Updated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}_"

        await loading.edit_text(text)
