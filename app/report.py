"""Monthly report: ask the agent for last month's report and WhatsApp it to every user.

Run on the 1st of each month, e.g. with cron:
    5 9 1 * *  cd /path/to/repo && .venv/bin/python -m app.report
or call POST /cron/monthly-report with the CRON_SECRET header.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from . import config, db, whatsapp
from .agent import handle_message

log = logging.getLogger(__name__)


def previous_month(today: date | None = None) -> str:
    today = today or datetime.now(config.TIMEZONE).date()
    return (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")


def report_prompt(month: str) -> str:
    pretty = datetime.strptime(month, "%Y-%m").strftime("%B %Y")
    return f"Give me my monthly expense report for {pretty} ({month})."


def send_monthly_reports(month: str | None = None) -> dict[str, str]:
    month = month or previous_month()
    with db.connect() as conn:
        users = db.all_users(conn)
    sent: dict[str, str] = {}
    for user_id in users:
        if config.ALLOWED_NUMBERS and user_id not in config.ALLOWED_NUMBERS:
            continue
        try:
            reply = handle_message(user_id, report_prompt(month))
            whatsapp.send(user_id, f"📊 *Your {month} report*\n\n{reply}")
            sent[user_id] = "ok"
        except Exception as e:  # keep going for the other users
            log.exception("monthly report failed for %s", user_id)
            sent[user_id] = f"error: {e}"
    return sent


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    db.init_db()
    print(send_monthly_reports())
