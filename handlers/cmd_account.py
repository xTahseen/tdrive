"""
/webui command — manage WebUI credentials via a button-driven flow.
Steps the user through: /webui → [Set Credentials] → asks username → asks password → saves.
"""
import hashlib
import logging

from pyrogram import Client, filters
from pyrogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

from config import Config

logger = logging.getLogger(__name__)

# Tracks per-user state: {"step": "username"|"password", "username": str, "prompt_msg_id": int}
_pending: dict[int, dict] = {}


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _main_keyboard(has_creds: bool) -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton("🔑 Set Credentials", callback_data="webui:set")]]
    if has_creds:
        buttons.append([InlineKeyboardButton("🗑 Clear Credentials", callback_data="webui:clear")])
    return InlineKeyboardMarkup(buttons)


async def _show_main(client, target, user_id: int, edit: bool = False):
    if not Config.WEBUI_ENABLED:
        text = (
            "⚠️ **WebUI is not enabled.**\n\n"
            "Set `WEBUI_ENABLED=true` in your environment variables and restart the bot."
        )
        if edit:
            await target.edit_text(text)
        else:
            await target.reply_text(text)
        return

    has_creds = await client.db.has_webui_credentials()
    status = "✅ Credentials set — login is **enabled**." if has_creds else "❌ No credentials — login is **disabled**."
    text = (
        f"🌐 **WebUI Account Manager**\n\n"
        f"**Status:** {status}\n"
        f"**URL:** {Config.WEBUI_BASE_URL}\n\n"
        f"Use the buttons below to manage your login credentials."
    )
    keyboard = _main_keyboard(has_creds)
    if edit:
        await target.edit_text(text, reply_markup=keyboard)
    else:
        await target.reply_text(text, reply_markup=keyboard)


def register(app: Client):

    # ── /webui command ─────────────────────────────────────────────────────────
    @app.on_message(filters.command("webui") & filters.private)
    async def webui_handler(client: Client, message: Message):
        _pending.pop(message.from_user.id, None)
        await _show_main(client, message, message.from_user.id, edit=False)

    # ── Inline button callbacks ────────────────────────────────────────────────
    @app.on_callback_query(filters.regex(r"^webui:"))
    async def webui_callback(client: Client, query: CallbackQuery):
        user_id = query.from_user.id
        action = query.data.split(":", 1)[1]

        if action == "set":
            await query.answer()
            _pending[user_id] = {"step": "username"}
            await query.message.edit_text(
                "👤 **Set WebUI Username**\n\n"
                "Please send your desired username.\n"
                "_(minimum 3 characters)_",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("❌ Cancel", callback_data="webui:cancel")]]
                ),
            )

        elif action == "clear":
            await query.answer()
            await query.message.edit_text(
                "🗑 **Clear credentials?**\n\n"
                "This will disable WebUI login until you set new credentials.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Yes, Clear", callback_data="webui:clear_confirm"),
                     InlineKeyboardButton("❌ Cancel",     callback_data="webui:back")],
                ]),
            )

        elif action == "clear_confirm":
            await query.answer()
            await client.db.clear_webui_credentials()
            logger.info(f"User {user_id} cleared WebUI credentials")
            await _show_main(client, query.message, user_id, edit=True)

        elif action == "cancel":
            await query.answer("Cancelled.")
            _pending.pop(user_id, None)
            await _show_main(client, query.message, user_id, edit=True)

        elif action == "back":
            await query.answer()
            await _show_main(client, query.message, user_id, edit=True)

    # ── Text input listener ────────────────────────────────────────────────────
    @app.on_message(
        filters.private & filters.text
        & ~filters.command(["start", "logout", "help", "drives", "storage", "search", "webui"]),
        group=2,
    )
    async def webui_text_input(client: Client, message: Message):
        user_id = message.from_user.id
        state = _pending.get(user_id)
        if not state:
            return

        text = message.text.strip()

        # ── Waiting for username ───────────────────────────────────────────────
        if state["step"] == "username":
            if len(text) < 3:
                await message.reply_text(
                    "❌ Username must be at least 3 characters. Please try again.",
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton("❌ Cancel", callback_data="webui:cancel")]]
                    ),
                )
                return

            _pending[user_id] = {"step": "password", "username": text}
            await message.reply_text(
                f"✅ Username: `{text}`\n\n"
                "🔑 **Now send your password.**\n"
                "_(minimum 6 characters)_",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("❌ Cancel", callback_data="webui:cancel")]]
                ),
            )

        # ── Waiting for password ───────────────────────────────────────────────
        elif state["step"] == "password":
            if len(text) < 6:
                await message.reply_text(
                    "❌ Password must be at least 6 characters. Please try again.",
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton("❌ Cancel", callback_data="webui:cancel")]]
                    ),
                )
                return

            username = state["username"]
            _pending.pop(user_id, None)

            await client.db.set_webui_credentials(
                hashed_username=_hash(username),
                hashed_password=_hash(text),
                user_id=user_id,
            )
            logger.info(f"User {user_id} updated WebUI credentials")

            await message.reply_text(
                f"✅ **WebUI credentials saved!**\n\n"
                f"👤 Username: `{username}`\n"
                f"🔑 Password: `{'•' * len(text)}`\n\n"
                f"🌐 Open your dashboard: {Config.WEBUI_BASE_URL}\n\n"
                f"⚠️ Delete these messages to keep your credentials safe.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🌐 Open WebUI", url=Config.WEBUI_BASE_URL)]]
                ),
            )
