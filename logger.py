"""
logger.py — structured event logging for the bot.

Events are written to:
  • MongoDB  (db.activity_logs)      — for /stats queries
  • Python logging                   — visible in stdout / log files
  • Telegram log group               — if Config.LOG_GROUP_ID is set

Log group message format examples
----------------------------------
👤 NEW USER
Name: John Doe (@johndoe)
ID: 123456789

🚫 USER BLOCKED BOT
Name: Jane Smith (@janesmith)
ID: 987654321

✅ USER UNBLOCKED BOT
Name: Jane Smith (@janesmith)
ID: 987654321

📤 FILE UPLOADED
User: John Doe (@johndoe) [123456789]
File: video.mp4 (45.2 MB)
Drive: john@gmail.com → MyFolder
Status: ✅ Success

📤 FILE UPLOADED
User: John Doe (@johndoe) [123456789]
File: big.zip (1.2 GB)
Drive: john@gmail.com → Root
Status: ❌ Failed — permission_denied
"""

import logging
from datetime import datetime

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING

logger = logging.getLogger(__name__)

# Pyrogram client reference — injected at startup via set_bot_client()
_bot_client = None


def set_bot_client(client):
    """Call this once at startup with the Pyrogram Client instance."""
    global _bot_client
    _bot_client = client


# ── index bootstrap ────────────────────────────────────────────────────────────

async def ensure_indexes(db: AsyncIOMotorDatabase):
    col = db.activity_logs
    await col.create_index([("event",   ASCENDING)])
    await col.create_index([("user_id", ASCENDING)])
    await col.create_index([("ts",      DESCENDING)])
    logger.info("activity_logs indexes ensured.")


# ── internal helpers ───────────────────────────────────────────────────────────

def _base(event: str, user_id: int, username: str | None, first_name: str | None) -> dict:
    return {
        "event":      event,
        "user_id":    user_id,
        "username":   username,
        "first_name": first_name,
        "ts":         datetime.utcnow(),
    }


def _user_line(first_name: str | None, username: str | None, user_id: int) -> str:
    name  = first_name or "Unknown"
    uname = f" (@{username})" if username else ""
    return f"{name}{uname} [`{user_id}`]"


def _fmt_size(size: int) -> str:
    if size < 1024:        return f"{size} B"
    elif size < 1024**2:   return f"{size/1024:.1f} KB"
    elif size < 1024**3:   return f"{size/1024**2:.1f} MB"
    return f"{size/1024**3:.2f} GB"


async def _send_to_log_group(text: str):
    """Send a message to the configured Telegram log group. Silently ignores errors."""
    from config import Config
    if not Config.LOG_GROUP_ID or _bot_client is None:
        return
    try:
        await _bot_client.send_message(Config.LOG_GROUP_ID, text)
    except Exception as e:
        logger.warning(f"Failed to send log to group {Config.LOG_GROUP_ID}: {e}")


# ── public API ─────────────────────────────────────────────────────────────────

async def log_user_join(
    db: AsyncIOMotorDatabase,
    user_id: int,
    username: str | None,
    first_name: str | None,
):
    doc = _base("user_join", user_id, username, first_name)
    await db.activity_logs.insert_one(doc)
    logger.info(f"[JOIN]    user_id={user_id}  @{username or '—'}  name={first_name!r}")

    text = (
        "👤 **NEW USER**\n"
        f"Name: {_user_line(first_name, username, user_id)}"
    )
    await _send_to_log_group(text)


async def log_user_block(
    db: AsyncIOMotorDatabase,
    user_id: int,
    username: str | None,
    first_name: str | None,
):
    doc = _base("user_block", user_id, username, first_name)
    await db.activity_logs.insert_one(doc)
    logger.info(f"[BLOCK]   user_id={user_id}  @{username or '—'}  name={first_name!r}")

    text = (
        "🚫 **USER BLOCKED BOT**\n"
        f"Name: {_user_line(first_name, username, user_id)}"
    )
    await _send_to_log_group(text)


async def log_user_unblock(
    db: AsyncIOMotorDatabase,
    user_id: int,
    username: str | None,
    first_name: str | None,
):
    doc = _base("user_unblock", user_id, username, first_name)
    await db.activity_logs.insert_one(doc)
    logger.info(f"[UNBLOCK] user_id={user_id}  @{username or '—'}  name={first_name!r}")

    text = (
        "✅ **USER UNBLOCKED BOT**\n"
        f"Name: {_user_line(first_name, username, user_id)}"
    )
    await _send_to_log_group(text)


async def log_file_upload(
    db: AsyncIOMotorDatabase,
    user_id: int,
    username: str | None,
    first_name: str | None,
    file_name: str,
    file_size: int,
    drive_email: str,
    folder_name: str | None,
    success: bool,
    error: str | None = None,
):
    doc = _base("file_upload", user_id, username, first_name)
    doc.update({
        "file_name":   file_name,
        "file_size":   file_size,
        "drive_email": drive_email,
        "folder_name": folder_name,
        "success":     success,
        "error":       error,
    })
    await db.activity_logs.insert_one(doc)

    status_str = "OK" if success else f"FAIL({error or '?'})"
    logger.info(
        f"[UPLOAD]  user_id={user_id}  file={file_name!r}  "
        f"size={_fmt_size(file_size)}  drive={drive_email}  status={status_str}"
    )

    folder_display = folder_name or "Root"
    if success:
        status_line = "✅ Success"
    else:
        reason = error or "unknown error"
        # strip "cancelled" uploads from the log group to avoid noise
        if reason == "cancelled":
            return
        status_line = f"❌ Failed — `{reason}`"

    text = (
        "📤 **FILE UPLOADED**\n"
        f"User: {_user_line(first_name, username, user_id)}\n"
        f"File: `{file_name}` ({_fmt_size(file_size)})\n"
        f"Drive: `{drive_email}` → `{folder_display}`\n"
        f"Status: {status_line}"
    )
    await _send_to_log_group(text)


# ── stats helpers (used by /stats command) ─────────────────────────────────────

async def get_stats(db: AsyncIOMotorDatabase) -> dict:
    col = db.activity_logs

    total_users    = await db.users.count_documents({})
    total_joins    = await col.count_documents({"event": "user_join"})
    total_blocks   = await col.count_documents({"event": "user_block"})
    total_uploads  = await col.count_documents({"event": "file_upload"})
    failed_uploads = await col.count_documents({"event": "file_upload", "success": False})

    pipeline = [
        {"$match": {"event": "file_upload", "success": True}},
        {"$group": {"_id": None, "total_bytes": {"$sum": "$file_size"}}},
    ]
    agg = await col.aggregate(pipeline).to_list(1)
    total_bytes = agg[0]["total_bytes"] if agg else 0

    unique_uploaders = len(await col.distinct("user_id", {"event": "file_upload"}))

    pipeline2 = [
        {"$match": {"event": {"$in": ["user_block", "user_unblock"]}}},
        {"$sort": {"ts": -1}},
        {"$group": {"_id": "$user_id", "last_event": {"$first": "$event"}}},
        {"$match": {"last_event": "user_block"}},
        {"$count": "n"},
    ]
    agg2 = await col.aggregate(pipeline2).to_list(1)
    currently_blocked = agg2[0]["n"] if agg2 else 0

    pipeline3 = [
        {"$match": {"event": "file_upload", "success": True}},
        {"$group": {
            "_id":        "$user_id",
            "count":      {"$sum": 1},
            "bytes":      {"$sum": "$file_size"},
            "first_name": {"$first": "$first_name"},
            "username":   {"$first": "$username"},
        }},
        {"$sort": {"count": -1}},
        {"$limit": 5},
    ]
    top_uploaders = await col.aggregate(pipeline3).to_list(5)

    return {
        "total_users":       total_users,
        "total_joins":       total_joins,
        "total_blocks":      total_blocks,
        "currently_blocked": currently_blocked,
        "total_uploads":     total_uploads,
        "failed_uploads":    failed_uploads,
        "total_bytes":       total_bytes,
        "unique_uploaders":  unique_uploaders,
        "top_uploaders":     top_uploaders,
    }
