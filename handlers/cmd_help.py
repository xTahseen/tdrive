from pyrogram import Client, filters
from pyrogram.types import Message

from config import Config

HELP_TEXT_BASE = """
🤖 **Google Drive Bot — Help**

**📤 Upload Files**
Send any file — it uploads to your active Drive (or default folder if set).

**☁️ /drives** — Everything drive-related:
  • Tap a drive name to set it as default ⭐
  • **👁 View** — browse that drive's files
  • **🗑 Delete** — remove that connection
  • **➕ Connect Google Drive** — opens Google sign-in; paste the redirect URL back here

**📂 File Manager** (opened via 👁 View in /drives):
  • Tap folders to navigate
  • Tap files for: ✏️ Rename · 🗑 Delete · 🔗 Link · 📥 Download
  • **📌 Set as Default** / **❌ Clear Default** — controls upload destination

**Other commands**
  • /storage — view storage usage for all connected drives
  • /search `query` — search files across all drives
  • /logout — disconnect the active drive
  • /help — this message
"""

HELP_TEXT_WEBUI = """
**🌐 Web Dashboard**
  • /setpassword `password` — set your WebUI login password
  • Then open: {webui_url}
  • Log in with your password to manage files in your browser
"""


def register(app: Client):
    @app.on_message(filters.command("help") & filters.private)
    async def help_handler(client: Client, message: Message):
        text = HELP_TEXT_BASE
        if Config.WEBUI_ENABLED:
            text += HELP_TEXT_WEBUI.format(webui_url=Config.WEBUI_BASE_URL)
        await message.reply_text(text, disable_web_page_preview=True)
