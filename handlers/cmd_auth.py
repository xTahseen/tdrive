
import logging
from urllib.parse import urlparse, parse_qs

from pyrogram import Client, filters
from pyrogram.types import Message

logger = logging.getLogger(__name__)

_pending_flows: dict = {}


def _extract_code_from_input(text: str):
    text = text.strip()
    if text.startswith("http://") or text.startswith("https://"):
        try:
            parsed = urlparse(text)
            params = parse_qs(parsed.query)
            code = params.get("code", [None])[0]
            state = params.get("state", [None])[0]
            return code, state
        except Exception:
            return None, None
    return text, None


def register(app: Client):

    @app.on_message(
        filters.private & filters.text
        & ~filters.command(["start", "logout", "help", "drives", "auth", "storage", "search", "setpassword"]),
        group=0
    )
    async def code_handler(client: Client, message: Message):
        user_id = message.from_user.id

        if not await client.db.is_awaiting_code(user_id):
            return

        raw_input = message.text.strip()
        pending = _pending_flows.get(user_id)

        if not pending:
            await message.reply_text(
                "⚠️ Authorization session expired. Use /drives to start again."
            )
            await client.db.clear_awaiting_code(user_id)
            return

        code, state_from_url = _extract_code_from_input(raw_input)

        if not code:
            await message.reply_text(
                "❌ Could not find an authorization code.\n\n"
                "Please paste the **full URL** from your browser after signing in.\n"
                "It looks like: `http://localhost/?state=...&code=4/0A...`"
            )
            return

        expected_state = pending.get("state")
        if state_from_url and expected_state and state_from_url != expected_state:
            logger.warning(f"State mismatch for user {user_id}")
            await message.reply_text(
                "❌ Security check failed (state mismatch).\n"
                "Use /drives to start fresh."
            )
            _pending_flows.pop(user_id, None)
            await client.db.clear_awaiting_code(user_id)
            return

        wait_msg = await message.reply_text("⏳ Verifying...")

        try:
            flow = pending["flow"]
            mode = pending.get("mode", "auth")
            email, idx = await client.gdrive.exchange_code(user_id, code, flow, None)

            drives = await client.db.get_all_drives(user_id)
            if len(drives) == 1:
                await client.db.set_active_drive(user_id, 0)
            else:
                await client.db.set_active_drive(user_id, idx)

            _pending_flows.pop(user_id, None)
            await client.db.clear_awaiting_code(user_id)

            await wait_msg.edit_text(
                f"✅ **{'Drive added' if mode == 'add_drive' else 'Connected'} — {email}**\n\n"
                "Use /drives to manage your accounts."
            )
        except Exception as e:
            logger.error(f"Code exchange failed for user {user_id}: {e}", exc_info=True)
            await wait_msg.edit_text(
                "❌ **Authorization failed.** Code may be expired.\n"
                "Use /drives to try again."
            )
            await client.db.clear_awaiting_code(user_id)
            _pending_flows.pop(user_id, None)
