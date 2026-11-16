"""End-to-end check against your real LLM (uses your .env), with a throwaway database.

    python -m app.smoketest

Sends realistic messages, then checks what was actually saved. Paste the output
back to your assistant if anything fails. Your real expenses.db is not touched.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from datetime import datetime, timedelta

from . import config, db
from .agent import handle_message

USER = "smoketest:user"


def main() -> int:
    tmp = tempfile.mkdtemp()
    config.DB_PATH = os.path.join(tmp, "smoke.db")
    db.init_db()
    today = datetime.now(config.TIMEZONE).date()
    yesterday = (today - timedelta(days=1)).isoformat()
    print(f"Model: {config.LLM_MODEL} @ {config.LLM_BASE_URL}\nToday: {today}\n")

    def rows():
        with db.connect() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT id, amount, currency, category, description, spent_on FROM expenses "
                "WHERE user_id = ? ORDER BY id", (USER,))]

    results: list[tuple[str, bool, str]] = []

    def step(msg: str, check=None, label: str = ""):
        t0 = time.time()
        reply = handle_message(USER, msg)
        secs = time.time() - t0
        print(f"you › {msg}\nbot › {reply}\n      ({secs:.1f}s)\n")
        if check:
            try:
                ok, detail = check(rows(), reply)
            except Exception as e:  # noqa: BLE001
                ok, detail = False, f"check crashed: {e}"
            results.append((label or msg, ok, detail))
        if secs > 12:
            results.append((f"speed: {msg}", False, f"took {secs:.1f}s (Twilio allows ~15s)"))

    def has(r, amount, category=None, date=None, currency=None):
        return any(abs(x["amount"] - amount) < 0.01
                   and (category is None or x["category"] == category)
                   and (date is None or x["spent_on"] == date)
                   and (currency is None or x["currency"] == currency) for x in r)

    step("chai 20", lambda r, _: (has(r, 20, "Food & Dining", today.isoformat(), config.DEFAULT_CURRENCY),
                                  str(r[-1:]) or "nothing saved"), "simple expense")
    step("uber 250, groceries 1.2k on card and netflix 649",
         lambda r, _: (has(r, 250, "Transport") and has(r, 1200, "Groceries") and has(r, 649, "Subscriptions"),
                       str(r[1:])), "three expenses in one message + 1.2k")
    step("paid electricity bill 1850 yesterday",
         lambda r, _: (has(r, 1850, "Bills & Utilities", yesterday), str(r[-1:])), "relative date")
    step("hotel $120", lambda r, _: (has(r, 120, None, None, "USD"), str(r[-1:])), "typed currency")
    step("sorry the chai was 30 not 20",
         lambda r, _: (has(r, 30, "Food & Dining") and not has(r, 20, "Food & Dining"), str(r[:1])), "correction")
    step("undo", lambda r, _: (not has(r, 120, None, None, "USD"), f"{len(r)} rows left"), "undo last")
    step("set food budget 5000", None)
    step("how much did I spend on transport this month?",
         lambda _, reply: ("250" in reply, "answer should mention 250"), "question")
    step("summary", lambda _, reply: ("📊" in reply or "summary" in reply.lower(), "report text"), "summary")

    print("=" * 60)
    passed = sum(ok for _, ok, _ in results)
    for label, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {label}" + ("" if ok else f"  ->  {detail}"))
    print(f"\n{passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
