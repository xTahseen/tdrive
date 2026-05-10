import logging
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)


def _fmt_bytes(n: int) -> str:
    if n <= 0:
        return "0 B"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            if unit in ("B", "KB"):
                return f"{int(n)} {unit}"
            return f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} PB"


def _bar(pct: float, length: int = 12) -> str:
    filled = max(0, min(length, round(pct / 100 * length)))
    return "█" * filled + "░" * (length - filled)


async def _fetch_about(client, user_id: int, drive_index: int) -> dict | None:
    try:
        creds = await client.gdrive._get_credentials(user_id, drive_index)
        if not creds:
            return None
        from gdrive import _run_sync
        def _call():
            service = build("drive", "v3", credentials=creds, cache_discovery=False)
            return service.about().get(fields="user(displayName,emailAddress),storageQuota").execute()
        return await _run_sync(_call)
    except Exception as e:
        logger.error(f"Storage fetch error user={user_id} drive={drive_index}: {e}")
        return None


async def _build_storage_text(client, user_id: int) -> str:
    drives = await client.db.get_all_drives(user_id)
    if not drives:
        return "❌ No connected drives. Use /drives to add one."

    active_idx = await client.db.get_active_drive_index(user_id)
    lines = ["☁️ **Google Drive Storage**\n"]

    # Fetch all drive storage info concurrently
    import asyncio as _asyncio
    about_results = await _asyncio.gather(*[_fetch_about(client, user_id, i) for i in range(len(drives))])

    for i, d in enumerate(drives):
        marker = "⭐" if i == active_idx else f"{i + 1}."
        fallback_email = d.get("email", f"Drive {i + 1}")

        about = about_results[i]

        if about is None:
            lines.append(f"{marker} **{fallback_email}**\n⚠️ Could not fetch storage info.\n\n")
            continue

        user_info = about.get("user", {})
        display   = user_info.get("displayName") or fallback_email
        email     = user_info.get("emailAddress") or fallback_email
        quota     = about.get("storageQuota", {})

        limit_raw = int(quota.get("limit") or 0)
        usage_raw = int(quota.get("usage") or 0)
        trash_raw = int(quota.get("usageInDriveTrash") or 0)
        free_raw  = max(0, limit_raw - usage_raw)

        used_pct  = (usage_raw / limit_raw * 100) if limit_raw > 0 else 0.0
        free_pct  = 100.0 - used_pct

        if limit_raw > 0:
            bar_line = f"   `{_bar(used_pct)}` {used_pct:.1f}% used\n"
            total_line = f"   💾 Total:  **{_fmt_bytes(limit_raw)}**"
        else:
            bar_line = ""
            total_line = "   💾 Total:  **Unlimited**"

        lines.append(
            f"{marker} **{display}**\n"
            f"   📧 `{email}`\n"
            f"{total_line}\n"
            f"   📤 Used:   **{_fmt_bytes(usage_raw)}** ({used_pct:.1f}%)\n"
            f"   📭 Free:   **{_fmt_bytes(free_raw)}** ({free_pct:.1f}%)\n"
            f"   🗑 Trash:  **{_fmt_bytes(trash_raw)}**\n"
            f"{bar_line}"
            "\n"
        )

    return "".join(lines).rstrip()


def _keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Refresh", callback_data="storage:refresh"),
        InlineKeyboardButton("✖ Close",    callback_data="storage:close"),
    ]])


def register(app: Client):

    @app.on_message(filters.command("storage") & filters.private)
    async def storage_handler(client: Client, message: Message):
        user_id = message.from_user.id
        if not await client.db.is_authenticated(user_id):
            await message.reply_text("❌ Not connected. Use /drives to add a Google Drive account.")
            return
        wait = await message.reply_text("⏳ Fetching storage info...")
        try:
            text = await _build_storage_text(client, user_id)
            await wait.edit_text(text, reply_markup=_keyboard())
        except Exception as e:
            logger.error(f"Storage command error: {e}", exc_info=True)
            await wait.edit_text("❌ Failed to fetch storage info. Please try again.")

    @app.on_callback_query(filters.regex(r"^storage:"))
    async def storage_callback(client: Client, query: CallbackQuery):
        user_id = query.from_user.id
        action  = query.data.split(":")[1]

        if action == "refresh":
            await query.answer("🔄 Refreshing...")
            try:
                if not await client.db.is_authenticated(user_id):
                    await query.message.edit_text("❌ No drives connected.", reply_markup=None)
                    return
                text = await _build_storage_text(client, user_id)
                await query.message.edit_text(text, reply_markup=_keyboard())
            except Exception as e:
                logger.error(f"Storage refresh error: {e}", exc_info=True)
                await query.answer("❌ Refresh failed.", show_alert=True)

        elif action == "close":
            await query.answer()
            try:
                await query.message.delete()
            except Exception:
                pass
