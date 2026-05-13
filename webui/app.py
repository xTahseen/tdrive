import asyncio
import hashlib
import hmac
import json
import logging
import time
import urllib.parse
from functools import wraps

from aiohttp import web
from config import Config
from database import Database
from gdrive import GoogleDriveManager, WEBUI_PAGE_SIZE

logger = logging.getLogger(__name__)
_web_pending_flows: dict = {}

def _sign(payload: dict) -> str:
    import base64
    data = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    sig = hmac.new(Config.WEBUI_SECRET_KEY.encode(), data.encode(), hashlib.sha256).hexdigest()
    return f"{data}.{sig}"

def _verify(token: str) -> dict | None:
    try:
        import base64
        data, sig = token.rsplit(".", 1)
        expected = hmac.new(Config.WEBUI_SECRET_KEY.encode(), data.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(base64.urlsafe_b64decode(data).decode())
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None

def _make_token(uid: int) -> str:
    return _sign({"uid": uid, "exp": time.time() + 86400 * 7})

def _hash_pw(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()

def _fmt_size(b: int) -> str:
    if b < 1048576: return f"{b // 1024} KB"
    if b < 1073741824: return f"{b // 1048576} MB"
    return f"{b // 1073741824} GB"

def require_auth(handler):
    @wraps(handler)
    async def wrapper(request: web.Request):
        tok = request.cookies.get("session")
        payload = _verify(tok) if tok else None
        if not payload:
            if request.path.startswith("/api/"):
                raise web.HTTPUnauthorized(reason="Not authenticated")
            raise web.HTTPFound("/")
        request["uid"] = payload["uid"]
        return await handler(request)
    return wrapper

def _icon(name: str, size: int = 20, cls: str = "", bg_color: str = None) -> str:
    cls_attr = f' class="{cls}"' if cls else ""
    icons = {
        "folder":       '<path d="M2.25 12.75V12A2.25 2.25 0 014.5 9.75h15A2.25 2.25 0 0121.75 12v.75m-8.69-6.44l-2.12-2.12a1.5 1.5 0 00-1.061-.44H4.5A2.25 2.25 0 002.25 6v12a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18V9a2.25 2.25 0 00-2.25-2.25h-5.379a1.5 1.5 0 01-1.06-.44z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
        "file":         '<path d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
        "image":        '<path d="M2.25 15.75l5.159-5.159a2.25 2.25 0 013.182 0l5.159 5.159m-1.5-1.5l1.409-1.409a2.25 2.25 0 013.182 0l2.909 2.909m-18 3.75h16.5a1.5 1.5 0 001.5-1.5V6a1.5 1.5 0 00-1.5-1.5H3.75A1.5 1.5 0 002.25 6v12a1.5 1.5 0 001.5 1.5zm10.5-11.25h.008v.008h-.008V8.25zm.375 0a.375.375 0 11-.75 0 .375.375 0 01.75 0z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
        "video":        '<path d="M15.75 10.5l4.72-4.72a.75.75 0 011.28.53v11.38a.75.75 0 01-1.28.53l-4.72-4.72M4.5 18.75h9a2.25 2.25 0 002.25-2.25v-9a2.25 2.25 0 00-2.25-2.25h-9A2.25 2.25 0 002.25 7.5v9a2.25 2.25 0 002.25 2.25z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
        "audio":        '<path d="M9 9l10.5-3m0 6.553v3.75a2.25 2.25 0 01-1.632 2.163l-1.32.377a1.803 1.803 0 11-.99-3.467l2.31-.66a2.25 2.25 0 001.632-2.163zm0 0V2.25L9 5.25v10.303m0 0v3.75a2.25 2.25 0 01-1.632 2.163l-1.32.377a1.803 1.803 0 01-.99-3.467l2.31-.66A2.25 2.25 0 009 15.553z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
        "pdf":          '<path d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
        "zip":          '<path d="M20.25 7.5l-.625 10.632a2.25 2.25 0 01-2.247 2.118H6.622a2.25 2.25 0 01-2.247-2.118L3.75 7.5M10 11.25h4M3.375 7.5h17.25c.621 0 1.125-.504 1.125-1.125v-1.5c0-.621-.504-1.125-1.125-1.125H3.375c-.621 0-1.125.504-1.125 1.125v1.5c0 .621.504 1.125 1.125 1.125z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
        "doc":          '<path d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
        "sheet":        '<path d="M3.375 19.5h17.25m-17.25 0a1.125 1.125 0 01-1.125-1.125M3.375 19.5h1.5C5.496 19.5 6 18.996 6 18.375m-3.75.125v-15.375C2.25 2.004 2.754 1.5 3.375 1.5h15.75c.621 0 1.125.504 1.125 1.125v15.375m-19.5 0v-15.375m0 0c0-.621.504-1.125 1.125-1.125h15.75c.621 0 1.125.504 1.125 1.125m-19.5 0v.375A1.125 1.125 0 003.375 3h17.25c.621 0 1.125.504 1.125 1.125v15" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
        "code":         '<path d="M17.25 6.75L22.5 12l-5.25 5.25m-10.5 0L1.5 12l5.25-5.25m7.5-3l-4.5 16.5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',

        "drive":        '<path d="M12 16.5V9.75m0 0l3 3m-3-3l-3 3M6.75 19.5a4.5 4.5 0 01-1.41-8.775 5.25 5.25 0 0110.233-2.33 3 3 0 013.758 3.848A3.752 3.752 0 0118 19.5H6.75z" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
        "home":         '<path d="M2.25 12l8.954-8.955a1.126 1.126 0 011.591 0L21.75 12M4.5 9.75v10.125c0 .621.504 1.125 1.125 1.125H9.75v-4.875c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125V21h4.125c.621 0 1.125-.504 1.125-1.125V9.75M8.25 21h8.25" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
        "search":       '<path d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 15.803 7.5 7.5 0 0015.803 15.803z" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
        "menu":         '<path d="M3.75 6.75h16.5M3.75 12h16.5m-16.5 5.25h16.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
        "back":         '<path d="M10.5 19.5L3 12m0 0l7.5-7.5M3 12h18" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
        "chevron_right":'<path d="M8.25 4.5l7.5 7.5-7.5 7.5" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>',
        "plus":         '<path d="M12 4.5v15m7.5-7.5h-15" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
        "close":        '<path d="M6 18L18 6M6 6l12 12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
        "refresh":      '<path d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',

        "upload":       '<path d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
        "download":     '<path d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
        "delete":       '<path d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 01-2.244 2.077H8.084a2.25 2.25 0 01-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 00-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 013.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 00-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 00-7.5 0" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
        "rename":       '<path d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L6.832 19.82a4.5 4.5 0 01-1.897 1.13l-2.685.8.8-2.685a4.5 4.5 0 011.13-1.897L16.863 4.487zm0 0L19.5 7.125" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
        "move":         '<path d="M7.5 21L3 16.5m0 0L7.5 12M3 16.5h13.5m0-13.5L21 7.5m0 0L16.5 12M21 7.5H7.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
        "copy":         '<path d="M15.75 17.25v3.375c0 .621-.504 1.125-1.125 1.125h-9.75a1.125 1.125 0 01-1.125-1.125V7.875c0-.621.504-1.125 1.125-1.125H6.75a9.06 9.06 0 011.5.124m7.5 10.376h3.375c.621 0 1.125-.504 1.125-1.125V11.25c0-4.46-3.243-8.161-7.5-8.876a9.06 9.06 0 00-1.5-.124H9.375c-.621 0-1.125.504-1.125 1.125v3.5m7.5 10.375H9.375a1.125 1.125 0 01-1.125-1.125v-9.25m12 6.625v-1.875a3.375 3.375 0 00-3.375-3.375h-1.5a1.125 1.125 0 01-1.125-1.125v-1.5a3.375 3.375 0 00-3.375-3.375H9.75" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
        "newfolder":    '<path d="M12 10.5v6m3-3H9m4.06-7.19l-2.12-2.12a1.5 1.5 0 00-1.061-.44H4.5A2.25 2.25 0 002.25 6v12a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18V9a2.25 2.25 0 00-2.25-2.25h-5.379a1.5 1.5 0 01-1.06-.44z" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
        "signout":      '<path d="M15.75 9V5.25A2.25 2.25 0 0013.5 3h-6a2.25 2.25 0 00-2.25 2.25v13.5A2.25 2.25 0 007.5 21h6a2.25 2.25 0 002.25-2.25V15M12 9l-3 3m0 0l3 3m-3-3h12.75" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
        "add_account":  '<path d="M19 7.5v3m0 0v3m0-3h3m-3 0h-3m-2.25-4.125a3.375 3.375 0 11-6.75 0 3.375 3.375 0 016.75 0zM4 19.235v-.11a6.375 6.375 0 0112.75 0v.109A12.318 12.318 0 0110.374 21c-2.331 0-4.512-.645-6.374-1.766z" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',

        "check":        '<path d="M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
        "close_circle": '<path d="M9.75 9.75l4.5 4.5m0-4.5l-4.5 4.5M21 12a9 9 0 11-18 0 9 9 0 0118 0z" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
        "alert":        '<path d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
        "shield_check": '<path d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
        "login_arrow":  '<path d="M15.75 9V5.25A2.25 2.25 0 0013.5 3h-6a2.25 2.25 0 00-2.25 2.25v13.5A2.25 2.25 0 007.5 21h6a2.25 2.25 0 002.25-2.25V15m3 0l3-3m0 0l-3-3m3 3H9" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
        "lock":         '<path d="M16.5 10.5V6.75a4.5 4.5 0 10-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 002.25-2.25v-6.75a2.25 2.25 0 00-2.25-2.25H6.75a2.25 2.25 0 00-2.25 2.25v6.75a2.25 2.25 0 002.25 2.25z" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
        "link":         '<path d="M13.19 8.688a4.5 4.5 0 011.242 7.244l-4.5 4.5a4.5 4.5 0 01-6.364-6.364l1.757-1.757m13.35-.622l1.757-1.757a4.5 4.5 0 00-6.364-6.364l-4.5 4.5a4.5 4.5 0 001.242 7.244" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
    }
    path = icons.get(name, icons["file"])
    svg = f'<svg{cls_attr} width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">{path}</svg>'
    if bg_color:
        return f'<div class="icon-bg" style="background-color:{bg_color}">{svg}</div>'
    return svg

def _file_icon(mime: str, size: int = 20) -> str:
    if not mime: return _icon("file", size, "", "#607d8b")  # Gray for unknown
    if "folder" in mime: return _icon("folder", size, "", "#0386c3")  # Blue
    if "image" in mime: return _icon("image", size, "", "#a78bfa")  # Purple (no background for images)
    if "video" in mime: return _icon("video", size, "", "#f87171")  # Red (no background for videos)
    if "audio" in mime: return _icon("audio", size, "", "#e55835")  # Orange-red
    if "pdf" in mime: return _icon("pdf", size, "", "#e53e3e")  # Red (no background)
    if "zip" in mime or "tar" in mime or "rar" in mime or "7z" in mime: return _icon("zip", size, "", "#795547")  # Brown
    if "word" in mime or "document" in mime: return _icon("doc", size, "", "#3f51b5")  # Deep blue
    if "sheet" in mime or "excel" in mime: return _icon("sheet", size, "", "#38a169")  # Green (no background)
    if "presentation" in mime or "powerpoint" in mime: return _icon("sheet", size, "", "#38a169")  # Green (no background)
    if "javascript" in mime or "json" in mime or "python" in mime or "html" in mime or "css" in mime: return _icon("code", size, "", "#60a5fa")  # Blue (no background)
    return _icon("file", size, "", "#607d8b")  # Gray for unknown

_CSS = """
:root {
  --bg: #000000;
  --header: #171717;
  --surface: #1a1a1a;
  --surface2: #222222;
  --surface3: #2a2a2a;
  --border: #2c2c2c;
  --border2: #3a3a3a;
  --accent: #0483c3;
  --accent2: #0369a1;
  --accent-dim: rgba(4,131,195,.13);
  --green: #22c55e;
  --red: #ef4444;
  --yellow: #f59e0b;
  --fab: #ffb200;
  --fab2: #e6a000;
  --text: #f0f0f0;
  --text2: #a0a0a0;
  --text3: #555;
  --folder: #0483c3;
  --r4: 4px;
  --r8: 8px;
  --r12: 12px;
  --r16: 16px;
  --sans: 'Google Sans', 'Roboto', 'Segoe UI', system-ui, -apple-system, sans-serif;
  --shadow: 0 8px 32px rgba(0,0,0,.6);
}
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html { height: 100%; -webkit-text-size-adjust: 100%; }
body {
  background: var(--bg); color: var(--text); font-family: var(--sans);
  font-size: 15px; height: 100%; -webkit-font-smoothing: antialiased;
  overflow-x: hidden;
}
a { color: var(--accent); text-decoration: none; }

::-webkit-scrollbar { width: 4px; height: 4px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--border2); border-radius: 2px; }

/* Icon colors - now using circular backgrounds instead */
.ico-folder { color: white; }
.ico-image  { color: white; }
.ico-video  { color: white; }
.ico-audio  { color: white; }
.ico-zip    { color: white; }
.ico-code   { color: white; }

/* Progress bar */
#bar {
  position: fixed; top: 0; left: 0; right: 0; height: 3px;
  background: var(--accent); transform: scaleX(0); transform-origin: left;
  transition: transform .4s; z-index: 9999; opacity: 0;
}
#bar.on  { transform: scaleX(.7); opacity: 1; }
#bar.done { transform: scaleX(1); opacity: 0; transition: transform .3s, opacity .4s .2s; }

/* Toast */
#toast {
  position: fixed; bottom: 90px; left: 50%; transform: translateX(-50%) translateY(20px);
  background: var(--surface2); border: 1px solid var(--border2);
  color: var(--text); border-radius: 24px; padding: 12px 22px;
  font-size: 14px; z-index: 9999; opacity: 0; pointer-events: none;
  transition: all .22s ease; white-space: nowrap;
  box-shadow: var(--shadow);
}
#toast.show { transform: translateX(-50%) translateY(0); opacity: 1; }
#toast.ok   { border-color: var(--green); color: var(--green); }
#toast.err  { border-color: var(--red);   color: var(--red);   }
#toast.warn { border-color: var(--yellow); color: var(--yellow); }

/* Buttons */
.btn {
  display: inline-flex; align-items: center; gap: 8px; border: none;
  border-radius: var(--r8); cursor: pointer; font-size: 14px; font-weight: 500;
  font-family: var(--sans); transition: all .15s; white-space: nowrap;
  padding: 0 16px; height: 40px;
}
.btn:active { transform: scale(.97); }
.btn-primary { background: var(--accent); color: #fff; }
.btn-primary:hover { background: var(--accent2); }
.btn-ghost { background: transparent; color: var(--text2); border: 1px solid var(--border2); }
.btn-ghost:hover { background: var(--surface3); color: var(--text); }
.btn-danger { background: rgba(239,68,68,.15); color: var(--red); border: 1px solid rgba(239,68,68,.3); }
.btn-danger:hover { background: rgba(239,68,68,.25); }
.btn-icon {
  width: 40px; height: 40px; padding: 0; border-radius: var(--r8);
  background: transparent; color: var(--text2); border: none;
  justify-content: center; display: inline-flex; align-items: center;
}
.btn-icon:hover { background: var(--surface3); color: var(--text); }
.btn-sm { height: 34px; padding: 0 13px; font-size: 13px; }
.btn-wide { width: 100%; justify-content: center; }

/* Inputs */
input, select {
  background: var(--surface3); color: var(--text); border: 1px solid var(--border);
  border-radius: var(--r8); padding: 11px 14px; font-size: 15px;
  font-family: var(--sans); width: 100%; outline: none;
  transition: border-color .15s, box-shadow .15s;
}
input:focus, select:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-dim); }
input::placeholder { color: var(--text3); }

/* Nav / Header */
nav {
  background: #171717;
  padding: 0 14px; display: flex; align-items: center; gap: 10px;
  height: 58px; position: sticky; top: 0; z-index: 200;
}
.nav-logo {
  display: flex; align-items: center;
  text-decoration: none; flex-shrink: 0;
}
.nav-logo-icon { color: var(--accent); }
.nav-logo-img {
  height: 36px; width: 36px; object-fit: contain;
  display: block; border-radius: 8px;
}
.nav-center { flex: 1; display: flex; justify-content: center; padding: 0 8px; }
.nav-search-wrap {
  display: flex; align-items: center; gap: 8px;
  background: rgba(255,255,255,0.07);
  border: 1px solid rgba(255,255,255,0.1);
  border-radius: 24px; padding: 0 14px; height: 38px;
  width: 100%; max-width: 420px;
  transition: background .18s, border-color .18s, box-shadow .18s;
}
.nav-search-wrap:focus-within {
  background: rgba(255,255,255,0.11);
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-dim);
}
.nav-search-wrap svg { color: var(--text3); flex-shrink: 0; }
#nav-search-input {
  background: transparent; border: none; outline: none;
  color: var(--text); font-size: 14px; font-family: var(--sans);
  width: 100%; padding: 0; box-shadow: none;
}
#nav-search-input::placeholder { color: var(--text3); }
.nav-avatar {
  width: 36px; height: 36px; border-radius: 50%;
  background: var(--accent); color: #fff; display: flex;
  align-items: center; justify-content: center; font-size: 15px;
  font-weight: 700; flex-shrink: 0; overflow: hidden; cursor: pointer;
}
.nav-avatar img { width: 100%; height: 100%; object-fit: cover; }

/* Avatar popup */
#av-popup {
  display: none; position: fixed; top: 62px; right: 12px; z-index: 900;
  background: rgba(26,26,26,0.82);
  backdrop-filter: blur(24px) saturate(160%);
  -webkit-backdrop-filter: blur(24px) saturate(160%);
  border: 1px solid rgba(255,255,255,0.09);
  border-radius: var(--r16); padding: 20px; width: 290px;
  box-shadow: 0 12px 48px rgba(0,0,0,.7);
  animation: mIn .18s ease;
}
#av-popup.open { display: block; }
.avp-head {
  display: flex; align-items: center; gap: 14px; margin-bottom: 18px;
}
.avp-avatar {
  width: 52px; height: 52px; border-radius: 50%; overflow: hidden; flex-shrink: 0;
  background: var(--accent); display: flex; align-items: center;
  justify-content: center; font-size: 20px; font-weight: 700; color: #fff;
}
.avp-avatar img { width: 100%; height: 100%; object-fit: cover; }
.avp-email { font-size: 13px; font-weight: 600; color: var(--text); word-break: break-all; }
.avp-label { font-size: 11px; color: var(--text3); margin-top: 2px; }
.avp-storage { margin-top: 4px; }
.avp-bar-wrap {
  height: 6px; background: var(--surface3); border-radius: 99px;
  overflow: hidden; margin: 8px 0 6px;
}
.avp-bar-fill {
  height: 100%; border-radius: 99px;
  background: linear-gradient(90deg, var(--accent), #06b6d4);
  transition: width .4s ease;
}
.avp-bar-fill.warn { background: linear-gradient(90deg, #f59e0b, #ef4444); }
.avp-storage-txt { font-size: 12px; color: var(--text3); }
.avp-storage-txt strong { color: var(--text2); }
.avp-sk { height: 10px; border-radius: 99px; background: var(--surface3);
  animation: skshimmer 1.4s infinite linear;
  background: linear-gradient(90deg,var(--surface2) 25%,var(--surface3) 50%,var(--surface2) 75%);
  background-size: 200%;
}

/* Avatar popup — drives accordion */
.avp-drives-toggle {
  display: flex; align-items: center; justify-content: space-between;
  cursor: pointer; user-select: none; -webkit-user-select: none;
  padding: 3px 0; border-radius: var(--r4);
  transition: color .15s;
}
.avp-drives-toggle:hover { color: var(--text); }
.avp-drives-label {
  font-size: 11px; font-weight: 700; letter-spacing: .08em;
  text-transform: uppercase; color: var(--text3);
}
.avp-drives-chevron {
  color: var(--text3); transition: transform .22s ease; flex-shrink: 0;
}
.avp-drives-chevron.open { transform: rotate(90deg); }
.avp-drives-body {
  overflow: hidden;
  max-height: 0;
  transition: max-height .28s cubic-bezier(.4,0,.2,1), opacity .22s;
  opacity: 0;
}
.avp-drives-body.open { max-height: 400px; opacity: 1; }
.avp-drive-row {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 6px; border-radius: var(--r8); cursor: pointer;
  transition: background .1s; text-decoration: none; color: var(--text);
}
.avp-drive-row:hover { background: var(--surface3); }
.avp-drive-row.current { background: var(--accent-dim); color: var(--accent); pointer-events: none; }
.avp-drive-av {
  width: 28px; height: 28px; border-radius: 50%; overflow: hidden;
  background: var(--accent); display: flex; align-items: center;
  justify-content: center; font-size: 11px; font-weight: 700; color: #fff;
  flex-shrink: 0;
}
.avp-drive-av img { width: 100%; height: 100%; object-fit: cover; }
.avp-drive-info { flex: 1; min-width: 0; }
.avp-drive-email {
  font-size: 13px; font-weight: 500; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis;
}
.avp-drive-sub { font-size: 11px; color: var(--text3); margin-top: 1px; }

/* Toolbar / Breadcrumb bar */
.toolbar {
  display: flex; align-items: center; gap: 8px; padding: 10px 14px;
  background: #171717;
  flex-shrink: 0; min-height: 52px;
}
.breadcrumb { display: flex; align-items: center; gap: 2px; flex: 1; min-width: 0; overflow-x: auto; overflow-y: hidden; scroll-behavior: smooth; scrollbar-width: none; -ms-overflow-style: none; }
.breadcrumb::-webkit-scrollbar { display: none; }
.bc-crumb {
  color: var(--text2); cursor: pointer; padding: 5px 8px; border-radius: var(--r4);
  font-size: 15px; text-transform: uppercase; white-space: nowrap;
  transition: all .1s; flex-shrink: 0;
}
.bc-crumb:hover { background: var(--surface2); color: var(--text); }
.bc-crumb.last { color: var(--accent); cursor: default; font-weight: 600; }
.bc-crumb.last:hover { background: transparent; }
.bc-sep { color: var(--text3); flex-shrink: 0; font-size: 25px; }

/* Layout */
.layout { display: flex; height: calc(100vh - 58px); overflow: hidden; }

/* Sidebar */
.sidebar {
  width: 230px; flex-shrink: 0; background: var(--header);
  border-right: 1px solid var(--border); padding: 12px 8px;
  display: flex; flex-direction: column; gap: 2px; overflow-y: auto;
}
.sb-item {
  display: flex; align-items: center; gap: 10px; padding: 11px 12px;
  border-radius: var(--r8); cursor: pointer; font-size: 15px; font-weight: 500;
  color: var(--text2); transition: all .12s; border: 1px solid transparent;
}
.sb-item:hover { background: var(--surface2); color: var(--text); }
.sb-item.active { background: var(--accent-dim); color: var(--accent); border-color: rgba(4,131,195,.2); }
.sb-item svg { flex-shrink: 0; }
.sb-divider { height: 1px; background: var(--border); margin: 8px 4px; }
.sb-label {
  font-size: 11px; font-weight: 700; letter-spacing: .1em; text-transform: uppercase;
  color: var(--text3); padding: 8px 12px 3px;
}
.drive-tab {
  display: flex; align-items: center; gap: 10px; padding: 9px 12px;
  border-radius: var(--r8); cursor: pointer; font-size: 13px;
  color: var(--text2); transition: all .12s; border: 1px solid transparent;
}
.drive-tab:hover { background: var(--surface2); color: var(--text); }
.drive-tab.active { background: var(--accent-dim); color: var(--accent); }
.drive-av {
  width: 26px; height: 26px; border-radius: 50%; overflow: hidden; flex-shrink: 0;
  background: var(--accent); display: flex; align-items: center; justify-content: center;
  font-size: 10px; font-weight: 700; color: #fff;
}
.drive-av img { width: 100%; height: 100%; object-fit: cover; }
.drive-email { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

/* Main area */
.main { flex: 1; display: flex; flex-direction: column; overflow: hidden; min-width: 0; }

/* File list */
.file-area { flex: 1; overflow-y: auto; padding-bottom: 100px; }

/* File item row — mobile-first card style */
.file-item {
  display: flex; align-items: center; gap: 16px;
  padding: 10px 12px; border-bottom: 0.9px solid rgba(44,44,44,.6);
  cursor: pointer; transition: background .08s; position: relative;
  user-select: none; -webkit-user-select: none;
}
.file-item:active { background: var(--surface2); }
.file-item.sel { background: rgba(4,131,195,.1); }
.file-item.sel .fi-cb { display: flex; }

/* Checkbox — hidden by default, shown in select mode or when item is selected */
.fi-cb {
  display: none; width: 20px; height: 20px; flex-shrink: 0;
  align-items: center; justify-content: center;
}
body.select-mode .fi-cb { display: flex; }

/* Custom circular checkbox */
.custom-cb {
  width: 20px; height: 20px; border-radius: 50%;
  border: 2px solid var(--border2);
  background: transparent;
  display: flex; align-items: center; justify-content: center;
  cursor: pointer; transition: all .18s ease;
  flex-shrink: 0; position: relative;
}
.custom-cb::after {
  content: '';
  width: 0; height: 0;
  border-radius: 50%;
  background: var(--accent);
  transition: all .15s ease;
  position: absolute;
}
.custom-cb.checked {
  background: var(--accent);
  border-color: var(--accent);
  box-shadow: 0 2px 8px rgba(4,131,195,.4);
}
.custom-cb.checked::after {
  content: '';
  width: 7px; height: 4px;
  background: transparent;
  border-radius: 0;
  border-left: 2px solid #fff;
  border-bottom: 2px solid #fff;
  transform: rotate(-45deg) translateY(-1px);
  position: static;
}
.custom-cb:not(.checked):hover {
  border-color: var(--accent);
  background: var(--accent-dim);
}

/* Search result checkbox */
.sri-cb {
  display: none; width: 20px; height: 20px; flex-shrink: 0;
  align-items: center; justify-content: center;
}
.sri.sri-sel .sri-cb { display: flex; }
body.select-mode .sri-cb { display: flex; }

/* File icon */
.fi-icon { flex-shrink: 0; display: flex; align-items: center; justify-content: center; width: 46px; height: 46px; }

/* Circular icon background (Android Material Design style) */
.icon-bg {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 46px;
  height: 46px;
  border-radius: 50%;
  flex-shrink: 0;
  box-shadow: 0 2px 8px rgba(0,0,0,.3);
}

.icon-bg svg {
  width: 24px;
  height: 24px;
  color: white;
  stroke: white;
}

/* File info */
.fi-info { flex: 1; min-width: 0; overflow: hidden; }
.fi-name {
  font-size: 16px; font-weight: 400; color: var(--text);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  line-height: 1.3;
}
.fi-name.fol { color: var(--text); font-weight: 400; }
.fi-meta {
  display: flex; justify-content: space-between; align-items: center;
  margin-top: 3px; width: 100%;
}
.fi-size { font-size: 12px; color: var(--text3); }
.fi-date { font-size: 12px; color: var(--text3); margin-left: auto; padding-left: 8px; white-space: nowrap; }

/* Per-item action button (download) — absolutely positioned, never takes flex space */
.fi-act {
  position: absolute; right: 10px; top: 50%; transform: translateY(-50%);
  opacity: 0; transition: opacity .15s; pointer-events: none;
}
@media (hover: hover) {
  .file-item:hover .fi-act { opacity: 1; pointer-events: auto; }
}

/* Empty state */
.empty {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  padding: 80px 24px; gap: 16px; color: var(--text3);
}
.empty-icon { color: var(--text3); opacity: .35; }
.empty h3 { font-size: 20px; font-weight: 600; color: var(--text2); }
.empty p { font-size: 14px; }

/* Skeleton */
.sk {
  background: linear-gradient(90deg, var(--surface) 25%, var(--surface3) 50%, var(--surface) 75%);
  background-size: 200% 100%; animation: sk 1.4s infinite; border-radius: var(--r4);
}
@keyframes sk { 0% { background-position: 200% 0 } 100% { background-position: -200% 0 } }
.sk-n { height: 15px; width: 55%; }
.sk-s { height: 12px; width: 30%; }
.sk-i { height: 28px; width: 28px; border-radius: var(--r4); flex-shrink: 0; }

/* Accent color swatches */
.ac-sw { transition: transform .15s, box-shadow .15s, border .15s; }
.ac-sw:hover { transform: scale(1.18); }
.ac-sw:active { transform: scale(.93); }
.ac-sw-on { transform: scale(1.1); }

/* Scroll sentinel spinner */
#scroll-spinner { padding: 16px; justify-content: center; }

/* Floating Action Button */
#fab {
  position: fixed; bottom: 24px; right: 20px; z-index: 400;
  display: flex; flex-direction: column; align-items: flex-end; gap: 12px;
  transition: bottom .3s ease;
}
body.select-mode #fab { bottom: 96px; }
.fab-main {
  width: 58px; height: 58px; border-radius: 50%;
  background: var(--fab); color: #fff; border: none;
  display: flex; align-items: center; justify-content: center;
  cursor: pointer; box-shadow: 0 4px 20px rgba(255,178,0,.4);
  transition: all .2s; font-size: 28px; font-weight: 300;
  flex-shrink: 0;
}
.fab-main:hover { background: var(--fab2); transform: scale(1.06); }
.fab-main:active { transform: scale(.96); }
.fab-main.open { transform: rotate(45deg); }
.fab-main.open:hover { transform: rotate(45deg) scale(1.06); }
.fab-options {
  display: flex; flex-direction: column; align-items: flex-end; gap: 10px;
  transform-origin: bottom right;
  animation: fabIn .18s ease;
}
@keyframes fabIn { from { opacity: 0; transform: scale(.85) translateY(10px); } }
.fab-opt {
  display: flex; align-items: center; gap: 10px;
  background: var(--surface2); border: 1px solid var(--border2);
  border-radius: 28px; padding: 10px 18px 10px 14px;
  cursor: pointer; font-size: 14px; font-weight: 600; color: var(--text);
  box-shadow: 0 4px 16px rgba(0,0,0,.5); transition: all .15s; white-space: nowrap;
}
.fab-opt:hover { background: var(--surface3); border-color: var(--accent); color: var(--accent); }
.fab-opt svg { flex-shrink: 0; }

/* Selection / bulk action bar */
#selbar {
  position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%) translateY(100px);
  background: var(--surface2); border: 1px solid var(--border2);
  border-radius: 36px; padding: 10px 14px; display: flex; align-items: center; gap: 6px;
  box-shadow: 0 8px 40px rgba(0,0,0,.7); z-index: 500; opacity: 0; pointer-events: none;
  transition: transform .28s cubic-bezier(.34,1.56,.64,1), opacity .18s;
  max-width: calc(100vw - 32px);
}
#selbar.show { transform: translateX(-50%) translateY(0); opacity: 1; pointer-events: auto; }
#selbar.above-modal { z-index: 1100; }
#selcnt { font-size: 13px; color: var(--text2); padding: 0 4px; white-space: nowrap; }
.selbar-sep { width: 1px; height: 22px; background: var(--border2); margin: 0 2px; flex-shrink: 0; }
.sel-btn {
  display: flex; flex-direction: column; align-items: center; gap: 2px;
  background: transparent; border: none; cursor: pointer; padding: 6px 8px;
  border-radius: var(--r8); color: var(--text2); transition: all .12s; flex-shrink: 0;
}
.sel-btn:hover { background: var(--surface3); color: var(--text); }
.sel-btn.danger { color: var(--red); }
.sel-btn.danger:hover { background: rgba(239,68,68,.15); }
.sel-btn span { font-size: 10px; font-weight: 600; white-space: nowrap; }
.sel-close {
  width: 30px; height: 30px; border-radius: 50%; background: var(--surface3);
  border: none; cursor: pointer; display: flex; align-items: center; justify-content: center;
  color: var(--text3); flex-shrink: 0; margin-left: 2px;
}
.sel-close:hover { color: var(--text); background: var(--border2); }

/* Modal */
.moverlay {
  display: none; position: fixed; inset: 0;
  background: rgba(0,0,0,.75); backdrop-filter: blur(6px);
  z-index: 1000; align-items: center; justify-content: center;
  padding: 16px;
}
.moverlay.open { display: flex; }
.modal {
  background: var(--surface); border: 1px solid var(--border2);
  border-radius: var(--r16); padding: 22px 20px; width: 100%;
  max-width: 420px; box-shadow: var(--shadow); animation: mIn .2s ease;
  max-height: 88vh; overflow-y: auto;
}
@keyframes mIn { from { transform: scale(.95) translateY(-8px); opacity: 0; } }

/* ── Preview overlay ── */
#preview-overlay {
  display: none; position: fixed; inset: 0; z-index: 2000;
  background: #000; flex-direction: column;
}
#preview-overlay.open { display: flex; }
#preview-bar {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 14px; background: rgba(0,0,0,0.85); backdrop-filter: blur(12px);
  border-bottom: 1px solid rgba(255,255,255,0.07);
  flex-shrink: 0; min-height: 52px; position: relative; z-index: 10;
}
#preview-title {
  flex: 1; min-width: 0; font-size: 14px; font-weight: 500; color: var(--text);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
#preview-dl-btn {
  width: 36px; height: 36px; border-radius: 50%; border: none; cursor: pointer;
  background: rgba(255,255,255,0.1); color: var(--text);
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0; transition: background .15s;
}
#preview-dl-btn:hover { background: var(--accent); color: #fff; }
#preview-copy-btn {
  width: 36px; height: 36px; border-radius: 50%; border: none; cursor: pointer;
  background: rgba(255,255,255,0.1); color: var(--text);
  display: none; align-items: center; justify-content: center;
  flex-shrink: 0; transition: background .15s;
}
#preview-copy-btn:hover { background: var(--accent); color: #fff; }
#preview-copy-btn.visible { display: flex; }
#preview-close-btn {
  width: 36px; height: 36px; border-radius: 50%; border: none; cursor: pointer;
  background: rgba(255,255,255,0.08); color: var(--text2);
  display: flex; align-items: center; justify-content: center; flex-shrink: 0;
  transition: background .15s, color .15s;
}
#preview-close-btn:hover { background: rgba(239,68,68,.25); color: #ef4444; }
#preview-body {
  flex: 1; overflow: auto; display: flex; align-items: center; justify-content: center;
  padding: 0; position: relative; background: #000;
}
#preview-body iframe {
  width: 100%; height: 100%; border: none; background: #fff;
}
#preview-body pre {
  margin: 0; padding: 24px; font-family: 'Courier New', monospace; font-size: 13px;
  line-height: 1.6; color: var(--text); white-space: pre-wrap; word-break: break-all;
  max-width: 900px; width: 100%; align-self: flex-start;
}
#preview-spinner {
  position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
  background: #000; z-index: 5;
}
#preview-spinner svg { animation: spin 1s linear infinite; color: var(--accent); }
@keyframes spin { to { transform: rotate(360deg); } }

/* ── Unsupported preview ── */
#preview-unsupported {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  text-align: center; padding: 48px 32px; gap: 0; min-height: 320px;
}
.pu-icon-wrap {
  width: 96px; height: 96px; border-radius: 50%;
  background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1);
  display: flex; align-items: center; justify-content: center;
  margin-bottom: 24px; opacity: .75;
}
#preview-unsupported h3 {
  font-size: 20px; font-weight: 700; color: var(--text); margin-bottom: 10px;
}
#preview-unsupported p {
  font-size: 14px; color: var(--text3); margin-bottom: 28px; max-width: 340px; line-height: 1.6;
}
.pu-dl-btn {
  display: inline-flex; align-items: center; gap: 10px;
  padding: 13px 28px; border-radius: 50px; border: none; cursor: pointer;
  background: var(--accent); color: #fff; font-size: 15px; font-weight: 600;
  font-family: var(--sans); transition: all .2s; box-shadow: 0 4px 20px rgba(4,131,195,.4);
}
.pu-dl-btn:hover { filter: brightness(1.12); transform: translateY(-1px); box-shadow: 0 6px 28px rgba(4,131,195,.5); }
.pu-dl-btn:active { transform: translateY(0); }

/* ── Image Viewer ── */
#img-viewer {
  position: relative; width: 100%; height: 100%;
  display: flex; align-items: center; justify-content: center; overflow: hidden;
}
#img-viewer img {
  max-width: 100%; max-height: 100%; object-fit: contain;
  user-select: none; -webkit-user-select: none; transition: opacity .25s;
}
.img-nav-btn {
  position: absolute; top: 50%; transform: translateY(-50%);
  width: 48px; height: 48px; border-radius: 50%; border: none; cursor: pointer;
  background: rgba(0,0,0,0.55); backdrop-filter: blur(8px);
  border: 1px solid rgba(255,255,255,0.12);
  color: #fff; display: flex; align-items: center; justify-content: center;
  z-index: 10; transition: all .18s; opacity: 0;
}
#img-viewer:hover .img-nav-btn { opacity: 1; }
.img-nav-btn:hover { background: rgba(4,131,195,0.7); border-color: var(--accent); transform: translateY(-50%) scale(1.1); }
.img-nav-btn.prev { left: 14px; }
.img-nav-btn.next { right: 14px; }
.img-nav-btn:disabled { opacity: 0 !important; pointer-events: none; }
.img-counter {
  position: absolute; bottom: 16px; left: 50%; transform: translateX(-50%);
  background: rgba(0,0,0,0.6); backdrop-filter: blur(8px);
  border: 1px solid rgba(255,255,255,0.1);
  color: var(--text2); font-size: 12px; font-weight: 500;
  padding: 5px 14px; border-radius: 20px; pointer-events: none; white-space: nowrap;
}

/* ── YouTube-style Video Player ── */
#video-player-wrap {
  position: relative; width: 100%; height: 100%;
  display: flex; flex-direction: column;
  background: #000; overflow: hidden;
}
#video-player-wrap video {
  flex: 1; width: 100%; min-height: 0; display: block;
}
#vid-nav-overlay {
  position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
  pointer-events: none; z-index: 3;
}
/* YouTube-style center play/pause flash — no full-screen flash */
#vid-play-flash {
  width: 72px; height: 72px; border-radius: 50%;
  background: rgba(0,0,0,.55); display: flex; align-items: center; justify-content: center;
  opacity: 0; pointer-events: none;
}
#vid-play-flash.flash {
  animation: vidFlashAnim 0.45s ease forwards;
}
@keyframes vidFlashAnim {
  0%   { opacity: 1; transform: scale(1); }
  60%  { opacity: 1; transform: scale(1.15); }
  100% { opacity: 0; transform: scale(1.25); }
}
/* YouTube-style overlay button group (prev | play/pause | next) */
#vid-overlay-center {
  position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);
  z-index: 4; pointer-events: auto;
  display: flex; align-items: center; justify-content: center;
  gap: 18px;
  transition: opacity .3s;
}
.vid-overlay-btn {
  border: none; background: rgba(0,0,0,0.52); backdrop-filter: blur(4px);
  border-radius: 50%; display: flex; align-items: center; justify-content: center;
  color: #fff; cursor: pointer; opacity: 0; transition: opacity .18s, transform .15s;
  border: 1px solid rgba(255,255,255,0.18);
  flex-shrink: 0;
}
.vid-overlay-btn:active { transform: scale(.92); }
.vid-overlay-btn.sm { width: 48px; height: 48px; }
.vid-overlay-btn.lg { width: 64px; height: 64px; }
#video-player-wrap:hover .vid-overlay-btn { opacity: 1; }
/* Hide overlay when controls are hidden */
#video-player-wrap.controls-hidden #vid-overlay-center { opacity: 0; pointer-events: none; }
.vid-overlay-btn.dimmed { cursor: not-allowed; pointer-events: none; }
.vid-overlay-btn.dimmed svg { opacity: 0.25; }
/* Double-tap seek ripple */
.vid-seek-ripple {
  position: absolute; top: 50%; transform: translateY(-50%);
  pointer-events: none; z-index: 6;
  display: flex; flex-direction: column; align-items: center; gap: 4px;
}
.vid-seek-ripple.left  { left: 24px; }
.vid-seek-ripple.right { right: 24px; }
.vid-seek-ripple-icon {
  width: 56px; height: 56px; border-radius: 50%;
  background: rgba(255,255,255,0.18); display: flex; align-items: center; justify-content: center;
  animation: seekRipple .5s ease forwards;
}
.vid-seek-ripple-label {
  font-size: 11px; font-weight: 700; color: #fff; text-shadow: 0 1px 4px rgba(0,0,0,.7);
  animation: seekRipple .5s ease forwards;
}
@keyframes seekRipple {
  0%   { opacity: 1; transform: scale(1); }
  80%  { opacity: 1; transform: scale(1.1); }
  100% { opacity: 0; transform: scale(1.15); }
}

/* Gradient scrims */
#video-player-wrap::after {
  content: ''; position: absolute; bottom: 0; left: 0; right: 0; height: 120px;
  background: linear-gradient(transparent, rgba(0,0,0,0.7));
  pointer-events: none; z-index: 4; transition: opacity .3s;
}
#video-player-wrap.controls-hidden::after { opacity: 0; }

#vid-controls {
  position: absolute; bottom: 0; left: 0; right: 0; z-index: 5;
  padding: 0 16px 14px; transition: opacity .3s, transform .3s;
  background: linear-gradient(transparent, rgba(0,0,0,.75));
}
#video-player-wrap.controls-hidden #vid-controls { opacity: 0; pointer-events: none; transform: translateY(8px); }

/* Progress bar */
#vid-progress-wrap {
  height: 18px; display: flex; align-items: center; cursor: pointer; margin-bottom: 8px;
  position: relative;
}
#vid-progress-track {
  width: 100%; height: 4px; background: rgba(255,255,255,0.25);
  border-radius: 4px; overflow: hidden; transition: height .18s; position: relative;
}
#vid-progress-wrap:hover #vid-progress-track { height: 6px; }
#vid-progress-buf {
  position: absolute; left: 0; top: 0; height: 100%;
  background: rgba(255,255,255,0.25); border-radius: 4px; pointer-events: none;
}
#vid-progress-fill {
  position: absolute; left: 0; top: 0; height: 100%;
  background: var(--accent); border-radius: 4px; pointer-events: none;
}
#vid-thumb {
  position: absolute; top: 50%; width: 14px; height: 14px; border-radius: 50%;
  background: #fff; transform: translate(-50%, -50%); pointer-events: none;
  box-shadow: 0 1px 6px rgba(0,0,0,.5); opacity: 0; transition: opacity .18s;
}
#vid-progress-wrap:hover #vid-thumb { opacity: 1; }

/* Bottom controls row */
#vid-controls-row {
  display: flex; align-items: center; gap: 10px;
}
.vid-btn {
  width: 36px; height: 36px; border: none; background: transparent; color: #fff;
  cursor: pointer; display: flex; align-items: center; justify-content: center;
  border-radius: 50%; flex-shrink: 0; transition: background .15s;
}
.vid-btn:hover { background: rgba(255,255,255,0.15); }
#vid-time {
  font-size: 13px; color: rgba(255,255,255,.9); font-weight: 500;
  white-space: nowrap; flex-shrink: 0; min-width: 90px;
}
#vid-title-bar {
  flex: 1; min-width: 0; font-size: 13px; color: rgba(255,255,255,.7);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
#vid-vol-wrap {
  display: flex; align-items: center; gap: 6px;
}
.vid-counter-badge {
  font-size: 11px; color: rgba(255,255,255,.55); padding: 3px 8px;
  background: rgba(255,255,255,0.08); border-radius: 12px; white-space: nowrap;
}

/* ── Audio Player / Playlist ── */
#audio-player-wrap {
  width: 100%; height: 100%; display: flex; flex-direction: column;
  background: linear-gradient(160deg, #0d1117 0%, #111 60%, #0d1821 100%);
  overflow: hidden;
}
#audio-now-playing {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  padding: 28px 24px 20px; flex-shrink: 0; gap: 16px;
}
#audio-art {
  width: 120px; height: 120px; border-radius: 50%;
  background: linear-gradient(135deg, var(--accent), #1a3a5c);
  display: flex; align-items: center; justify-content: center;
  box-shadow: 0 8px 40px rgba(4,131,195,.35);
  animation: audioSpin 8s linear infinite paused;
}
#audio-art.playing { animation-play-state: running; }
@keyframes audioSpin { to { transform: rotate(360deg); } }
#audio-now-title {
  font-size: 16px; font-weight: 700; color: var(--text);
  text-align: center; max-width: 300px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
#audio-now-sub { font-size: 12px; color: var(--text3); }

/* Audio scrubber */
#audio-scrubber-wrap { padding: 0 24px; flex-shrink: 0; }
#audio-progress-wrap {
  height: 20px; display: flex; align-items: center; cursor: pointer;
  position: relative;
}
#audio-progress-track {
  width: 100%; height: 3px; background: rgba(255,255,255,0.15);
  border-radius: 3px; overflow: visible; position: relative; transition: height .18s;
}
#audio-progress-wrap:hover #audio-progress-track { height: 5px; }
#audio-progress-fill {
  position: absolute; left: 0; top: 0; height: 100%;
  background: var(--accent); border-radius: 3px; pointer-events: none;
}
#audio-thumb {
  position: absolute; top: 50%; width: 12px; height: 12px; border-radius: 50%;
  background: #fff; transform: translate(-50%,-50%);
  opacity: 0; pointer-events: none; transition: opacity .18s;
}
#audio-progress-wrap:hover #audio-thumb { opacity: 1; }
#audio-times {
  display: flex; justify-content: space-between; font-size: 11px; color: var(--text3);
  margin-top: 6px; padding: 0 2px;
}

/* Audio controls row */
#audio-controls {
  display: flex; align-items: center; justify-content: center;
  gap: 6px; padding: 12px 24px 8px; flex-shrink: 0;
}
.aud-btn {
  width: 40px; height: 40px; border: none; background: transparent; color: var(--text2);
  cursor: pointer; display: flex; align-items: center; justify-content: center;
  border-radius: 50%; transition: all .15s;
}
.aud-btn:hover { color: var(--text); background: rgba(255,255,255,.08); }
#aud-play-btn {
  width: 54px; height: 54px; border-radius: 50%;
  background: var(--accent); color: #fff; border: none; cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  box-shadow: 0 4px 20px rgba(4,131,195,.45); transition: all .18s;
}
#aud-play-btn:hover { filter: brightness(1.15); transform: scale(1.05); }

/* Playlist */
#audio-playlist-wrap {
  flex: 1; min-height: 0; display: flex; flex-direction: column;
  border-top: 1px solid rgba(255,255,255,0.07);
}
#audio-playlist-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 20px 8px; flex-shrink: 0;
  font-size: 11px; font-weight: 700; letter-spacing: .08em;
  text-transform: uppercase; color: var(--text3);
}
#audio-playlist {
  flex: 1; overflow-y: auto; padding: 0 8px 16px;
}
.apl-item {
  display: flex; align-items: center; gap: 12px;
  padding: 9px 12px; border-radius: var(--r8);
  cursor: pointer; transition: background .1s; position: relative;
}
.apl-item:hover { background: rgba(255,255,255,0.05); }
.apl-item.active { background: rgba(4,131,195,0.15); }
.apl-item.active .apl-name { color: var(--accent); }
.apl-idx { font-size: 12px; color: var(--text3); width: 22px; text-align: center; flex-shrink: 0; }
.apl-icon { flex-shrink: 0; }
.apl-name {
  flex: 1; min-width: 0; font-size: 13px; color: var(--text2);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.apl-dur { font-size: 11px; color: var(--text3); flex-shrink: 0; }

.modal-title {
  font-size: 16px; font-weight: 700; margin-bottom: 16px;
  display: flex; align-items: center; gap: 9px; color: var(--text);
}
.fg { margin-bottom: 14px; }
.fg label { display: block; font-size: 11px; color: var(--text3); margin-bottom: 5px; text-transform: uppercase; letter-spacing: .05em; font-weight: 600; }
.macts { display: flex; gap: 8px; justify-content: flex-end; margin-top: 18px; }

/* Upload dropzone */
.dropzone {
  border: 2px dashed var(--border2); border-radius: var(--r12); padding: 24px 16px;
  text-align: center; color: var(--text3); cursor: pointer; transition: all .18s; position: relative;
}
.dropzone:hover, .dropzone.dragover {
  border-color: var(--accent); color: var(--accent); background: var(--accent-dim);
}
.dropzone input[type=file] { position: absolute; inset: 0; opacity: 0; cursor: pointer; font-size: 0; }
.dz-icon { margin: 0 auto 8px; }
.dz-txt { font-size: 15px; font-weight: 600; margin-top: 2px; }
.dz-hint { font-size: 12px; margin-top: 4px; opacity: .7; }
.ulist { margin-top: 10px; max-height: 160px; overflow-y: auto; display: flex; flex-direction: column; gap: 6px; }
.uitem { padding: 9px 11px; background: var(--surface2); border-radius: var(--r8); font-size: 13px; }
.uitem-top { display: flex; align-items: center; gap: 10px; }
.uname { flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ust { color: var(--text3); white-space: nowrap; }
.ubar { height: 3px; background: var(--border); border-radius: 99px; overflow: hidden; margin-top: 7px; }
.ufill { height: 100%; background: var(--accent); border-radius: 99px; transition: width .25s; width: 0%; }

/* Folder tree */
.ftree { max-height: 220px; overflow-y: auto; border: 1px solid var(--border); border-radius: var(--r8); }
.fti {
  display: flex; align-items: center; gap: 10px; padding: 12px 14px; cursor: pointer;
  font-size: 14px; border-bottom: 1px solid rgba(44,44,44,.4); transition: background .09s;
}
.fti:last-child { border-bottom: none; }
.fti:hover { background: var(--surface2); }
.fti.sel { background: var(--accent-dim); color: var(--accent); }

/* Search results — match file manager style */
.sri {
  display: flex; align-items: center; gap: 14px; padding: 10px 12px;
  cursor: pointer; border-bottom: 0.9px solid rgba(44,44,44,.6);
  transition: background .08s; position: relative; user-select: none;
  -webkit-user-select: none;
}
.sri:last-child { border-bottom: none; }
.sri:active { background: var(--surface2); }
.sri.sri-sel { background: rgba(4,131,195,.1); }
.sri-icon { flex-shrink: 0; display: flex; align-items: center; justify-content: center; width: 46px; height: 46px; }
.sri-info { flex: 1; min-width: 0; overflow: hidden; }
.sri-name { font-size: 15px; font-weight: 400; color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; line-height: 1.3; }
.sri-meta { display: flex; justify-content: space-between; align-items: center; margin-top: 3px; }
.sri-size { font-size: 12px; color: var(--text3); }
.sri-date { font-size: 12px; color: var(--text3); margin-left: auto; padding-left: 8px; white-space: nowrap; }


/* Login */
.lp { min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 20px; }
.lcard {
  background: var(--header); border: 1px solid var(--border2);
  border-radius: var(--r16); padding: 40px 32px;
  width: 100%; max-width: 380px; box-shadow: var(--shadow);
}
.llogo { text-align: center; margin-bottom: 32px; }
.llogo-icon { color: var(--accent); margin: 0 auto 16px; }
.llogo h1 { font-size: 26px; font-weight: 800; }
.llogo p { color: var(--text2); font-size: 14px; margin-top: 6px; }
.lerr {
  background: rgba(239,68,68,.1); border: 1px solid rgba(239,68,68,.3);
  border-radius: var(--r8); padding: 12px 14px; color: var(--red);
  font-size: 14px; margin-bottom: 16px; display: flex; align-items: center; gap: 8px;
}
.lhint { font-size: 12px; color: var(--text3); text-align: center; margin-top: 20px; line-height: 1.6; }

/* Accounts page */
.pg-accounts { padding: 24px 16px; }
.pg-accounts h1 { font-size: 22px; font-weight: 800; margin-bottom: 4px; }
.pg-accounts .sub { color: var(--text2); font-size: 14px; margin-bottom: 24px; }
.agrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px,1fr)); gap: 14px; }
.acard {
  background: var(--header); border: 1px solid var(--border);
  border-radius: var(--r12); padding: 24px 16px; text-align: center;
  cursor: pointer; transition: all .18s; text-decoration: none;
  display: flex; flex-direction: column; align-items: center; gap: 12px;
}
.acard:hover { border-color: var(--accent); transform: translateY(-2px); box-shadow: 0 6px 24px rgba(0,0,0,.4); }
.acard .av {
  width: 62px; height: 62px; border-radius: 50%; overflow: hidden;
  background: var(--accent); display: flex; align-items: center;
  justify-content: center; font-size: 24px; font-weight: 700; color: #fff;
}
.acard .av img { width: 100%; height: 100%; object-fit: cover; }
.acard .ae { font-size: 13px; font-weight: 600; color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 100%; }
.acard .al { font-size: 11px; color: var(--text3); }
.add-card { border: 1px dashed var(--border2); background: transparent; }
.add-card:hover { border-color: var(--accent); background: var(--accent-dim); }
.add-card .av { background: var(--surface2); color: var(--text3); }

/* Info page */
.info-page { min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 20px; }
.info-card {
  background: var(--header); border: 1px solid var(--border2);
  border-radius: var(--r16); padding: 44px 36px; text-align: center;
  max-width: 420px; width: 100%; box-shadow: var(--shadow);
}
.info-card h2 { font-size: 22px; font-weight: 800; margin: 18px 0 10px; }
.info-card p { font-size: 14px; color: var(--text2); margin-bottom: 4px; }

/* Responsive */
@media (max-width: 640px) {
  .sidebar { display: none; }
  nav { padding: 0 10px; gap: 6px; }
  .nav-search-wrap { max-width: 100%; }
  .toolbar { padding: 8px 12px; }
  .fab-main { width: 54px; height: 54px; }
  #selbar { padding: 8px 10px; gap: 2px; }
  .sel-btn { padding: 5px 6px; }
  .sel-btn span { display: none; }
  .moverlay { padding: 12px; }
}
"""

_JS = """
function toast(msg, type='') {
  const t = document.getElementById('toast');
  t.textContent = msg; t.className = 'show ' + type;
  clearTimeout(t._t); t._t = setTimeout(() => t.className = '', 3500);
}
function bar(on) {
  const b = document.getElementById('bar');
  b.className = on ? 'on' : 'done';
  if (!on) setTimeout(() => b.className = '', 800);
}
function openModal(id) { document.getElementById('m-' + id).classList.add('open'); }
function closeModal(id) {
  document.getElementById('m-' + id).classList.remove('open');
  if (id !== 'search') {
    const sm = document.getElementById('m-search');
    if (sm && sm.classList.contains('open')) sm.style.zIndex = '';
  }
}
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.moverlay').forEach(el =>
    el.addEventListener('click', e => {
      if (e.target === el) {
        el.classList.remove('open');
        if (el.id !== 'm-search') {
          const sm = document.getElementById('m-search');
          if (sm && sm.classList.contains('open')) sm.style.zIndex = '';
        }
      }
    })
  );
  if (typeof navRoot === 'function') navRoot();
});
"""

def _page(body: str, title: str = "Drive") -> web.Response:
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>{title} — GDrive Bot</title>
<link rel="icon" type="image/png" href="/static/favicon.png">
<link rel="shortcut icon" href="/static/favicon.png">
<link rel="apple-touch-icon" href="/static/favicon.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Google+Sans:wght@400;500;700&family=Roboto:wght@400;500;700&display=swap" rel="stylesheet">
<style>{_CSS}</style>
</head><body>
<div id="bar"></div>
<div id="toast"></div>
{body}
<script>{_JS}</script>
</body></html>"""
    return web.Response(text=html, content_type="text/html")


async def handle_login(request: web.Request) -> web.Response:
    tok = request.cookies.get("session")
    if tok and _verify(tok):
        raise web.HTTPFound("/drives")

    error = ""
    if request.method == "POST":
        data = await request.post()
        pw = data.get("password", "")
        db: Database = request.app["db"]
        stored = await db.get_webui_password()
        if stored and _hash_pw(pw) == stored:
            uid = await db.get_first_user_id()
            if uid is None:
                error = "No Telegram user found. Send /start to the bot first."
            else:
                resp = web.HTTPFound("/drives")
                resp.set_cookie("session", _make_token(uid), max_age=86400*7, httponly=True, samesite="Lax")
                raise resp
        else:
            error = "Incorrect password."

    err_html = f'<div class="lerr">{_icon("alert",16)} {error}</div>' if error else ""
    return _page(f"""
<div class="lp">
  <div class="lcard">
    <div class="llogo">
      <div class="llogo-icon"><img src="/static/logo.png" style="width:80px;height:80px;object-fit:contain;border-radius:16px" alt="GDrive Bot"></div>
      <h1>GDrive Bot</h1>
      <p>Sign in to access your files</p>
    </div>
    {err_html}
    <form method="POST">
      <div class="fg">
        <label>Password</label>
        <input name="password" type="password" placeholder="Enter your password" autofocus autocomplete="current-password">
      </div>
      <button type="submit" class="btn btn-primary btn-wide" style="height:46px;margin-top:8px;font-size:16px">
        {_icon("login_arrow",18)} Sign In
      </button>
    </form>
    <p class="lhint">Set password via Telegram:<br><code>/setpassword <pass></code></p>
  </div>
</div>""", "Sign in")


async def handle_logout(request: web.Request) -> web.Response:
    resp = web.HTTPFound("/")
    resp.del_cookie("session")
    raise resp


@require_auth
async def handle_drives(request: web.Request) -> web.Response:
    uid = request["uid"]
    db: Database = request.app["db"]
    gdrive: GoogleDriveManager = request.app["gdrive"]

    try:
        drives = await db.get_all_drives(uid)
    except Exception as e:
        logger.error(f"handle_drives get_all_drives: {e}", exc_info=True)
        drives = []

    try:
        auth_url, flow, state = gdrive.get_auth_url_for_web()
        _web_pending_flows[uid] = {"flow": flow, "state": state}
        await db.save_oauth_state(uid, state)
    except Exception as e:
        logger.error(f"handle_drives auth_url: {e}", exc_info=True)
        auth_url = "#"

    cards = ""
    for i, d in enumerate(drives):
        email = d.get("email", f"Drive {i+1}")
        pic   = d.get("picture", "")
        ltr   = email[0].upper() if email else "?"
        av    = f'<img src="{pic}" onerror="this.style.display=\'none\'">' if pic else ltr
        cards += f'''<a href="/drive/{i}" class="acard">
          <div class="av">{av}</div>
          <div class="ae" title="{email}">{email}</div>
          <div class="al">Drive {i+1}</div>
        </a>'''

    cards += f'''<a href="{auth_url}" class="acard add-card">
      <div class="av">{_icon("add_account", 28)}</div>
      <div class="ae">Add account</div>
      <div class="al">Connect Google Drive</div>
    </a>'''

    first_email = drives[0].get("email", "") if drives else ""
    first_pic   = drives[0].get("picture", "") if drives else ""
    av_nav = f'<img src="{first_pic}">' if first_pic else (first_email[0].upper() if first_email else "?")

    return _page(f"""
<nav>
  <a href="/drives" class="nav-logo" title="My Drives">
    <img src="/static/logo.png" class="nav-logo-img" alt="GDrive Bot">
  </a>
  <div class="nav-center"></div>
  <a href="/logout" class="btn btn-ghost btn-sm">{_icon("signout",16)} Sign out</a>
  <div class="nav-avatar">{av_nav}</div>
</nav>
<div class="pg-accounts">
  <h1>My Drives</h1>
  <p class="sub">Select an account to browse files</p>
  <div class="agrid">{cards}</div>
</div>""", "My Drives")


@require_auth
async def handle_browser(request: web.Request) -> web.Response:
    di  = int(request.match_info["di"])
    uid = request["uid"]
    db: Database  = request.app["db"]

    try:
        drives = await db.get_all_drives(uid)
    except Exception as e:
        logger.error(f"handle_browser get_all_drives: {e}", exc_info=True)
        raise web.HTTPFound("/drives")

    if di >= len(drives):
        raise web.HTTPFound("/drives")

    email = drives[di].get("email", f"Drive {di+1}")
    pic   = drives[di].get("picture", "")
    ltr   = email[0].upper() if email else "?"
    av_nav = f'<img src="{pic}">' if pic else ltr

    drive_tabs = ""
    if len(drives) > 1:
        for i, d in enumerate(drives):
            em = d.get("email", f"Drive {i+1}")
            p  = d.get("picture", "")
            l  = em[0].upper() if em else "?"
            av = f'<img src="{p}">' if p else l
            active = " active" if i == di else ""
            drive_tabs += f'<div class="drive-tab{active}" onclick="switchDrive({i})"><div class="drive-av">{av}</div><span class="drive-email" title="{em}">{em}</span></div>'

    max_mb = _fmt_size(Config.MAX_FILE_SIZE)

    # Build auth URL for "Add account" link in the accordion
    try:
        gdrive: GoogleDriveManager = request.app["gdrive"]
        _auth_url, _flow, _state = gdrive.get_auth_url_for_web()
        _web_pending_flows[uid] = {"flow": _flow, "state": _state}
        import asyncio as _asyncio
        _asyncio.ensure_future(request.app["db"].save_oauth_state(uid, _state))
    except Exception:
        _auth_url = "/drives"

    # Build drives JSON for JS (only email, picture, index)
    import json as _json
    drives_js = _json.dumps([
        {"email": d.get("email", f"Drive {i+1}"), "picture": d.get("picture", ""), "index": i}
        for i, d in enumerate(drives)
    ])
    auth_url_js = _json.dumps(_auth_url)

    skel = "".join(f"""<div class="file-item">
      <div class="fi-cb"><div class="custom-cb"></div></div>
      <div class="sk sk-i"></div>
      <div class="fi-info">
        <div class="sk sk-n" style="margin-bottom:6px"></div>
        <div class="sk sk-s"></div>
      </div>
    </div>""" for _ in range(10))

    drives_section = ""
    if len(drives) > 1:
        drives_section = f'<div class="sb-label">Accounts</div>{drive_tabs}'

    return _page(f"""
<nav>
  <a href="/drives" class="nav-logo" title="My Drives">
    <img src="/static/logo.png" class="nav-logo-img" alt="GDrive Bot">
  </a>
  <div class="nav-center">
    <div class="nav-search-wrap">
      {_icon("search", 16)}
      <input id="nav-search-input" type="text" placeholder="Search files and folders…"
        onkeydown="if(event.key==='Enter')doNavSearch()"
        oninput="onNavSearchInput(this.value)">
    </div>
  </div>
  <div class="nav-avatar" title="{email}" onclick="toggleAvPopup()" id="nav-av">{av_nav}</div>
</nav>

<!-- Avatar popup -->
<div id="av-popup">
  <div class="avp-head">
    <div class="avp-avatar" id="avp-av">{av_nav}</div>
    <div>
      <div class="avp-email">{email}</div>
      <div class="avp-label">Drive {di + 1}</div>
    </div>
  </div>
  <div class="avp-storage" id="avp-storage">
    <div class="avp-sk" style="width:100%;height:6px;margin:8px 0 6px"></div>
    <div class="avp-sk" style="width:60%;height:10px;margin-top:4px"></div>
  </div>
  <div style="margin-top:14px;border-top:1px solid var(--border);padding-top:14px">
    <!-- Drives accordion (only rendered if >1 drive) -->
    <div id="avp-drives-section"></div>
  </div>
  <div style="margin-top:14px;border-top:1px solid var(--border);padding-top:14px">
    <div style="font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--text3);margin-bottom:10px">Accent Color</div>
    <div id="accent-swatches" style="display:flex;flex-wrap:wrap;gap:8px;align-items:center"></div>
  </div>
  <div style="margin-top:14px;border-top:1px solid var(--border);padding-top:14px">
    <a href="/logout" class="btn btn-ghost btn-wide" style="color:var(--red);border-color:rgba(239,68,68,.3);justify-content:center;gap:8px">{_icon("signout",16)} Sign out</a>
  </div>
</div>

<div class="layout">
  <aside class="sidebar">
    <div class="sb-item active" onclick="navRoot()">
      {_icon("home", 18)} My Drive
    </div>
    <div class="sb-item" onclick="openSearchModal()">
      {_icon("search", 18)} Search
    </div>

    <div class="sb-divider"></div>
    {drives_section}
    <div class="drive-tab" onclick="window.location.href='/drives'">
      <div class="drive-av" style="background:var(--surface3)">{_icon("add_account",15)}</div>
      <span class="drive-email">Add account</span>
    </div>

    <div class="sb-divider"></div>
    <a href="/logout" class="sb-item" style="color:var(--red)">
      {_icon("signout", 18)} Sign out
    </a>
  </aside>

  <div class="main">
    <!-- Breadcrumb toolbar -->
    <div class="toolbar">
      <button class="btn btn-icon" id="back-btn" onclick="goBack()" style="display:none" title="Back">{_icon("back",20)}</button>
      <div class="breadcrumb" id="bc">
        <span class="bc-crumb last">My Drive</span>
      </div>
    </div>

    <!-- File list -->
    <div class="file-area" id="file-area">
      <div id="fl">{skel}</div>
    </div>

    <!-- Infinite scroll sentinel -->
    <div id="scroll-sentinel" style="height:60px;display:flex;align-items:center;justify-content:center;">
      <div id="scroll-spinner" style="display:none">
        <div class="sk" style="width:120px;height:14px;border-radius:8px"></div>
      </div>
    </div>
  </div>
</div>

<!-- Floating Action Button -->
<div id="fab">
  <div id="fab-opts" class="fab-options" style="display:none"></div>
  <button class="fab-main" id="fab-btn" onclick="toggleFab()" title="New">
    {_icon("plus", 26)}
  </button>
</div>

<!-- Selection/bulk action bar -->
<div id="selbar">
  <button class="sel-close" onclick="clearSel()" title="Close">{_icon("close",14)}</button>
  <span id="selcnt">0 selected</span>
  <div class="selbar-sep"></div>
  <button class="sel-btn" onclick="selDl()" title="Download">{_icon("download",20)}<span>Download</span></button>
  <button class="sel-btn" onclick="selRename()" title="Rename">{_icon("rename",20)}<span>Rename</span></button>
  <button class="sel-btn" onclick="selMove()" title="Move">{_icon("move",20)}<span>Move</span></button>
  <button class="sel-btn" onclick="selCopy()" title="Copy">{_icon("copy",20)}<span>Copy</span></button>
  <button class="sel-btn danger" onclick="selDel()" title="Delete">{_icon("delete",20)}<span>Delete</span></button>
</div>

<!-- Rename modal -->
<div class="moverlay" id="m-rename"><div class="modal">
  <div class="modal-title">{_icon("rename",18)} Rename</div>
  <div class="fg"><label>New name</label><input id="i-rename" type="text"></div>
  <div class="macts">
    <button class="btn btn-ghost btn-sm" onclick="closeModal('rename')">Cancel</button>
    <button class="btn btn-primary btn-sm" onclick="doRename()">Rename</button>
  </div>
</div></div>

<!-- New folder modal -->
<div class="moverlay" id="m-mkdir"><div class="modal">
  <div class="modal-title">{_icon("newfolder",18)} New folder</div>
  <div class="fg"><label>Folder name</label><input id="i-mkdir" type="text" placeholder="Untitled folder"></div>
  <div class="macts">
    <button class="btn btn-ghost btn-sm" onclick="closeModal('mkdir')">Cancel</button>
    <button class="btn btn-primary btn-sm" onclick="doMkdir()">Create</button>
  </div>
</div></div>

<!-- Delete modal -->
<div class="moverlay" id="m-delete"><div class="modal">
  <div class="modal-title">{_icon("delete",18)} Move to trash</div>
  <p id="del-msg" style="color:var(--text2);font-size:14px;margin-bottom:6px"></p>
  <p style="font-size:12px;color:var(--text3)">This action cannot be undone.</p>
  <div class="macts">
    <button class="btn btn-ghost btn-sm" onclick="closeModal('delete')">Cancel</button>
    <button class="btn btn-danger btn-sm" onclick="doDelete()">Move to trash</button>
  </div>
</div></div>

<!-- Upload modal -->
<div class="moverlay" id="m-upload"><div class="modal">
  <div class="modal-title">{_icon("upload",18)} Upload files</div>
  <div class="dropzone" id="dz">
    <input type="file" id="fi" multiple onchange="addFiles(this.files)">
    <div class="dz-icon">{_icon("upload", 34)}</div>
    <div class="dz-txt">Drop files here or tap to browse</div>
    <div class="dz-hint">Max {max_mb} per file</div>
  </div>
  <div class="ulist" id="ulist"></div>
  <div class="macts" style="margin-top:14px">
    <button class="btn btn-ghost btn-sm" onclick="closeModal('upload');uploadQ=[];document.getElementById('ulist').innerHTML='';document.getElementById('fi').value=''">Cancel</button>
    <button class="btn btn-primary btn-sm" id="ubtn" onclick="startUpload()">{_icon("upload",15)} Upload</button>
  </div>
</div></div>

<!-- Move modal -->
<div class="moverlay" id="m-move"><div class="modal">
  <div class="modal-title">{_icon("move",18)} Move to</div>
  <p style="font-size:12px;color:var(--text3);margin-bottom:10px">Select destination folder</p>
  <div class="ftree" id="move-tree"></div>
  <div class="macts">
    <button class="btn btn-ghost btn-sm" onclick="closeModal('move')">Cancel</button>
    <button class="btn btn-primary btn-sm" onclick="confirmMove()">Move here</button>
  </div>
</div></div>

<!-- Copy modal -->
<div class="moverlay" id="m-copy"><div class="modal">
  <div class="modal-title">{_icon("copy",18)} Copy to</div>
  <p style="font-size:12px;color:var(--text3);margin-bottom:10px">Select destination folder</p>
  <div class="ftree" id="copy-tree"></div>
  <div class="macts">
    <button class="btn btn-ghost btn-sm" onclick="closeModal('copy')">Cancel</button>
    <button class="btn btn-primary btn-sm" onclick="confirmCopy()">Copy here</button>
  </div>
</div></div>

<!-- Search modal -->
<div class="moverlay" id="m-search"><div class="modal" style="max-width:480px">
  <div class="modal-title">{_icon("search",18)} Search Drive</div>
  <div class="fg" style="display:flex;gap:8px;margin-bottom:0">
    <input id="i-search" type="text" placeholder="Search files and folders..." style="flex:1">
    <button class="btn btn-primary" onclick="doSearch()" style="flex-shrink:0">{_icon("search",16)}</button>
  </div>
  <div id="search-res" style="max-height:340px;overflow-y:auto;margin-top:12px;border:1px solid var(--border);border-radius:var(--r8)"></div>
  <div class="macts"><button class="btn btn-ghost btn-sm" onclick="clearSel();closeModal('search')">Close</button></div>
</div></div>

<!-- ── Preview overlay ── -->
<div id="preview-overlay">
  <div id="preview-bar">
    <button id="preview-close-btn" onclick="closePreview()" title="Close">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
    </button>
    <span id="preview-title"></span>
    <button id="preview-copy-btn" onclick="previewCopyText()" title="Copy text">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>
    </button>
    <button id="preview-dl-btn" onclick="previewDownload()" title="Download">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3"/></svg>
    </button>
  </div>
  <div id="preview-body">
    <div id="preview-spinner" style="display:none">
      <svg width="42" height="42" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/></svg>
    </div>
  </div>
</div>

<!-- Hidden icon store for JS -->
<div id="icons" style="display:none"
  data-folder='{_icon("folder",32,"","#0386c3").replace("'","&#39;")}'
  data-file='{_icon("file",32,"","#607d8b").replace("'","&#39;")}'
  data-image='{_icon("image",32,"","#a78bfa").replace("'","&#39;")}'
  data-video='{_icon("video",32,"","#f87171").replace("'","&#39;")}'
  data-audio='{_icon("audio",32,"","#e55835").replace("'","&#39;")}'
  data-pdf='{_icon("pdf",32,"","#e53e3e").replace("'","&#39;")}'
  data-zip='{_icon("zip",32,"","#795547").replace("'","&#39;")}'
  data-doc='{_icon("doc",32,"","#3f51b5").replace("'","&#39;")}'
  data-sheet='{_icon("sheet",32,"","#38a169").replace("'","&#39;")}'
  data-code='{_icon("code",32,"","#60a5fa").replace("'","&#39;")}'
  data-dl='{_icon("download",20).replace("'","&#39;")}'
  data-rename='{_icon("rename",20).replace("'","&#39;")}'
  data-move='{_icon("move",20).replace("'","&#39;")}'
  data-del='{_icon("delete",20).replace("'","&#39;")}'
  data-copy='{_icon("copy",20).replace("'","&#39;")}'
></div>

<script>
const DI = {di};
const DRIVES_DATA = {drives_js};
const AUTH_URL = {auth_url_js};
const IC = document.getElementById('icons').dataset;

function getIco(m) {{
  if(!m) return IC.file;
  if(m.includes('folder'))  return IC.folder;
  if(m.includes('image'))   return IC.image;
  if(m.includes('video'))   return IC.video;
  if(m.includes('audio'))   return IC.audio;
  if(m.includes('pdf'))     return IC.pdf;
  if(m.includes('zip')||m.includes('tar')||m.includes('rar')) return IC.zip;
  if(m.includes('word')||m.includes('document')) return IC.doc;
  if(m.includes('sheet')||m.includes('excel'))   return IC.sheet;
  if(m.includes('presentation')||m.includes('powerpoint')) return IC.sheet;
  if(m.includes('javascript')||m.includes('json')||m.includes('python')||m.includes('html')) return IC.code;
  return IC.file;
}}

let folder='root', stack=[], files=[], sel=new Set();
let renameId=null, renameIds=[], delIds=[], moveIds=[], copyIds=[];
let uploadQ=[];
let fabOpen=false;
let longPressTimer=null;
let _nextTok=null, _loading=false, _allLoaded=false;
let _scrollObs=null;

function sz(b){{if(!b||isNaN(b))return'—';b=+b;if(b<1024)return b+' B';if(b<1048576)return(b/1024).toFixed(1)+' KB';if(b<1073741824)return(b/1048576).toFixed(1)+' MB';return(b/1073741824).toFixed(2)+' GB'}}
function dt(s){{if(!s)return'—';const d=new Date(s),now=new Date(),diff=now-d;if(diff<86400000)return d.toLocaleTimeString([],{{hour:'2-digit',minute:'2-digit'}});if(diff<604800000)return d.toLocaleDateString([],{{weekday:'short',month:'short',day:'numeric'}});return d.toLocaleDateString([],{{year:'numeric',month:'short',day:'numeric'}})}}
function isFol(f){{return f.mimeType&&f.mimeType.includes('folder')}}

function navRoot(){{folder='root';stack=[];renderBC();load();document.querySelector('.sb-item')?.classList.add('active')}}
function navFolder(id,name){{
  folder=id;
  const i=stack.findIndex(x=>x.id===id);
  if(i>=0)stack=stack.slice(0,i+1);
  else stack.push({{id,name}});
  renderBC();load();
}}
function goBack(){{
  if(stack.length===0)return;
  stack.pop();
  if(stack.length===0){{folder='root';renderBC();load()}}
  else{{const s=stack[stack.length-1];folder=s.id;renderBC();load()}}
}}
function switchDrive(i){{window.location.href='/drive/'+i}}
function refresh(){{load()}}

function renderBC(){{
  let h=`<span class="bc-crumb" onclick="navRoot()">My Drive</span>`;
  stack.forEach((f,i)=>{{
    const last=i===stack.length-1;
    h+=`<span class="bc-sep">›</span><span class="bc-crumb${{last?' last':''}}" onclick="navFolder('${{f.id}}','${{f.name.replace(/'/g,"&#39;")}}')">` +
       `${{f.name}}</span>`;
  }});
  const bc=document.getElementById('bc');
  bc.innerHTML=h;
  setTimeout(()=>{{bc.scrollLeft=bc.scrollWidth;}},0);
  document.getElementById('back-btn').style.display=stack.length>0?'':'none';
}}

function _stopObserver(){{
  if(_scrollObs){{_scrollObs.disconnect();_scrollObs=null;}}
}}
function _startObserver(){{
  _stopObserver();
  const sentinel=document.getElementById('scroll-sentinel');
  if(!sentinel)return;
  _scrollObs=new IntersectionObserver(entries=>{{
    if(entries[0].isIntersecting&&!_loading&&!_allLoaded&&_nextTok){{
      _loadMore();
    }}
  }},{{rootMargin:'200px'}});
  _scrollObs.observe(sentinel);
}}

async function load(){{
  _stopObserver();
  _nextTok=null; _loading=false; _allLoaded=false;
  files=[]; sel=new Set();
  clearSel(); closeFab();
  const fl=document.getElementById('fl');
  fl.innerHTML=Array(8).fill(`<div class="file-item">
    <div class="fi-cb"><div class="custom-cb"></div></div>
    <div class="sk sk-i"></div>
    <div class="fi-info">
      <div class="sk sk-n" style="margin-bottom:6px"></div>
      <div class="sk sk-s"></div>
    </div>
  </div>`).join('');
  document.getElementById('scroll-spinner').style.display='none';
  bar(true);
  try{{
    const r=await fetch(`/api/files?drive=${{DI}}&folder=${{encodeURIComponent(folder)}}`);
    if(!r.ok)throw new Error('HTTP '+r.status);
    const d=await r.json();
    files=d.files||[];
    _nextTok=d.next_page_token||null;
    _allLoaded=!_nextTok;
    render();
    if(!_allLoaded)_startObserver();
  }}catch(e){{
    fl.innerHTML=`<div class="empty"><div class="empty-icon">${{IC.file}}</div><h3>Failed to load</h3><p>${{e.message}}</p></div>`;
  }}
  bar(false);
}}

async function _loadMore(){{
  if(_loading||_allLoaded||!_nextTok)return;
  _loading=true;
  document.getElementById('scroll-spinner').style.display='flex';
  try{{
    const r=await fetch(`/api/files?drive=${{DI}}&folder=${{encodeURIComponent(folder)}}&page_token=${{encodeURIComponent(_nextTok)}}`);
    if(!r.ok)throw new Error('HTTP '+r.status);
    const d=await r.json();
    const newFiles=d.files||[];
    _nextTok=d.next_page_token||null;
    _allLoaded=!_nextTok;
    files=[...files,...newFiles];
    renderAppend(newFiles);
    if(_allLoaded)_stopObserver();
  }}catch(e){{
    console.error('loadMore failed',e);
  }}
  document.getElementById('scroll-spinner').style.display='none';
  _loading=false;
}}

function _fileItemHTML(f){{
  const isF=isFol(f);
  const nm=f.name.replace(/"/g,'&quot;').replace(/'/g,'&#39;');
  const selcls=sel.has(f.id)?' sel':'';
  const chkCls=sel.has(f.id)?'custom-cb checked':'custom-cb';
  const sizeStr=isF?'Directory':sz(f.size);
  return`<div class="file-item${{selcls}}" data-id="${{f.id}}" data-name="${{nm}}" data-fol="${{isF}}"
      onclick="itemClick(event,'${{f.id}}')"
      oncontextmenu="event.preventDefault();longPress('${{f.id}}')"
      ontouchstart="startLong(event,'${{f.id}}')" ontouchend="endLong()" ontouchmove="endLong()">
    <div class="fi-cb" onclick="event.stopPropagation();toggleSelCustom('${{f.id}}',this.querySelector('.custom-cb'))">
      <div class="${{chkCls}}"></div>
    </div>
    <div class="fi-icon">${{getIco(f.mimeType)}}</div>
    <div class="fi-info">
      <div class="fi-name${{isF?' fol':''}}">${{f.name}}</div>
      <div class="fi-meta">
        <span class="fi-size">${{sizeStr}}</span>
        <span class="fi-date">${{dt(f.modifiedTime)}}</span>
      </div>
    </div>
    ${{!isF?`<button class="btn-icon fi-act" title="Download" onclick="event.stopPropagation();dlFile('${{f.id}}')">
      ${{IC.dl}}</button>`:''}}
  </div>`;
}}

function render(){{
  const fl=document.getElementById('fl');
  if(!files.length){{
    fl.innerHTML=`<div class="empty"><div class="empty-icon">${{IC.folder}}</div><h3>This folder is empty</h3><p>Upload files or create a folder</p></div>`;
    return;
  }}
  let s=[...files];
  s.sort((a,b)=>{{
    const af=isFol(a),bf=isFol(b);
    if(af&&!bf)return-1;if(!af&&bf)return 1;
    return(a.name||'').toLowerCase()<(b.name||'').toLowerCase()?-1:1;
  }});
  fl.innerHTML=s.map(f=>_fileItemHTML(f)).join('');
}}

function itemClick(e,id){{
  if(e.target.classList.contains('custom-cb'))return;
  if(document.body.classList.contains('select-mode')){{
    const cb=e.currentTarget.querySelector('.custom-cb');
    if(cb)toggleSelCustom(id,cb);
    return;
  }}
  const row=e.currentTarget;
  if(row.dataset.fol==='true')navFolder(id,row.dataset.name);
  else {{
    const f=files.find(x=>x.id===id);
    openPreview(id, f?f.mimeType:'', f?f.name:'');
  }}
}}

let _pvId=null;
let _imgList=[], _imgIdx=0;
let _vidList=[], _vidIdx=0;
let _audList=[], _audIdx=0;
let _vidEl=null, _vidHideTimer=null, _vidDragging=false;
let _audEl=null, _audDragging=false;

function _getSiblingFiles(mime){{
  const m=mime||'';
  if(m.includes('image')) return files.filter(f=>f.mimeType&&f.mimeType.includes('image'));
  if(m.includes('video')) return files.filter(f=>f.mimeType&&f.mimeType.includes('video'));
  if(m.includes('audio')) return files.filter(f=>f.mimeType&&f.mimeType.includes('audio'));
  return [];
}}

function openPreview(id,mime,name){{
  _pvId=id;
  const ov=document.getElementById('preview-overlay');
  document.getElementById('preview-title').textContent=name||'Preview';
  const body=document.getElementById('preview-body');
  const spinner=document.getElementById('preview-spinner');
  _clearPreviewBody(body,spinner);
  spinner.style.display='flex';
  ov.classList.add('open');

  const m=mime||'';
  const src=`/api/preview/${{id}}?drive=${{DI}}`;

  function hideSpinner(){{spinner.style.display='none';}}

  if(m.includes('image')){{
    document.getElementById('preview-copy-btn').classList.remove('visible');
    const siblings=_getSiblingFiles(m);
    _imgList=siblings.length?siblings:[{{id,name,mimeType:mime}}];
    _imgIdx=_imgList.findIndex(f=>f.id===id);
    if(_imgIdx<0)_imgIdx=0;
    _buildImageViewer(body,hideSpinner);
  }} else if(m.includes('video')){{
    document.getElementById('preview-copy-btn').classList.remove('visible');
    const siblings=_getSiblingFiles(m);
    _vidList=siblings.length?siblings:[{{id,name,mimeType:mime}}];
    _vidIdx=_vidList.findIndex(f=>f.id===id);
    if(_vidIdx<0)_vidIdx=0;
    _buildVideoPlayer(body,hideSpinner);
  }} else if(m.includes('audio')){{
    document.getElementById('preview-copy-btn').classList.remove('visible');
    const siblings=_getSiblingFiles(m);
    _audList=siblings.length?siblings:[{{id,name,mimeType:mime}}];
    _audIdx=_audList.findIndex(f=>f.id===id);
    if(_audIdx<0)_audIdx=0;
    _buildAudioPlayer(body,hideSpinner);
  }} else if(m.includes('pdf')||m==='application/pdf'){{
    document.getElementById('preview-copy-btn').classList.remove('visible');
    const fr=document.createElement('iframe');
    fr.src=src; fr.style.width='100%'; fr.style.height='100%';
    fr.onload=hideSpinner;
    body.appendChild(fr);
  }} else if(m.includes('text')||m.includes('json')||m.includes('javascript')||
             m.includes('python')||m.includes('html')||m.includes('css')||m.includes('xml')){{
    fetch(src).then(r=>{{if(!r.ok)throw new Error(r.status);return r.text();}}).then(txt=>{{
      hideSpinner();
      document.getElementById('preview-copy-btn').classList.add('visible');
      const pre=document.createElement('pre');pre.textContent=txt;body.appendChild(pre);
    }}).catch(()=>{{hideSpinner();document.getElementById('preview-copy-btn').classList.remove('visible');showUnsupported(name,getIco(m));}});
  }} else {{
    document.getElementById('preview-copy-btn').classList.remove('visible');
    hideSpinner();showUnsupported(name,getIco(m));
  }}
}}

function previewCopyText(){{
  const pre=document.querySelector('#preview-body pre');
  if(!pre)return;
  navigator.clipboard.writeText(pre.textContent).then(()=>{{
    const btn=document.getElementById('preview-copy-btn');
    btn.innerHTML='<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
    btn.style.background='var(--green)';btn.style.color='#fff';
    setTimeout(()=>{{
      btn.innerHTML='<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>';
      btn.style.background='';btn.style.color='';
    }},1800);
    toast('Copied to clipboard','ok');
  }}).catch(()=>toast('Copy failed','err'));
}}

function _buildImageViewer(body,onReady){{
  const wrap=document.createElement('div');wrap.id='img-viewer';
  const img=document.createElement('img');img.style.opacity='0';img.style.transition='opacity .22s';
  const btnPrev=document.createElement('button');
  btnPrev.className='img-nav-btn prev';
  btnPrev.innerHTML='<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>';
  btnPrev.onclick=()=>_imgNavigate(-1);
  const btnNext=document.createElement('button');
  btnNext.className='img-nav-btn next';
  btnNext.innerHTML='<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>';
  btnNext.onclick=()=>_imgNavigate(1);
  const counter=document.createElement('div');counter.className='img-counter';
  wrap.appendChild(btnPrev);wrap.appendChild(img);wrap.appendChild(btnNext);wrap.appendChild(counter);
  body.appendChild(wrap);
  _imgLoadCurrent(img,counter,btnPrev,btnNext,onReady);

  // ── Pinch-to-zoom & swipe ──
  let _z=1,_zx=0,_zy=0,_px=0,_py=0;
  let _pinchDist0=0,_pinchZ0=1;
  let _swipeStartX=0,_swipeStartY=0,_swipeActive=false;
  function _dist(t){{return Math.hypot(t[0].clientX-t[1].clientX,t[0].clientY-t[1].clientY);}}
  function _applyTransform(){{img.style.transform=`translate(${{_zx}}px,${{_zy}}px) scale(${{_z}})`;img.style.transition='none';}}
  function _resetZoom(){{_z=1;_zx=0;_zy=0;_applyTransform();img.style.transition='opacity .22s';}}

  wrap.addEventListener('touchstart',e=>{{
    if(e.touches.length===2){{
      e.preventDefault();
      _swipeActive=false;
      _pinchDist0=_dist(e.touches);
      _pinchZ0=_z;
    }} else if(e.touches.length===1){{
      _swipeStartX=e.touches[0].clientX;
      _swipeStartY=e.touches[0].clientY;
      _swipeActive=true;
      if(_z>1){{_px=e.touches[0].clientX;_py=e.touches[0].clientY;}}
    }}
  }},{{passive:false}});

  wrap.addEventListener('touchmove',e=>{{
    if(e.touches.length===2){{
      e.preventDefault();
      _swipeActive=false;
      const d=_dist(e.touches);
      _z=Math.min(5,Math.max(1,_pinchZ0*(d/_pinchDist0)));
      if(_z<=1){{_zx=0;_zy=0;}}
      _applyTransform();
    }} else if(e.touches.length===1&&_z>1){{
      e.preventDefault();
      const dx=e.touches[0].clientX-_px;
      const dy=e.touches[0].clientY-_py;
      _px=e.touches[0].clientX;_py=e.touches[0].clientY;
      _zx+=dx;_zy+=dy;_applyTransform();
    }}
  }},{{passive:false}});

  wrap.addEventListener('touchend',e=>{{
    if(e.touches.length<2&&_z>1){{/* stay zoomed */return;}}
    if(_z<=1.05)_resetZoom();
    if(_swipeActive&&e.changedTouches.length===1&&_z<=1.05){{
      const dx=e.changedTouches[0].clientX-_swipeStartX;
      const dy=e.changedTouches[0].clientY-_swipeStartY;
      if(Math.abs(dx)>50&&Math.abs(dx)>Math.abs(dy)*1.5){{
        if(dx<0)_imgNavigate(1);
        else _imgNavigate(-1);
      }}
      _swipeActive=false;
    }}
  }},{{passive:true}});

  // Double-tap to reset zoom
  let _dtTap=0;
  wrap.addEventListener('touchend',e=>{{
    const now=Date.now();
    if(now-_dtTap<300&&e.changedTouches.length===1){{
      if(_z>1)_resetZoom();
    }}
    _dtTap=now;
  }},{{passive:true}});
}}

function _imgLoadCurrent(img,counter,btnPrev,btnNext,onReady){{
  const f=_imgList[_imgIdx];
  if(!f)return;
  _pvId=f.id;
  document.getElementById('preview-title').textContent=f.name||'Image';
  img.style.opacity='0';
  img.onload=()=>{{if(onReady)onReady();img.style.opacity='1';}};
  img.onerror=()=>{{if(onReady)onReady();showUnsupported(f.name,IC.image);}};
  img.src=`/api/preview/${{f.id}}?drive=${{DI}}`;
  counter.textContent=`${{_imgIdx+1}} / ${{_imgList.length}}`;
  btnPrev.disabled=_imgIdx===0;
  btnNext.disabled=_imgIdx===_imgList.length-1;
  if(_imgList.length<=1){{btnPrev.style.display='none';btnNext.style.display='none';counter.style.display='none';}}
}}

function _imgNavigate(dir){{
  const ni=_imgIdx+dir;
  if(ni<0||ni>=_imgList.length)return;
  _imgIdx=ni;
  const wrap=document.getElementById('img-viewer');
  if(!wrap)return;
  const img=wrap.querySelector('img');
  // Reset any zoom/pan applied
  img.style.transform='';
  const counter=wrap.querySelector('.img-counter');
  const btnPrev=wrap.querySelector('.img-nav-btn.prev');
  const btnNext=wrap.querySelector('.img-nav-btn.next');
  _imgLoadCurrent(img,counter,btnPrev,btnNext,null);
}}

document.addEventListener('keydown',e=>{{
  const ov=document.getElementById('preview-overlay');
  if(!ov||!ov.classList.contains('open'))return;
  if(e.key==='ArrowLeft')_imgNavigate(-1);
  if(e.key==='ArrowRight')_imgNavigate(1);
}});

function _fmtTime(s){{
  if(isNaN(s)||!isFinite(s))return'0:00';
  const m=Math.floor(s/60),sc=Math.floor(s%60);
  return m+':'+(sc<10?'0':'')+sc;
}}

function _buildVideoPlayer(body,onReady){{
  const f=_vidList[_vidIdx];if(!f)return;
  const wrap=document.createElement('div');wrap.id='video-player-wrap';
  const vid=document.createElement('video');
  vid.playsInline=true;vid.preload='auto';
  _vidEl=vid;
  wrap.innerHTML=`
    <div id="vid-nav-overlay">
      <div id="vid-play-flash"></div>
    </div>
    <div id="vid-overlay-center">
      <button class="vid-overlay-btn sm" id="vid-ol-prev" onclick="vidNavigate(-1)" title="Previous">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor"><path d="M6 6h2v12H6zm3.5 6 8.5 6V6z"/></svg>
      </button>
      <button class="vid-overlay-btn lg" id="vid-ol-play" onclick="vidTogglePlay()" title="Play/Pause">
        <svg id="vid-overlay-play-ico" width="34" height="34" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
      </button>
      <button class="vid-overlay-btn sm" id="vid-ol-next" onclick="vidNavigate(1)" title="Next">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor"><path d="M6 18l8.5-6L6 6v12z"/><rect x="16" y="6" width="2" height="12"/></svg>
      </button>
    </div>
    <div id="vid-controls">
      <div id="vid-progress-wrap">
        <div id="vid-progress-track">
          <div id="vid-progress-buf"></div>
          <div id="vid-progress-fill"></div>
          <div id="vid-thumb"></div>
        </div>
      </div>
      <div id="vid-controls-row">
        <button class="vid-btn" id="vid-play-btn" title="Play/Pause" onclick="vidTogglePlay()">
          <svg id="vid-play-ico" width="26" height="26" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
        </button>
        <span id="vid-time">0:00 / 0:00</span>
        <span id="vid-title-bar"></span>
        <div id="vid-vol-wrap">
          <button class="vid-btn" id="vid-mute-btn" onclick="vidToggleMute()">
            <svg id="vid-vol-ico" width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z"/></svg>
          </button>
        </div>
        <button class="vid-btn" id="vid-full-btn" onclick="vidToggleFullscreen()" title="Fullscreen">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z"/></svg>
        </button>
      </div>
    </div>`;
  wrap.insertBefore(vid,wrap.firstChild);
  body.appendChild(wrap);

  vid.src=`/api/preview/${{f.id}}?drive=${{DI}}`;
  document.getElementById('vid-title-bar').textContent=f.name||'';
  _vidUpdateNavBtns();

  vid.oncanplay=()=>{{
    if(onReady){{onReady();onReady=null;}}
    vid.play().then(()=>{{_vidUpdatePlayBtn();}}).catch(()=>{{}});
  }};
  vid.onerror=()=>{{if(onReady){{onReady();onReady=null;}}showUnsupported(f.name,IC.video);}};

  vid.addEventListener('timeupdate',_vidSyncProgress);
  vid.addEventListener('progress',_vidSyncBuffer);
  vid.addEventListener('ended',()=>{{if(_vidIdx<_vidList.length-1)vidNavigate(1);}});

  let _tapTimer=null, _tapCount=0, _tapX=0;
  vid.addEventListener('click',e=>{{
    _tapCount++;_tapX=e.clientX;
    if(_tapTimer)clearTimeout(_tapTimer);
    _tapTimer=setTimeout(()=>{{
      if(_tapCount>=2){{
        const rect=vid.getBoundingClientRect();
        const pct=(_tapX-rect.left)/rect.width;
        const secs=10;
        if(pct<0.5){{vid.currentTime=Math.max(0,vid.currentTime-secs);_vidShowSeekRipple('left',-secs);}}
        else{{vid.currentTime=Math.min(vid.duration||0,vid.currentTime+secs);_vidShowSeekRipple('right',secs);}}
      }}
      _tapCount=0;_tapTimer=null;
    }},280);
  }});

  const w=wrap;
  w.addEventListener('mousemove',_vidShowControls);
  w.addEventListener('touchstart',_vidShowControls,{{passive:true}});

  const pw=document.getElementById('vid-progress-wrap');
  pw.addEventListener('mousedown',e=>{{_vidDragging=true;_vidSeekTo(e,pw);}});
  document.addEventListener('mousemove',e=>{{if(_vidDragging)_vidSeekTo(e,pw);}});
  document.addEventListener('mouseup',()=>{{_vidDragging=false;}});
  pw.addEventListener('touchstart',e=>{{_vidDragging=true;_vidSeekTouch(e,pw);}},{{passive:true}});
  document.addEventListener('touchmove',e=>{{if(_vidDragging)_vidSeekTouch(e,pw);}},{{passive:true}});
  document.addEventListener('touchend',()=>{{_vidDragging=false;}});
}}

function _vidShowControls(){{
  const w=document.getElementById('video-player-wrap');
  if(w)w.classList.remove('controls-hidden');
  clearTimeout(_vidHideTimer);
  if(_vidEl&&!_vidEl.paused){{
    _vidHideTimer=setTimeout(()=>{{const w2=document.getElementById('video-player-wrap');if(w2)w2.classList.add('controls-hidden');}},3000);
  }}
}}

function _vidSyncProgress(){{
  const vid=_vidEl;if(!vid)return;
  const pct=vid.duration?vid.currentTime/vid.duration*100:0;
  const fill=document.getElementById('vid-progress-fill');
  const thumb=document.getElementById('vid-thumb');
  if(fill)fill.style.width=pct+'%';
  if(thumb)thumb.style.left=pct+'%';
  const t=document.getElementById('vid-time');
  if(t)t.textContent=_fmtTime(vid.currentTime)+' / '+_fmtTime(vid.duration);
}}
function _vidSyncBuffer(){{
  const vid=_vidEl;if(!vid||!vid.duration)return;
  let buf=0;
  for(let i=0;i<vid.buffered.length;i++){{if(vid.buffered.start(i)<=vid.currentTime&&vid.currentTime<=vid.buffered.end(i)){{buf=vid.buffered.end(i)/vid.duration*100;break;}}}}
  const b=document.getElementById('vid-progress-buf');if(b)b.style.width=buf+'%';
}}
function _vidSeekTo(e,pw){{
  const vid=_vidEl;if(!vid||!vid.duration)return;
  const r=pw.getBoundingClientRect();
  const pct=Math.max(0,Math.min(1,(e.clientX-r.left)/r.width));
  vid.currentTime=pct*vid.duration;_vidSyncProgress();
}}
function _vidSeekTouch(e,pw){{
  if(!e.touches.length)return;
  _vidSeekTo(e.touches[0],pw);
}}
function vidTogglePlay(){{
  const vid=_vidEl;if(!vid)return;
  if(vid.paused){{vid.play();_vidFlash(true);}}else{{vid.pause();_vidFlash(false);}}
  _vidUpdatePlayBtn();_vidShowControls();
}}
function _vidFlash(playing){{
  const fl=document.getElementById('vid-play-flash');if(!fl)return;
  fl.innerHTML=playing
    ?'<svg width="36" height="36" viewBox="0 0 24 24" fill="#fff"><path d="M8 5v14l11-7z"/></svg>'
    :'<svg width="36" height="36" viewBox="0 0 24 24" fill="#fff"><path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/></svg>';
  fl.classList.remove('flash');
  void fl.offsetWidth;
  fl.classList.add('flash');
}}
function _vidShowSeekRipple(side,secs){{
  const wrap=document.getElementById('video-player-wrap');if(!wrap)return;
  wrap.querySelectorAll(`.vid-seek-ripple.${{side}}`).forEach(r=>r.remove());
  const r=document.createElement('div');
  r.className=`vid-seek-ripple ${{side}}`;
  const arrow=secs>0?'&#9654;&#9654;':'&#9664;&#9664;';
  r.innerHTML=`<div class="vid-seek-ripple-icon"><svg width="28" height="28" viewBox="0 0 24 24" fill="#fff">${{secs>0?'<path d="M5.59 7.41L10.18 12l-4.59 4.59L7 18l6-6-6-6-1.41 1.41zm9 0L19.18 12l-4.59 4.59L16 18l6-6-6-6-1.41 1.41z"/>':'<path d="M18.41 7.41L13.83 12l4.58 4.59L17 18l-6-6 6-6 1.41 1.41zm-9 0L4.83 12l4.58 4.59L8 18 2 12l6-6 1.41 1.41z"/>'}}</svg></div>
    <span class="vid-seek-ripple-label">${{Math.abs(secs)}} sec</span>`;
  wrap.appendChild(r);
  setTimeout(()=>r.remove(),600);
}}
function _vidUpdatePlayBtn(){{
  const paused=_vidEl?_vidEl.paused:true;
  const path=paused?'<path d="M8 5v14l11-7z"/>':'<path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/>';
  const ico=document.getElementById('vid-play-ico');
  if(ico)ico.innerHTML=path;
  const oico=document.getElementById('vid-overlay-play-ico');
  if(oico)oico.innerHTML=path;
}}
function vidToggleMute(){{
  const vid=_vidEl;if(!vid)return;
  vid.muted=!vid.muted;
  const ico=document.getElementById('vid-vol-ico');if(!ico)return;
  ico.innerHTML=vid.muted
    ?'<path d="M16.5 12c0-1.77-1.02-3.29-2.5-4.03v2.21l2.45 2.45c.03-.2.05-.41.05-.63zm2.5 0c0 .94-.2 1.82-.54 2.64l1.51 1.51C20.63 14.91 21 13.5 21 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89.86 5 3.54 5 6.71zM4.27 3L3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06c1.38-.31 2.63-.95 3.69-1.81L19.73 21 21 19.73l-9-9L4.27 3zM12 4L9.91 6.09 12 8.18V4z"/>'
    :'<path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z"/>';
}}
function vidSetVol(v){{if(_vidEl)_vidEl.volume=v;}}
function vidToggleFullscreen(){{
  const w=document.getElementById('video-player-wrap');if(!w)return;
  if(!document.fullscreenElement){{
    const p=w.requestFullscreen||w.webkitRequestFullscreen||w.mozRequestFullScreen||w.msRequestFullscreen;
    if(p){{
      p.call(w).then(()=>{{
        try{{
          const scr=screen.orientation||screen.msOrientation;
          if(scr&&scr.lock){{
            scr.lock('landscape').catch(()=>{{}});
          }} else if(screen.lockOrientation){{
            screen.lockOrientation('landscape');
          }} else if(screen.mozLockOrientation){{
            screen.mozLockOrientation('landscape');
          }}
        }}catch(e){{}}
      }}).catch(()=>{{}});
    }}
  }} else {{
    const exit=document.exitFullscreen||document.webkitExitFullscreen||document.mozCancelFullScreen||document.msExitFullscreen;
    if(exit)exit.call(document).catch(()=>{{}});
    try{{
      const scr=screen.orientation||screen.msOrientation;
      if(scr&&scr.unlock)scr.unlock();
      else if(screen.unlockOrientation)screen.unlockOrientation();
      else if(screen.mozUnlockOrientation)screen.mozUnlockOrientation();
    }}catch(e){{}}
  }}
  document.addEventListener('fullscreenchange',_vidUpdateFullBtn,{{once:true}});
  document.addEventListener('webkitfullscreenchange',_vidUpdateFullBtn,{{once:true}});
}}
function _vidUpdateFullBtn(){{
  const btn=document.getElementById('vid-full-btn');if(!btn)return;
  const isFs=!!document.fullscreenElement||!!document.webkitFullscreenElement;
  btn.innerHTML=isFs
    ?'<svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M5 16h3v3h2v-5H5v2zm3-8H5v2h5V5H8v3zm6 11h2v-3h3v-2h-5v5zm2-11V5h-2v5h5V8h-3z"/></svg>'
    :'<svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z"/></svg>';
}}
function _vidUpdateNavBtns(){{
  const pb=document.getElementById('vid-ol-prev');
  const nb=document.getElementById('vid-ol-next');
  if(pb){{
    pb.classList.remove('hidden');
    pb.classList.toggle('dimmed',_vidIdx===0);
  }}
  if(nb){{
    nb.classList.remove('hidden');
    nb.classList.toggle('dimmed',_vidIdx===_vidList.length-1);
  }}
}}
function vidNavigate(dir){{
  const ni=_vidIdx+dir;
  if(ni<0||ni>=_vidList.length)return;
  _vidIdx=ni;
  const f=_vidList[_vidIdx];
  _pvId=f.id;
  document.getElementById('preview-title').textContent=f.name||'Video';
  const tb=document.getElementById('vid-title-bar');if(tb)tb.textContent=f.name||'';
  if(_vidEl){{
    _vidEl.src=`/api/preview/${{f.id}}?drive=${{DI}}`;
    _vidEl.load();
    _vidEl.oncanplay=()=>{{_vidEl.play().then(()=>_vidUpdatePlayBtn()).catch(()=>{{}});}};
  }}
  _vidUpdateNavBtns();
}}

function _buildAudioPlayer(body,onReady){{
  const f=_audList[_audIdx];if(!f)return;
  const wrap=document.createElement('div');wrap.id='audio-player-wrap';
  wrap.innerHTML=`
    <audio id="audio-el" preload="auto"></audio>
    <div id="audio-now-playing">
      <div id="audio-art">
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="rgba(255,255,255,0.6)" stroke-width="1.5" stroke-linecap="round"><path d="M9 9l10.5-3m0 6.553v3.75a2.25 2.25 0 01-1.632 2.163l-1.32.377a1.803 1.803 0 11-.99-3.467l2.31-.66a2.25 2.25 0 001.632-2.163zm0 0V2.25L9 5.25v10.303m0 0v3.75a2.25 2.25 0 01-1.632 2.163l-1.32.377a1.803 1.803 0 01-.99-3.467l2.31-.66A2.25 2.25 0 009 15.553z"/></svg>
      </div>
      <div id="audio-now-title">${{f.name||'Audio'}}</div>
      <div id="audio-now-sub">${{_audIdx+1}} of ${{_audList.length}}</div>
    </div>
    <div id="audio-scrubber-wrap">
      <div id="audio-progress-wrap">
        <div id="audio-progress-track">
          <div id="audio-progress-fill"></div>
          <div id="audio-thumb"></div>
        </div>
      </div>
      <div id="audio-times"><span id="aud-cur">0:00</span><span id="aud-dur">0:00</span></div>
    </div>
    <div id="audio-controls">
      <button class="aud-btn" onclick="audNavigate(-1)" id="aud-prev-btn" title="Previous">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor"><path d="M6 6h2v12H6zm3.5 6 8.5 6V6z"/></svg>
      </button>
      <button id="aud-play-btn" onclick="audTogglePlay()" title="Play/Pause">
        <svg id="aud-play-ico" width="26" height="26" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
      </button>
      <button class="aud-btn" onclick="audNavigate(1)" id="aud-next-btn" title="Next">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor"><path d="M6 18l8.5-6L6 6v12z"/><rect x="16" y="6" width="2" height="12"/></svg>
      </button>
    </div>
    <div id="audio-playlist-wrap">
      <div id="audio-playlist-header">
        <span>Playlist</span>
        <span style="font-weight:400">${{_audList.length}} tracks</span>
      </div>
      <div id="audio-playlist">
        ${{_audList.map((af,i)=>`
          <div class="apl-item${{i===_audIdx?' active':''}}" onclick="audPlayIdx(${{i}})" id="apl-${{af.id}}">
            <span class="apl-idx">${{i+1}}</span>
            <div class="apl-icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M9 9l10.5-3m0 6.553v3.75a2.25 2.25 0 01-1.632 2.163l-1.32.377a1.803 1.803 0 11-.99-3.467l2.31-.66a2.25 2.25 0 001.632-2.163zm0 0V2.25L9 5.25v10.303m0 0v3.75a2.25 2.25 0 01-1.632 2.163l-1.32.377a1.803 1.803 0 01-.99-3.467l2.31-.66A2.25 2.25 0 009 15.553z"/></svg></div>
            <span class="apl-name">${{af.name}}</span>
          </div>`).join('')}}
      </div>
    </div>`;
  body.appendChild(wrap);

  _audEl=wrap.querySelector('#audio-el');
  _audLoadTrack(onReady);

  _audEl.addEventListener('timeupdate',_audSyncProgress);
  _audEl.addEventListener('ended',()=>{{if(_audIdx<_audList.length-1)audNavigate(1);}});

  const pw=document.getElementById('audio-progress-wrap');
  pw.addEventListener('mousedown',e=>{{_audDragging=true;_audSeekTo(e,pw);}});
  document.addEventListener('mousemove',e=>{{if(_audDragging)_audSeekTo(e,pw);}});
  document.addEventListener('mouseup',()=>{{_audDragging=false;}});
  pw.addEventListener('touchstart',e=>{{_audDragging=true;_audSeekTouch(e,pw);}},{{passive:true}});
  document.addEventListener('touchmove',e=>{{if(_audDragging)_audSeekTouch(e,pw);}},{{passive:true}});
  document.addEventListener('touchend',()=>{{_audDragging=false;}});
}}

function _audLoadTrack(onReady){{
  const f=_audList[_audIdx];if(!f||!_audEl)return;
  _pvId=f.id;
  document.getElementById('preview-title').textContent=f.name||'Audio';
  const title=document.getElementById('audio-now-title');if(title)title.textContent=f.name||'Audio';
  const sub=document.getElementById('audio-now-sub');if(sub)sub.textContent=`${{_audIdx+1}} of ${{_audList.length}}`;
  _audEl.src=`/api/preview/${{f.id}}?drive=${{DI}}`;
  _audEl.oncanplay=()=>{{
    if(onReady){{onReady();onReady=null;}}
    _audEl.play().then(()=>{{_audUpdatePlayBtn();_audUpdateArt(true);}}).catch(()=>{{}});
  }};
  _audEl.onerror=()=>{{if(onReady){{onReady();onReady=null;}}}};
  document.querySelectorAll('.apl-item').forEach((el,i)=>el.classList.toggle('active',i===_audIdx));
  const activeEl=document.getElementById(`apl-${{f.id}}`);
  if(activeEl)activeEl.scrollIntoView({{block:'nearest',behavior:'smooth'}});
  _audUpdateBtns();
  _audUpdateArt(false);
}}
function _audSyncProgress(){{
  const vid=_audEl;if(!vid)return;
  const pct=vid.duration?vid.currentTime/vid.duration*100:0;
  const fill=document.getElementById('audio-progress-fill');
  const thumb=document.getElementById('audio-thumb');
  if(fill)fill.style.width=pct+'%';
  if(thumb)thumb.style.left=pct+'%';
  const cur=document.getElementById('aud-cur');if(cur)cur.textContent=_fmtTime(vid.currentTime);
  const dur=document.getElementById('aud-dur');if(dur)dur.textContent=_fmtTime(vid.duration);
}}
function _audSeekTo(e,pw){{
  const a=_audEl;if(!a||!a.duration)return;
  const r=pw.getBoundingClientRect();
  const pct=Math.max(0,Math.min(1,(e.clientX-r.left)/r.width));
  a.currentTime=pct*a.duration;_audSyncProgress();
}}
function _audSeekTouch(e,pw){{if(!e.touches.length)return;_audSeekTo(e.touches[0],pw);}}
function audTogglePlay(){{
  const a=_audEl;if(!a)return;
  if(a.paused){{a.play();_audUpdateArt(true);}}else{{a.pause();_audUpdateArt(false);}}
  _audUpdatePlayBtn();
}}
function _audUpdatePlayBtn(){{
  const ico=document.getElementById('aud-play-ico');if(!ico||!_audEl)return;
  ico.innerHTML=_audEl.paused
    ?'<path d="M8 5v14l11-7z"/>'
    :'<path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/>';
}}
function _audUpdateArt(playing){{
  const art=document.getElementById('audio-art');
  if(art)art.classList.toggle('playing',playing);
}}
function _audUpdateBtns(){{
  const pb=document.getElementById('aud-prev-btn');
  const nb=document.getElementById('aud-next-btn');
  if(pb)pb.disabled=_audIdx===0;
  if(nb)nb.disabled=_audIdx===_audList.length-1;
}}
function audPlayIdx(idx){{
  if(idx<0||idx>=_audList.length)return;
  const wasPlaying=_audEl&&!_audEl.paused;
  _audIdx=idx;
  _audLoadTrack(null);
  if(wasPlaying)setTimeout(()=>{{if(_audEl)_audEl.play();_audUpdateArt(true);_audUpdatePlayBtn();}},120);
}}
function audNavigate(dir){{
  audPlayIdx(_audIdx+dir);
}}

function showUnsupported(name,icon){{
  const body=document.getElementById('preview-body');
  const d=document.createElement('div');
  d.id='preview-unsupported';
  d.innerHTML=`
    <div class="pu-icon-wrap">${{icon}}</div>
    <h3>Can't preview this file</h3>
    <p>No preview is available for this file type.<br>You can download it to open it locally.</p>
    <button class="pu-dl-btn" onclick="previewDownload()">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3"/></svg>
      Download file
    </button>`;
  body.appendChild(d);
}}

function _clearPreviewBody(body,spinner){{
  if(_vidEl){{try{{_vidEl.pause();_vidEl.src='';}}catch(e){{/* ignore */}}}}_vidEl=null;
  if(_audEl){{try{{_audEl.pause();_audEl.src='';}}catch(e){{/* ignore */}}}}_audEl=null;
  clearTimeout(_vidHideTimer);
  [...body.children].forEach(c=>{{if(c!==spinner)c.remove();}});
  spinner.style.display='flex';
}}

function closePreview(){{
  const ov=document.getElementById('preview-overlay');
  ov.classList.remove('open');
  document.getElementById('preview-copy-btn').classList.remove('visible');
  const body=document.getElementById('preview-body');
  const spinner=document.getElementById('preview-spinner');
  if(_vidEl){{try{{_vidEl.pause();_vidEl.src='';}}catch(e){{/* ignore */}}}}_vidEl=null;
  if(_audEl){{try{{_audEl.pause();_audEl.src='';}}catch(e){{/* ignore */}}}}_audEl=null;
  clearTimeout(_vidHideTimer);
  [...body.children].forEach(c=>{{if(c!==spinner)c.remove();}});
  spinner.style.display='none';
  _pvId=null;
}}

function previewDownload(){{
  if(_pvId)dlFile(_pvId);
}}

document.addEventListener('keydown', e=>{{ if(e.key==='Escape'){{
  const ov=document.getElementById('preview-overlay');
  if(ov&&ov.classList.contains('open')){{closePreview();return;}}
}}}});


function startLong(e,id){{
  longPressTimer=setTimeout(()=>longPress(id),500);
}}
function endLong(){{clearTimeout(longPressTimer)}}

function longPress(id){{
  if(!document.body.classList.contains('select-mode')){{
    document.body.classList.add('select-mode');
  }}
  const row=document.querySelector(`[data-id="${{id}}"]`);
  const cb=row?.querySelector('.custom-cb');
  if(cb&&!cb.classList.contains('checked'))toggleSelCustom(id,cb);
}}

function dlFile(id){{window.open(`/api/download/${{id}}?drive=${{DI}}`,'_blank')}}

function toggleSelCustom(id,cb){{
  const isChecked=cb.classList.contains('checked');
  if(isChecked){{
    cb.classList.remove('checked');
    sel.delete(id);
  }}else{{
    cb.classList.add('checked');
    sel.add(id);
  }}
  const r=document.querySelector(`.file-item[data-id="${{id}}"]`);
  if(r)r.classList.toggle('sel',!isChecked);
  if(!document.body.classList.contains('select-mode')){{
    document.body.classList.add('select-mode');
  }}
  updateSel();
}}
function toggleSel(id,cb){{
  cb.checked?sel.add(id):sel.delete(id);
  const r=document.querySelector(`[data-id="${{id}}"]`);
  if(r)r.classList.toggle('sel',cb.checked);
  updateSel();
}}
function updateSel(){{
  const b=document.getElementById('selbar');
  document.getElementById('selcnt').textContent=sel.size+' selected';
  b.classList.toggle('show',sel.size>0);
  if(sel.size===0){{
    document.body.classList.remove('select-mode');
  }}
}}
function clearSel(){{
  sel.clear();
  document.querySelectorAll('.file-item .custom-cb').forEach(c=>c.classList.remove('checked'));
  document.querySelectorAll('.file-item.sel').forEach(r=>r.classList.remove('sel'));
  document.querySelectorAll('.sri.sri-sel').forEach(r=>r.classList.remove('sri-sel'));
  document.body.classList.remove('select-mode');
  document.getElementById('selbar').classList.remove('above-modal');
  const closeBtn=document.querySelector('#selbar .sel-close');
  if(closeBtn)closeBtn.onclick=function(){{clearSel();}};
  updateSel();
}}

function _pushSearchBehind(){{
  const sm=document.getElementById('m-search');
  if(sm&&sm.classList.contains('open'))sm.style.zIndex='900';
}}
function _restoreSearchZ(){{
  const sm=document.getElementById('m-search');
  if(sm)sm.style.zIndex='';
}}

function selDl(){{[...sel].forEach(id=>window.open(`/api/download/${{id}}?drive=${{DI}}`,'_blank'))}}
function selRename(){{
  if(sel.size===1){{_pushSearchBehind();openRename([...sel][0],document.querySelector(`[data-id="${{[...sel][0]}}"]`)?.dataset.name||'')}}
  else toast('Select only one item to rename','warn');
}}
function selMove(){{if(sel.size){{_pushSearchBehind();openMoveFor([...sel])}}}}
function selCopy(){{if(sel.size){{_pushSearchBehind();openCopyFor([...sel])}}}}
function selDel(){{
  if(sel.size){{
    _pushSearchBehind();
    const name=document.querySelector(`[data-id="${{[...sel][0]}}"]`)?.dataset.name||'';
    openDel([...sel],name);
  }}
}}

function toggleFab(){{
  fabOpen=!fabOpen;
  const btn=document.getElementById('fab-btn');
  const opts=document.getElementById('fab-opts');
  if(fabOpen){{
    btn.classList.add('open');
    opts.style.display='flex';
    opts.innerHTML=`
      <div class="fab-opt" onclick="closeFab();openMkdir()">
        ${{IC.rename.replace('width="20"','width="22"').replace('height="20"','height="22"')}}
        <span>New Folder</span>
      </div>
      <div class="fab-opt" onclick="closeFab();openModal('upload')">
        ${{IC.dl.replace('width="20"','width="22"').replace('height="20"','height="22"')}}
        <span>Upload File</span>
      </div>`;
  }}else{{
    closeFab();
  }}
}}
function closeFab(){{
  fabOpen=false;
  document.getElementById('fab-btn').classList.remove('open');
  document.getElementById('fab-opts').style.display='none';
}}
document.addEventListener('click',e=>{{
  if(fabOpen&&!document.getElementById('fab').contains(e.target))closeFab();
}});

function renderAppend(newFiles){{
  const fl=document.getElementById('fl');
  newFiles.sort((a,b)=>{{
    const af=isFol(a),bf=isFol(b);
    if(af&&!bf)return-1;if(!af&&bf)return 1;
    return(a.name||'').toLowerCase()<(b.name||'').toLowerCase()?-1:1;
  }});
  const frag=document.createDocumentFragment();
  newFiles.forEach(f=>{{
    const div=document.createElement('div');
    div.innerHTML=_fileItemHTML(f);
    frag.appendChild(div.firstChild);
  }});
  fl.appendChild(frag);
}}

function openRename(id,name){{
  renameId=id;
  document.getElementById('i-rename').value=name;
  openModal('rename');
  setTimeout(()=>document.getElementById('i-rename').select(),60);
}}
async function doRename(){{
  const name=document.getElementById('i-rename').value.trim();if(!name)return;
  closeModal('rename');bar(true);
  const r=await fetch(`/api/rename/${{renameId}}?drive=${{DI}}`,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{name}})}});
  bar(false);r.ok?toast('Renamed','ok'):toast('Failed to rename','err');load();
}}

function openMkdir(){{
  document.getElementById('i-mkdir').value='';
  openModal('mkdir');
  setTimeout(()=>document.getElementById('i-mkdir').focus(),60);
}}
async function doMkdir(){{
  const name=document.getElementById('i-mkdir').value.trim();if(!name)return;
  closeModal('mkdir');bar(true);
  const r=await fetch(`/api/mkdir?drive=${{DI}}`,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{name,parent:folder}})}});
  bar(false);r.ok?toast('Folder created','ok'):toast('Failed','err');load();
}}

function openDel(ids,name){{
  delIds=ids;
  document.getElementById('del-msg').textContent=ids.length===1?`Delete "${{name}}"?`:`Delete ${{ids.length}} items?`;
  openModal('delete');
}}
async function doDelete(){{
  closeModal('delete');bar(true);
  await Promise.all(delIds.map(id=>fetch(`/api/delete/${{id}}?drive=${{DI}}`,{{method:'POST'}})));
  bar(false);toast('Deleted','ok');clearSel();load();
}}

async function openMoveFor(ids){{moveIds=ids;openModal('move');await loadTree('move-tree')}}
async function loadTree(tid){{
  const el=document.getElementById(tid);
  el.innerHTML='<div style="padding:16px;text-align:center;color:var(--text3);font-size:14px">Loading...</div>';
  const r=await fetch(`/api/files?drive=${{DI}}&folder=root`);
  const d=await r.json();
  const fols=(d.files||[]).filter(f=>isFol(f));
  el.innerHTML=`<div class="fti" data-fid="root" onclick="pickTree(this)">${{IC.folder}} My Drive</div>`+
    fols.map(f=>`<div class="fti" data-fid="${{f.id}}" onclick="pickTree(this)">&nbsp;&nbsp;${{IC.folder}} ${{f.name}}</div>`).join('');
}}
function pickTree(el){{el.closest('.ftree').querySelectorAll('.fti').forEach(e=>e.classList.remove('sel'));el.classList.add('sel')}}
async function confirmMove(){{
  const s=document.querySelector('#move-tree .fti.sel');if(!s){{toast('Select a folder','warn');return}}
  closeModal('move');bar(true);
  await Promise.all(moveIds.map(id=>fetch(`/api/move/${{id}}?drive=${{DI}}`,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{parent:s.dataset.fid}})}})));
  bar(false);toast('Moved','ok');clearSel();load();
}}

async function openCopyFor(ids){{copyIds=ids;openModal('copy');await loadTree('copy-tree')}}
async function confirmCopy(){{
  const s=document.querySelector('#copy-tree .fti.sel');if(!s){{toast('Select a folder','warn');return}}
  closeModal('copy');bar(true);
  await Promise.all(copyIds.map(id=>fetch(`/api/copy/${{id}}?drive=${{DI}}`,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{parent:s.dataset.fid}})}})));
  bar(false);toast('Copied','ok');clearSel();load();
}}

const dz=document.getElementById('dz');
if(dz){{
  dz.addEventListener('dragover',e=>{{e.preventDefault();dz.classList.add('dragover')}});
  dz.addEventListener('dragleave',()=>dz.classList.remove('dragover'));
  dz.addEventListener('drop',e=>{{e.preventDefault();dz.classList.remove('dragover');addFiles(e.dataTransfer.files)}});
}}
function addFiles(flist){{
  [...flist].forEach(f=>{{
    const id='u'+Date.now()+Math.random().toString(36).slice(2);
    uploadQ.push({{id,file:f,status:'pending'}});
    const el=document.createElement('div');el.className='uitem';el.id=id;
    el.innerHTML=`<div class="uitem-top"><span>${{getIco(f.type)}}</span><span class="uname">${{f.name}}</span><span class="ust" id="${{id}}-st">Pending</span></div><div class="ubar"><div class="ufill" id="${{id}}-f"></div></div>`;
    document.getElementById('ulist').appendChild(el);
  }});
  document.getElementById('fi').value='';
}}
async function startUpload(){{
  if(!uploadQ.length){{toast('No files selected','warn');return}}
  document.getElementById('ubtn').disabled=true;
  for(const item of uploadQ){{
    if(item.status!=='pending')continue;
    const st=document.getElementById(item.id+'-st');
    const fill=document.getElementById(item.id+'-f');
    st.textContent='Uploading...';st.style.color='var(--accent)';
    try{{
      await new Promise((res,rej)=>{{
        const xhr=new XMLHttpRequest();xhr.open('POST','/api/upload');
        xhr.upload.onprogress=e=>{{if(e.lengthComputable){{const p=Math.round(e.loaded/e.total*100);fill.style.width=p+'%';st.textContent=p+'%';}}}}
        xhr.onload=()=>{{if(xhr.status<300){{fill.style.width='100%';fill.style.background='var(--green)';st.textContent='Done';st.style.color='var(--green)';item.status='done';res()}}else{{st.textContent='Error';st.style.color='var(--red)';item.status='err';rej()}}}}
        xhr.onerror=()=>{{st.textContent='Error';st.style.color='var(--red)';rej()}};
        const fd=new FormData();fd.append('file',item.file);fd.append('folder',folder);fd.append('drive',DI);
        xhr.send(fd);
      }});
    }}catch(e){{item.status='err'}}
  }}
  document.getElementById('ubtn').disabled=false;
  const done=uploadQ.filter(x=>x.status==='done').length;
  if(done)toast(`Uploaded ${{done}} file${{done>1?'s':''}}`, 'ok');
  closeModal('upload');uploadQ=[];document.getElementById('ulist').innerHTML='';
  load();
}}

function doNavSearch(){{
  const q=document.getElementById('nav-search-input').value.trim();
  if(!q)return;
  document.getElementById('i-search').value=q;
  document.getElementById('search-res').innerHTML='';
  openModal('search');
  doSearch();
}}
function onNavSearchInput(v){{
}}

let _sriLongTimer=null;
function closeSriFromSearch(){{
  const sr=document.getElementById('search-res');
  sr&&sr.querySelectorAll('.sri.sri-sel').forEach(e=>e.classList.remove('sri-sel'));
  sr&&sr.querySelectorAll('.custom-cb').forEach(c=>c.classList.remove('checked'));
  sr&&sr.querySelectorAll('.sri-cb').forEach(c=>c.style.display='');
  clearSel();
}}
function sriLongPress(f,el){{
  if(!files.find(x=>x.id===f.id)) files.push(f);
  sel.clear();
  document.querySelectorAll('.sri .custom-cb').forEach(c=>c.classList.remove('checked'));
  document.querySelectorAll('.sri.sri-sel').forEach(e=>e.classList.remove('sri-sel'));
  sel.add(f.id);
  el.classList.add('sri-sel');
  const cb=el.querySelector('.custom-cb');
  if(cb)cb.classList.add('checked');
  document.querySelectorAll('.sri-cb').forEach(c=>c.style.display='flex');
  const selbar=document.getElementById('selbar');
  selbar.classList.add('above-modal');
  document.getElementById('selcnt').textContent='1 selected';
  selbar.classList.add('show');
  document.querySelector('#selbar .sel-close').onclick=function(){{closeSriFromSearch();}};
}}
function sriToggleCb(id,cb){{
  const el=document.getElementById('search-res').querySelector(`[data-id="${{id}}"]`);
  const f=el&&el._sriData;if(!f)return;
  const isChecked=cb.classList.contains('checked');
  if(isChecked){{
    cb.classList.remove('checked');
    sel.delete(id);
    el.classList.remove('sri-sel');
  }}else{{
    if(!files.find(x=>x.id===id))files.push(f);
    cb.classList.add('checked');
    sel.add(id);
    el.classList.add('sri-sel');
    const selbar=document.getElementById('selbar');
    selbar.classList.add('above-modal');
    selbar.classList.add('show');
    document.querySelector('#selbar .sel-close').onclick=function(){{closeSriFromSearch();}};
  }}
  document.getElementById('selcnt').textContent=`${{sel.size}} selected`;
  if(sel.size===0){{clearSel();document.querySelectorAll('.sri-cb').forEach(c=>c.style.display='');}}
}}

function openSearchModal(){{
  document.getElementById('i-search').value='';
  document.getElementById('search-res').innerHTML='';
  openModal('search');
  setTimeout(()=>document.getElementById('i-search').focus(),60);
}}
async function doSearch(){{
  const q=document.getElementById('i-search').value.trim();if(!q)return;
  const el=document.getElementById('search-res');
  clearSel();
  el.innerHTML='<div style="padding:16px;text-align:center;color:var(--text3);font-size:14px">Searching...</div>';
  const r=await fetch(`/api/search?drive=${{DI}}&q=${{encodeURIComponent(q)}}`);
  const d=await r.json();
  if(!d.files||!d.files.length){{
    el.innerHTML=`<div style="padding:20px;text-align:center;color:var(--text3);font-size:14px">No results for "${{q}}"</div>`;
    return;
  }}
  el.innerHTML=d.files.map(f=>{{
    const isF=isFol(f);
    const nm=f.name.replace(/"/g,'&quot;').replace(/'/g,'&#39;');
    const sizeStr=isF?'Directory':sz(f.size);
    const dateStr=dt(f.modifiedTime);
    const selCls=sel.has(f.id)?'sri sri-sel':'sri';
    const cbCls=sel.has(f.id)?'custom-cb checked':'custom-cb';
    return`<div class="${{selCls}}" data-id="${{f.id}}"
        onclick="sriClick(event,'${{f.id}}')"
        oncontextmenu="event.preventDefault();sriLongPressById('${{f.id}}')"
        ontouchstart="sriStartLong(event,'${{f.id}}')" ontouchend="sriEndLong()" ontouchmove="sriEndLong()">
      <div class="sri-cb" onclick="event.stopPropagation();sriToggleCb('${{f.id}}',this.querySelector('.custom-cb'))">
        <div class="${{cbCls}}"></div>
      </div>
      <div class="sri-icon">${{getIco(f.mimeType)}}</div>
      <div class="sri-info">
        <div class="sri-name">${{f.name}}</div>
        <div class="sri-meta">
          <span class="sri-size">${{sizeStr}}</span>
          <span class="sri-date">${{dateStr}}</span>
        </div>
      </div>
    </div>`;
  }}).join('');
  d.files.forEach(f=>{{
    const el2=el.querySelector(`[data-id="${{f.id}}"]`);
    if(el2)el2._sriData=f;
  }});
}}
function sriClick(e,id){{
  if(e.target.classList.contains('custom-cb'))return;
  const el=document.getElementById('search-res').querySelector(`[data-id="${{id}}"]`);
  const f=el&&el._sriData;if(!f)return;
  if(document.getElementById('selbar').classList.contains('show')){{
    if(!files.find(x=>x.id===f.id)) files.push(f);
    const cb=el.querySelector('.custom-cb');
    if(sel.has(id)){{
      sel.delete(id);
      el.classList.remove('sri-sel');
      if(cb)cb.classList.remove('checked');
    }}else{{
      sel.add(id);
      el.classList.add('sri-sel');
      if(cb)cb.classList.add('checked');
    }}
    if(sel.size===0){{clearSel();document.querySelectorAll('.sri-cb').forEach(c=>c.style.display='');return;}}
    document.getElementById('selcnt').textContent=`${{sel.size}} selected`;
    return;
  }}
  if(isFol(f)){{closeModal('search');clearSel();navFolder(f.id,f.name);}}
  else {{closeModal('search');if(!files.find(x=>x.id===f.id))files.push(f);openPreview(f.id,f.mimeType,f.name);}}
}}
function sriLongPressById(id){{
  const el=document.getElementById('search-res').querySelector(`[data-id="${{id}}"]`);
  const f=el&&el._sriData;if(!f)return;
  sriLongPress(f,el);
}}
function sriStartLong(e,id){{_sriLongTimer=setTimeout(()=>sriLongPressById(id),500);}}
function sriEndLong(){{clearTimeout(_sriLongTimer);}}

document.addEventListener('keydown',e=>{{
  if(e.key==='Enter'){{
    if(document.getElementById('m-rename').classList.contains('open'))doRename();
    else if(document.getElementById('m-mkdir').classList.contains('open'))doMkdir();
    else if(document.getElementById('m-search').classList.contains('open'))doSearch();
  }}
  if(e.key==='Escape'){{
    const sm=document.getElementById('m-search');
    if(sm)sm.style.zIndex='';
    document.querySelectorAll('.moverlay.open').forEach(m=>m.classList.remove('open'));
    clearSel();closeFab();
  }}
}});

let _storageLoaded=false;
function toggleAvPopup(){{
  const p=document.getElementById('av-popup');
  const open=p.classList.toggle('open');
  if(open&&!_storageLoaded)loadStorageInfo();
}}
async function loadStorageInfo(){{
  try{{
    const r=await fetch(`/api/storage?drive=${{DI}}`);
    if(!r.ok)throw new Error('HTTP '+r.status);
    const d=await r.json();
    _storageLoaded=true;
    const limit=d.limit,usage=d.usage;
    const pct=limit>0?Math.min(100,Math.round(usage/limit*100)):0;
    const warn=pct>=80;
    const fmtUsage=_fmtBytes(usage);
    const fmtLimit=limit>0?_fmtBytes(limit):'Unlimited';
    document.getElementById('avp-storage').innerHTML=`
      <div class="avp-bar-wrap"><div class="avp-bar-fill${{warn?' warn':''}}" style="width:${{pct}}%"></div></div>
      <div class="avp-storage-txt"><strong>${{fmtUsage}}</strong> used of ${{fmtLimit}}${{limit>0?` (${{pct}}%)`:''}}</div>`;
  }}catch(e){{
    document.getElementById('avp-storage').innerHTML=`<div style="font-size:12px;color:var(--text3)">Could not load storage info</div>`;
  }}
}}
function _fmtBytes(b){{
  if(!b)return'0 B';
  if(b<1024)return b+' B';
  if(b<1048576)return(b/1024).toFixed(1)+' KB';
  if(b<1073741824)return(b/1048576).toFixed(1)+' MB';
  if(b<1099511627776)return(b/1073741824).toFixed(2)+' GB';
  return(b/1099511627776).toFixed(2)+' TB';
}}
document.addEventListener('click',e=>{{
  const p=document.getElementById('av-popup');
  const av=document.getElementById('nav-av');
  if(p&&p.classList.contains('open')&&!p.contains(e.target)&&!av.contains(e.target)){{
    p.classList.remove('open');
  }}
}});

const ACCENT_COLORS = [
  {{name:'Blue',     hex:'#0483c3', hex2:'#0369a1'}},
  {{name:'Indigo',   hex:'#6366f1', hex2:'#4f46e5'}},
  {{name:'Purple',   hex:'#a855f7', hex2:'#9333ea'}},
  {{name:'Pink',     hex:'#ec4899', hex2:'#db2777'}},
  {{name:'Rose',     hex:'#f43f5e', hex2:'#e11d48'}},
  {{name:'Orange',   hex:'#f97316', hex2:'#ea6c0a'}},
  {{name:'Amber',    hex:'#f59e0b', hex2:'#d97706'}},
  {{name:'Green',    hex:'#22c55e', hex2:'#16a34a'}},
  {{name:'Teal',     hex:'#14b8a6', hex2:'#0d9488'}},
  {{name:'Cyan',     hex:'#06b6d4', hex2:'#0891b2'}},
];
function _hexToRgba(hex,a){{
  const r=parseInt(hex.slice(1,3),16),g=parseInt(hex.slice(3,5),16),b=parseInt(hex.slice(5,7),16);
  return`rgba(${{r}},${{g}},${{b}},${{a}})`;
}}
function applyAccent(hex,hex2,save){{
  const r=document.documentElement.style;
  r.setProperty('--accent',hex);
  r.setProperty('--accent2',hex2);
  r.setProperty('--accent-dim',_hexToRgba(hex,.13));
  r.setProperty('--folder',hex);
  document.querySelectorAll('.ac-sw').forEach(s=>s.classList.toggle('ac-sw-on',s.dataset.hex===hex));
  if(save)localStorage.setItem('gdrive_accent',JSON.stringify({{hex,hex2}}));
}}
function initAccentSwatches(){{
  const el=document.getElementById('accent-swatches');
  if(!el)return;
  const saved=JSON.parse(localStorage.getItem('gdrive_accent')||'null');
  const activeHex=(saved&&saved.hex)||'#0483c3';
  el.innerHTML=ACCENT_COLORS.map(c=>`
    <button class="ac-sw${{c.hex===activeHex?' ac-sw-on':''}}" data-hex="${{c.hex}}" data-hex2="${{c.hex2}}"
      title="${{c.name}}"
      onclick="applyAccent('${{c.hex}}','${{c.hex2}}',true)"
      style="width:28px;height:28px;border-radius:50%;background:${{c.hex}};border:2px solid ${{c.hex===activeHex?'#fff':'transparent'}};
             box-shadow:${{c.hex===activeHex?'0 0 0 2px '+c.hex:'none'}};cursor:pointer;transition:all .15s;flex-shrink:0;outline:none">
    </button>`).join('');
  if(saved)applyAccent(saved.hex,saved.hex2,false);
}}
(function(){{
  const orig=applyAccent;
  const _orig=applyAccent;
  window.applyAccent=function(hex,hex2,save){{
    _orig(hex,hex2,save);
    document.querySelectorAll('.ac-sw').forEach(s=>{{
      const on=s.dataset.hex===hex;
      s.style.border='2px solid '+(on?'#fff':'transparent');
      s.style.boxShadow=on?'0 0 0 2px '+hex:'none';
    }});
  }};
}})();

initAccentSwatches();

// ── Drives accordion in avatar popup ──
function initDrivesAccordion(){{
  const sec = document.getElementById('avp-drives-section');
  if(!sec) return;

  const chevronSVG = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M8.25 4.5l7.5 7.5-7.5 7.5"/></svg>`;
  const addIconSVG = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M19 7.5v3m0 0v3m0-3h3m-3 0h-3m-2.25-4.125a3.375 3.375 0 11-6.75 0 3.375 3.375 0 016.75 0zM4 19.235v-.11a6.375 6.375 0 0112.75 0v.109A12.318 12.318 0 0110.374 21c-2.331 0-4.512-.645-6.374-1.766z"/></svg>`;

  const driveRows = DRIVES_DATA.map(d => {{
    const isCurrent = d.index === DI;
    const av = d.picture
      ? `<img src="${{d.picture}}" onerror="this.style.display='none'">`
      : `<span>${{(d.email||'?')[0].toUpperCase()}}</span>`;
    return `<a href="/drive/${{d.index}}" class="avp-drive-row${{isCurrent?' current':''}}" ${{isCurrent?'onclick="return false"':''}}>
      <div class="avp-drive-av">${{av}}</div>
      <div class="avp-drive-info">
        <div class="avp-drive-email">${{d.email}}</div>
        <div class="avp-drive-sub">Drive ${{d.index+1}}${{isCurrent?' · Current':''}}</div>
      </div>
    </a>`;
  }}).join('');

  const addRow = `<a href="${{AUTH_URL}}" class="avp-drive-row" style="color:var(--accent)">
    <div class="avp-drive-av" style="background:var(--surface3);color:var(--accent)">${{addIconSVG}}</div>
    <div class="avp-drive-info">
      <div class="avp-drive-email">Add account</div>
      <div class="avp-drive-sub">Connect Google Drive</div>
    </div>
  </a>`;

  sec.innerHTML = `
    <div class="avp-drives-toggle" id="avp-drives-toggle" onclick="toggleDrivesAccordion()">
      <span class="avp-drives-label">Drives</span>
      <span class="avp-drives-chevron" id="avp-drives-chevron">${{chevronSVG}}</span>
    </div>
    <div class="avp-drives-body" id="avp-drives-body">
      <div style="padding:4px 0">${{driveRows}}${{addRow}}</div>
    </div>`;
}}

function toggleDrivesAccordion(){{
  const body = document.getElementById('avp-drives-body');
  const chev = document.getElementById('avp-drives-chevron');
  if(!body) return;
  const open = body.classList.toggle('open');
  chev && chev.classList.toggle('open', open);
}}

initDrivesAccordion();
</script>""", f"My Drive — {email}")


async def handle_oauth_callback(request: web.Request) -> web.Response:
    code  = request.rel_url.query.get("code")
    state = request.rel_url.query.get("state")
    err   = request.rel_url.query.get("error")

    def _err(msg):
        return _page(f"""
<div class="info-page">
  <div class="info-card">
    <div style="color:var(--red)">{_icon("alert", 52)}</div>
    <h2>Authorization Failed</h2>
    <p>{msg}</p>
    <a href="/" class="btn btn-ghost" style="margin-top:24px">{_icon("back",16)} Back to login</a>
  </div>
</div>""", "Error")

    if err: return _err(err)
    if not code: return _err("No authorization code received from Google.")

    db: Database = request.app["db"]
    gdrive: GoogleDriveManager = request.app["gdrive"]

    uid = None
    tok = request.cookies.get("session")
    p = _verify(tok) if tok else None
    if p: uid = p["uid"]
    if uid is None and state:
        uid = await db.get_user_id_by_oauth_state(state)
    if uid is None and state:
        for cu, pd in _web_pending_flows.items():
            if pd.get("state") == state:
                uid = cu; break
    if uid is None:
        return _err("Session not found. Log in to the WebUI first, then click Add account.")

    flow = None
    pd = _web_pending_flows.get(uid)
    if pd and (not state or pd.get("state") == state):
        flow = pd["flow"]
    if flow is None:
        try:
            from handlers.cmd_auth import _pending_flows as _bf
            bp = _bf.get(uid)
            if bp and (not state or bp.get("state") == state):
                flow = bp["flow"]
        except ImportError:
            pass
    if flow is None:
        flow = gdrive.build_web_flow()

    try:
        email, idx = await gdrive.exchange_code(uid, code, flow, None)
        _web_pending_flows.pop(uid, None)
        await db.delete_oauth_state(uid)
        try:
            from handlers.cmd_auth import _pending_flows as _bf
            _bf.pop(uid, None)
        except ImportError:
            pass

        await _save_pic(gdrive, db, uid, idx)

        bot = request.app.get("bot")
        if bot:
            try:
                await bot.send_message(uid, f"\u2705 **Drive Connected!**\n\n\U0001F4E7 {email}\n\nOpen the WebUI to manage files.")
            except Exception as e:
                logger.warning(f"Telegram notify failed: {e}")

        return _page(f"""
<div class="info-page">
  <div class="info-card">
    <div style="color:var(--green)">{_icon("shield_check", 52)}</div>
    <h2>Drive Connected!</h2>
    <p style="margin-bottom:4px">{email}</p>
    <p>Your Google Drive has been linked successfully.</p>
    <a href="/" class="btn btn-primary" style="margin-top:24px;justify-content:center">{_icon("login_arrow",16)} Open WebUI</a>
  </div>
</div>""", "Connected")

    except Exception as e:
        logger.error(f"OAuth callback error uid={uid}: {e}", exc_info=True)
        _web_pending_flows.pop(uid, None)
        await db.delete_oauth_state(uid)
        return _err("Authorization code expired or invalid. Please try again.")


async def _save_pic(gdrive, db, user_id, drive_index):
    try:
        creds = await gdrive._get_credentials(user_id, drive_index)
        if not creds: return
        from googleapiclient.discovery import build as _build
        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(None, lambda: _build("oauth2","v2",credentials=creds,cache_discovery=False).userinfo().get().execute())
        pic = info.get("picture")
        if pic:
            doc = await db._get_doc(user_id)
            drives = list((doc or {}).get("drives", []))
            if 0 <= drive_index < len(drives):
                drives[drive_index]["picture"] = pic
                await db.db.users.update_one({"user_id": user_id}, {"$set": {"drives": drives}})
                await db._invalidate(user_id)
    except Exception as e:
        logger.warning(f"Could not save profile pic: {e}")


@require_auth
async def api_files(request: web.Request) -> web.Response:
    uid = request["uid"]
    di  = int(request.rel_url.query.get("drive", 0))
    fid = request.rel_url.query.get("folder", "root")
    pt  = request.rel_url.query.get("page_token") or None
    gdrive: GoogleDriveManager = request.app["gdrive"]
    try:
        files, next_token = await gdrive.list_folder(uid, fid, pt, di, page_size=WEBUI_PAGE_SIZE)
        return web.json_response({"files": files, "next_page_token": next_token})
    except PermissionError:
        raise web.HTTPUnauthorized()
    except Exception as e:
        logger.error(f"api_files: {e}", exc_info=True)
        raise web.HTTPInternalServerError(reason=str(e))

@require_auth
async def api_storage(request: web.Request) -> web.Response:
    uid = request["uid"]
    di  = int(request.rel_url.query.get("drive", 0))
    gdrive: GoogleDriveManager = request.app["gdrive"]
    try:
        quota = await gdrive.get_storage_quota(uid, di)
        return web.json_response(quota)
    except PermissionError:
        raise web.HTTPUnauthorized()
    except Exception as e:
        logger.error(f"api_storage: {e}", exc_info=True)
        raise web.HTTPInternalServerError(reason=str(e))

@require_auth
async def api_rename(request: web.Request) -> web.Response:
    uid  = request["uid"]
    fid  = request.match_info["fid"]
    di   = int(request.rel_url.query.get("drive", 0))
    body = await request.json()
    name = body.get("name", "").strip()
    if not name: raise web.HTTPBadRequest(reason="Name required")
    try:
        return web.json_response(await request.app["gdrive"].rename_file(uid, fid, name, di))
    except Exception as e:
        raise web.HTTPInternalServerError(reason=str(e))

@require_auth
async def api_delete(request: web.Request) -> web.Response:
    uid = request["uid"]
    fid = request.match_info["fid"]
    di  = int(request.rel_url.query.get("drive", 0))
    try:
        await request.app["gdrive"].delete_file(uid, fid, di)
        return web.json_response({"ok": True})
    except Exception as e:
        raise web.HTTPInternalServerError(reason=str(e))

@require_auth
async def api_mkdir(request: web.Request) -> web.Response:
    uid    = request["uid"]
    di     = int(request.rel_url.query.get("drive", 0))
    body   = await request.json()
    name   = body.get("name", "").strip()
    parent = body.get("parent", "root")
    if not name: raise web.HTTPBadRequest(reason="Name required")
    try:
        return web.json_response(await request.app["gdrive"].create_folder(uid, name, parent, di))
    except Exception as e:
        raise web.HTTPInternalServerError(reason=str(e))

@require_auth
async def api_download(request: web.Request) -> web.Response:
    """Proxy download: stream bytes from Google through our server (no browser redirect).
    This avoids Google's automated-request detection which triggers when browsers
    hit googleapis.com directly with an access_token in the query string.
    Instead we fetch server-to-server using Authorization: Bearer, just like goindex.
    """
    import aiohttp as _aiohttp
    uid = request["uid"]
    fid = request.match_info["fid"]
    di  = int(request.rel_url.query.get("drive", 0))
    try:
        meta         = await request.app["gdrive"].get_file_meta_for_download(uid, fid, di)
        mime         = meta.get("mimeType", "")
        export_map   = meta.get("export_map", {})
        access_token = meta.get("access_token", "")
        file_id      = meta.get("id", fid)
        file_name    = meta.get("name", fid)

        if mime in export_map:
            export_mime, ext = export_map[mime]
            gdrive_url = (f"https://www.googleapis.com/drive/v3/files/{file_id}/export"
                          f"?alt=media&mimeType={urllib.parse.quote(export_mime)}")
            if not file_name.endswith(ext):
                file_name += ext
        else:
            gdrive_url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"

        req_headers = {"Authorization": f"Bearer {access_token}"}
        range_hdr = request.headers.get("Range")
        if range_hdr:
            req_headers["Range"] = range_hdr

        async with _aiohttp.ClientSession() as session:
            async with session.get(gdrive_url, headers=req_headers) as gr:
                if gr.status == 403:
                    abuse_url = gdrive_url + "&acknowledgeAbuse=true"
                    async with session.get(abuse_url, headers=req_headers) as gr2:
                        gr = gr2

                resp_headers = {
                    "Content-Disposition": (
                        f'attachment; filename="{urllib.parse.quote(file_name)}"'
                    ),
                    "Content-Type": gr.headers.get(
                        "Content-Type", "application/octet-stream"
                    ),
                }
                for h in ("Content-Length", "Content-Range", "Accept-Ranges"):
                    if h in gr.headers:
                        resp_headers[h] = gr.headers[h]

                response = web.StreamResponse(status=gr.status, headers=resp_headers)
                await response.prepare(request)
                async for chunk in gr.content.iter_chunked(65536):
                    await response.write(chunk)
                await response.write_eof()
                return response

    except web.HTTPException:
        raise
    except PermissionError:
        raise web.HTTPUnauthorized()
    except Exception as e:
        logger.error(f"api_download: {e}", exc_info=True)
        raise web.HTTPInternalServerError(reason=str(e))

@require_auth
async def api_preview(request: web.Request) -> web.Response:
    """Serve file inline for browser preview (images, video, audio, PDF, text)."""
    import aiohttp as _aiohttp
    uid = request["uid"]
    fid = request.match_info["fid"]
    di  = int(request.rel_url.query.get("drive", 0))
    try:
        meta         = await request.app["gdrive"].get_file_meta_for_download(uid, fid, di)
        mime         = meta.get("mimeType", "")
        export_map   = meta.get("export_map", {})
        access_token = meta.get("access_token", "")
        file_id      = meta.get("id", fid)
        file_name    = meta.get("name", fid)

        if mime in export_map:
            export_mime, ext = export_map[mime]
            gdrive_url = (f"https://www.googleapis.com/drive/v3/files/{file_id}/export"
                          f"?alt=media&mimeType={urllib.parse.quote(export_mime)}")
            if not file_name.endswith(ext):
                file_name += ext
            serve_mime = export_mime
        else:
            gdrive_url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"
            serve_mime = mime or "application/octet-stream"

        req_headers = {"Authorization": f"Bearer {access_token}"}
        range_hdr = request.headers.get("Range")
        if range_hdr:
            req_headers["Range"] = range_hdr

        async with _aiohttp.ClientSession() as session:
            async with session.get(gdrive_url, headers=req_headers) as gr:
                if gr.status == 403:
                    abuse_url = gdrive_url + "&acknowledgeAbuse=true"
                    async with session.get(abuse_url, headers=req_headers) as gr2:
                        gr = gr2

                resp_headers = {
                    "Content-Disposition": (
                        f'inline; filename="{urllib.parse.quote(file_name)}"'
                    ),
                    "Content-Type": gr.headers.get("Content-Type", serve_mime),
                }
                for h in ("Content-Length", "Content-Range", "Accept-Ranges"):
                    if h in gr.headers:
                        resp_headers[h] = gr.headers[h]

                response = web.StreamResponse(status=gr.status, headers=resp_headers)
                await response.prepare(request)
                async for chunk in gr.content.iter_chunked(65536):
                    await response.write(chunk)
                await response.write_eof()
                return response

    except web.HTTPException:
        raise
    except PermissionError:
        raise web.HTTPUnauthorized()
    except Exception as e:
        logger.error(f"api_preview: {e}", exc_info=True)
        raise web.HTTPInternalServerError(reason=str(e))

@require_auth
async def api_move(request: web.Request) -> web.Response:
    uid    = request["uid"]
    fid    = request.match_info["fid"]
    di     = int(request.rel_url.query.get("drive", 0))
    body   = await request.json()
    parent = body.get("parent", "root")
    try:
        return web.json_response(await request.app["gdrive"].move_file(uid, fid, parent, di))
    except Exception as e:
        raise web.HTTPInternalServerError(reason=str(e))

@require_auth
async def api_copy(request: web.Request) -> web.Response:
    uid    = request["uid"]
    fid    = request.match_info["fid"]
    di     = int(request.rel_url.query.get("drive", 0))
    body   = await request.json()
    parent = body.get("parent")
    try:
        return web.json_response(await request.app["gdrive"].copy_file(uid, fid, parent, None, di))
    except Exception as e:
        raise web.HTTPInternalServerError(reason=str(e))

@require_auth
async def api_upload(request: web.Request) -> web.Response:
    uid = request["uid"]
    gdrive: GoogleDriveManager = request.app["gdrive"]
    try:
        reader = await request.multipart()
        di = 0; folder_id = "root"; file_data = None; file_name = "upload"
        async for part in reader:
            if part.name == "drive":
                di = int(await part.read_chunk())
            elif part.name == "folder":
                folder_id = (await part.read_chunk()).decode()
            elif part.name == "file":
                file_name = part.filename or "upload"
                chunks = []
                while True:
                    chunk = await part.read_chunk(65536)
                    if not chunk: break
                    chunks.append(chunk)
                file_data = b"".join(chunks)
        if file_data is None:
            raise web.HTTPBadRequest(reason="No file")
        return web.json_response(await gdrive.upload_bytes(uid, file_data, file_name, folder_id, di))
    except web.HTTPBadRequest:
        raise
    except Exception as e:
        logger.error(f"api_upload: {e}", exc_info=True)
        raise web.HTTPInternalServerError(reason=str(e))

@require_auth
async def api_search(request: web.Request) -> web.Response:
    uid = request["uid"]
    di  = int(request.rel_url.query.get("drive", 0))
    q   = request.rel_url.query.get("q", "").strip()
    if not q: return web.json_response({"files": []})
    try:
        files = await request.app["gdrive"].search_files(uid, q, di)
        return web.json_response({"files": files})
    except Exception as e:
        raise web.HTTPInternalServerError(reason=str(e))


def create_app(db: Database, gdrive: GoogleDriveManager, bot=None) -> web.Application:
    import os
    app = web.Application(client_max_size=Config.MAX_FILE_SIZE + 10 * 1024 * 1024)
    app["db"]     = db
    app["gdrive"] = gdrive
    app["bot"]    = bot

    _webui_dir = os.path.dirname(os.path.abspath(__file__))
    app.router.add_static("/static", _webui_dir, show_index=False)

    app.router.add_route("GET",  "/",            handle_login)
    app.router.add_route("POST", "/",            handle_login)
    app.router.add_get("/logout",                handle_logout)
    app.router.add_get("/drives",                handle_drives)
    app.router.add_get("/drive/{di}",            handle_browser)
    app.router.add_get("/oauth/callback",        handle_oauth_callback)
    app.router.add_get("/api/files",             api_files)
    app.router.add_post("/api/rename/{fid}",     api_rename)
    app.router.add_post("/api/delete/{fid}",     api_delete)
    app.router.add_post("/api/mkdir",            api_mkdir)
    app.router.add_get("/api/download/{fid}",    api_download)
    app.router.add_get("/api/preview/{fid}",     api_preview)
    app.router.add_post("/api/move/{fid}",       api_move)
    app.router.add_post("/api/copy/{fid}",       api_copy)
    app.router.add_post("/api/upload",           api_upload)
    app.router.add_get("/api/search",            api_search)
    app.router.add_get("/api/storage",           api_storage)
    return app
