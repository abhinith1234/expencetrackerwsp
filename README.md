# WhatsApp Expense Tracker 💸

A WhatsApp bot you text your daily spending to. It understands casual messages, sorts
each expense into a category, tracks budgets, answers questions about your spending, and
sends you a report at the end of every month.

Built with **Python + FastAPI**, **Twilio WhatsApp**, **Claude** (Anthropic API), and **SQLite**.

## What you can send it

| You type | Bot does |
|---|---|
| `chai 20` | Logs ₹20 → Food & Dining |
| `uber 250, lunch 400 swiggy, groceries 1.2k on card` | Logs 3 expenses in one go |
| `paid electricity bill 1850 yesterday` | Logs it against yesterday's date |
| `hotel $120` | Logs in USD (you can type any currency) |
| `undo` / `that was 300 not 250` / `move it to groceries` | Deletes or fixes the last entry |
| `set food budget 8000` / `monthly budget 50k` | Sets category or overall budgets |
| `how much did I spend on food this month?` | Looks it up and answers |
| `swiggy vs zomato last 3 months` / `biggest expenses this week` | Any question about your data |
| `am I over budget?` | Budget vs spent, with days left |
| `report for October` | Full monthly report with tips |
| `make my default currency USD` | Changes the currency used when you don't name one |
| `/reset` | Clears the bot's short chat memory (expenses are kept) |

Categories: Food & Dining, Groceries, Transport, Fuel, Shopping, Bills & Utilities,
Rent & Housing, Entertainment, Health & Medical, Education, Travel, Personal Care,
Subscriptions, Gifts & Donations, Family & Kids, Investments & Savings, EMI & Loans, Other.

## How it works

```
WhatsApp ──► Twilio ──► POST /whatsapp (FastAPI)
                          │  replies "200 OK" right away
                          ▼
                    background task
                          │
                  Claude (claude-opus-5-5) ◄──► tools ──► SQLite
                  add_expenses · query_expenses · update_expense
                  delete_expense · set_budget · budget_status
                          │
                          ▼
               Twilio API ──► reply on WhatsApp
```

- `app/agent.py` – system prompt and the Claude tool-use loop.
- `app/tools.py` – the tools Claude can call, each locked to the sender's phone number.
- `app/db.py` – SQLite tables for expenses, budgets, users, and short chat history.
- `app/main.py` – the Twilio webhook (checks Twilio's signature) and the monthly-report endpoint.
- `app/report.py` – sends each user last month's report.
- `app/cli.py` – chat with the bot in your terminal, no WhatsApp needed.

Claude never makes up numbers. Every answer comes from SQL lookups on your stored expenses.

## Setup

### 1. Install

```bash
python3 -m venv .venv
```

```bash
source .venv/bin/activate
```

```bash
pip install -r requirements-dev.txt
```

```bash
cp .env.example .env
```

### 2. Get a Claude API key

Create a key at <https://platform.claude.com> and put it in `.env` as `ANTHROPIC_API_KEY`.

Try the bot in your terminal before setting up WhatsApp:

```bash
python -m app.cli
```

### 3. Connect WhatsApp (Twilio sandbox, free to try)

1. Sign up at <https://www.twilio.com>, then open **Console → Messaging → Try it out → Send a WhatsApp message**.
2. From your phone, send the join code shown there (e.g. `join something-word`) to the sandbox number.
3. Copy your **Account SID** and **Auth Token** into `.env`. Set `TWILIO_WHATSAPP_FROM`
   to the sandbox number (`whatsapp:+14155238886`).
4. Put your own number in `ALLOWED_NUMBERS`, e.g. `whatsapp:+919876543210`, so nobody else can use your bot.

### 4. Run the server and expose it

```bash
uvicorn app.main:app --port 8000
```

In another terminal, make it reachable from the internet (e.g. with [ngrok](https://ngrok.com)):

```bash
ngrok http 8000
```

Set `PUBLIC_BASE_URL` in `.env` to the `https://….ngrok-free.app` URL and restart uvicorn.
In the Twilio sandbox settings, set **"When a message comes in"** to
`https://….ngrok-free.app/whatsapp` (method **POST**).

Now send `lunch 250` on WhatsApp. 🎉

### 5. Monthly report

Run this on the 1st of every month. It sends each user the previous month's report:

```bash
python -m app.report
```

For example, with cron at 9:05 AM on the 1st:

```
5 9 1 * * cd /path/to/expencetrackerwsp && .venv/bin/python -m app.report
```

If you host the bot somewhere without cron, use any scheduler (GitHub Actions, cron-job.org, Render cron)
to call:

```bash
curl -X POST https://your-host/cron/monthly-report -H "X-Cron-Secret: $CRON_SECRET"
```

> **WhatsApp's 24-hour rule:** WhatsApp only lets a business send free-form messages within 24 hours
> of your last message. If you text the bot daily, the report always arrives. If you go quiet, it won't
> be delivered until you set up an approved message template on a production WhatsApp sender. You can
> always just ask: *"report for this month"*.

## Deploying

Any host that runs a Python web server works (Railway, Render, Fly.io, a small VPS). Start command:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Put `DB_PATH` on a persistent disk or volume, or your data is lost on redeploy. Set every variable
from `.env.example` in the host's environment settings. Point the Twilio webhook at
`https://your-host/whatsapp`.

## Configuration

| Variable | Default | Notes |
|---|---|---|
| `CLAUDE_MODEL` | `claude-opus-5-5` | Model used for understanding and answering |
| `CLAUDE_EFFORT` | `medium` | `low` is faster and cheaper; `high` thinks harder on tricky questions |
| `DEFAULT_CURRENCY` | `INR` | Used when you don't name a currency. Each user can change their own |
| `TIMEZONE` | `Asia/Kolkata` | Decides what "today" and "yesterday" mean |
| `HISTORY_TURNS` | `6` | How many past exchanges the bot remembers (for "change that to 300") |
| `ALLOWED_NUMBERS` | *(empty = anyone)* | Comma-separated `whatsapp:+…` numbers |

If Claude's safety filter turns down a message, the request is retried on a fallback model chosen
by Anthropic (`fallbacks: "default"`). To turn this off, delete the `betas` and `fallbacks` lines
in `app/agent.py`.

## Tests

```bash
pytest -q
```

The tests use a fake Claude client, so they need no API key and cost nothing.
