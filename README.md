# declutter_bot

Personal task management Telegram bot — built with [Claude Code](https://claude.ai/code), powered by [Google Gemini 2.5 Flash](https://aistudio.google.com/apikey).

---

## Features

- **AI task sorting** — dumps sorted into 🔴 Today / 🟡 This Week / ⚪ Whenever via Gemini
- **Morning briefing** — top 3 priorities at 10:30 AM IST, flags tasks older than 7 days
- **Evening check-in** — 11 PM prompt to log completions; carries over the rest
- **Snooze / defer** — natural language ("remind me about X next Monday") → 9 AM reminder
- **Stale task pruning** — one-tap inline archive buttons in morning summary
- **JSON persistence** — tasks survive restarts; snoozed reminders re-register on boot

---

## Setup

### Prerequisites
- Python 3.10+
- Gemini API key → [aistudio.google.com/apikey](https://aistudio.google.com/apikey) (free)
- Telegram bot token → [@BotFather](https://t.me/BotFather) → `/newbot`

### Install

```bash
git clone https://github.com/shreym95/declutter_bot.git
cd declutter_bot
pip install -r requirements.txt
```

### Configure

```bash
cp .env.example .env
# Edit .env with your BOT_TOKEN and GEMINI_KEY
```

### Run

```bash
python stress_bot.py
```

---

## Architecture

Single-file, no database. State stored in `tasks.json`.

```
stress_bot.py
├── Data layer       load/save tasks.json, task CRUD helpers
├── Gemini layer     persistent chat sessions + one-shot calls for extraction & snooze parsing
├── Snooze layer     natural language date parsing, run_once scheduling, restart re-registration
├── Handlers         /start, /help, handle_message (state machine with 30-min expiry flags)
├── Scheduled jobs   morning_summary, evening_review, snooze_reminder
└── main()           wire handlers, schedule daily jobs, restore snoozed tasks from disk
```

### Key design decisions

| Decision | Rationale |
|---|---|
| One-shot Gemini call for task extraction | Keeps extraction logic out of chat history |
| One-shot Gemini call for snooze parsing | Stateless date parsing; today's date injected into prompt |
| `context.bot_data` flags with expiry timestamps | Multi-turn flows (mark done, snooze, EOD) don't bleed into unrelated messages |
| Task list rendered from `tasks.json` directly | Eliminates hallucination on list/next-action queries |
| Snoozed tasks re-registered on boot | `run_once` jobs are in-memory; persistence requires a scan of `tasks.json` at startup |

---

## Dependencies

```
python-telegram-bot[job-queue]>=21.9   # Telegram + APScheduler
google-genai>=0.3.0                    # Gemini API
python-dotenv>=1.0.0                   # Credential loading
pytz>=2024.1                           # Timezone handling
tzdata>=2024.1                         # IANA tz data (required on Windows)
```

---

## Built with

- [Claude Code](https://claude.ai/code) — AI coding assistant used for the entire development lifecycle
- [Google Gemini 2.5 Flash](https://deepmind.google/technologies/gemini/) — task intelligence, snooze parsing, summaries
