"""Agent loop tests with a fake LLM client (no API calls)."""

import json
from types import SimpleNamespace as NS

import httpx
import openai

from fastapi.testclient import TestClient

from app import agent, config, db


def reply(content=None, tool_calls=None, finish="stop"):
    msg = NS(content=content, tool_calls=tool_calls)
    return NS(choices=[NS(message=msg, finish_reason=finish)])


def call(id_, name, args):
    return NS(id=id_, type="function", function=NS(name=name, arguments=json.dumps(args)))


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kwargs):
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_logs_expense_via_tool_then_replies(tmp_db):
    fake = FakeClient([
        reply(tool_calls=[call("t1", "add_expenses", {"expenses": [{
            "amount": "60", "currency": None, "category": "Food & Dining", "description": "Dosa",
            "merchant": None, "payment_method": "cash", "date": "2026-11-16"}]})], finish="tool_calls"),
        reply("<think>easy</think>✅ **Logged** ₹60 Food & Dining (Dosa)"),
    ])
    out = agent.handle_message("whatsapp:+911", "dosa 60 cash", client=fake)
    assert out == "✅ *Logged* ₹60 Food & Dining (Dosa)"

    second = fake.calls[1]["messages"]
    assert second[0]["role"] == "system"
    assert second[-2]["tool_calls"][0]["id"] == "t1"
    assert second[-1]["role"] == "tool" and second[-1]["tool_call_id"] == "t1"
    assert "error" not in json.loads(second[-1]["content"])
    assert fake.calls[0]["tools"][0]["function"]["name"] == "add_expenses"

    with db.connect() as conn:
        row = conn.execute("SELECT amount, currency, payment_method FROM expenses").fetchone()
        assert tuple(row) == (60.0, "INR", "cash")
        hist = db.recent_history(conn, "whatsapp:+911", 5)
    assert [h["role"] for h in hist] == ["user", "assistant"]


def test_bad_tool_json_is_reported_back(tmp_db):
    bad = NS(id="t1", type="function", function=NS(name="add_expenses", arguments="{not json"))
    fake = FakeClient([reply(tool_calls=[bad]), reply("sorry, fixed")])
    assert agent.handle_message("u", "x 5", client=fake) == "sorry, fixed"
    assert "invalid JSON" in fake.calls[1]["messages"][-1]["content"]


def test_history_is_sent_on_next_message(tmp_db):
    fake = FakeClient([reply("hi!"), reply("ok")])
    agent.handle_message("u", "hello", client=fake)
    agent.handle_message("u", "and again", client=fake)
    msgs = fake.calls[1]["messages"]
    assert msgs[1] == {"role": "user", "content": "hello"}
    assert msgs[2] == {"role": "assistant", "content": "hi!"}
    assert msgs[3]["content"].endswith("and again") and "[Context: today is" in msgs[3]["content"]


def test_rate_limit_is_friendly(tmp_db):
    req = httpx.Request("POST", "http://x")
    err = openai.RateLimitError("slow down", response=httpx.Response(429, request=req), body=None)
    fake = FakeClient([err])
    assert "limit" in agent.handle_message("u", "tea 10", client=fake)


def test_webhook_acknowledges_and_replies_in_background(tmp_db, monkeypatch):
    from app import main

    monkeypatch.setattr(config, "VALIDATE_TWILIO_SIGNATURE", False)
    monkeypatch.setattr(config, "ALLOWED_NUMBERS", {"whatsapp:+911"})
    sent = []
    monkeypatch.setattr(main, "handle_message", lambda u, t: f"echo:{t}")
    monkeypatch.setattr(main.whatsapp, "send", lambda to, body: sent.append((to, body)))

    with TestClient(main.app) as c:
        r = c.post("/whatsapp", data={"From": "whatsapp:+911", "Body": "tea 20"})
        assert r.status_code == 200 and "<Response>" in r.text
        c.post("/whatsapp", data={"From": "whatsapp:+999", "Body": "hack"})
    assert sent == [("whatsapp:+911", "echo:tea 20")]


def test_webhook_rejects_bad_signature(tmp_db, monkeypatch):
    from app import main

    monkeypatch.setattr(config, "VALIDATE_TWILIO_SIGNATURE", True)
    monkeypatch.setattr(config, "TWILIO_AUTH_TOKEN", "secret")
    with TestClient(main.app) as c:
        r = c.post("/whatsapp", data={"From": "x", "Body": "y"}, headers={"X-Twilio-Signature": "bad"})
    assert r.status_code == 403


def test_split_message():
    from app.whatsapp import split_message

    long = "\n".join(f"line {i} " + "x" * 50 for i in range(100))
    parts = split_message(long, limit=500)
    assert all(len(p) <= 500 for p in parts)
    assert "\n".join(parts).replace("\n", "") == long.replace("\n", "")
