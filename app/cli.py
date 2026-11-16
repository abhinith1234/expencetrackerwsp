"""Chat with the bot in your terminal - no WhatsApp/Twilio needed.

    python -m app.cli
"""

from __future__ import annotations

from . import db
from .agent import handle_message

USER = "cli:local"


def main() -> None:
    db.init_db()
    print("Expense bot (terminal mode). Type an expense or a question. Ctrl+C to quit.\n")
    while True:
        try:
            text = input("you › ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if text:
            print(f"bot › {handle_message(USER, text)}\n")


if __name__ == "__main__":
    main()
