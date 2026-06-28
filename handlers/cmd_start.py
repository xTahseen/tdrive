from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from logger import log_user_join


def register(app: Client):
    @app.on_message(filters.command("start") & filters.private)
    async def start_handler(client: Client, message: Message):
        user = message.from_user

        # ensure_user returns the pymongo result; upserted_id is set only on
        # a real insert (i.e. the very first /start from this user).
        result = await client.db.db.users.update_one(
            {"user_id": user.id},
            {"$setOnInsert": {
                "user_id":    user.id,
                "created_at": __import__("datetime").datetime.utcnow(),
            }},
            upsert=True,
        )
        is_new_user = result.upserted_id is not None

        if is_new_user:
            await log_user_join(
                client.db.db,
                user_id    = user.id,
                username   = user.username,
                first_name = user.first_name,
            )

        is_auth = await client.db.is_authenticated(user.id)

        if not is_auth:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("☁️ Connect Google Drive", callback_data="auth_start")],
                [InlineKeyboardButton("❓ Help", callback_data="show_help")],
            ])
            body = "Use /drives to connect your Google Drive account."
        else:
            drives       = await client.db.get_all_drives(user.id)
            active_idx   = await client.db.get_active_drive_index(user.id)
            active_email = drives[active_idx].get("email", "Drive") if drives else "Drive"
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("☁️ Drives", callback_data="drive:list"),
                 InlineKeyboardButton("📂 Files",  callback_data="my_files")],
                [InlineKeyboardButton("❓ Help", callback_data="show_help")],
            ])
            body = f"**Default:** {active_email} \n**Total:** {len(drives)}"

        await message.reply_text(
            f"👋 **Hello, {user.first_name}!**\n\n"
            f"{body}\n\n"
            "I upload Telegram files directly to Google Drive.",
            reply_markup=keyboard,
        )
