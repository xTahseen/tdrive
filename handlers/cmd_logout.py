from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton


def register(app: Client):

    @app.on_message(filters.command("logout") & filters.private)
    async def logout_handler(client: Client, message: Message):
        user_id = message.from_user.id

        if not await client.db.is_authenticated(user_id):
            await message.reply_text("ℹ️ You are not connected to any Google account.")
            return

        await message.reply_text(
            "⚠️ **Are you sure you want to disconnect Google Drive?**\n\n"
            "Your stored access token will be deleted.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Yes, Logout", callback_data="logout_confirm"),
                    InlineKeyboardButton("❌ Cancel", callback_data="cancel_action"),
                ]
            ]),
        )
