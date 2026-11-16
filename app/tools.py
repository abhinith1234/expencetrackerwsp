"""Tools Claude can call to read and write the user's expenses.

Each tool has a JSON schema (sent to Claude) and a Python implementation.
All implementations are scoped to a single user_id, so one user can never
see or change another user's data no matter what the model asks for.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from . import db

CATEGORIES = [
    "Food & Dining",
    "Groceries",
    "Transport",
    "Fuel",
    "Shopping",
    "Bills & Utilities",
    "Rent & Housing",
    "Entertainment",
    "Health & Medical",
    "Education",
    "Travel",
    "Personal Care",
    "Subscriptions",
    "Gifts & Donations",
    "Family & Kids",
    "Investments & Savings",
    "EMI & Loans",
    "Other",
]
BUDGET_TARGETS = CATEGORIES + ["total"]
GROUP_BY = ["none", "category", "day", "week", "month", "merchant", "payment_method"]

_DATE = {"type": "string", "description": "Date as YYYY-MM-DD."}
_CURRENCY_OR_NULL = {
    "type": ["string", "null"],
    "description": "ISO 4217 code like INR, USD, EUR. null = the user's default currency.",
}


def _nullable(schema: dict, description: str) -> dict:
    s = dict(schema)
    t = s["type"]
    s["type"] = [t, "null"] if isinstance(t, str) else t
    s["description"] = description
    return s


TOOL_SCHEMAS: list[dict] = [
    {
        "name": "add_expenses",
        "description": (
            "Record one or more expenses the user spent money on. Call this whenever the user "
            "reports spending, even casually (e.g. 'chai 20', 'uber 250 and lunch 400'). "
            "One entry per distinct purchase. Pick the single best category."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "expenses": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "amount": {"type": "number", "description": "Positive amount spent."},
                            "currency": _CURRENCY_OR_NULL,
                            "category": {"type": "string", "enum": CATEGORIES},
                            "description": {"type": "string", "description": "Short label, e.g. 'Lunch at office canteen'."},
                            "merchant": _nullable({"type": "string"}, "Shop/app/vendor if mentioned (Swiggy, Amazon, Uber...), else null."),
                            "payment_method": _nullable({"type": "string"}, "UPI, cash, credit card, etc. if mentioned, else null."),
                            "date": _DATE,
                        },
                        "required": ["amount", "currency", "category", "description", "merchant", "payment_method", "date"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["expenses"],
            "additionalProperties": False,
        },
    },
    {
        "name": "query_expenses",
        "description": (
            "Look up the user's expenses between two dates (inclusive), optionally filtered by "
            "category or a text search over description/merchant, and optionally grouped. "
            "Returns totals per currency, group totals, and the matching items (newest first, "
            "with their ids). Use this to answer any question about past spending."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": _DATE,
                "end_date": _DATE,
                "category": {
                    "anyOf": [{"type": "string", "enum": CATEGORIES}, {"type": "null"}],
                    "description": "Filter to one category, or null for all.",
                },
                "search": _nullable({"type": "string"}, "Case-insensitive text to match in description or merchant, or null."),
                "group_by": {"type": "string", "enum": GROUP_BY},
                "limit": _nullable({"type": "integer"}, "Max items to return (default 25, max 200)."),
            },
            "required": ["start_date", "end_date", "category", "search", "group_by", "limit"],
            "additionalProperties": False,
        },
    },
    {
        "name": "update_expense",
        "description": "Correct an existing expense by id. Pass null for fields that should not change.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "expense_id": {"type": "integer"},
                "amount": _nullable({"type": "number"}, "New amount or null."),
                "currency": _CURRENCY_OR_NULL | {"description": "New currency or null to keep."},
                "category": {
                    "anyOf": [{"type": "string", "enum": CATEGORIES}, {"type": "null"}],
                    "description": "New category or null to keep.",
                },
                "description": _nullable({"type": "string"}, "New description or null."),
                "merchant": _nullable({"type": "string"}, "New merchant or null."),
                "date": _nullable({"type": "string"}, "New date YYYY-MM-DD or null."),
            },
            "required": ["expense_id", "amount", "currency", "category", "description", "merchant", "date"],
            "additionalProperties": False,
        },
    },
    {
        "name": "delete_expense",
        "description": (
            "Delete an expense. Pass its id, or null to delete the most recently added expense "
            "(for 'undo' / 'delete that')."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"expense_id": {"type": ["integer", "null"]}},
            "required": ["expense_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "set_budget",
        "description": (
            "Set a monthly budget for a category, or for 'total' overall spending. "
            "An amount of 0 removes the budget."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": BUDGET_TARGETS},
                "amount": {"type": "number"},
                "currency": _CURRENCY_OR_NULL,
            },
            "required": ["category", "amount", "currency"],
            "additionalProperties": False,
        },
    },
    {
        "name": "budget_status",
        "description": "Show each budget for a month next to what has been spent so far, and how many days are left.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"month": {"type": "string", "description": "Month as YYYY-MM."}},
            "required": ["month"],
            "additionalProperties": False,
        },
    },
    {
        "name": "set_default_currency",
        "description": "Change the currency used when the user doesn't name one.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"currency": {"type": "string", "description": "ISO 4217 code, e.g. INR, USD."}},
            "required": ["currency"],
            "additionalProperties": False,
        },
    },
]


class ToolError(Exception):
    """An error message that should be shown to Claude so it can fix its call."""


# ---------- helpers ----------

def _parse_date(value: str, field: str = "date") -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError):
        raise ToolError(f"{field} must be YYYY-MM-DD, got {value!r}")


def _currency(conn: sqlite3.Connection, user_id: str, value: str | None) -> str:
    if value is None or not str(value).strip():
        return db.get_default_currency(conn, user_id)
    cur = str(value).strip().upper()
    symbols = {"₹": "INR", "RS": "INR", "RS.": "INR", "$": "USD", "€": "EUR", "£": "GBP", "AED": "AED"}
    cur = symbols.get(cur, cur)
    if not re.fullmatch(r"[A-Z]{3}", cur):
        raise ToolError(f"currency must be a 3-letter ISO code like INR or USD, got {value!r}")
    return cur


def _round(x: float) -> float:
    return round(float(x), 2)


def _month_bounds(month: str) -> tuple[date, date]:
    try:
        start = datetime.strptime(month, "%Y-%m").date()
    except ValueError:
        raise ToolError(f"month must be YYYY-MM, got {month!r}")
    next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return start, next_month - timedelta(days=1)


# ---------- implementations ----------

def add_expenses(conn, user_id, expenses: list[dict]) -> dict:
    if not expenses:
        raise ToolError("expenses list is empty")
    created = []
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for e in expenses:
        amount = float(e["amount"])
        if amount <= 0:
            raise ToolError(f"amount must be positive, got {amount}")
        if e["category"] not in CATEGORIES:
            raise ToolError(f"unknown category {e['category']!r}")
        row = (
            user_id,
            _round(amount),
            _currency(conn, user_id, e.get("currency")),
            e["category"],
            (e.get("description") or e["category"]).strip()[:200],
            (e.get("merchant") or None),
            (e.get("payment_method") or None),
            _parse_date(e["date"]),
            now,
        )
        cur = conn.execute(
            "INSERT INTO expenses(user_id, amount, currency, category, description, merchant, "
            "payment_method, spent_on, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            row,
        )
        created.append({"id": cur.lastrowid, "amount": row[1], "currency": row[2], "category": row[3],
                        "description": row[4], "date": row[7]})

    # Month-to-date totals for the touched categories, so Claude can warn about budgets.
    month_info = []
    for month in sorted({c["date"][:7] for c in created}):
        status = budget_status(conn, user_id, month)
        touched = {c["category"] for c in created if c["date"].startswith(month)}
        month_info.append({
            "month": month,
            "spent_by_category": {k: v for k, v in status["spent_by_category"].items() if k.split(" (")[0] in touched},
            "budgets": [b for b in status["budgets"] if b["category"] in touched or b["category"] == "total"],
        })
    return {"saved": created, "month_to_date": month_info}


def query_expenses(conn, user_id, start_date, end_date, category=None, search=None,
                   group_by="none", limit=None) -> dict:
    start = _parse_date(start_date, "start_date")
    end = _parse_date(end_date, "end_date")
    if start > end:
        start, end = end, start
    where = ["user_id = ?", "spent_on BETWEEN ? AND ?"]
    params: list[Any] = [user_id, start, end]
    if category:
        where.append("category = ?")
        params.append(category)
    if search:
        where.append("(LOWER(description) LIKE ? OR LOWER(COALESCE(merchant,'')) LIKE ?)")
        like = f"%{search.lower()}%"
        params += [like, like]
    where_sql = " AND ".join(where)

    totals = [
        {"currency": r["currency"], "total": _round(r["total"]), "count": r["n"]}
        for r in conn.execute(
            f"SELECT currency, SUM(amount) total, COUNT(*) n FROM expenses WHERE {where_sql} "
            "GROUP BY currency ORDER BY total DESC", params)
    ]

    groups = []
    if group_by and group_by != "none":
        key_sql = {
            "category": "category",
            "day": "spent_on",
            "week": "strftime('%Y-W%W', spent_on)",
            "month": "substr(spent_on, 1, 7)",
            "merchant": "COALESCE(merchant, '(none)')",
            "payment_method": "COALESCE(payment_method, '(unknown)')",
        }.get(group_by)
        if key_sql is None:
            raise ToolError(f"group_by must be one of {GROUP_BY}")
        order = "grp" if group_by in {"day", "week", "month"} else "total DESC"
        groups = [
            {"group": r["grp"], "currency": r["currency"], "total": _round(r["total"]), "count": r["n"]}
            for r in conn.execute(
                f"SELECT {key_sql} grp, currency, SUM(amount) total, COUNT(*) n FROM expenses "
                f"WHERE {where_sql} GROUP BY grp, currency ORDER BY {order}", params)
        ]

    limit = max(1, min(int(limit or 25), 200))
    items = [
        dict(r) for r in conn.execute(
            "SELECT id, spent_on AS date, amount, currency, category, description, merchant, payment_method "
            f"FROM expenses WHERE {where_sql} ORDER BY spent_on DESC, id DESC LIMIT ?", params + [limit])
    ]
    total_count = sum(t["count"] for t in totals)
    return {
        "range": [start, end],
        "totals": totals,
        "groups": groups,
        "items": items,
        "items_truncated": total_count > len(items),
    }


def update_expense(conn, user_id, expense_id, amount=None, currency=None, category=None,
                   description=None, merchant=None, date=None) -> dict:
    row = conn.execute("SELECT * FROM expenses WHERE id = ? AND user_id = ?", (expense_id, user_id)).fetchone()
    if not row:
        raise ToolError(f"no expense with id {expense_id}")
    changes: dict[str, Any] = {}
    if amount is not None:
        if amount <= 0:
            raise ToolError("amount must be positive")
        changes["amount"] = _round(amount)
    if currency is not None:
        changes["currency"] = _currency(conn, user_id, currency)
    if category is not None:
        if category not in CATEGORIES:
            raise ToolError(f"unknown category {category!r}")
        changes["category"] = category
    if description is not None:
        changes["description"] = description.strip()[:200]
    if merchant is not None:
        changes["merchant"] = merchant or None
    if date is not None:
        changes["spent_on"] = _parse_date(date)
    if not changes:
        return {"updated": False, "reason": "no fields to change"}
    sets = ", ".join(f"{k} = ?" for k in changes)
    conn.execute(f"UPDATE expenses SET {sets} WHERE id = ? AND user_id = ?", [*changes.values(), expense_id, user_id])
    new = conn.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
    return {"updated": True, "expense": dict(new)}


def delete_expense(conn, user_id, expense_id=None) -> dict:
    if expense_id is None:
        row = conn.execute(
            "SELECT * FROM expenses WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,)).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM expenses WHERE id = ? AND user_id = ?", (expense_id, user_id)).fetchone()
    if not row:
        raise ToolError("no matching expense to delete")
    conn.execute("DELETE FROM expenses WHERE id = ?", (row["id"],))
    return {"deleted": dict(row)}


def set_budget(conn, user_id, category, amount, currency=None) -> dict:
    if category not in BUDGET_TARGETS:
        raise ToolError(f"unknown budget category {category!r}")
    cur = _currency(conn, user_id, currency)
    if amount <= 0:
        conn.execute("DELETE FROM budgets WHERE user_id = ? AND category = ? AND currency = ?",
                     (user_id, category, cur))
        return {"removed": True, "category": category, "currency": cur}
    conn.execute(
        "INSERT INTO budgets(user_id, category, amount, currency) VALUES (?,?,?,?) "
        "ON CONFLICT(user_id, category, currency) DO UPDATE SET amount = excluded.amount",
        (user_id, category, _round(amount), cur),
    )
    return {"saved": True, "category": category, "amount": _round(amount), "currency": cur}


def budget_status(conn, user_id, month, today: date | None = None) -> dict:
    start, end = _month_bounds(month)
    from . import config  # local import to avoid a cycle at module import time
    today = today or datetime.now(config.TIMEZONE).date()

    spent: dict[tuple[str, str], float] = {}
    for r in conn.execute(
        "SELECT category, currency, SUM(amount) total FROM expenses "
        "WHERE user_id = ? AND spent_on BETWEEN ? AND ? GROUP BY category, currency",
        (user_id, start.isoformat(), end.isoformat()),
    ):
        spent[(r["category"], r["currency"])] = r["total"]

    budgets = []
    for b in conn.execute("SELECT category, amount, currency FROM budgets WHERE user_id = ? ORDER BY category", (user_id,)):
        if b["category"] == "total":
            used = sum(v for (_, c), v in spent.items() if c == b["currency"])
        else:
            used = spent.get((b["category"], b["currency"]), 0.0)
        budgets.append({
            "category": b["category"],
            "currency": b["currency"],
            "budget": _round(b["amount"]),
            "spent": _round(used),
            "remaining": _round(b["amount"] - used),
            "percent_used": round(100 * used / b["amount"], 1) if b["amount"] else None,
        })

    if start <= today <= end:
        days_left = (end - today).days + 1
    elif today < start:
        days_left = (end - start).days + 1
    else:
        days_left = 0
    return {
        "month": month,
        "days_in_month": end.day,
        "days_left_including_today": days_left,
        "spent_by_category": {f"{cat} ({cur})": _round(v) for (cat, cur), v in sorted(spent.items())},
        "budgets": budgets,
    }


def set_default_currency(conn, user_id, currency) -> dict:
    cur = _currency(conn, user_id, currency)
    db.set_default_currency(conn, user_id, cur)
    return {"default_currency": cur}


_IMPLS: dict[str, Callable[..., dict]] = {
    "add_expenses": add_expenses,
    "query_expenses": query_expenses,
    "update_expense": update_expense,
    "delete_expense": delete_expense,
    "set_budget": set_budget,
    "budget_status": budget_status,
    "set_default_currency": set_default_currency,
}


def run_tool(conn: sqlite3.Connection, user_id: str, name: str, tool_input: dict) -> tuple[str, bool]:
    """Run a tool for this user. Returns (json_result, is_error)."""
    impl = _IMPLS.get(name)
    if impl is None:
        return json.dumps({"error": f"unknown tool {name}"}), True
    try:
        result = impl(conn, user_id, **tool_input)
        return json.dumps(result, default=str), False
    except ToolError as e:
        return json.dumps({"error": str(e)}), True
    except (TypeError, ValueError, KeyError) as e:
        return json.dumps({"error": f"bad input: {e}"}), True
