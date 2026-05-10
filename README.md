# 📁 Telegram → Google Drive Bot

A Telegram bot built with **Pyrogram** that lets anyone upload Telegram files directly to their own **Google Drive** account. OAuth tokens are securely stored per-user in **MongoDB**.

---

## ✨ Features

- 🔐 **Per-user Google OAuth2** — each user connects their own Google Drive
- ☁️ **Upload any file** — documents, photos, videos, audio, voice, animations
- 📊 **Live progress** — shows download and upload progress in real time
- 📂 **Browse files** — list your 10 most recent Drive uploads
- 🔄 **Auto token refresh** — access tokens are refreshed automatically
- 🚪 **Logout** — users can revoke access at any time
- 🐳 **Docker ready** — one command to start everything

---

## 🚀 Quick Start

### 1. Clone the repo

```bash
git clone https://github.com/yourname/gdrive-telegram-bot
cd gdrive-telegram-bot
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your credentials
```

### 3. Run with Docker (recommended)

```bash
docker-compose up -d
```

### 3b. Or run directly

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
mkdir downloads
python bot.py
```

---

## 🔑 Getting Credentials

### Telegram

| Credential | Where to get it |
|---|---|
| `API_ID` & `API_HASH` | https://my.telegram.org/apps |
| `BOT_TOKEN` | [@BotFather](https://t.me/BotFather) |

### Google Cloud

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or select existing)
3. Enable these APIs:
   - **Google Drive API**
   - **Google People API** (for email fetch)
4. Go to **Credentials → Create Credentials → OAuth 2.0 Client ID**
5. Choose **Desktop app** as the application type
6. Under **Authorized redirect URIs**, add:
   ```
   urn:ietf:wg:oauth:2.0:oob
   ```
7. Copy `Client ID` and `Client Secret` to `.env`

> **Important:** Go to **OAuth consent screen** and add your test users if the app is in *Testing* mode.

---

## 📋 Bot Commands

| Command | Description |
|---|---|
| `/start` | Welcome message and status |
| `/auth` | Connect your Google Drive account |
| `/logout` | Disconnect Google Drive |
| `/myfiles` | List your 10 most recent files |
| `/help` | Show help message |

---

## 🏗️ Project Structure

```
gdrive_bot/
├── bot.py              # Entry point
├── config.py           # Settings from .env
├── database.py         # MongoDB async operations
├── gdrive.py           # Google Drive OAuth + upload logic
├── handlers/
│   ├── __init__.py     # Handler registration
│   ├── cmd_start.py    # /start
│   ├── cmd_auth.py     # /auth + code exchange
│   ├── cmd_logout.py   # /logout
│   ├── cmd_myfiles.py  # /myfiles
│   ├── cmd_help.py     # /help
│   ├── on_file.py      # File/media handler
│   └── on_callback.py  # Inline keyboard callbacks
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

---

## 🔒 Security Notes

- OAuth tokens are stored per-user in MongoDB (never shared between users)
- Tokens are automatically refreshed when expired
- The OOB (out-of-band) OAuth flow is used — no web server required
- Users can revoke access from [Google Account Settings](https://myaccount.google.com/permissions) at any time
- The `/logout` command deletes the token from the database immediately

---

## 🐳 Production Deployment

The included `docker-compose.yml` sets up:
- The bot container
- MongoDB 7 with persistent volume storage
- Health checks and auto-restart

```bash
# Start
docker-compose up -d

# View logs
docker-compose logs -f bot

# Stop
docker-compose down
```

---

## 🛠️ Environment Variables

| Variable | Required | Description |
|---|---|---|
| `API_ID` | ✅ | Telegram API ID |
| `API_HASH` | ✅ | Telegram API Hash |
| `BOT_TOKEN` | ✅ | Bot token from BotFather |
| `MONGO_URI` | ✅ | MongoDB connection string |
| `DB_NAME` | ❌ | Database name (default: `gdrive_bot`) |
| `GOOGLE_CLIENT_ID` | ✅ | Google OAuth Client ID |
| `GOOGLE_CLIENT_SECRET` | ✅ | Google OAuth Client Secret |
| `OAUTH_REDIRECT_URI` | ❌ | OAuth redirect (default: OOB) |
| `MAX_FILE_SIZE` | ❌ | Max file bytes (default: 2GB) |
| `TEMP_DIR` | ❌ | Temp folder (default: `./downloads`) |

---

## 📄 License

MIT
