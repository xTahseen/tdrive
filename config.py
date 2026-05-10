import os
from dotenv import load_dotenv

load_dotenv()


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

    # ── WebUI settings ─────────────────────────────────────────────────────────
    WEBUI_ENABLED: bool = os.getenv("WEBUI_ENABLED", "false").lower() in ("1", "true", "yes")
    WEBUI_HOST: str = os.getenv("WEBUI_HOST", "0.0.0.0")
    WEBUI_PORT: int = int(os.getenv("WEBUI_PORT", "8080"))
    # Public base URL used as OAuth callback root, e.g. https://yourdomain.com
    WEBUI_BASE_URL: str = os.getenv("WEBUI_BASE_URL", "http://localhost:8080")
    # Secret key for signing JWT session tokens — CHANGE THIS in production
    WEBUI_SECRET_KEY: str = os.getenv("WEBUI_SECRET_KEY", "change-me-in-production")

    @classmethod
    def validate(cls):
        required = {
            "API_ID": cls.API_ID,
            "API_HASH": cls.API_HASH,
            "BOT_TOKEN": cls.BOT_TOKEN,
            "MONGO_URI": cls.MONGO_URI,
            "GOOGLE_CLIENT_ID": cls.GOOGLE_CLIENT_ID,
            "GOOGLE_CLIENT_SECRET": cls.GOOGLE_CLIENT_SECRET,
        }

        missing = [k for k, v in required.items() if not v or v == 0]

        if missing:
            raise ValueError(f"Missing required config: {', '.join(missing)}")

        if cls.WEBUI_ENABLED and cls.WEBUI_SECRET_KEY == "change-me-in-production":
            import warnings
            warnings.warn("WEBUI_SECRET_KEY is using the default value. Set a strong secret in .env!")
