"""
on_member_events.py — detect when users block or unblock the bot.

How Telegram actually signals a block
--------------------------------------
Telegram does NOT send a ChatMemberUpdated event when a user blocks a bot
in a private chat. The only reliable signals are:

1. A `UserIsBlocked` / `InputUserDeactivated` error raised when the bot
   tries to send a message to that user.
2. The raw `updateUserStatus` TL object, where status becomes
   `UserStatusEmpty` — but that just means "last seen hidden", not a block.

The most robust approach used by production bots (e.g. aiogram, PTB) is:
  • Catch `UserIsBlocked` on every outgoing send and mark the user blocked.
  • Catch the reverse: when a previously-blocked user sends ANY message,
    mark them unblocked (because Telegram only lets messages through after
    an unblock).

We hook both sides here:
  • `_check_blocked_on_send` — a helper called by any handler that sends
    proactively (not used directly here; imported in logger so the
    log-group sender can call it).
  • `on_message` with a broad private filter — if we receive any message
    from a user we previously marked as blocked, log the unblock.
"""

import logging

from pyrogram import Client, filters
from pyrogram.errors import UserIsBlocked, InputUserDeactivated
from pyrogram.types import Message

from logger import log_user_block, log_user_unblock

logger = logging.getLogger(__name__)

_blocked_users: set[int] = set()


def mark_blocked(user_id: int):
    _blocked_users.add(user_id)


def mark_unblocked(user_id: int):
    _blocked_users.discard(user_id)


def is_known_blocked(user_id: int) -> bool:
    return user_id in _blocked_users


async def handle_send_error(
    error: Exception,
    client: Client,
    user_id: int,
    username: str | None,
    first_name: str | None,
) -> bool:
    """
    Call this in an except block whenever the bot tries to send to a user.
    Returns True if the error was a block/deactivation (caller can suppress it).
    """
    if isinstance(error, (UserIsBlocked, InputUserDeactivated)):
        if user_id not in _blocked_users:
            mark_blocked(user_id)
            await log_user_block(client.db.db, user_id, username, first_name)
        return True
    return False


def register(app: Client):

    @app.on_message(filters.private)
    async def detect_unblock(client: Client, message: Message):
        """
        Any incoming private message means the user can reach us — so if we
        had them marked as blocked, they must have unblocked the bot.
        """
        user_id    = message.from_user.id
        username   = message.from_user.username
        first_name = message.from_user.first_name

        if user_id in _blocked_users:
            mark_unblocked(user_id)
            await log_user_unblock(client.db.db, user_id, username, first_name)
            logger.info(f"[UNBLOCK] detected via incoming message from user_id={user_id}")
