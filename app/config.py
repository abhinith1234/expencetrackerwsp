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


# --- LLM (any OpenAI-compatible endpoint: Groq, Ollama, OpenRouter, LM Studio...) ---
# Groq free tier (default):  https://api.groq.com/openai/v1   + free key from console.groq.com
# Ollama on your computer:   http://localhost:11434/v1          (no key needed)
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "") or "not-needed"
LLM_MODEL = os.getenv("LLM_MODEL", "openai/gpt-oss-120b")
# Optional: low | medium | high for reasoning models such as gpt-oss. Empty = don't send it.
LLM_REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "").strip()
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.2"))

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

