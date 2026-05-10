from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton


def register(app: Client):
    @app.on_message(filters.command("start") & filters.private)
    async def start_handler(client: Client, message: Message):
        user = message.from_user

        # Ensure user exists in DB (even before connecting a Drive)
        await client.db.ensure_user(user.id)

        is_auth = await client.db.is_authenticated(user.id)

        if not is_auth:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("☁️ Connect Google Drive", callback_data="auth_start")],
                [InlineKeyboardButton("❓ Help", callback_data="show_help")],
            ])
            body = "Use /drives to connect your Google Drive account."
        else:
            drives = await client.db.get_all_drives(user.id)
            active_idx = await client.db.get_active_drive_index(user.id)
            active_email = drives[active_idx].get("email", "Drive") if drives else "Drive"
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("☁️ Drives", callback_data="drive:list"),
                 InlineKeyboardButton("📂 Files", callback_data="my_files")],
                [InlineKeyboardButton("❓ Help", callback_data="show_help")],
            ])
            body = f"**Default:** {active_email} \n**Total:** {len(drives)}"

        await message.reply_text(
            f"👋 **Hello, {user.first_name}!**\n\n"
            f"{body}\n\n"
            "I upload Telegram files directly to Google Drive.",
            reply_markup=keyboard,
        )
