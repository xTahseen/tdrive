import os
from dotenv import load_dotenv

load_dotenv()


def _parse_int_list(env_val: str) -> list[int]:
    """Parse a comma-separated list of Telegram user IDs from an env var."""
    if not env_val:
        return []
    return [int(x.strip()) for x in env_val.split(",") if x.strip().isdigit()]


class Config:
    API_ID: int = int(os.getenv("API_ID", "0"))
    API_HASH: str = os.getenv("API_HASH", "")
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")

    MONGO_URI: str = os.getenv("MONGO_URI", "mongodb://localhost:27017")
    DB_NAME: str = os.getenv("DB_NAME", "gdrive_bot")

    GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "")

    OAUTH_REDIRECT_URI: str = os.getenv("OAUTH_REDIRECT_URI", "http://localhost")

    GOOGLE_SCOPES: list = [
        "https://www.googleapis.com/auth/drive",
        "https://www.googleapis.com/auth/drive.file",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile",
        "openid",
    ]

    MAX_FILE_SIZE: int = int(os.getenv("MAX_FILE_SIZE", str(2 * 1024 * 1024 * 1024)))
    TEMP_DIR: str = os.getenv("TEMP_DIR", "./downloads")

    # ── Admin ──────────────────────────────────────────────────────────────────
    # Comma-separated Telegram user IDs that may use /stats
    # e.g.  ADMIN_IDS=123456789,987654321
    ADMIN_IDS: list[int] = _parse_int_list(os.getenv("ADMIN_IDS", ""))

    # Telegram group/channel ID where all activity logs are sent
    # Use a negative ID for groups/channels, e.g. LOG_GROUP_ID=-1001234567890
    LOG_GROUP_ID: int | None = int(os.getenv("LOG_GROUP_ID", "0")) or None

    # ── Upload queue ───────────────────────────────────────────────────────────
    UPLOAD_WORKERS: int = int(os.getenv("UPLOAD_WORKERS", "4"))
    UPLOAD_QUEUE_PER_USER: int = int(os.getenv("UPLOAD_QUEUE_PER_USER", "5"))

    # ── WebUI settings ─────────────────────────────────────────────────────────
    WEBUI_ENABLED: bool = os.getenv("WEBUI_ENABLED", "false").lower() in ("1", "true", "yes")
    WEBUI_HOST: str = os.getenv("WEBUI_HOST", "0.0.0.0")
    WEBUI_PORT: int = int(os.getenv("WEBUI_PORT", "8080"))
    WEBUI_BASE_URL: str = os.getenv("WEBUI_BASE_URL", "http://localhost:8080")
    WEBUI_SECRET_KEY: str = os.getenv("WEBUI_SECRET_KEY", "change-me-in-production")

    @classmethod
    def validate(cls):
        required = {
            "API_ID":               cls.API_ID,
            "API_HASH":             cls.API_HASH,
            "BOT_TOKEN":            cls.BOT_TOKEN,
            "MONGO_URI":            cls.MONGO_URI,
            "GOOGLE_CLIENT_ID":     cls.GOOGLE_CLIENT_ID,
            "GOOGLE_CLIENT_SECRET": cls.GOOGLE_CLIENT_SECRET,
        }

        missing = [k for k, v in required.items() if not v or v == 0]
        if missing:
            raise ValueError(f"Missing required config: {', '.join(missing)}")

        if not cls.ADMIN_IDS:
            import warnings
            warnings.warn(
                "ADMIN_IDS is not set. The /stats command will be inaccessible. "
                "Add your Telegram user ID to ADMIN_IDS in .env."
            )

        if not cls.LOG_GROUP_ID:
            import warnings
            warnings.warn(
                "LOG_GROUP_ID is not set. Activity logs will only appear in stdout. "
                "Set LOG_GROUP_ID=<your group chat ID> in .env to enable Telegram logging."
            )

        if cls.WEBUI_ENABLED and cls.WEBUI_SECRET_KEY == "change-me-in-production":
            import warnings
            warnings.warn("WEBUI_SECRET_KEY is using the default value. Set a strong secret in .env!")
