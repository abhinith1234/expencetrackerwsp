"""The bot's brain: an open-source LLM that turns a WhatsApp message into tool calls and a reply.

Works with any OpenAI-compatible chat API that supports tool calling:
Groq (free tier, hosts open models like gpt-oss and Qwen), Ollama (runs models on
your own computer), OpenRouter, LM Studio, vLLM, etc. Configure it in `.env`.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta

import openai

from . import config, db
from .tools import CATEGORIES, TOOL_SCHEMAS, run_tool

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 8

OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": t["name"],
            "description": t["description"],
            "parameters": t["input_schema"],
        },
    }
    for t in TOOL_SCHEMAS
]

SYSTEM_PROMPT = f"""You are a friendly personal expense-tracking assistant that lives in WhatsApp.
The user texts you their spending in casual language and asks questions about their money.
You MUST use the provided tools to save and read data. Never pretend you saved something
without calling add_expenses, and never invent numbers - always look them up.

What you do:
1. Logging: when the user mentions money they spent, call add_expenses. Messages are often
   terse or mixed ("auto 80, dosa 60 cash", "paid 1.2k electricity bill yesterday", "netflix 649").
   Create one entry per item, pick the best category, resolve relative dates ("yesterday",
   "last friday") using the dates given in the [Context] line, and read shorthand like 1.2k = 1200.
   If the user names a currency or symbol ($, €, USD, rs) use its ISO code; otherwise pass null so
   their default currency is used. Only ask a question if the amount itself is missing.
2. Questions: answer questions about spending ("how much on food this month?", "what did I spend
   at Swiggy last week?", "am I over budget?") by calling query_expenses and/or budget_status.
   To compare periods, query each period.
3. Corrections: "undo", "delete that", "that was 300 not 200", "move it to groceries" -> use
   delete_expense / update_expense. Use query_expenses to find the id when needed.
4. Budgets: "set food budget 8000", "monthly budget 50k" -> set_budget (category 'total' for overall).
5. Reports: when asked for a monthly report, call query_expenses for that month with
   group_by "category", query the previous month the same way, call budget_status, and query the
   month's items. Then write: total spent, category breakdown (largest first, with % of total),
   change vs last month, budget performance, the top 3 expenses, and 2-3 specific saving tips
   based on the data.
6. Budgeting advice: give concrete, numbers-based suggestions grounded in their real data.

Categories (use exactly these names): {", ".join(CATEGORIES)}.
Dates are always YYYY-MM-DD; months are YYYY-MM.

Reply style (this is WhatsApp, not a web page):
- Be brief. A logged expense gets a one or two line confirmation, e.g.
  "✅ Logged ₹60 Food & Dining (Dosa) · Food this month: ₹2,340 / ₹8,000"
- Mention a budget only when one exists; warn when a category passes 80% or 100%.
- Format with WhatsApp syntax only: *bold*, _italic_, and "• " bullet lines.
  No markdown headings (#), no tables, no links, no **double asterisks**.
- Use currency symbols when obvious (₹, $, €, £) and thousands separators.
- If the message is not about money (greetings etc.), reply briefly and say what you can do.
"""

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _client() -> openai.OpenAI:
    return openai.OpenAI(base_url=config.LLM_BASE_URL, api_key=config.LLM_API_KEY)


def _context_line(conn, user_id: str) -> str:
    now = datetime.now(config.TIMEZONE)
    yesterday = now.date() - timedelta(days=1)
    currency = db.get_default_currency(conn, user_id)
    return (f"[Context: today is {now:%A %Y-%m-%d} (yesterday was {yesterday:%Y-%m-%d}), "
            f"current month {now:%Y-%m}, time {now:%H:%M} ({config.TIMEZONE.key}). "
            f"User's default currency: {currency}.]")


def _clean_reply(text: str | None) -> str:
    text = _THINK_RE.sub("", text or "").strip()
    # WhatsApp uses single asterisks for bold; many models emit markdown **bold**.
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    text = re.sub(r"^#{1,6}\s*(.+)$", r"*\1*", text, flags=re.MULTILINE)
    return text


def handle_message(user_id: str, text: str, client: openai.OpenAI | None = None) -> str:
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


def _run_agent(client: openai.OpenAI, conn, user_id: str, messages: list[dict]) -> str:
    convo = [{"role": "system", "content": SYSTEM_PROMPT}, *messages]
    extra = {}
    if config.LLM_REASONING_EFFORT:
        extra["reasoning_effort"] = config.LLM_REASONING_EFFORT

    for _ in range(MAX_TOOL_ROUNDS):
        try:
            response = client.chat.completions.create(
                model=config.LLM_MODEL,
                messages=convo,
                tools=OPENAI_TOOLS,
                tool_choice="auto",
                temperature=config.LLM_TEMPERATURE,
                max_tokens=4096,
                **extra,
            )
        except openai.RateLimitError:
            log.warning("Rate limited by LLM provider")
            return "⏳ I've hit my free usage limit for the moment — please try again in a minute."
        except openai.APIStatusError as e:
            log.error("LLM API error %s: %s", e.status_code, e.message)
            return "⚠️ Sorry, something went wrong on my side. Please try again."
        except openai.APIConnectionError:
            log.exception("Could not reach LLM at %s", config.LLM_BASE_URL)
            return "⚠️ I couldn't reach my brain just now. Please try again shortly."

        choice = response.choices[0]
        msg = choice.message
        tool_calls = msg.tool_calls or []

        if tool_calls:
            convo.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}}
                    for tc in tool_calls
                ],
            })
            for tc in tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                    if not isinstance(args, dict):
                        raise ValueError("arguments must be a JSON object")
                    output, is_error = run_tool(conn, user_id, tc.function.name, args)
                except (json.JSONDecodeError, ValueError) as e:
                    output, is_error = json.dumps({"error": f"invalid JSON arguments: {e}"}), True
                log.info("tool %s(%s) -> %s%s", tc.function.name, tc.function.arguments,
                         "ERROR " if is_error else "", output[:300])
                convo.append({"role": "tool", "tool_call_id": tc.id, "content": output})
            conn.commit()
            continue

        reply = _clean_reply(msg.content)
        if choice.finish_reason == "length" and not reply:
            return "That answer got too long — could you ask about a shorter period?"
        return reply or "👍"

    return "I got a bit tangled up working that out. Could you rephrase?"
