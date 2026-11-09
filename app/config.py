import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = Path(os.getenv("APP_ENV_FILE", BASE_DIR / ".env"))
load_dotenv(ENV_FILE)

DATA_DIR = Path(os.getenv("APP_DATA_DIR", BASE_DIR / "data"))
MEDIA_DIR = Path(os.getenv("APP_MEDIA_DIR", BASE_DIR / "media"))
LOG_DIR = Path(os.getenv("APP_LOG_DIR", BASE_DIR / "logs"))
for d in (DATA_DIR, MEDIA_DIR, LOG_DIR):
    d.mkdir(parents=True, exist_ok=True)

DB_PATH = Path(os.getenv("DB_PATH", DATA_DIR / "posts.db"))
if not DB_PATH.is_absolute():
    DB_PATH = ENV_FILE.parent / DB_PATH
TOKEN_PATH = Path(os.getenv("TOKEN_PATH", DATA_DIR / "token.json"))
if not TOKEN_PATH.is_absolute():
    TOKEN_PATH = ENV_FILE.parent / TOKEN_PATH

CLIENT_ID = os.getenv("LINKEDIN_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("LINKEDIN_CLIENT_SECRET", "")
REDIRECT_URI = os.getenv("LINKEDIN_REDIRECT_URI", "http://localhost:8000/callback")
LINKEDIN_VERSION = os.getenv("LINKEDIN_VERSION", "202610")
SCOPES = "openid profile w_member_social"

APP_PASSWORD = os.getenv("APP_PASSWORD", "")
CHECK_EVERY_SECONDS = int(os.getenv("CHECK_EVERY_SECONDS", "30"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
MISSED_AFTER_HOURS = int(os.getenv("MISSED_AFTER_HOURS", "24"))

MAX_POST_CHARS = 3000
MAX_IMAGE_BYTES = 8 * 1024 * 1024
ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif"}

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
ALERT_TO = os.getenv("ALERT_TO", "")

DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8501"))
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"
if CHECK_EVERY_SECONDS < 1:
    raise ValueError("CHECK_EVERY_SECONDS must be positive")

BACKUP_DIR = Path(os.getenv("APP_BACKUP_DIR", BASE_DIR / "backups"))

# SMTP remains available for existing installations; choose resend in .env.
EMAIL_PROVIDER = os.getenv("EMAIL_PROVIDER", "smtp").strip().lower()
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
RESEND_FROM = os.getenv("RESEND_FROM", "").strip()
