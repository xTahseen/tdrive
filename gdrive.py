import asyncio
import logging
import mimetypes
import io
import time
from concurrent.futures import ThreadPoolExecutor

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from googleapiclient.errors import HttpError

from config import Config
from database import Database

logger = logging.getLogger(__name__)

FOLDER_MIME = "application/vnd.google-apps.folder"
PAGE_SIZE = 10        # Telegram bot file manager: 10 items per page
WEBUI_PAGE_SIZE = 20  # WebUI infinite scroll: 20 items per fetch

GOOGLE_EXPORT_MAP = {
    "application/vnd.google-apps.document":
        ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
    "application/vnd.google-apps.spreadsheet":
        ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
    "application/vnd.google-apps.presentation":
        ("application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"),
    "application/vnd.google-apps.drawing":
        ("image/png", ".png"),
    "application/vnd.google-apps.script":
        ("application/zip", ".zip"),
}

# Thread pool for running blocking Google API calls off the event loop
_executor = ThreadPoolExecutor(max_workers=32, thread_name_prefix="gdrive")


def _run_sync(func, *args, **kwargs):
    """Run a blocking callable in the thread pool, returning a coroutine."""
    loop = asyncio.get_event_loop()
    return loop.run_in_executor(_executor, lambda: func(*args, **kwargs))


def _client_config() -> dict:
    return {
        "web": {
            "client_id": Config.GOOGLE_CLIENT_ID,
            "client_secret": Config.GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [Config.OAUTH_REDIRECT_URI],
        }
    }


# ── Credential cache ───────────────────────────────────────────────────────────
# Stores (creds, expire_time) keyed by (user_id, drive_index).
# Avoids a DB round-trip + Credentials rebuild on every button press.
_cred_cache: dict[tuple, tuple] = {}
_CRED_TTL = 300  # seconds before we recheck (token lifetime is ~3600 s)


class GoogleDriveManager:
    def __init__(self, db: Database):
        self.db = db

    def get_auth_url_for_web(self):
        """Like get_auth_url but uses the WebUI OAuth callback URL."""
        web_redirect = f"{Config.WEBUI_BASE_URL}/oauth/callback"
        flow = Flow.from_client_config(
            _client_config(),
            scopes=Config.GOOGLE_SCOPES,
            redirect_uri=web_redirect,
        )
        auth_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
        )
        return auth_url, flow, state

    def build_web_flow(self) -> Flow:
        """Build a fresh OAuth Flow for the WebUI redirect URI.
        Used to reconstruct a flow when the in-memory pending dict is gone
        (e.g. after a server restart) so the callback can still exchange the code."""
        web_redirect = f"{Config.WEBUI_BASE_URL}/oauth/callback"
        return Flow.from_client_config(
            _client_config(),
            scopes=Config.GOOGLE_SCOPES,
            redirect_uri=web_redirect,
        )

    async def exchange_code(self, user_id: int, code: str, flow: Flow, account_index: int = None) -> str:
        await _run_sync(flow.fetch_token, code=code)
        creds = flow.credentials
        email = await self._get_email(creds)

        token_data = {
            "_email": email or "unknown",
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": list(creds.scopes) if creds.scopes else Config.GOOGLE_SCOPES,
        }

        idx = await self.db.save_token(user_id, token_data, account_index)

        if email:
            await self.db.update_drive_email(user_id, idx, email)
            await self.db.save_user_email(user_id, email)

        return email or "unknown", idx

    async def _get_credentials(self, user_id: int, drive_index: int = None):
        """Return credentials, using an in-process cache to avoid DB hits."""
        if drive_index is None:
            drive_index = await self.db.get_active_drive_index(user_id)

        cache_key = (user_id, drive_index)
        now = time.monotonic()

        # Return cached creds if still fresh and not expired
        if cache_key in _cred_cache:
            cached_creds, cached_until = _cred_cache[cache_key]
            if now < cached_until and not cached_creds.expired:
                return cached_creds

        token_data = await self.db.get_token_for_drive(user_id, drive_index)
        if not token_data:
            return None

        creds = Credentials(
            token=token_data.get("token"),
            refresh_token=token_data.get("refresh_token"),
            token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=token_data.get("client_id", Config.GOOGLE_CLIENT_ID),
            client_secret=token_data.get("client_secret", Config.GOOGLE_CLIENT_SECRET),
            scopes=token_data.get("scopes", Config.GOOGLE_SCOPES),
        )

        if creds.expired and creds.refresh_token:
            try:
                await _run_sync(creds.refresh, Request())
                token_data["token"] = creds.token
                await self.db.update_drive_token(user_id, drive_index, token_data)
            except Exception as e:
                logger.error(f"Token refresh failed for user {user_id} drive {drive_index}: {e}")
                _cred_cache.pop(cache_key, None)
                return None

        _cred_cache[cache_key] = (creds, now + _CRED_TTL)
        return creds

    def invalidate_cache(self, user_id: int, drive_index: int = None):
        """Call after logout / token change so stale creds are not reused."""
        if drive_index is None:
            keys = [k for k in _cred_cache if k[0] == user_id]
        else:
            keys = [(user_id, drive_index)]
        for k in keys:
            _cred_cache.pop(k, None)

    def _service(self, name: str, version: str, creds: Credentials):
        """Build a Google API service object (uses googleapiclient's own cache)."""
        return build(name, version, credentials=creds, cache_discovery=False)

    async def _get_email(self, creds: Credentials):
        try:
            def _fetch():
                svc = build("oauth2", "v2", credentials=creds, cache_discovery=False)
                return svc.userinfo().get().execute()
            info = await _run_sync(_fetch)
            return info.get("email")
        except Exception as e:
            logger.warning(f"Could not fetch email: {e}")
            return None

    # ── File / folder operations ───────────────────────────────────────────────

    async def list_folder(self, user_id: int, folder_id: str = "root",
                          page_token: str = None, drive_index: int = None,
                          page_size: int = None):
        creds = await self._get_credentials(user_id, drive_index)
        if not creds:
            raise PermissionError("Not authenticated.")

        _ps = page_size if page_size is not None else PAGE_SIZE

        def _call():
            svc = self._service("drive", "v3", creds)
            kwargs = dict(
                q=f"'{folder_id}' in parents and trashed = false",
                pageSize=_ps,
                fields="nextPageToken, files(id, name, mimeType, size, webViewLink, webContentLink, modifiedTime, parents, thumbnailLink, hasThumbnail)",
                orderBy="folder,name",
            )
            if page_token:
                kwargs["pageToken"] = page_token
            result = svc.files().list(**kwargs).execute()
            return result.get("files", []), result.get("nextPageToken")

        return await _run_sync(_call)

    async def get_file(self, user_id: int, file_id: str, drive_index: int = None) -> dict:
        creds = await self._get_credentials(user_id, drive_index)
        if not creds:
            raise PermissionError("Not authenticated.")

        def _call():
            svc = self._service("drive", "v3", creds)
            return svc.files().get(
                fileId=file_id,
                fields="id, name, mimeType, size, webViewLink, parents, modifiedTime"
            ).execute()

        return await _run_sync(_call)

    async def rename_file(self, user_id: int, file_id: str, new_name: str, drive_index: int = None) -> dict:
        creds = await self._get_credentials(user_id, drive_index)
        if not creds:
            raise PermissionError("Not authenticated.")

        def _call():
            svc = self._service("drive", "v3", creds)
            return svc.files().update(
                fileId=file_id,
                body={"name": new_name},
                fields="id, name"
            ).execute()

        return await _run_sync(_call)

    async def delete_file(self, user_id: int, file_id: str, drive_index: int = None):
        creds = await self._get_credentials(user_id, drive_index)
        if not creds:
            raise PermissionError("Not authenticated.")

        def _call():
            svc = self._service("drive", "v3", creds)
            svc.files().delete(fileId=file_id).execute()

        await _run_sync(_call)

    async def create_folder(self, user_id: int, folder_name: str,
                            parent_id: str = "root", drive_index: int = None) -> dict:
        creds = await self._get_credentials(user_id, drive_index)
        if not creds:
            raise PermissionError("Not authenticated.")

        def _call():
            svc = self._service("drive", "v3", creds)
            return svc.files().create(
                body={"name": folder_name, "mimeType": FOLDER_MIME, "parents": [parent_id]},
                fields="id, name, webViewLink"
            ).execute()

        return await _run_sync(_call)

    async def get_folder_link(self, user_id: int, folder_id: str, drive_index: int = None) -> str:
        creds = await self._get_credentials(user_id, drive_index)
        if not creds:
            raise PermissionError("Not authenticated.")

        def _call():
            svc = self._service("drive", "v3", creds)
            return svc.files().get(fileId=folder_id, fields="webViewLink").execute()

        f = await _run_sync(_call)
        return f.get("webViewLink", f"https://drive.google.com/drive/folders/{folder_id}")

    async def get_breadcrumb(self, user_id: int, folder_id: str, drive_index: int = None) -> list:
        if folder_id == "root":
            return [("root", "My Drive")]

        creds = await self._get_credentials(user_id, drive_index)
        if not creds:
            return [("root", "My Drive")]

        def _walk():
            """Walk parent chain synchronously — all in one thread, no await overhead."""
            svc = self._service("drive", "v3", creds)
            crumbs = []
            current_id = folder_id
            for _ in range(6):
                try:
                    f = svc.files().get(fileId=current_id, fields="id, name, parents").execute()
                    crumbs.append((f["id"], f["name"]))
                    parents = f.get("parents", [])
                    if not parents:
                        break
                    current_id = parents[0]
                    if current_id == "root" or current_id is None:
                        break
                except Exception:
                    break
            crumbs.reverse()
            return [("root", "My Drive")] + crumbs

        return await _run_sync(_walk)

    async def get_file_meta_for_download(self, user_id: int, file_id: str, drive_index: int = None) -> dict:
        """Return file metadata + a short-lived access token for mirrored downloads.
        The webui uses this to redirect the browser straight to Google's CDN —
        the file bytes never pass through our server."""
        creds = await self._get_credentials(user_id, drive_index)
        if not creds:
            raise PermissionError("Not authenticated.")

        def _call():
            svc = self._service("drive", "v3", creds)
            meta = svc.files().get(
                fileId=file_id,
                fields="id, name, mimeType, size, webContentLink"
            ).execute()
            # Refresh token if needed so we have a valid access_token
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
            return {
                "id": meta.get("id"),
                "name": meta.get("name", "file"),
                "mimeType": meta.get("mimeType", "application/octet-stream"),
                "size": meta.get("size"),
                "webContentLink": meta.get("webContentLink"),
                "access_token": creds.token,
                "export_map": GOOGLE_EXPORT_MAP,
            }

        return await _run_sync(_call)

    async def download_file(self, user_id: int, file_id: str, drive_index: int = None) -> tuple[bytes, str, str]:
        creds = await self._get_credentials(user_id, drive_index)
        if not creds:
            raise PermissionError("Not authenticated.")

        def _call():
            svc = self._service("drive", "v3", creds)
            meta = svc.files().get(fileId=file_id, fields="id, name, mimeType, size").execute()
            name = meta.get("name", "file")
            mime = meta.get("mimeType", "application/octet-stream")
            buf = io.BytesIO()

            if mime in GOOGLE_EXPORT_MAP:
                export_mime, ext = GOOGLE_EXPORT_MAP[mime]
                if not name.endswith(ext):
                    name += ext
                request = svc.files().export_media(fileId=file_id, mimeType=export_mime)
                mime = export_mime
            else:
                request = svc.files().get_media(fileId=file_id)

            downloader = MediaIoBaseDownload(buf, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            return buf.getvalue(), name, mime

        return await _run_sync(_call)

    async def upload_file(self, user_id: int, file_path: str, file_name: str,
                          folder_id: str = None, progress_callback=None,
                          drive_index: int = None) -> dict:
        creds = await self._get_credentials(user_id, drive_index)
        if not creds:
            raise PermissionError("User not authenticated with Google Drive.")

        mime_type, _ = mimetypes.guess_type(file_name)
        mime_type = mime_type or "application/octet-stream"

        file_metadata = {"name": file_name}
        if folder_id:
            file_metadata["parents"] = [folder_id]

        # Upload runs in executor; progress callbacks are dispatched back to the
        # event loop so they can safely call async Telegram API methods.
        loop = asyncio.get_event_loop()

        def _call():
            svc = self._service("drive", "v3", creds)
            media = MediaFileUpload(file_path, mimetype=mime_type, resumable=True, chunksize=5 * 1024 * 1024)
            request = svc.files().create(body=file_metadata, media_body=media, fields="id, name, webViewLink, size")
            response = None
            last_pct = -1
            retries = 0
            max_retries = 5
            while response is None:
                try:
                    status, response = request.next_chunk()
                except (BrokenPipeError, ConnectionError, OSError, HttpError) as e:
                    # Transient network errors (broken pipe, reset connections,
                    # 5xx from Drive) are expected occasionally on resumable
                    # uploads. google-api-python-client's resumable upload
                    # tracks how many bytes were actually committed, so
                    # calling next_chunk() again resumes from where it left
                    # off rather than restarting the whole upload. Retrying
                    # here (with a short backoff) is the fix recommended by
                    # Google's own resumable-upload docs and avoids surfacing
                    # a hard failure to the user for a one-off network blip.
                    retries += 1
                    if retries > max_retries:
                        raise
                    wait = min(2 ** retries, 30)
                    logger.warning(
                        f"Upload chunk error for {file_name!r} (attempt "
                        f"{retries}/{max_retries}): {e}. Retrying in {wait}s."
                    )
                    time.sleep(wait)
                    continue
                if status and progress_callback:
                    pct = int(status.progress() * 100)
                    if pct != last_pct:
                        last_pct = pct
                        # Schedule the async callback on the event loop from this thread
                        asyncio.run_coroutine_threadsafe(progress_callback(pct), loop)
            return response

        response = await _run_sync(_call)

        # Set public read permission (non-blocking)
        def _perm():
            try:
                svc = self._service("drive", "v3", creds)
                svc.permissions().create(
                    fileId=response["id"],
                    body={"type": "anyone", "role": "reader"},
                ).execute()
            except HttpError as e:
                logger.warning(f"Could not set file permission: {e}")

        asyncio.ensure_future(_run_sync(_perm))

        return response

    async def list_files(self, user_id: int, page_size: int = 10, drive_index: int = None) -> list:
        creds = await self._get_credentials(user_id, drive_index)
        if not creds:
            raise PermissionError("Not authenticated.")

        def _call():
            svc = self._service("drive", "v3", creds)
            results = svc.files().list(
                pageSize=page_size,
                fields="files(id, name, size, webViewLink, createdTime)",
                orderBy="createdTime desc",
            ).execute()
            return results.get("files", [])

        return await _run_sync(_call)

    async def move_file(self, user_id: int, file_id: str, new_parent_id: str, drive_index: int = None) -> dict:
        creds = await self._get_credentials(user_id, drive_index)
        if not creds:
            raise PermissionError("Not authenticated.")

        def _call():
            svc = self._service("drive", "v3", creds)
            # Get current parents first
            f = svc.files().get(fileId=file_id, fields="parents").execute()
            old_parents = ",".join(f.get("parents", []))
            return svc.files().update(
                fileId=file_id,
                addParents=new_parent_id,
                removeParents=old_parents,
                fields="id, name, parents"
            ).execute()

        return await _run_sync(_call)

    async def copy_file(self, user_id: int, file_id: str, dest_folder_id: str = None,
                        new_name: str = None, drive_index: int = None) -> dict:
        creds = await self._get_credentials(user_id, drive_index)
        if not creds:
            raise PermissionError("Not authenticated.")

        def _call():
            svc = self._service("drive", "v3", creds)
            body = {}
            if new_name:
                body["name"] = new_name
            if dest_folder_id:
                body["parents"] = [dest_folder_id]
            return svc.files().copy(
                fileId=file_id,
                body=body,
                fields="id, name, mimeType, size, modifiedTime"
            ).execute()

        return await _run_sync(_call)

    async def upload_bytes(self, user_id: int, data: bytes, file_name: str,
                           folder_id: str = "root", drive_index: int = None) -> dict:
        """Upload raw bytes (from browser upload) to Google Drive."""
        import tempfile, os
        creds = await self._get_credentials(user_id, drive_index)
        if not creds:
            raise PermissionError("Not authenticated.")

        mime_type, _ = mimetypes.guess_type(file_name)
        mime_type = mime_type or "application/octet-stream"

        # Write bytes to a temp file for MediaFileUpload
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file_name)[1])
        try:
            tmp.write(data)
            tmp.close()
            file_metadata = {"name": file_name}
            if folder_id and folder_id != "root":
                file_metadata["parents"] = [folder_id]
            else:
                file_metadata["parents"] = ["root"]

            def _call():
                svc = self._service("drive", "v3", creds)
                media = MediaFileUpload(tmp.name, mimetype=mime_type, resumable=True, chunksize=5 * 1024 * 1024)
                request = svc.files().create(body=file_metadata, media_body=media,
                                              fields="id, name, mimeType, size, modifiedTime, webViewLink")
                response = None
                while response is None:
                    _, response = request.next_chunk()
                return response

            return await _run_sync(_call)
        finally:
            try:
                os.unlink(tmp.name)
            except Exception:
                pass

    async def get_storage_quota(self, user_id: int, drive_index: int = None) -> dict:
        """Return storage usage dict: {limit, usage, usageInDrive, usageInDriveTrash}. All bytes as int."""
        creds = await self._get_credentials(user_id, drive_index)
        if not creds:
            raise PermissionError("Not authenticated.")

        def _call():
            svc = self._service("drive", "v3", creds)
            about = svc.about().get(fields="storageQuota").execute()
            q = about.get("storageQuota", {})
            return {
                "limit":              int(q.get("limit", 0) or 0),
                "usage":              int(q.get("usage", 0) or 0),
                "usageInDrive":       int(q.get("usageInDrive", 0) or 0),
                "usageInDriveTrash":  int(q.get("usageInDriveTrash", 0) or 0),
            }

        return await _run_sync(_call)

    async def search_files(self, user_id: int, query: str, drive_index: int = None) -> list:
        creds = await self._get_credentials(user_id, drive_index)
        if not creds:
            raise PermissionError("Not authenticated.")

        def _call():
            svc = self._service("drive", "v3", creds)
            safe = query.replace("'", "\\'")
            result = svc.files().list(
                q=f"name contains '{safe}' and trashed = false",
                pageSize=50,
                fields="files(id, name, mimeType, size, modifiedTime, parents, thumbnailLink, hasThumbnail)",
                orderBy="modifiedTime desc",
            ).execute()
            return result.get("files", [])

        return await _run_sync(_call)

    async def set_file_shared(self, user_id: int, file_id: str, drive_index: int = None) -> bool:
        """Grant anyone-with-link reader access to a Drive file/folder."""
        creds = await self._get_credentials(user_id, drive_index)
        if not creds:
            raise PermissionError("Not authenticated.")

        def _call():
            svc = self._service("drive", "v3", creds)
            # Check if permission already exists
            perms = svc.permissions().list(fileId=file_id, fields="permissions(id,type,role)").execute()
            for p in perms.get("permissions", []):
                if p.get("type") == "anyone":
                    return True  # already public
            svc.permissions().create(
                fileId=file_id,
                body={"type": "anyone", "role": "reader"},
                fields="id",
            ).execute()
            return True

        return await _run_sync(_call)

    async def set_file_private(self, user_id: int, file_id: str, drive_index: int = None) -> bool:
        """Remove anyone-with-link access from a Drive file/folder."""
        creds = await self._get_credentials(user_id, drive_index)
        if not creds:
            raise PermissionError("Not authenticated.")

        def _call():
            svc = self._service("drive", "v3", creds)
            perms = svc.permissions().list(fileId=file_id, fields="permissions(id,type)").execute()
            for p in perms.get("permissions", []):
                if p.get("type") == "anyone":
                    try:
                        svc.permissions().delete(fileId=file_id, permissionId=p["id"]).execute()
                    except Exception:
                        pass
            return True

        return await _run_sync(_call)

    async def get_file_web_link(self, user_id: int, file_id: str, drive_index: int = None) -> str | None:
        """Return the webViewLink for a Drive file/folder."""
        creds = await self._get_credentials(user_id, drive_index)
        if not creds:
            raise PermissionError("Not authenticated.")

        def _call():
            svc = self._service("drive", "v3", creds)
            return svc.files().get(fileId=file_id, fields="webViewLink").execute().get("webViewLink")

        return await _run_sync(_call)

    async def list_folder_contents(self, user_id: int, folder_id: str, drive_index: int = None) -> list:
        """List files inside a shared folder (for the public share page)."""
        creds = await self._get_credentials(user_id, drive_index)
        if not creds:
            raise PermissionError("Not authenticated.")

        def _call():
            svc = self._service("drive", "v3", creds)
            result = svc.files().list(
                q=f"'{folder_id}' in parents and trashed = false",
                pageSize=100,
                fields="files(id, name, mimeType, size, modifiedTime, thumbnailLink, hasThumbnail)",
                orderBy="folder,name",
            ).execute()
            return result.get("files", [])

        return await _run_sync(_call)
