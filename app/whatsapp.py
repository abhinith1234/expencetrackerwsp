"""Sending WhatsApp messages through Twilio."""

from __future__ import annotations

import logging

from twilio.rest import Client

from . import config

log = logging.getLogger(__name__)

# Twilio rejects WhatsApp bodies longer than 1600 characters.
MAX_LEN = 1500


def split_message(text: str, limit: int = MAX_LEN) -> list[str]:
    """Split a long reply on paragraph/line boundaries so each part fits in one message."""
    text = text.strip()
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:  # a single giant line: hard-wrap it
            if current:
                parts.append(current)
                current = ""
            parts.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            parts.append(current)
            current = line
        else:
            current = candidate
    if current:
        parts.append(current)
    return [p for p in (p.strip() for p in parts) if p]


_client: Client | None = None


def _twilio() -> Client:
    global _client
    if _client is None:
        _client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
    return _client


def send(to: str, text: str) -> None:
    """Send `text` to a WhatsApp number like 'whatsapp:+919876543210'."""
    for part in split_message(text):
        msg = _twilio().messages.create(from_=config.TWILIO_WHATSAPP_FROM, to=to, body=part)
        log.info("sent %s to %s", msg.sid, to)
