"""SQLite storage for expenses, budgets, user settings and short chat history."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS expenses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT    NOT NULL,
    amount      REAL    NOT NULL,
    currency    TEXT    NOT NULL,
    category    TEXT    NOT NULL,
    description TEXT    NOT NULL,
    merchant    TEXT,
    payment_method TEXT,
    spent_on    TEXT    NOT NULL,          -- YYYY-MM-DD
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_expenses_user_date ON expenses(user_id, spent_on);

CREATE TABLE IF NOT EXISTS budgets (
    user_id   TEXT NOT NULL,
    category  TEXT NOT NULL,               -- a category name, or 'total'
    amount    REAL NOT NULL,
    currency  TEXT NOT NULL,
    PRIMARY KEY (user_id, category, currency)
);

CREATE TABLE IF NOT EXISTS users (
    user_id          TEXT PRIMARY KEY,
    default_currency TEXT NOT NULL,
    last_seen_at     TEXT
);

CREATE TABLE IF NOT EXISTS history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT NOT NULL,
    role       TEXT NOT NULL,              -- 'user' | 'assistant'
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_user ON history(user_id, id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect(path: str | None = None) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(path or config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(path: str | None = None) -> None:
    with connect(path) as conn:
        conn.executescript(SCHEMA)


# ---------- users ----------

def touch_user(conn: sqlite3.Connection, user_id: str) -> None:
    conn.execute(
        "INSERT INTO users(user_id, default_currency, last_seen_at) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET last_seen_at = excluded.last_seen_at",
        (user_id, config.DEFAULT_CURRENCY, _now()),
    )


def get_default_currency(conn: sqlite3.Connection, user_id: str) -> str:
    row = conn.execute("SELECT default_currency FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return row["default_currency"] if row else config.DEFAULT_CURRENCY


def set_default_currency(conn: sqlite3.Connection, user_id: str, currency: str) -> None:
    touch_user(conn, user_id)
    conn.execute("UPDATE users SET default_currency = ? WHERE user_id = ?", (currency.upper(), user_id))


def all_users(conn: sqlite3.Connection) -> list[str]:
    return [r["user_id"] for r in conn.execute("SELECT user_id FROM users")]


# ---------- history ----------

def add_history(conn: sqlite3.Connection, user_id: str, role: str, content: str) -> None:
    conn.execute(
        "INSERT INTO history(user_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (user_id, role, content, _now()),
    )


def recent_history(conn: sqlite3.Connection, user_id: str, turns: int) -> list[dict]:
    rows = conn.execute(
        "SELECT role, content FROM history WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, turns * 2),
    ).fetchall()
    msgs = [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
    # The API needs the conversation to start with a user turn and alternate roles.
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    cleaned: list[dict] = []
    for m in msgs:
        if cleaned and cleaned[-1]["role"] == m["role"]:
            cleaned[-1]["content"] += "\n" + m["content"]
        else:
            cleaned.append(dict(m))
    return cleaned


def clear_history(conn: sqlite3.Connection, user_id: str) -> None:
    conn.execute("DELETE FROM history WHERE user_id = ?", (user_id,))
