"""Agent loop tests with a fake Claude client (no API calls)."""

from types import SimpleNamespace as NS

from fastapi.testclient import TestClient

from app import agent, config, db


def text(t):
    return NS(type="text", text=t)


def tool_use(id_, name, inp):
    return NS(type="tool_use", id=id_, name=name, input=inp)


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kwargs):
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.responses.pop(0)


def test_logs_expense_via_tool_then_replies(tmp_db):
    fake = FakeClient([
        NS(stop_reason="tool_use", content=[
            NS(type="thinking", thinking="", signature="s"),
            tool_use("t1", "add_expenses", {"expenses": [{
                "amount": 60, "currency": None, "category": "Food & Dining", "description": "Dosa",
                "merchant": None, "payment_method": "cash", "date": "2026-11-16"}]}),
        ]),
        NS(stop_reason="end_turn", content=[text("✅ Logged ₹60 Food & Dining (Dosa)")]),
    ])
    reply = agent.handle_message("whatsapp:+911", "dosa 60 cash", client=fake)
    assert reply == "✅ Logged ₹60 Food & Dining (Dosa)"

    # Second request carried the tool result back, matched by id.
    second = fake.calls[1]["messages"]
    assert second[-1]["content"][0]["tool_use_id"] == "t1"
    assert second[-1]["content"][0]["is_error"] is False
    assert fake.calls[0]["fallbacks"] == "default"
    assert fake.calls[0]["tools"][0]["name"] == "add_expenses"

    with db.connect() as conn:
        row = conn.execute("SELECT amount, currency, payment_method FROM expenses").fetchone()
        assert tuple(row) == (60.0, "INR", "cash")
        hist = db.recent_history(conn, "whatsapp:+911", 5)
    assert [h["role"] for h in hist] == ["user", "assistant"]


def test_history_is_sent_on_next_message(tmp_db):
    fake = FakeClient([NS(stop_reason="end_turn", content=[text("hi!")]),
                       NS(stop_reason="end_turn", content=[text("ok")])])
    agent.handle_message("u", "hello", client=fake)
    agent.handle_message("u", "and again", client=fake)
    msgs = fake.calls[1]["messages"]
    assert msgs[0] == {"role": "user", "content": "hello"}
    assert msgs[1] == {"role": "assistant", "content": "hi!"}
    assert msgs[2]["content"].endswith("and again") and "[Context: today is" in msgs[2]["content"]


def test_refusal_is_handled(tmp_db):
    fake = FakeClient([NS(stop_reason="refusal", content=[])])
    assert "can't help" in agent.handle_message("u", "something odd", client=fake)


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
