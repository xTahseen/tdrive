import logging
import time
from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ASCENDING

logger = logging.getLogger(__name__)

_doc_cache: dict[int, tuple[dict, float]] = {}
_DOC_TTL = 10.0


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
            maxPoolSize=50,
            minPoolSize=5,
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
        await self.db.share_links.create_index([("token", ASCENDING)], unique=True)
        await self.db.share_links.create_index([("file_id", ASCENDING)])
        logger.info("Database indexes ensured.")

    async def close(self):
        if self.client:
            self.client.close()


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


    async def save_token(self, user_id: int, token_data: dict, account_index: int = None):
        doc = await self._get_doc(user_id)
        drives = list((doc or {}).get("drives", []))

        email = token_data.pop("_email", None)
        token_data_clean = dict(token_data)

        if account_index is not None and 0 <= account_index < len(drives):
            drives[account_index]["token"] = token_data_clean
            if email:
                drives[account_index]["email"] = email
        else:
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


    async def get_active_drive_index(self, user_id: int) -> int:
        doc = await self._get_doc(user_id)
        return (doc or {}).get("active_drive", 0)

    async def set_active_drive(self, user_id: int, drive_index: int):
        await self.db.users.update_one(
            {"user_id": user_id},
            {"$set": {"active_drive": drive_index, "updated_at": datetime.utcnow()}},
        )
        await self._invalidate(user_id)


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


    async def set_webui_credentials(
        self, hashed_username: str, hashed_password: str, user_id: int = None
    ):
        """Store hashed WebUI username and password (and optionally the owner's Telegram user_id).

        Bumps `session_version` so any WebUI session cookie issued before this
        change is rejected by require_auth() on its next request, even though
        the cookie's own signature/expiry are still technically valid."""
        update_fields = {
            "hashed_username": hashed_username,
            "hashed_password": hashed_password,
            "updated_at": datetime.utcnow(),
        }
        if user_id is not None:
            update_fields["user_id"] = user_id
        await self.db.settings.update_one(
            {"key": "webui_credentials"},
            {"$set": update_fields, "$inc": {"session_version": 1}},
            upsert=True,
        )

    async def get_webui_credentials(self) -> tuple[str | None, str | None]:
        """Return (hashed_username, hashed_password) or (None, None) if not set."""
        doc = await self.db.settings.find_one({"key": "webui_credentials"})
        if not doc:
            return None, None
        return doc.get("hashed_username"), doc.get("hashed_password")

    async def has_webui_credentials(self) -> bool:
        """Return True if credentials have been configured."""
        u, p = await self.get_webui_credentials()
        return u is not None and p is not None

    async def get_webui_session_version(self) -> int:
        """Current session_version for the WebUI credentials doc.

        Returns -1 if no credentials doc exists at all (cleared, or never
        set) so that any previously-issued token — which always carries a
        non-negative `ver` — is automatically treated as stale."""
        doc = await self.db.settings.find_one({"key": "webui_credentials"})
        if not doc:
            return -1
        return doc.get("session_version", 0)

    async def clear_webui_credentials(self):
        """Remove WebUI credentials entirely, disabling login."""
        await self.db.settings.delete_one({"key": "webui_credentials"})

    async def get_webui_owner_id(self) -> int | None:
        """Return the Telegram user_id of whoever set the WebUI credentials."""
        doc = await self.db.settings.find_one({"key": "webui_credentials"})
        return doc.get("user_id") if doc else None

    async def get_first_user_id(self) -> int | None:
        """Return the first user_id found in the DB (for session binding).
        Falls back through: webui_credentials owner -> users collection."""
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


    async def create_share_link(
        self,
        token: str,
        file_id: str,
        file_name: str,
        mime_type: str,
        is_folder: bool,
        user_id: int,
        drive_index: int,
        password_hash: str | None = None,
    ) -> dict:
        """Store a new share link. Returns the document."""
        doc = {
            "token": token,
            "file_id": file_id,
            "file_name": file_name,
            "mime_type": mime_type,
            "is_folder": is_folder,
            "user_id": user_id,
            "drive_index": drive_index,
            "password_hash": password_hash,
            "created_at": datetime.utcnow(),
        }
        await self.db.share_links.insert_one(doc)
        return doc

    async def get_share_link(self, token: str) -> dict | None:
        """Fetch a share link by token."""
        return await self.db.share_links.find_one({"token": token})

    async def delete_share_link(self, token: str):
        """Remove a share link by token."""
        await self.db.share_links.delete_one({"token": token})

    async def get_share_links_for_file(self, file_id: str) -> list[dict]:
        """Return all share links for a given Drive file_id."""
        cursor = self.db.share_links.find({"file_id": file_id})
        return [doc async for doc in cursor]

    async def get_all_share_links(self, user_id: int) -> list[dict]:
        """Return all share links created by a user."""
        cursor = self.db.share_links.find({"user_id": user_id})
        return [doc async for doc in cursor]

    async def ensure_share_index(self):
        """Create index on share_links.token (unique)."""
        await self.db.share_links.create_index([("token", ASCENDING)], unique=True)
        await self.db.share_links.create_index([("file_id", ASCENDING)])
