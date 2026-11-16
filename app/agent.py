"""The Claude-powered brain: turns a WhatsApp message into tool calls and a reply."""

from __future__ import annotations

import logging
from datetime import datetime

import anthropic

from . import config, db
from .tools import CATEGORIES, TOOL_SCHEMAS, run_tool

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 8

# Kept byte-for-byte stable so it is served from the prompt cache.
# Anything that changes per request (date, currency) goes in the user turn instead.
SYSTEM_PROMPT = f"""You are a friendly personal expense-tracking assistant that lives in WhatsApp.
The user texts you their spending in casual language and asks questions about their money.

What you do:
1. Logging: when the user mentions money they spent, record it with add_expenses. Messages are often
   terse or mixed ("auto 80, dosa 60 cash", "paid 1.2k electricity bill yesterday", "netflix 649").
   Parse every item, infer the category, resolve relative dates ("yesterday", "last friday") using the
   date given in the message, and interpret shorthand like 1.2k = 1200. If the user names a currency
   or symbol ($, €, USD, rs) use it; otherwise pass null so their default currency is used.
   Only ask a clarifying question if the amount itself is missing or truly ambiguous.
2. Questions: answer questions about their spending ("how much on food this month?", "what did I
   spend at Swiggy last week?", "am I over budget?") by calling query_expenses / budget_status.
   Never guess numbers - always look them up. Compare periods by querying each period.
3. Corrections: "undo", "delete that", "that was 300 not 200", "move it to groceries" -> use
   delete_expense / update_expense. Use query_expenses to find the id when needed.
4. Budgets: "set food budget 8000", "monthly budget 50k" -> set_budget ('total' for overall).
5. Reports: when asked for a report/summary for a month, query that month grouped by category,
   the previous month for comparison, budget_status, and the largest items. Then write a short report:
   total spent, category breakdown (biggest first, with % of total), change vs last month,
   budget performance, the top 3 expenses, and 2-3 specific, practical saving tips based on the data.
6. Budgeting advice: give concrete, numbers-based suggestions grounded in their actual data.

Categories available: {", ".join(CATEGORIES)}.

Reply style (this is WhatsApp, not a web page):
- Be brief. A logged expense gets a one or two line confirmation, e.g.
  "✅ Logged ₹60 Food & Dining (Dosa) · Food this month: ₹2,340 / ₹8,000"
- Mention a budget only when one exists, and warn when a category passes 80% or 100%.
- Format with WhatsApp syntax only: *bold*, _italic_, and simple "• " bullet lines.
  No markdown headings (#), no tables, no links.
- Use the currency symbol when obvious (₹, $, €, £) and thousands separators.
- If the message is not about money (greetings etc.), reply briefly and remind them what you can do.
"""


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


def _context_line(conn, user_id: str) -> str:
    now = datetime.now(config.TIMEZONE)
    currency = db.get_default_currency(conn, user_id)
    return (f"[Context: today is {now:%A %Y-%m-%d}, time {now:%H:%M} ({config.TIMEZONE.key}). "
            f"User's default currency: {currency}.]")


def _text_of(content) -> str:
    return "\n".join(b.text for b in content if getattr(b, "type", None) == "text").strip()


def handle_message(user_id: str, text: str, client: anthropic.Anthropic | None = None) -> str:
    """Process one incoming message from `user_id` and return the reply text."""
    client = client or _client()
    text = (text or "").strip()
    if not text:
        return "Send me an expense like *lunch 250* or ask *how much did I spend this month?*"

    with db.connect() as conn:
        db.touch_user(conn, user_id)

        if text.lower() in {"/reset", "reset chat"}:
            db.clear_history(conn, user_id)
            return "🧹 Chat memory cleared. Your expenses are untouched."

        messages: list[dict] = db.recent_history(conn, user_id, config.HISTORY_TURNS)
        user_turn = f"{_context_line(conn, user_id)}\n{text}"
        if messages and messages[-1]["role"] == "user":
            messages[-1] = {"role": "user", "content": messages[-1]["content"] + "\n" + user_turn}
        else:
            messages.append({"role": "user", "content": user_turn})

        reply = _run_agent(client, conn, user_id, messages)

        db.add_history(conn, user_id, "user", text)
        db.add_history(conn, user_id, "assistant", reply)
        return reply


def _run_agent(client: anthropic.Anthropic, conn, user_id: str, messages: list[dict]) -> str:
    for _ in range(MAX_TOOL_ROUNDS):
        try:
            response = client.beta.messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                tools=TOOL_SCHEMAS,
                messages=messages,
                thinking={"type": "adaptive"},
                output_config={"effort": config.CLAUDE_EFFORT},
                cache_control={"type": "ephemeral"},
                # If a safety classifier declines, let the API retry on a recommended fallback model.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.RateLimitError:
            log.warning("Rate limited by Claude API")
            return "⏳ I'm a bit busy right now — please send that again in a minute."
        except anthropic.APIStatusError as e:
            log.error("Claude API error %s: %s", e.status_code, e.message)
            return "⚠️ Sorry, something went wrong on my side. Please try again."
        except anthropic.APIConnectionError:
            log.exception("Could not reach Claude API")
            return "⚠️ I couldn't reach my brain just now. Please try again shortly."

        if response.stop_reason == "refusal":
            return "Sorry, I can't help with that one. I can track expenses and answer budget questions."

        # Keep every block (thinking, fallback markers, tool calls) exactly as returned.
        messages.append({"role": "assistant", "content": response.content})

        tool_calls = [b for b in response.content if b.type == "tool_use"]
        if response.stop_reason == "tool_use" and tool_calls:
            results = []
            for call in tool_calls:
                output, is_error = run_tool(conn, user_id, call.name, call.input)
                log.info("tool %s(%s) -> %s", call.name, call.input, output[:300])
                results.append({"type": "tool_result", "tool_use_id": call.id,
                                "content": output, "is_error": is_error})
            conn.commit()
            messages.append({"role": "user", "content": results})
            continue

        if response.stop_reason == "pause_turn":
            continue

        reply = _text_of(response.content)
        if response.stop_reason == "max_tokens" and not reply:
            return "That answer got too long — could you ask about a shorter period?"
        return reply or "👍"

    return "I got a bit tangled up working that out. Could you rephrase?"
