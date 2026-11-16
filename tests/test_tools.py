import json
from datetime import date

from app import db
from app.tools import TOOL_SCHEMAS, budget_status, run_tool


def call(conn, name, **kw):
    out, err = run_tool(conn, "u1", name, kw)
    return json.loads(out), err


def exp(amount, category, d, currency=None, desc="x", merchant=None):
    return {"amount": amount, "currency": currency, "category": category, "description": desc,
            "merchant": merchant, "payment_method": None, "date": d}


def test_add_and_query(tmp_db):
    with db.connect() as conn:
        db.touch_user(conn, "u1")
        res, err = call(conn, "add_expenses", expenses=[
            exp(250, "Food & Dining", "2026-11-10", desc="Lunch", merchant="Swiggy"),
            exp(80, "Transport", "2026-11-10", desc="Auto"),
            exp(20, "Food & Dining", "2026-11-11", currency="usd", desc="Burger"),
        ])
        assert not err
        assert [s["currency"] for s in res["saved"]] == ["INR", "INR", "USD"]

        q, err = call(conn, "query_expenses", start_date="2026-11-01", end_date="2026-11-30",
                      category=None, search=None, group_by="category", limit=None)
        assert not err
        totals = {t["currency"]: t["total"] for t in q["totals"]}
        assert totals == {"INR": 330.0, "USD": 20.0}
        assert {"group": "Food & Dining", "currency": "INR", "total": 250.0, "count": 1} in q["groups"]

        q, _ = call(conn, "query_expenses", start_date="2026-11-01", end_date="2026-11-30",
                    category=None, search="swig", group_by="none", limit=5)
        assert [i["description"] for i in q["items"]] == ["Lunch"]


def test_users_are_isolated(tmp_db):
    with db.connect() as conn:
        call(conn, "add_expenses", expenses=[exp(100, "Other", "2026-11-01")])
        out, _ = run_tool(conn, "u2", "query_expenses", {
            "start_date": "2026-01-01", "end_date": "2026-12-31", "category": None,
            "search": None, "group_by": "none", "limit": None})
        assert json.loads(out)["items"] == []
        out, err = run_tool(conn, "u2", "delete_expense", {"expense_id": None})
        assert err


def test_update_delete_undo(tmp_db):
    with db.connect() as conn:
        res, _ = call(conn, "add_expenses", expenses=[exp(200, "Groceries", "2026-11-05"),
                                                      exp(50, "Other", "2026-11-05")])
        first, last = res["saved"][0]["id"], res["saved"][1]["id"]
        up, err = call(conn, "update_expense", expense_id=first, amount=300, currency=None,
                       category="Food & Dining", description=None, merchant=None, date=None)
        assert not err and up["expense"]["amount"] == 300 and up["expense"]["category"] == "Food & Dining"
        d, err = call(conn, "delete_expense", expense_id=None)  # undo = latest
        assert not err and d["deleted"]["id"] == last


def test_budgets(tmp_db):
    with db.connect() as conn:
        call(conn, "set_budget", category="Food & Dining", amount=1000, currency=None)
        call(conn, "set_budget", category="total", amount=5000, currency=None)
        call(conn, "add_expenses", expenses=[exp(900, "Food & Dining", "2026-11-03"),
                                             exp(600, "Transport", "2026-11-04")])
        s = budget_status(conn, "u1", "2026-11", today=date(2026, 11, 16))
        by = {b["category"]: b for b in s["budgets"]}
        assert by["Food & Dining"]["percent_used"] == 90.0
        assert by["total"]["spent"] == 1500.0
        assert s["days_left_including_today"] == 15


def test_bad_inputs_are_reported_not_raised(tmp_db):
    with db.connect() as conn:
        res, err = call(conn, "add_expenses", expenses=[exp(-5, "Other", "2026-11-01")])
        assert err and "positive" in res["error"]
        res, err = call(conn, "add_expenses", expenses=[exp(5, "Other", "yesterday")])
        assert err and "YYYY-MM-DD" in res["error"]
        res, err = call(conn, "set_default_currency", currency="rupees")
        assert err


def test_default_currency(tmp_db):
    with db.connect() as conn:
        call(conn, "set_default_currency", currency="usd")
        res, _ = call(conn, "add_expenses", expenses=[exp(5, "Other", "2026-11-01")])
        assert res["saved"][0]["currency"] == "USD"


def test_schemas_are_well_formed():
    for t in TOOL_SCHEMAS:
        schema = t["input_schema"]
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])


def test_tolerates_sloppy_model_arguments(tmp_db):
    with db.connect() as conn:
        out, err = run_tool(conn, "u1", "add_expenses", {"expenses": [
            {"amount": "₹1,200", "category": "Groceries", "description": "veg", "date": "2026-11-02",
             "currency": "", "merchant": "null"}], "made_up_arg": 1})
        assert not err, out
        saved = json.loads(out)["saved"][0]
        assert saved["amount"] == 1200 and saved["currency"] == "INR"
        out, err = run_tool(conn, "u1", "set_budget", {"category": "total", "amount": "50000"})
        assert not err, out
