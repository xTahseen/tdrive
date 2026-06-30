import os
import asyncio
import logging
import time
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.errors import UserIsBlocked, InputUserDeactivated
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from config import Config
from logger import log_file_upload
from tasker import get_queue_manager, UploadJob
from handlers.on_member_events import handle_send_error

logger = logging.getLogger(__name__)

Path(Config.TEMP_DIR).mkdir(parents=True, exist_ok=True)

_file_filter = (
    filters.document
    | filters.video
    | filters.audio
    | filters.photo
    | filters.voice
    | filters.video_note
    | filters.animation
    | filters.sticker
)


def _get_file_info(message: Message) -> tuple[str, int]:
    if message.document:
        return message.document.file_name or "document", message.document.file_size or 0
    elif message.video:
        name = message.video.file_name or f"video_{message.video.file_id[:8]}.mp4"
        return name, message.video.file_size or 0
    elif message.audio:
        name = message.audio.file_name or f"audio_{message.audio.file_id[:8]}.mp3"
        return name, message.audio.file_size or 0
    elif message.photo:
        return f"photo_{message.photo.file_id[:8]}.jpg", message.photo.file_size or 0
    elif message.voice:
        return f"voice_{message.voice.file_id[:8]}.ogg", message.voice.file_size or 0
    elif message.video_note:
        return f"videonote_{message.video_note.file_id[:8]}.mp4", message.video_note.file_size or 0
    elif message.animation:
        return f"animation_{message.animation.file_id[:8]}.mp4", message.animation.file_size or 0
    elif message.sticker:
        ext = ".webp" if not message.sticker.is_animated else ".tgs"
        return f"sticker_{message.sticker.file_id[:8]}{ext}", message.sticker.file_size or 0
    return "file", 0


def _cancel_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data=f"upload:cancel:{user_id}")]
    ])


def register(app: Client):

    @app.on_callback_query(filters.regex(r"^upload:cancel:"))
    async def cancel_upload(client: Client, query):
        uid = int(query.data.split(":")[2])
        if query.from_user.id != uid:
            await query.answer("This isn't your transfer.", show_alert=True)
            return
        mgr = get_queue_manager()
        cancelled = await mgr.cancel_active(uid)
        if cancelled:
            await query.answer("Cancelling…", show_alert=False)
        else:
            await query.answer("No active transfer to cancel.", show_alert=True)

    @app.on_message(_file_filter & filters.private)
    async def file_handler(client: Client, message: Message):
        user_id    = message.from_user.id
        username   = message.from_user.username
        first_name = message.from_user.first_name

        if not await client.db.is_authenticated(user_id):
            await message.reply_text(
                "❌ **Not connected!**\n\n"
                "Please connect your Google Drive first with /drives.",
                quote=True,
            )
            return

        file_name, file_size = _get_file_info(message)

        if file_size > Config.MAX_FILE_SIZE:
            size_gb = Config.MAX_FILE_SIZE / (1024 ** 3)
            await message.reply_text(
                f"❌ File too large. Maximum allowed size is **{size_gb:.1f} GB**.",
                quote=True,
            )
            return

        mgr     = get_queue_manager()
        pending = mgr.queue_size(user_id)
        active  = mgr.is_active(user_id)

        if active or pending > 0:
            position = pending + 1
            queue_msg = await message.reply_text(
                f"⏳ **Queued** — position **#{position}**\n"
                f"`{file_name}` will start after your current upload finishes.",
                quote=True,
            )
            status_msg_holder = [queue_msg]
        else:
            status_msg_holder = [None]

        job = UploadJob(
            user_id   = user_id,
            file_name = file_name,
            file_size = file_size,
            coro_fn   = None,
        )
        job.coro_fn = lambda: _do_upload(
            client, message, user_id, username, first_name,
            file_name, file_size, status_msg_holder, job.job_id,
        )

        async def _position_cb(new_pos: int, _smh=status_msg_holder, _fn=file_name):
            msg = _smh[0]
            if msg is not None:
                try:
                    await msg.edit_text(
                        f"⏳ **Queued** — position **#{new_pos}**\n"
                        f"`{_fn}` will start after your current upload finishes."
                    )
                except Exception:
                    pass

        job._position_cb = _position_cb

        ok, reason = await mgr.enqueue(job)
        if not ok:
            if status_msg_holder[0]:
                await status_msg_holder[0].edit_text(reason)
            else:
                await message.reply_text(reason, quote=True)


async def _do_upload(
    client: Client,
    message: Message,
    user_id: int,
    username: str | None,
    first_name: str | None,
    file_name: str,
    file_size: int,
    status_msg_holder: list,
    job_id: int,
):
    drives     = await client.db.get_all_drives(user_id)
    active_idx = await client.db.get_active_drive_index(user_id)

    if active_idx >= len(drives):
        active_idx = 0

    active_email = drives[active_idx].get("email", "Drive") if drives else "Drive"
    folder_id, folder_name = await client.db.get_default_folder(user_id)

    if not folder_id:
        folder_id = None
        folder_name = None

    folder_display = f"`{folder_name}`" if folder_name else "Root"
    drive_display  = f"`{active_email}`"

    if status_msg_holder[0] is not None:
        status_msg = status_msg_holder[0]
        try:
            await status_msg.edit_text(
                "**Processing…**",
                reply_markup=_cancel_keyboard(user_id),
            )
        except Exception:
            pass
    else:
        status_msg = await message.reply_text(
            "**Processing…**",
            reply_markup=_cancel_keyboard(user_id),
            quote=True,
        )
        status_msg_holder[0] = status_msg

    file_path = os.path.join(Config.TEMP_DIR, f"{user_id}_{job_id}_{file_name}")

    try:
        last_update = [time.time()]
        dl_start    = [time.time()]

        async def download_progress(current, total):
            now = time.time()
            if now - last_update[0] > 2:
                elapsed = now - dl_start[0]
                speed   = current / elapsed if elapsed > 0 else 0
                eta     = int((total - current) / speed) if speed > 0 else 0
                last_update[0] = now
                pct = int(current / total * 100) if total else 0
                bar = _progress_bar(pct)
                try:
                    await status_msg.edit_text(
                        f"**Downloading: {pct}%**\n"
                        f"{bar}\n"
                        f"`{_fmt_size(current)}` / `{_fmt_size(total)}`\n"
                        f"Speed: `{_fmt_speed(speed)}`\n"
                        f"ETA: `{_fmt_eta(eta)}`",
                        reply_markup=_cancel_keyboard(user_id),
                    )
                except Exception:
                    pass

        await message.download(file_name=file_path, progress=download_progress)

    except asyncio.CancelledError:
        await status_msg.edit_text("⏹ **Download cancelled.**", reply_markup=None)
        _cleanup(file_path)
        await log_file_upload(
            client.db.db, user_id, username, first_name,
            file_name, file_size, active_email, folder_name,
            success=False, error="cancelled",
        )
        raise
    except Exception as e:
        logger.error(f"Download error for user {user_id}: {e}")
        if not await handle_send_error(e, client, user_id, username, first_name):
            await status_msg.edit_text(
                "❌ Failed to download the file from Telegram.", reply_markup=None
            )
        _cleanup(file_path)
        await log_file_upload(
            client.db.db, user_id, username, first_name,
            file_name, file_size, active_email, folder_name,
            success=False, error=f"download_error: {str(e)[:200]}",
        )
        return

    try:
        upload_size    = os.path.getsize(file_path)
        last_update[0] = time.time()
        ul_start       = time.time()

        async def upload_progress(pct: int):
            now = time.time()
            if now - last_update[0] > 2:
                elapsed    = now - ul_start
                bytes_done = int(pct / 100 * upload_size)
                speed      = bytes_done / elapsed if elapsed > 0 else 0
                remaining  = upload_size - bytes_done
                eta        = int(remaining / speed) if speed > 0 else 0
                last_update[0] = now
                bar = _progress_bar(pct)
                try:
                    await status_msg.edit_text(
                        f"**Uploading: {pct}%**\n"
                        f"{bar}\n"
                        f"`{_fmt_size(bytes_done)}` / `{_fmt_size(upload_size)}`\n"
                        f"Speed: `{_fmt_speed(speed)}`\n"
                        f"ETA: `{_fmt_eta(eta)}`\n"
                        f"{drive_display}  →  {folder_display}",
                        reply_markup=_cancel_keyboard(user_id),
                    )
                except Exception:
                    pass

        await status_msg.edit_text(
            f"**Uploading…**\n`{file_name}`\n"
            f"{drive_display}  →  {folder_display}",
            reply_markup=_cancel_keyboard(user_id),
        )

        result = await client.gdrive.upload_file(
            user_id=user_id,
            file_path=file_path,
            file_name=file_name,
            folder_id=folder_id,
            progress_callback=upload_progress,
            drive_index=active_idx,
        )

        link       = result.get("webViewLink", "N/A")
        drive_name = result.get("name", file_name)
        drive_size = _fmt_size(int(result.get("size", upload_size)))
        file_id    = result.get("id", "")

        await log_file_upload(
            client.db.db, user_id, username, first_name,
            file_name, upload_size, active_email, folder_name,
            success=True,
        )

        from handlers.file_manager import _fkey as _fm_fkey, _cb as _fm_cb
        fk  = _fm_fkey(file_id)
        pfk = _fm_fkey(folder_id) if folder_id else 0

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✏️ Rename", callback_data=_fm_cb("fm", "rename", fk, pfk)),
                InlineKeyboardButton("🗑 Delete",  callback_data=_fm_cb("fm", "del_confirm", fk, pfk)),
            ],
            [
                InlineKeyboardButton("📥 Download", callback_data=_fm_cb("fm", "dl", fk, pfk)),
                InlineKeyboardButton("🔗 View in Drive", url=link),
            ],
        ])

        await status_msg.edit_text(
            f"**Upload Successful! 🎉**\n\n"
            f"**Title:** `{drive_name}`\n"
            f"**Size:** `{drive_size}`\n"
            f"**Account:** `{active_email}`\n"
            f"**Folder:** `{folder_name or 'Drive root'}`",
            reply_markup=keyboard,
        )

    except asyncio.CancelledError:
        await status_msg.edit_text("⏹ **Upload cancelled.**", reply_markup=None)
        _cleanup(file_path)
        await log_file_upload(
            client.db.db, user_id, username, first_name,
            file_name, file_size, active_email, folder_name,
            success=False, error="cancelled",
        )
        raise
    except PermissionError:
        await status_msg.edit_text(
            "❌ **Drive access denied.**\n\n"
            "Your session may have expired. Please /logout and reconnect via /drives.",
            reply_markup=None,
        )
        await log_file_upload(
            client.db.db, user_id, username, first_name,
            file_name, file_size, active_email, folder_name,
            success=False, error="permission_denied",
        )
    except Exception as e:
        logger.error(f"Upload error for user {user_id}: {e}")
        if not await handle_send_error(e, client, user_id, username, first_name):
            await status_msg.edit_text(
                f"❌ **Upload failed.**\n\n`{str(e)[:200]}`\n\nPlease try again.",
                reply_markup=None,
            )
        await log_file_upload(
            client.db.db, user_id, username, first_name,
            file_name, file_size, active_email, folder_name,
            success=False, error=str(e)[:300],
        )
    finally:
        _cleanup(file_path)



def _fmt_size(size: int) -> str:
    if size < 1024:        return f"{size} B"
    elif size < 1024**2:   return f"{size/1024:.1f} KB"
    elif size < 1024**3:   return f"{size/1024**2:.1f} MB"
    return f"{size/1024**3:.2f} GB"

def _fmt_speed(bps: float) -> str:
    if bps < 1024:         return f"{bps:.1f} B/s"
    elif bps < 1024**2:    return f"{bps/1024:.1f} KB/s"
    elif bps < 1024**3:    return f"{bps/1024**2:.2f} MB/s"
    return f"{bps/1024**3:.2f} GB/s"

def _fmt_eta(seconds: int) -> str:
    if seconds < 60:       return f"{seconds}s"
    elif seconds < 3600:   return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m"

def _progress_bar(pct: int, length: int = 10) -> str:
    filled = int(pct / 100 * length)
    return "▰" * filled + "▱" * (length - filled)

def _cleanup(path: str):
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass
