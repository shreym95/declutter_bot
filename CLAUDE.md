# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

**stress_bot** is a personal stress and task manager Telegram bot powered by Google Gemini 2.5 Flash. Users send their tasks (one or many) to the bot, which uses Gemini to sort them by priority (today/this week/whenever) and suggest the next immediate action. The bot maintains per-user conversation history to provide context-aware responses.

## Quick Start

### Setup (one-time, ~5 minutes)
1. Get a **free** Gemini API key from [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
2. Message `@BotFather` on Telegram → `/newbot` → copy the BOT_TOKEN
3. Install dependencies: `pip install python-telegram-bot google-genai`
4. Edit `stress_bot.py`: replace `BOT_TOKEN` and `GEMINI_KEY` at the top with your credentials
5. Run: `python stress_bot.py`

### Running the Bot
```bash
python stress_bot.py
```

The bot will output "🤖 Stress bot is running (Gemini)..." and start polling for Telegram messages. Press Ctrl+C to stop.

## Architecture

The bot has three main layers:

1. **Telegram Integration** (lines 99-182)
   - Handlers for `/start`, `/help`, and message routing via `python-telegram-bot`
   - Quick-reply buttons to let users ask "What should I do now?" or view their list
   - All user interactions funnel through `handle_message()`
   - Typing indicator shows the bot is thinking

2. **Gemini Integration** (lines 74-95)
   - `get_chat()` creates or retrieves a persistent Gemini chat session per user
   - `ask_gemini()` sends messages and receives replies with full conversation memory
   - Uses Gemini 2.5 Flash (free API, fast responses, good quality)
   - System prompt (lines 34-72) defines the bot's personality and task-sorting logic
   - Each chat session configured with `max_output_tokens=600` to keep responses concise

3. **User State Management** (lines 31-32, 74-89)
   - In-memory dict `user_chats` maps user IDs to persistent Gemini chat sessions
   - Gemini handles conversation history automatically (no manual tracking needed)
   - Chat session is reset on `/start` or when user taps "Clear & start fresh"
   - Data is lost on bot restart (acceptable for a lightweight personal tool)

## Key Design Decisions

- **System Prompt as Core Logic**: The sorting algorithm (urgent → 🔴, upcoming → 🟡, no deadline → ⚪) and response format are entirely defined in the system prompt (lines 34-72). Modify this section to change how Gemini prioritizes tasks.
- **Lightweight State**: No database — just in-memory chat sessions. Fine for a personal tool, but conversations are lost on restart. Users can tap "Clear & start fresh" to reset anytime.
- **Persistent Chat Sessions**: Each user gets one Gemini chat session that persists for the lifetime of the bot. Gemini automatically maintains full conversation history, so the bot remembers all prior exchanges.
- **Free API**: Uses Gemini 2.5 Flash on the free tier (aistudio.google.com/apikey) with no authentication required beyond the API key. No Anthropic credits needed.

## Common Development Tasks

### Adding a New Quick-Reply Button
1. Add a button to the keyboard in `start()` (lines 104-108)
2. Add a mapping in the `button_map` dict in `handle_message()` (lines 138-142)
3. The button text will be rewritten to the mapped query before sending to Gemini

### Changing the Task-Sorting Logic
Edit the system prompt (lines 34-72). For example:
- To add a 4th priority level, add it to the format example and describe when Gemini should use it
- To change the emoji or wording, update the bullets and the "Start here" line
- To adjust tone or response length, modify lines 49, 59, or 81

### Testing the Bot Response Locally (without Telegram)
Call `ask_gemini()` directly from a script:
```python
import asyncio
user_id = 123
message = "Buy groceries, finish report, call mom"
reply = asyncio.run(ask_gemini(user_id, message))
print(reply)
```

### Persisting User Data Across Bot Restarts
Replace the in-memory `user_chats` dict with a database:
1. Use SQLite or another embedded DB to store chat history per user_id
2. Modify `get_chat()` (line 74) to load from DB instead of creating fresh sessions
3. Implement a save-on-close handler to persist chat state on bot shutdown

Alternatively, use Gemini's API to export/archive chat history before restarting.

## Dependencies

- `python-telegram-bot`: Telegram bot framework (async handlers)
- `google-genai`: Google Gemini API client (free, no authentication beyond API key)
- Built-in: `asyncio`

## File Structure

```
stress_bot/
├── stress_bot.py      # Single file; handles Telegram, Gemini, and state
└── CLAUDE.md          # This file
```

## Monitoring & Debugging

- Bot prints errors to stdout (line 160): watch for API failures or Telegram connectivity issues
- If the bot stops responding, check that BOT_TOKEN and GEMINI_KEY are valid
- Gemini API is free on the standard tier; check [aistudio.google.com](https://aistudio.google.com) for usage stats
- Telegram rate limits (generous, ~30 msgs/sec per bot) are unlikely to be hit in personal use

## Model Selection

The bot uses `gemini-2.5-flash` (line 78). This is a good default because:
- **Free** — no Anthropic credits or billing needed
- **Fast** — 1-2 second response time typical for Telegram UX
- **Strong** — good at understanding context, sorting ambiguous tasks, and generating concise responses
- **Multimodal** — can handle images if you later add photo support

If you need even faster responses, consider `gemini-1.5-flash`; if you need higher accuracy on complex reasoning, consider `gemini-2.0-pro` (paid).
