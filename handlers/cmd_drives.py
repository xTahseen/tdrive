import logging
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

logger = logging.getLogger(__name__)


async def build_drives_message(client, user_id: int):
    drives = await client.db.get_all_drives(user_id)
    active = await client.db.get_active_drive_index(user_id)

    rows = []
    for i, d in enumerate(drives):
        email = d.get("email", f"Drive {i + 1}")
        label = f"✓ {email}" if i == active else email
        rows.append([
            InlineKeyboardButton(label, callback_data=f"drive:switch:{i}"),
            InlineKeyboardButton("View",   callback_data=f"drive:view:{i}"),
            InlineKeyboardButton("Delete", callback_data=f"drive:remove:{i}"),
        ])

    try:
        auth_url, flow, state = client.gdrive.get_auth_url_for_web()
        await client.db.save_oauth_state(user_id, state)
        rows.append([InlineKeyboardButton("☁️ Connect to Gdrive", url=auth_url)])
        footer = "Click **Connect to Gdrive** to link a Google account via the WebUI."
    except Exception as e:
        logger.error(f"Failed to generate auth URL: {e}")
        rows.append([InlineKeyboardButton("☁️ Connect to Gdrive (retry)", callback_data="drive:add_retry")])
        footer = "⚠️ Could not generate sign-in link. Tap the button to retry."

    if not drives:
        text = "☁️ **Google Drive Accounts**\n\nNo accounts connected yet.\n\n" + footer
    else:
        text = "☁️ **Google Drive Accounts**\n\n" + footer

    return text, InlineKeyboardMarkup(rows)


def register(app: Client):

    @app.on_message(filters.command("drives") & filters.private)
    async def drives_handler(client: Client, message: Message):
        text, keyboard = await build_drives_message(client, message.from_user.id)
        await message.reply_text(text, reply_markup=keyboard)
