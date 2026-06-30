import asyncio
import logging
import os
import tempfile

_active_dl_tasks: dict[int, asyncio.Task] = {}

from pyrogram import Client, filters
from pyrogram.types import (
    CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, Message
)
from gdrive import FOLDER_MIME

logger = logging.getLogger(__name__)

_id_store: dict[int, str] = {0: "root"}
_id_rev: dict[str, int] = {"root": 0}
_id_counter = [1]

_tok_store: dict[int, str] = {}
_tok_rev: dict[str, int] = {}
_tok_counter = [1]


def _fkey(drive_id: str) -> int:
    if drive_id in _id_rev:
        return _id_rev[drive_id]
    k = _id_counter[0]
    _id_counter[0] += 1
    _id_store[k] = drive_id
    _id_rev[drive_id] = k
    return k


def _fid(key: int) -> str:
    return _id_store.get(key, "root")


def _pkey(token: str | None) -> int:
    if not token:
        return 0
    if token in _tok_rev:
        return _tok_rev[token]
    k = _tok_counter[0]
    _tok_counter[0] += 1
    _tok_store[k] = token
    _tok_rev[token] = k
    return k


def _ptok(key: int) -> str | None:
    if key == 0:
        return None
    return _tok_store.get(key)


def _cb(*parts) -> str:
    s = ":".join(str(p) for p in parts)
    assert len(s.encode()) <= 64, f"callback too long ({len(s.encode())}): {s}"
    return s


def _parse_fk(raw: str) -> int:
    try:
        return int(raw)
    except (ValueError, TypeError):
        if raw == "root":
            return 0
        return 0


_pending_input: dict[int, dict] = {}

_page_history: dict[int, dict] = {}


def _fmt(size) -> str:
    try:
        size = int(size)
    except (TypeError, ValueError):
        return ""
    if size < 1024:
        return f"{size}B"
    elif size < 1024 ** 2:
        return f"{size/1024:.1f}KB"
    elif size < 1024 ** 3:
        return f"{size/1024**2:.1f}MB"
    return f"{size/1024**3:.1f}GB"


def _is_folder(item: dict) -> bool:
    return item.get("mimeType") == FOLDER_MIME


def _icon(item: dict) -> str:
    if _is_folder(item):
        return "📁"
    mime = item.get("mimeType", "")
    if "image" in mime:        return "🖼"
    if "video" in mime:        return "🎬"
    if "audio" in mime:        return "🎵"
    if "pdf" in mime:          return "📕"
    if "zip" in mime or "compressed" in mime: return "🗜"
    if "spreadsheet" in mime or "excel" in mime: return "📊"
    if "document" in mime or "word" in mime:  return "📝"
    return "📄"


def _is_downloadable(item: dict) -> bool:
    mime = item.get("mimeType", "")
    if _is_folder(item):
        return False
    from gdrive import GOOGLE_EXPORT_MAP
    if mime in GOOGLE_EXPORT_MAP:
        return True
    if mime.startswith("application/vnd.google-apps."):
        return False
    return True


async def _build_browser(client, user_id: int, folder_id: str, page_token: str = None):
    """
    Render a folder page.

    Pagination works via an in-memory history stack stored in _page_history.
    The stack for (user, folder) is a list of page tokens already visited:
        [None, "tok_p2", "tok_p3"]
    None represents page 1 (no token).  The last element is the current page.
    - Next  → push next_token onto stack, re-render with it.
    - Prev  → pop the stack, re-render with the new last element.
    We only pass action+folder_key in callback data, so there is no token
    embedded in the button — the handler looks up the stack instead.
    """
    items, next_token = await client.gdrive.list_folder(user_id, folder_id, page_token)
    breadcrumb = await client.gdrive.get_breadcrumb(user_id, folder_id)

    crumb_text = " › ".join(name for _, name in breadcrumb)
    parent_id = breadcrumb[-2][0] if len(breadcrumb) >= 2 else "root"

    def_fid, def_fname = await client.db.get_default_folder(user_id)
    def_info = f"\n📌 Default upload folder: **{def_fname}**" if def_fname else ""

    text = f"📂 **{crumb_text}**{def_info}\n"
    if not items:
        text += "\nThis folder is empty."

    fk = _fkey(folder_id)
    pk = _fkey(parent_id)
    rows = []

    if folder_id == "root":
        rows.append([InlineKeyboardButton("➕ New Folder", callback_data=_cb("fm", "new_folder", fk))])
    else:
        folder_actions = [
            InlineKeyboardButton("⚙️ Folder Options", callback_data=_cb("fm", "folder_opts", fk, pk))
        ]
        if def_fid == folder_id:
            folder_actions.append(
                InlineKeyboardButton("❌ Clear Default", callback_data=_cb("fm", "clrdef", fk))
            )
        else:
            folder_actions.append(
                InlineKeyboardButton("📌 Set as Default", callback_data=_cb("fm", "setdef", fk))
            )
        rows.append(folder_actions)

    for item in items:
        icon = _icon(item)
        name = item["name"]
        iid = item["id"]
        ik = _fkey(iid)
        label = f"{icon} {name}"
        sz = _fmt(item.get("size")) if not _is_folder(item) else ""
        if sz:
            label += f" ({sz})"
        if len(label) > 48:
            label = label[:45] + "…"

        if _is_folder(item):
            rows.append([InlineKeyboardButton(label, callback_data=_cb("fm", "browse", ik, 0))])
        else:
            rows.append([InlineKeyboardButton(label, callback_data=_cb("fm", "file", ik, fk))])

    history = _page_history.get(user_id, {}).get(folder_id, [None])
    on_first_page = len(history) <= 1

    nav_row = []
    if not on_first_page:
        nav_row.append(InlineKeyboardButton("« Prev", callback_data=_cb("fm", "pg_prev", fk)))
    if next_token:
        ntk = _pkey(next_token)
        nav_row.append(InlineKeyboardButton("Next »", callback_data=_cb("fm", "pg_next", fk, ntk)))
    if nav_row:
        rows.append(nav_row)

    if folder_id != "root":
        rows.append([InlineKeyboardButton("« Back", callback_data=_cb("fm", "browse", pk, 0))])

    return text, InlineKeyboardMarkup(rows)


def _file_keyboard(fk: int, pfk: int, link: str, downloadable: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("✏️ Rename", callback_data=_cb("fm", "rename", fk, pfk)),
         InlineKeyboardButton("✗ Delete", callback_data=_cb("fm", "del_confirm", fk, pfk))],
        [InlineKeyboardButton("🔗 Link", url=link)],
    ]
    if downloadable:
        rows.append([InlineKeyboardButton("📥 Download", callback_data=_cb("fm", "dl", fk, pfk))])
    rows.append([InlineKeyboardButton("« Back", callback_data=_cb("fm", "browse", pfk, 0))])
    return InlineKeyboardMarkup(rows)


def _folder_opts_keyboard(fk: int, pfk: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ New Folder", callback_data=_cb("fm", "new_folder", fk)),
         InlineKeyboardButton("✏️ Rename",    callback_data=_cb("fm", "rename", fk, pfk))],
        [InlineKeyboardButton("🔗 Link", callback_data=_cb("fm", "share", fk, pfk)),
         InlineKeyboardButton("✗ Delete",     callback_data=_cb("fm", "del_confirm", fk, pfk))],
        [InlineKeyboardButton("« Back", callback_data=_cb("fm", "browse", fk, 0))],
    ])


async def open_file_manager(client: Client, message: Message, user_id: int):
    if not await client.db.is_authenticated(user_id):
        await message.reply_text("❌ Connect Google Drive first. Use /drives.")
        return
    wait = await message.reply_text("📂 Opening File Manager...")
    try:
        if user_id not in _page_history:
            _page_history[user_id] = {}
        _page_history[user_id]["root"] = [None]
        text, keyboard = await _build_browser(client, user_id, "root")
        await wait.edit_text(text, reply_markup=keyboard)
    except Exception as e:
        logger.error(f"FM open error: {e}", exc_info=True)
        await wait.edit_text("❌ Failed to open file manager. Try again.")


def register(app: Client):

    @app.on_callback_query(filters.regex(r"^fm:"))
    async def fm_callback(client: Client, query: CallbackQuery):
        user_id = query.from_user.id
        parts = query.data.split(":")
        action = parts[1] if len(parts) > 1 else ""

        if not await client.db.is_authenticated(user_id):
            await query.answer("❌ Use /drives to connect Google Drive.", show_alert=True)
            return

        if action == "browse":
            fk  = _parse_fk(parts[2]) if len(parts) > 2 else 0
            folder_id = _fid(fk)
            await query.answer()
            if user_id not in _page_history:
                _page_history[user_id] = {}
            _page_history[user_id][folder_id] = [None]
            try:
                text, keyboard = await _build_browser(client, user_id, folder_id, page_token=None)
                await query.message.edit_text(text, reply_markup=keyboard)
            except Exception as e:
                logger.error(f"Browse error: {e}", exc_info=True)
                await query.answer("❌ Failed to load folder.", show_alert=True)

        elif action == "pg_next":
            fk  = _parse_fk(parts[2]) if len(parts) > 2 else 0
            ntk = _parse_fk(parts[3]) if len(parts) > 3 else 0
            folder_id  = _fid(fk)
            next_token = _ptok(ntk)
            await query.answer()
            if user_id not in _page_history:
                _page_history[user_id] = {}
            stack = _page_history[user_id].setdefault(folder_id, [None])
            stack.append(next_token)
            try:
                text, keyboard = await _build_browser(client, user_id, folder_id, page_token=next_token)
                await query.message.edit_text(text, reply_markup=keyboard)
            except Exception as e:
                logger.error(f"pg_next error: {e}", exc_info=True)
                await query.answer("❌ Failed to load next page.", show_alert=True)

        elif action == "pg_prev":
            fk = _parse_fk(parts[2]) if len(parts) > 2 else 0
            folder_id = _fid(fk)
            await query.answer()
            stack = _page_history.get(user_id, {}).get(folder_id, [None])
            if len(stack) > 1:
                stack.pop()
            page_token = stack[-1]
            try:
                text, keyboard = await _build_browser(client, user_id, folder_id, page_token=page_token)
                await query.message.edit_text(text, reply_markup=keyboard)
            except Exception as e:
                logger.error(f"pg_prev error: {e}", exc_info=True)
                await query.answer("❌ Failed to load previous page.", show_alert=True)

        elif action == "file":
            fk  = _parse_fk(parts[2])
            pfk = _parse_fk(parts[3]) if len(parts) > 3 else 0
            file_id = _fid(fk)
            await query.answer()
            try:
                f = await client.gdrive.get_file(user_id, file_id)
                name = f.get("name", "Untitled")
                size = _fmt(f.get("size"))
                mime = f.get("mimeType", "")
                link = f.get("webViewLink", "")
                mod  = f.get("modifiedTime", "")[:10]
                icon = _icon(f)
                downloadable = _is_downloadable(f)
                text = (
                    f"{icon} **{name}**\n\n"
                    f"**Size:** `{size}`\n"
                    f"**Type:** `{mime}`\n"
                    f"**Modified:** `{mod}`"
                )
                await query.message.edit_text(
                    text, reply_markup=_file_keyboard(fk, pfk, link, downloadable)
                )
            except Exception as e:
                logger.error(f"File info error: {e}", exc_info=True)
                await query.answer("❌ Failed to get file info.", show_alert=True)

        elif action == "dl:cancel":
            task = _active_dl_tasks.get(user_id)
            if task and not task.done():
                task.cancel()
                await query.answer("⏹ Cancelling download...", show_alert=False)
            else:
                await query.answer("No active download to cancel.", show_alert=True)

        elif action == "dl":
            fk  = _parse_fk(parts[2])
            pfk = _parse_fk(parts[3]) if len(parts) > 3 else 0
            file_id = _fid(fk)
            await query.answer("📥 Preparing download...")

            cancel_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data=_cb("fm", "dl:cancel", fk, pfk))]
            ])
            status = await query.message.reply_text(
                "📥 Downloading from Google Drive...",
                reply_markup=cancel_kb,
            )

            async def _do_drive_dl():
                try:
                    content, fname, mime = await client.gdrive.download_file(user_id, file_id)
                    suffix = os.path.splitext(fname)[1] or ".bin"
                    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix,
                                                     prefix=f"gdl_{user_id}_") as tmp:
                        tmp.write(content)
                        tmp_path = tmp.name
                    await status.edit_text(f"📤 Sending **{fname}** to you...", reply_markup=None)
                    await client.send_document(
                        chat_id=query.message.chat.id,
                        document=tmp_path,
                        file_name=fname,
                        caption=f"📄 **{fname}**\n_Downloaded from Google Drive_",
                    )
                    await status.delete()
                    os.unlink(tmp_path)
                except asyncio.CancelledError:
                    await status.edit_text("⏹ **Download cancelled.**", reply_markup=None)
                except Exception as e:
                    logger.error(f"Download error: {e}", exc_info=True)
                    await status.edit_text(f"❌ Download failed: `{str(e)[:200]}`", reply_markup=None)
                finally:
                    _active_dl_tasks.pop(user_id, None)

            old = _active_dl_tasks.get(user_id)
            if old and not old.done():
                old.cancel()
            _active_dl_tasks[user_id] = asyncio.ensure_future(_do_drive_dl())

        elif action == "folder_opts":
            fk  = _parse_fk(parts[2])
            pfk = _parse_fk(parts[3]) if len(parts) > 3 else 0
            folder_id = _fid(fk)
            await query.answer()
            try:
                f = await client.gdrive.get_file(user_id, folder_id)
                name = f.get("name", "Folder")
                await query.message.edit_text(
                    f"📁 **{name}**\n\nChoose an action:",
                    reply_markup=_folder_opts_keyboard(fk, pfk)
                )
            except Exception as e:
                logger.error(f"Folder opts error: {e}", exc_info=True)
                await query.answer("❌ Failed.", show_alert=True)

        elif action == "setdef":
            fk = _parse_fk(parts[2]) if len(parts) > 2 else 0
            folder_id = _fid(fk)
            await query.answer()
            try:
                f = await client.gdrive.get_file(user_id, folder_id)
                name = f.get("name", "Folder")
                drive_idx = await client.db.get_active_drive_index(user_id)
                await client.db.set_default_folder(user_id, drive_idx, folder_id, name)
                text, keyboard = await _build_browser(client, user_id, folder_id)
                await query.message.edit_text(
                    f"📌 **{name}** set as default upload folder!\n\n" + text,
                    reply_markup=keyboard
                )
            except Exception as e:
                logger.error(f"Set default folder error: {e}", exc_info=True)
                await query.answer("❌ Failed to set default folder.", show_alert=True)

        elif action == "clrdef":
            fk = _parse_fk(parts[2]) if len(parts) > 2 else 0
            folder_id = _fid(fk)
            await query.answer()
            try:
                drive_idx = await client.db.get_active_drive_index(user_id)
                await client.db.clear_default_folder(user_id, drive_idx)
                text, keyboard = await _build_browser(client, user_id, folder_id)
                await query.message.edit_text(
                    "✅ Default folder cleared. Files will upload to Drive root.\n\n" + text,
                    reply_markup=keyboard
                )
            except Exception as e:
                logger.error(f"Clear default folder error: {e}", exc_info=True)
                await query.answer("❌ Failed.", show_alert=True)

        elif action == "share":
            fk  = _parse_fk(parts[2])
            pfk = _parse_fk(parts[3]) if len(parts) > 3 else 0
            folder_id = _fid(fk)
            await query.answer()
            try:
                link = await client.gdrive.get_folder_link(user_id, folder_id)
                f    = await client.gdrive.get_file(user_id, folder_id)
                name = f.get("name", "Folder")
                await query.message.edit_text(
                    f"🔗 **Link — {name}**\n\n`{link}`\n\n_Anyone with this link can view._",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🌐 Open Link", url=link)],
                        [InlineKeyboardButton("« Back", callback_data=_cb("fm", "folder_opts", fk, pfk))],
                    ])
                )
            except Exception as e:
                logger.error(f"Share error: {e}", exc_info=True)
                await query.answer("❌ Failed to get link.", show_alert=True)

        elif action == "rename":
            fk  = _parse_fk(parts[2])
            pfk = _parse_fk(parts[3]) if len(parts) > 3 else 0
            file_id = _fid(fk)
            await query.answer()
            _pending_input[user_id] = {"action": "rename", "fk": fk, "pfk": pfk}
            try:
                f = await client.gdrive.get_file(user_id, file_id)
                old_name = f.get("name", "")
                await query.message.edit_text(
                    f"✏️ **Rename**\n\nCurrent name: `{old_name}`\n\nSend the new name:",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("❌ Cancel", callback_data=_cb("fm", "cancel_input", pfk))]
                    ])
                )
            except Exception as e:
                logger.error(f"Rename prompt error: {e}", exc_info=True)
                await query.answer("❌ Failed.", show_alert=True)

        elif action == "del_confirm":
            fk  = _parse_fk(parts[2])
            pfk = _parse_fk(parts[3]) if len(parts) > 3 else 0
            file_id = _fid(fk)
            await query.answer()
            try:
                f    = await client.gdrive.get_file(user_id, file_id)
                name = f.get("name", "this item")
                warn = "\n⚠️ _This deletes the folder and ALL its contents._" if _is_folder(f) else ""
                await query.message.edit_text(
                    f"✗ **Delete** — are you sure you want to delete **{name}**?{warn}",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("✅ Yes, Delete", callback_data=_cb("fm", "del_do", fk, pfk)),
                         InlineKeyboardButton("❌ Cancel",      callback_data=_cb("fm", "browse", pfk, 0))],
                    ])
                )
            except Exception as e:
                logger.error(f"Delete confirm error: {e}", exc_info=True)
                await query.answer("❌ Failed.", show_alert=True)

        elif action == "del_do":
            fk  = _parse_fk(parts[2])
            pfk = _parse_fk(parts[3]) if len(parts) > 3 else 0
            file_id   = _fid(fk)
            parent_id = _fid(pfk)
            await query.answer("✗ Deleting...")
            try:
                await client.gdrive.delete_file(user_id, file_id)
                text, keyboard = await _build_browser(client, user_id, parent_id)
                await query.message.edit_text("✅ Deleted!\n\n" + text, reply_markup=keyboard)
            except Exception as e:
                logger.error(f"Delete error: {e}", exc_info=True)
                await query.answer("❌ Delete failed.", show_alert=True)

        elif action == "new_folder":
            fk = _parse_fk(parts[2]) if len(parts) > 2 else 0
            await query.answer()
            _pending_input[user_id] = {"action": "new_folder", "fk": fk, "pfk": fk}
            await query.message.edit_text(
                "➕ **New Folder**\n\nSend the name for your new folder:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Cancel", callback_data=_cb("fm", "cancel_input", fk))]
                ])
            )

        elif action == "cancel_input":
            fk = _parse_fk(parts[2]) if len(parts) > 2 else 0
            folder_id = _fid(fk)
            _pending_input.pop(user_id, None)
            await query.answer("Cancelled.")
            try:
                text, keyboard = await _build_browser(client, user_id, folder_id)
                await query.message.edit_text(text, reply_markup=keyboard)
            except Exception as e:
                logger.error(f"Cancel input error: {e}", exc_info=True)

        elif action == "noop":
            await query.answer()

    @app.on_message(
        filters.private & filters.text
        & ~filters.command(["start", "logout", "help", "drives", "auth", "storage", "search", "webui"]),
        group=1
    )
    async def fm_text_input(client: Client, message: Message):
        user_id = message.from_user.id

        pending = _pending_input.get(user_id)
        if not pending:
            return

        action    = pending["action"]
        fk        = pending["fk"]
        pfk       = pending["pfk"]
        file_id   = _fid(fk)
        parent_id = _fid(pfk)
        new_text  = message.text.strip()
        _pending_input.pop(user_id, None)

        if action == "rename":
            try:
                result = await client.gdrive.rename_file(user_id, file_id, new_text)
                wait = await message.reply_text(f"✅ Renamed to **{result['name']}**!\n\nLoading folder...")
                text, keyboard = await _build_browser(client, user_id, parent_id)
                await wait.edit_text(text, reply_markup=keyboard)
            except Exception as e:
                logger.error(f"Rename error: {e}", exc_info=True)
                await message.reply_text("❌ Rename failed. Please try again.")

        elif action == "new_folder":
            try:
                result = await client.gdrive.create_folder(user_id, new_text, parent_id)
                wait = await message.reply_text(f"✅ Folder **{result['name']}** created!\n\nLoading folder...")
                text, keyboard = await _build_browser(client, user_id, parent_id)
                await wait.edit_text(text, reply_markup=keyboard)
            except Exception as e:
                logger.error(f"New folder error: {e}", exc_info=True)
                await message.reply_text("❌ Could not create folder. Please try again.")
