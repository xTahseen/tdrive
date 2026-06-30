import asyncio
import logging
from pyrogram import Client, filters
from pyrogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)


def _fmt(size) -> str:
    try:
        size = float(size)
    except (TypeError, ValueError):
        return ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} PB"


def _icon(mime: str) -> str:
    if "folder" in mime:          return "📁"
    if "image" in mime:           return "🖼"
    if "video" in mime:           return "🎬"
    if "audio" in mime:           return "🎵"
    if "pdf" in mime:             return "📕"
    if "zip" in mime or "compressed" in mime: return "🗜"
    if "spreadsheet" in mime or "excel" in mime: return "📊"
    if "document" in mime or "word" in mime:  return "📝"
    return "📄"


async def _search_files(client, user_id: int, query: str, drive_index: int) -> list:
    creds = await client.gdrive._get_credentials(user_id, drive_index)
    if not creds:
        return []
    from gdrive import _run_sync
    safe_query = query.replace("'", "\\'")
    def _call():
        service = build("drive", "v3", credentials=creds, cache_discovery=False)
        result = service.files().list(
            q=f"name contains '{safe_query}' and trashed = false",
            pageSize=15,
            fields="files(id, name, mimeType, size, webViewLink, modifiedTime, parents)",
            orderBy="modifiedTime desc",
        ).execute()
        return result.get("files", [])
    return await _run_sync(_call)



async def _do_search(client: Client, user_id: int, query: str, status_msg: Message):
    """Run search across all drives and edit status_msg with results."""
    drives      = await client.db.get_all_drives(user_id)
    active_idx  = await client.db.get_active_drive_index(user_id)

    if not drives:
        await status_msg.edit_text("❌ No drives connected. Use /drives.")
        return

    all_results: list[tuple[int, dict]] = []

    async def _search_one(i):
        try:
            return i, await _search_files(client, user_id, query, i)
        except Exception as e:
            logger.error(f"Search error drive {i}: {e}")
            return i, []

    results = await asyncio.gather(*[_search_one(i) for i in range(len(drives))])
    for i, files in results:
        for f in files:
            all_results.append((i, f))

    if not all_results:
        await status_msg.edit_text(
            f"🔍 No results for **{query}**\n\nTry a different keyword.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✖ Close", callback_data="search:close")
            ]])
        )
        return

    drive_emails = [d.get("email", f"Drive {i+1}") for i, d in enumerate(drives)]

    header = f"🔍 **Search results for:** `{query}`\n_{len(all_results)} file(s) found_\n\n"
    rows   = []

    for drive_idx, f in all_results:
        fid   = f["id"]
        fname = f.get("name", "Untitled")
        mime  = f.get("mimeType", "")
        size  = _fmt(f.get("size")) if "folder" not in mime else ""
        mod   = f.get("modifiedTime", "")[:10]
        icon  = _icon(mime)
        email = drive_emails[drive_idx]
        mark  = "⭐ " if drive_idx == active_idx else ""

        label = f"{icon} {fname}"
        if size:
            label += f" ({size})"
        if len(label) > 50:
            label = label[:47] + "…"

        rows.append([
            InlineKeyboardButton(
                label,
                callback_data=f"sr:info:{drive_idx}:{fid}"
            ),
        ])

    rows = rows[:15]
    rows.append([
        InlineKeyboardButton("✖ Close", callback_data="search:close")
    ])

    await status_msg.edit_text(
        header,
        reply_markup=InlineKeyboardMarkup(rows),
    )


def register(app: Client):

    @app.on_message(filters.command("search") & filters.private)
    async def search_cmd(client: Client, message: Message):
        user_id = message.from_user.id

        if not await client.db.is_authenticated(user_id):
            await message.reply_text("❌ Not connected. Use /drives first.")
            return

        parts = message.text.split(None, 1)
        if len(parts) < 2 or not parts[1].strip():
            await message.reply_text(
                "🔍 **Search Google Drive**\n\n"
                "Send your search query:\n"
                "`/search filename`"
            )
            return

        query = parts[1].strip()
        status = await message.reply_text(f"🔍 Searching for **{query}**...")
        await _do_search(client, user_id, query, status)

    @app.on_callback_query(filters.regex(r"^sr:"))
    async def search_callback(client: Client, query: CallbackQuery):
        user_id = query.from_user.id
        parts   = query.data.split(":")
        action  = parts[1] if len(parts) > 1 else ""

        if not await client.db.is_authenticated(user_id):
            await query.answer("❌ Not connected.", show_alert=True)
            return

        if action == "info":
            drive_idx = int(parts[2])
            file_id   = parts[3]

            await query.answer("📄 Loading file info...")

            await client.db.set_active_drive(user_id, drive_idx)

            from handlers.file_manager import _fkey, _fid, _cb, _icon as fm_icon, _fmt as fm_fmt, _is_downloadable, _file_keyboard
            from gdrive import FOLDER_MIME

            try:
                f = await client.gdrive.get_file(user_id, file_id, drive_index=drive_idx)

                fk   = _fkey(file_id)
                parents = f.get("parents", [])
                parent_id = parents[0] if parents else "root"
                pfk  = _fkey(parent_id)

                name = f.get("name", "Untitled")
                size = fm_fmt(f.get("size"))
                mime = f.get("mimeType", "")
                link = f.get("webViewLink", "")
                mod  = f.get("modifiedTime", "")[:10]
                icon = fm_icon(f)
                downloadable = _is_downloadable(f)

                text = (
                    f"{icon} **{name}**\n\n"
                    f"**Size:** `{size}`\n"
                    f"**Type:** `{mime}`\n"
                    f"**Modified:** `{mod}`"
                )

                await query.message.edit_text(
                    text,
                    reply_markup=_file_keyboard(fk, pfk, link, downloadable),
                )

            except Exception as e:
                logger.error(f"Search file open error: {e}", exc_info=True)
                await query.answer("❌ Could not open file.", show_alert=True)

    @app.on_callback_query(filters.regex(r"^search:close$"))
    async def search_close(client: Client, query: CallbackQuery):
        await query.answer()
        try:
            await query.message.delete()
        except Exception:
            pass
