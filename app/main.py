"""FastAPI web server that receives WhatsApp messages from Twilio.

Twilio gives a webhook ~15 seconds to answer, and the LLM may take longer when it
runs several lookups, so we acknowledge immediately and send the real reply
through the Twilio API from a background task.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request, Response
from twilio.request_validator import RequestValidator

from . import config, db, whatsapp
from .agent import handle_message

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="WhatsApp Expense Tracker", lifespan=lifespan)
EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


@app.get("/health")
def health() -> dict:
    return {"ok": True}


def _verify_twilio(request: Request, form: dict, signature: str | None) -> None:
    if not config.VALIDATE_TWILIO_SIGNATURE:
        return
    url = (config.PUBLIC_BASE_URL + request.url.path) if config.PUBLIC_BASE_URL else str(request.url)
    if not signature or not RequestValidator(config.TWILIO_AUTH_TOKEN).validate(url, form, signature):
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")


def _reply_in_background(user_id: str, text: str) -> None:
    try:
        reply = handle_message(user_id, text)
    except Exception:
        log.exception("failed handling message from %s", user_id)
        reply = "⚠️ Sorry, something went wrong. Please try again."
    try:
        whatsapp.send(user_id, reply)
    except Exception:
        log.exception("failed sending reply to %s", user_id)


@app.post("/whatsapp")
async def whatsapp_webhook(
    request: Request,
    background: BackgroundTasks,
    x_twilio_signature: str | None = Header(default=None),
) -> Response:
    form = dict(await request.form())
    _verify_twilio(request, form, x_twilio_signature)

    user_id = form.get("From", "")
    body = form.get("Body", "")
    if config.ALLOWED_NUMBERS and user_id not in config.ALLOWED_NUMBERS:
        log.warning("ignoring message from unlisted number %s", user_id)
        return Response(EMPTY_TWIML, media_type="application/xml")

    if int(form.get("NumMedia", "0") or 0) > 0 and not body:
        background.add_task(whatsapp.send, user_id,
                            "I can only read text for now — type the expense, e.g. *groceries 1450*.")
    else:
        background.add_task(_reply_in_background, user_id, body)
    return Response(EMPTY_TWIML, media_type="application/xml")

