import logging
import time
from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ASCENDING

logger = logging.getLogger(__name__)

# ── Simple in-process user-doc cache ──────────────────────────────────────────
# Keyed by user_id → (doc, expire_monotonic).
# TTL is short (10 s) so mutations (token save, drive switch) are reflected quickly
# while still eliminating the multiple redundant find_one calls per button press.
_doc_cache: dict[int, tuple[dict, float]] = {}
_DOC_TTL = 10.0  # seconds


def _cache_get(user_id: int) -> dict | None:
    entry = _doc_cache.get(user_id)
    if entry and time.monotonic() < entry[1]:
        return entry[0]
    return None


def _cache_set(user_id: int, doc: dict):
    _doc_cache[user_id] = (doc, time.monotonic() + _DOC_TTL)


def _cache_del(user_id: int):
    _doc_cache.pop(user_id, None)


class Database:
    def __init__(self, uri: str, db_name: str):
        self.uri = uri
        self.db_name = db_name
        self.client = None
        self.db = None

    async def connect(self):
        self.client = AsyncIOMotorClient(
            self.uri,
            maxPoolSize=50,          # allow up to 50 concurrent DB connections
            minPoolSize=5,           # keep 5 warm
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
        )
        self.db = self.client[self.db_name]
        await self.db.users.create_index([("user_id", ASCENDING)], unique=True)
        await self.db.oauth_states.create_index([("user_id", ASCENDING)], unique=True)
        await self.db.oauth_states.create_index([("state", ASCENDING)])
        await self.db.oauth_states.create_index(
            [("created_at", ASCENDING)],
            expireAfterSeconds=600
        )
        logger.info("Database indexes ensured.")

    async def close(self):
        if self.client:
            self.client.close()

    # ── internal: fetch user doc (with cache) ─────────────────────────────────

    async def _get_doc(self, user_id: int) -> dict | None:
        cached = _cache_get(user_id)
        if cached is not None:
            return cached
        doc = await self.db.users.find_one({"user_id": user_id})
        if doc:
            _cache_set(user_id, doc)
        return doc

    async def _invalidate(self, user_id: int):
        _cache_del(user_id)

    # ── token / drive storage ──────────────────────────────────────────────────

    async def save_token(self, user_id: int, token_data: dict, account_index: int = None):
        doc = await self._get_doc(user_id)
        drives = list((doc or {}).get("drives", []))

        email = token_data.pop("_email", None)
        token_data_clean = dict(token_data)

        if account_index is not None and 0 <= account_index < len(drives):
            # Explicit index update (e.g. token refresh)
            drives[account_index]["token"] = token_data_clean
            if email:
                drives[account_index]["email"] = email
        else:
            # Check if this email already exists — replace it instead of adding a duplicate
            existing_index = None
            if email:
                for i, d in enumerate(drives):
                    if d.get("email", "").lower() == email.lower():
                        existing_index = i
                        break
            if existing_index is not None:
                drives[existing_index]["token"] = token_data_clean
                drives[existing_index]["email"] = email
                account_index = existing_index
            else:
                drives.append({
                    "email": email or "unknown",
                    "token": token_data_clean,
                    "default_folder_id": None,
                    "default_folder_name": None,
                })
                account_index = len(drives) - 1

        active_idx = (doc or {}).get("active_drive", 0)

        await self.db.users.update_one(
            {"user_id": user_id},
            {
                "$set": {
                    "user_id": user_id,
                    "drives": drives,
                    "active_drive": active_idx,
                    "updated_at": datetime.utcnow(),
                },
                "$setOnInsert": {"created_at": datetime.utcnow()},
                "$unset": {"token": ""},
            },
            upsert=True,
        )
        await self._invalidate(user_id)
        return account_index

    async def get_token(self, user_id: int) -> dict | None:
        doc = await self._get_doc(user_id)
        if not doc:
            return None
        drives = doc.get("drives", [])
        if not drives:
            return None
        active = doc.get("active_drive", 0)
        if active >= len(drives):
            active = 0
        return drives[active].get("token")

    async def get_token_for_drive(self, user_id: int, drive_index: int) -> dict | None:
        doc = await self._get_doc(user_id)
        if not doc:
            return None
        drives = doc.get("drives", [])
        if drive_index < 0 or drive_index >= len(drives):
            return None
        return drives[drive_index].get("token")

    async def delete_token(self, user_id: int, drive_index: int = None):
        doc = await self._get_doc(user_id)
        if not doc:
            return

        if drive_index is None:
            await self.db.users.update_one(
                {"user_id": user_id},
                {"$set": {"drives": [], "active_drive": 0, "updated_at": datetime.utcnow()}, "$unset": {"token": ""}},
            )
        else:
            drives = list(doc.get("drives", []))
            if 0 <= drive_index < len(drives):
                drives.pop(drive_index)

            if not drives:
                new_active = 0
            else:
                old_active = doc.get("active_drive", 0)
                if old_active >= len(drives):
                    new_active = max(0, len(drives) - 1)
                elif old_active > drive_index:
                    new_active = old_active - 1
                else:
                    new_active = old_active

            update: dict = {"$set": {"drives": drives, "active_drive": new_active, "updated_at": datetime.utcnow()}}
            if not drives:
                update["$unset"] = {"token": ""}
            await self.db.users.update_one({"user_id": user_id}, update)

        await self._invalidate(user_id)

    async def is_authenticated(self, user_id: int) -> bool:
        token = await self.get_token(user_id)
        return token is not None

    async def ensure_user(self, user_id: int):
        """Create a minimal user record if one doesn't exist yet.
        This allows get_first_user_id() to work before a Drive is connected."""
        await self.db.users.update_one(
            {"user_id": user_id},
            {"$setOnInsert": {"user_id": user_id, "created_at": datetime.utcnow()}},
            upsert=True,
        )

    async def get_all_drives(self, user_id: int) -> list:
        doc = await self._get_doc(user_id)
        if not doc:
            return []
        drives = doc.get("drives", [])
        if not drives:
            return []
        return [
            {
                "email": d.get("email", "unknown"),
                "picture": d.get("picture", ""),
                "default_folder_id": d.get("default_folder_id"),
                "default_folder_name": d.get("default_folder_name"),
            }
            for d in drives
        ]

    # ── active drive ───────────────────────────────────────────────────────────

    async def get_active_drive_index(self, user_id: int) -> int:
        doc = await self._get_doc(user_id)
        return (doc or {}).get("active_drive", 0)

    async def set_active_drive(self, user_id: int, drive_index: int):
        await self.db.users.update_one(
            {"user_id": user_id},
            {"$set": {"active_drive": drive_index, "updated_at": datetime.utcnow()}},
        )
        await self._invalidate(user_id)

    # ── token updates ──────────────────────────────────────────────────────────

    async def update_drive_token(self, user_id: int, drive_index: int, token_data: dict):
        doc = await self._get_doc(user_id)
        if not doc:
            return
        drives = list(doc.get("drives", []))
        if 0 <= drive_index < len(drives):
            drives[drive_index]["token"] = token_data
            await self.db.users.update_one(
                {"user_id": user_id},
                {"$set": {"drives": drives, "updated_at": datetime.utcnow()}},
            )
            await self._invalidate(user_id)

    async def update_drive_email(self, user_id: int, drive_index: int, email: str):
        doc = await self._get_doc(user_id)
        if not doc:
            return
        drives = list(doc.get("drives", []))
        if 0 <= drive_index < len(drives):
            drives[drive_index]["email"] = email
            await self.db.users.update_one(
                {"user_id": user_id},
                {"$set": {"drives": drives, "updated_at": datetime.utcnow()}},
            )
            await self._invalidate(user_id)

    # ── default folder ─────────────────────────────────────────────────────────

    async def set_default_folder(self, user_id: int, drive_index: int,
                                  folder_id: str | None, folder_name: str | None):
        doc = await self._get_doc(user_id)
        if not doc:
            return
        drives = list(doc.get("drives", []))
        if 0 <= drive_index < len(drives):
            drives[drive_index]["default_folder_id"] = folder_id
            drives[drive_index]["default_folder_name"] = folder_name
            await self.db.users.update_one(
                {"user_id": user_id},
                {"$set": {"drives": drives, "updated_at": datetime.utcnow()}},
            )
            await self._invalidate(user_id)

    async def clear_default_folder(self, user_id: int, drive_index: int):
        await self.set_default_folder(user_id, drive_index, None, None)

    async def get_default_folder(self, user_id: int) -> tuple[str | None, str | None]:
        doc = await self._get_doc(user_id)
        if not doc:
            return None, None
        drives = doc.get("drives", [])
        active = doc.get("active_drive", 0)
        if not drives or active >= len(drives):
            return None, None
        d = drives[active]
        return d.get("default_folder_id"), d.get("default_folder_name")

    # ── user info ──────────────────────────────────────────────────────────────

    async def save_user_email(self, user_id: int, email: str):
        await self.db.users.update_one(
            {"user_id": user_id},
            {"$set": {"google_email": email, "updated_at": datetime.utcnow()}},
            upsert=True,
        )
        await self._invalidate(user_id)

    async def get_user_info(self, user_id: int) -> dict | None:
        return await self.db.users.find_one(
            {"user_id": user_id},
            {"_id": 0, "google_email": 1, "created_at": 1},
        )

    # ── oauth state ────────────────────────────────────────────────────────────

    async def save_oauth_state(self, user_id: int, state: str):
        await self.db.oauth_states.update_one(
            {"user_id": user_id},
            {"$set": {"state": state, "created_at": datetime.utcnow()}},
            upsert=True,
        )

    async def get_oauth_state(self, user_id: int) -> str | None:
        doc = await self.db.oauth_states.find_one({"user_id": user_id})
        return doc["state"] if doc else None

    async def get_user_id_by_oauth_state(self, state: str) -> int | None:
        """Reverse lookup: given a state string, find the user_id that owns it.
        Used by the OAuth callback to identify the user when no session cookie exists."""
        doc = await self.db.oauth_states.find_one({"state": state})
        return doc["user_id"] if doc else None

    async def delete_oauth_state(self, user_id: int):
        await self.db.oauth_states.delete_one({"user_id": user_id})

    # ── awaiting_code flag ─────────────────────────────────────────────────────

    async def set_awaiting_code(self, user_id: int):
        await self.db.users.update_one(
            {"user_id": user_id},
            {"$set": {"awaiting_code": True}},
            upsert=True,
        )
        await self._invalidate(user_id)

    async def is_awaiting_code(self, user_id: int) -> bool:
        doc = await self._get_doc(user_id)
        return bool(doc and doc.get("awaiting_code"))

    async def clear_awaiting_code(self, user_id: int):
        await self.db.users.update_one(
            {"user_id": user_id},
            {"$unset": {"awaiting_code": ""}},
        )
        await self._invalidate(user_id)

    # ── migration helper ───────────────────────────────────────────────────────

    async def migrate_legacy_tokens(self):
        cursor = self.db.users.find({"token": {"$exists": True}})
        count = 0
        async for doc in cursor:
            user_id = doc["user_id"]
            token   = doc.get("token")
            drives  = doc.get("drives", [])

            if not token:
                await self.db.users.update_one({"user_id": user_id}, {"$unset": {"token": ""}})
                count += 1
                continue

            if drives:
                await self.db.users.update_one({"user_id": user_id}, {"$unset": {"token": ""}})
            else:
                email = doc.get("google_email", "unknown")
                new_drives = [{"email": email, "token": token, "default_folder_id": None, "default_folder_name": None}]
                await self.db.users.update_one(
                    {"user_id": user_id},
                    {"$set": {"drives": new_drives, "active_drive": 0}, "$unset": {"token": ""}},
                )
            count += 1
            _cache_del(user_id)

        if count:
            logger.info(f"migrate_legacy_tokens: fixed {count} document(s).")

    # ── WebUI password & session ───────────────────────────────────────────────

    async def set_webui_password(self, hashed: str, user_id: int = None):
        """Store hashed WebUI password (and optionally the owner's Telegram user_id)."""
        update_fields = {
            "value": hashed,
            "updated_at": __import__("datetime").datetime.utcnow(),
        }
        if user_id is not None:
            update_fields["user_id"] = user_id
        await self.db.settings.update_one(
            {"key": "webui_password"},
            {"$set": update_fields},
            upsert=True,
        )

    async def get_webui_password(self) -> str | None:
        doc = await self.db.settings.find_one({"key": "webui_password"})
        return doc["value"] if doc else None

    async def get_webui_owner_id(self) -> int | None:
        """Return the Telegram user_id of whoever set the WebUI password."""
        doc = await self.db.settings.find_one({"key": "webui_password"})
        return doc.get("user_id") if doc else None

    async def get_first_user_id(self) -> int | None:
        """Return the first user_id found in the DB (for session binding).
        Falls back through: webui_password owner -> users collection."""
        owner = await self.get_webui_owner_id()
        if owner:
            return owner
        doc = await self.db.users.find_one({}, sort=[("created_at", 1)])
        return doc["user_id"] if doc else None

    async def get_all_user_ids(self) -> list[int]:
        """Return all user_ids (for admin features)."""
        ids = []
        async for doc in self.db.users.find({}, {"user_id": 1}):
            ids.append(doc["user_id"])
        return ids
