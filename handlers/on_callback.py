import logging
from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from .file_manager import open_file_manager

logger = logging.getLogger(__name__)


def register(app: Client):

    @app.on_callback_query(~filters.regex(r"^fm:") & ~filters.regex(r"^storage:") & ~filters.regex(r"^upload:") & ~filters.regex(r"^sr:") & ~filters.regex(r"^search:") & ~filters.regex(r"^webui:"))
    async def callback_handler(client: Client, query: CallbackQuery):
        user_id = query.from_user.id
        data = query.data

        if data == "auth_start":
            await query.answer()
            await query.message.reply_text(
                "Use /drives to connect a Google Drive account."
            )

        elif data == "cancel_action":
            await query.answer("Cancelled.")
            await query.message.edit_text("✅ Action cancelled.")

        elif data == "my_files":
            await query.answer()
            if not await client.db.is_authenticated(user_id):
                await query.message.reply_text("❌ Not connected. Use /drives.")
                return
            await open_file_manager(client, query.message, user_id)

        elif data == "show_help":
            await query.answer()
            from .cmd_help import HELP_TEXT
            await query.message.reply_text(HELP_TEXT, disable_web_page_preview=True)

        elif data.startswith("drive:"):
            parts = data.split(":")
            action = parts[1] if len(parts) > 1 else ""

            if action == "list":
                await query.answer()
                from .cmd_drives import build_drives_message
                text, keyboard = await build_drives_message(client, user_id)
                await query.message.edit_text(text, reply_markup=keyboard)

            elif action == "switch":
                idx = int(parts[2])
                await client.db.set_active_drive(user_id, idx)
                drives = await client.db.get_all_drives(user_id)
                email = drives[idx].get("email", f"Drive {idx+1}") if idx < len(drives) else "?"
                await query.answer(f"⭐ {email} set as default.", show_alert=False)
                from .cmd_drives import build_drives_message
                text, keyboard = await build_drives_message(client, user_id)
                await query.message.edit_text(text, reply_markup=keyboard)

            elif action == "view":
                idx = int(parts[2])
                await client.db.set_active_drive(user_id, idx)
                await query.answer()
                await open_file_manager(client, query.message, user_id)

            elif action == "add_retry":
                await query.answer()
                from .cmd_drives import build_drives_message
                text, keyboard = await build_drives_message(client, user_id)
                await query.message.edit_text(text, reply_markup=keyboard)

            elif action == "remove":
                idx = int(parts[2])
                drives = await client.db.get_all_drives(user_id)
                email = drives[idx].get("email", f"Drive {idx+1}") if idx < len(drives) else "?"
                await query.answer()
                await query.message.edit_text(
                    f"🗑 Remove **{email}**?\n\n"
                    "_Files stay in Google Drive — only the connection is removed._",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("✅ Yes, Remove", callback_data=f"drive:remove_confirm:{idx}"),
                         InlineKeyboardButton("❌ Cancel", callback_data="drive:list")]
                    ])
                )

            elif action == "remove_confirm":
                idx = int(parts[2])
                drives = await client.db.get_all_drives(user_id)
                email = drives[idx].get("email", f"Drive {idx+1}") if idx < len(drives) else "?"
                await client.db.delete_token(user_id, idx)
                await query.answer(f"✅ {email} removed.", show_alert=False)
                from .cmd_drives import build_drives_message
                text, keyboard = await build_drives_message(client, user_id)
                await query.message.edit_text(text, reply_markup=keyboard)

        else:
            await query.answer("Unknown action.")
