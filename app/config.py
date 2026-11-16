"""Runtime configuration, read from environment variables (see .env.example)."""

import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


# --- Claude ---
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
# low | medium | high | xhigh | max. "medium" is a good balance for logging + Q&A.
CLAUDE_EFFORT = os.getenv("CLAUDE_EFFORT", "medium")

# --- App ---
DB_PATH = os.getenv("DB_PATH", "expenses.db")
TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Asia/Kolkata"))
DEFAULT_CURRENCY = os.getenv("DEFAULT_CURRENCY", "INR").upper()
# How many previous back-and-forth messages to remember per user (for "change that to 300").
HISTORY_TURNS = int(os.getenv("HISTORY_TURNS", "6"))
# Comma-separated WhatsApp numbers allowed to use the bot, e.g. "whatsapp:+919876543210".
# Empty = anyone who messages your Twilio number.
ALLOWED_NUMBERS = {n.strip() for n in os.getenv("ALLOWED_NUMBERS", "").split(",") if n.strip()}

# --- Twilio ---
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
# The WhatsApp sender, e.g. "whatsapp:+14155238886" for the Twilio sandbox.
TWILIO_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM", "")
# Public URL Twilio calls (e.g. your ngrok URL). Needed to verify Twilio's signature.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
VALIDATE_TWILIO_SIGNATURE = _bool("VALIDATE_TWILIO_SIGNATURE", True)

# Shared secret protecting the /cron/monthly-report endpoint.
CRON_SECRET = os.getenv("CRON_SECRET", "")
