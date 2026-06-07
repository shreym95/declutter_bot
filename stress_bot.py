"""
Personal Stress & Task Manager Bot — powered by Google Gemini
──────────────────────────────────────────────────────────────
Drop tasks anytime. Gemini sorts them and tells you exactly what to do next.

SETUP (one time, ~5 minutes):
  1. Get a FREE Gemini API key → https://aistudio.google.com/apikey
  2. Message @BotFather on Telegram → /newbot → copy your BOT_TOKEN
  3. Paste both keys below
  4. Run:
       pip install "python-telegram-bot[job-queue]>=21.9" google-genai tzdata
       python stress_bot.py
"""

# ─── YOUR KEYS ────────────────────────────────────────────────────────────────
BOT_TOKEN   = "YOUR_BOT_TOKEN_HERE"   # from @BotFather on Telegram
GEMINI_KEY  = "YOUR_GEMINI_KEY_HERE"       # free from aistudio.google.com/apikey
# ──────────────────────────────────────────────────────────────────────────────

import json
import time
import datetime

from google import genai
from google.genai import types
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes
)

# One Gemini client, one chat session per user
gemini_client = genai.Client(api_key=GEMINI_KEY)

# { user_id: chat_session }
user_chats: dict = {}

# Persists across restarts — set from tasks.json on boot, then on /start
PRIMARY_USER_ID: int | None = None

DATA_FILE = "tasks.json"

SYSTEM_PROMPT = """You are a calm, focused personal stress coach on Telegram.

The user is often overwhelmed, can't prioritise, and feels lost. Your job:

1. When they send tasks (one or many lines), sort them into:
   🔴 Do Today / 🟡 This Week / ⚪ Whenever

2. Use ONE simple question to sort each task:
   "When does something actually break if this isn't done?"
   - Real consequence today → 🔴 Do Today
   - Needs doing soon, no emergency today → 🟡 This Week
   - No real deadline → ⚪ Whenever

3. Always end with ONE specific next step — not a list of tips, just one action.

4. Keep responses SHORT and calm. Max 200 words. No bullet-point overload.

5. If they seem stressed or overwhelmed, acknowledge it in one line before sorting.

6. If they ask "what should I do now?" or "what's next?" — look at their 🔴 list and pick ONE task to focus on.

7. Never ask the user to rate importance/urgency themselves — you decide from context.

8. Remember tasks from earlier in this conversation and track what they've marked done.

9. MORNING SUMMARY MODE (triggered by system): When given a task list for a morning
   summary, identify the top 3 priorities, flag any tasks older than 7 days, and give
   one sentence of encouragement. Stay under 150 words.

10. END OF DAY MODE (triggered by system): When the user reports what they completed,
    acknowledge it warmly, mark those tasks as done, and note what carries over to
    tomorrow. Keep it under 100 words.

Tone: like a calm, clear-headed friend — not a productivity app, not a therapist.

Format your task sort like this (only when sorting tasks):
🔴 *Do today*
• task name

🟡 *This week*
• task name

⚪ *Whenever*
• task name

➡️ *Start here:* [one specific task + one sentence on how to begin]
"""

# ─── DATA LAYER ───────────────────────────────────────────────────────────────

def load_data() -> dict:
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_data(data: dict):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def get_user_data(data: dict, uid: int) -> dict:
    key = str(uid)
    if key not in data:
        data[key] = {"tasks": []}
    return data[key]

def get_active_tasks(tasks: list) -> list:
    now = time.time()
    return [
        t for t in tasks
        if not t["done"] and not t["archived"]
        and (t["snoozed_until"] is None or t["snoozed_until"] <= now)
    ]

def get_stale_tasks(tasks: list, days: int = 7) -> list:
    cutoff = time.time() - (days * 86400)
    return [
        t for t in tasks
        if not t["done"] and not t["archived"]
        and t["created_at"] < cutoff
        and t["snoozed_until"] is None
    ]

def format_tasks_for_gemini(tasks: list) -> str:
    if not tasks:
        return "(no tasks)"
    priority_label = {"today": "🔴 Do today", "week": "🟡 This week", "whenever": "⚪ Whenever"}
    lines = []
    for t in tasks:
        label = priority_label.get(t["priority"], "⚪ Whenever")
        lines.append(f"[{label}] {t['text']}")
    return "\n".join(lines)

def make_task(text: str, priority: str = "whenever") -> dict:
    ts = int(time.time())
    return {
        "id": f"t_{ts}_{hash(text) % 10000:04d}",
        "text": text,
        "priority": priority,   # "today" | "week" | "whenever"
        "created_at": ts,
        "snoozed_until": None,
        "done": False,
        "archived": False,
    }

async def extract_tasks_from_reply(uid: int, user_message: str, gemini_reply: str) -> list[dict]:
    """One-shot Gemini call to extract a structured task list from a sorted reply."""
    prompt = (
        f"The user sent this message:\n{user_message}\n\n"
        f"The assistant sorted it like this:\n{gemini_reply}\n\n"
        f"Extract the tasks and their priorities. "
        f"Reply ONLY with a JSON array, no markdown, no explanation. Format:\n"
        f'[{{"text": "task name", "priority": "today|week|whenever"}}, ...]\n'
        f"If there are no tasks to extract (e.g. the user just chatted), reply with: []"
    )
    try:
        response = gemini_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(max_output_tokens=300)
        )
        raw = response.text.strip().strip("```json").strip("```").strip()
        items = json.loads(raw)
        if not isinstance(items, list):
            return []
        now = int(time.time())
        tasks = []
        for item in items:
            if isinstance(item, dict) and "text" in item:
                priority = item.get("priority", "whenever")
                if priority not in ("today", "week", "whenever"):
                    priority = "whenever"
                tasks.append(make_task(item["text"], priority))
        return tasks
    except Exception:
        return []

def upsert_tasks(existing: list, new_tasks: list):
    """Add new tasks, skip if same text already exists (case-insensitive)."""
    existing_texts = {t["text"].lower() for t in existing if not t["archived"]}
    for t in new_tasks:
        if t["text"].lower() not in existing_texts:
            existing.append(t)
            existing_texts.add(t["text"].lower())

def mark_tasks_done_by_text(tasks: list, done_text: str):
    """Mark any task whose text is mentioned in done_text as done."""
    done_lower = done_text.lower()
    for t in tasks:
        if not t["done"] and t["text"].lower() in done_lower:
            t["done"] = True

# ─── GEMINI LAYER ─────────────────────────────────────────────────────────────

def get_chat(user_id: int):
    if user_id not in user_chats:
        user_chats[user_id] = gemini_client.chats.create(
            model="gemini-2.5-flash",
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                max_output_tokens=600,
            )
        )
    return user_chats[user_id]

def reset_chat(user_id: int):
    if user_id in user_chats:
        del user_chats[user_id]

async def ask_gemini(user_id: int, message: str) -> str:
    chat = get_chat(user_id)
    response = chat.send_message(message)
    return response.text

# ─── TELEGRAM HANDLERS ────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global PRIMARY_USER_ID
    name = update.effective_user.first_name or "there"
    uid  = update.effective_user.id
    reset_chat(uid)

    # Persist primary user ID
    PRIMARY_USER_ID = uid
    data = load_data()
    data.setdefault("meta", {})["primary_uid"] = uid
    save_data(data)

    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("➡️ What should I do now?"), KeyboardButton("📋 Show my task list")],
         [KeyboardButton("✅ Mark task done"),         KeyboardButton("🗑 Clear & start fresh")],
         [KeyboardButton("😮‍💨 I'm overwhelmed")]],
        resize_keyboard=True
    )

    await update.message.reply_text(
        f"Hey {name} 👋\n\n"
        "I'm your personal task sorter. Whenever something pops into your head, "
        "just send it to me — one task or ten at once.\n\n"
        "I'll tell you what needs to happen *today*, what can wait, "
        "and exactly *what to do next*. No forms, no apps — just this chat.\n\n"
        "Go ahead — what's on your mind right now?",
        parse_mode="Markdown",
        reply_markup=keyboard
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "*How to use this bot:*\n\n"
        "• Type any task(s) — one per line or all at once\n"
        "• Ask *'what should I do now?'* anytime\n"
        "• Say *'done: [task]'* to mark something complete\n"
        "• Tap *✅ Mark task done* and tell me what you finished\n"
        "• Say *'clear'* or tap the button to start fresh\n"
        "• Just vent if you're overwhelmed — I'll help untangle it\n\n"
        "_Powered by Google Gemini. Tasks saved to tasks.json._",
        parse_mode="Markdown"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global PRIMARY_USER_ID
    uid  = update.effective_user.id
    text = update.message.text.strip()

    # Ensure primary_uid is set if user skipped /start
    if PRIMARY_USER_ID is None:
        PRIMARY_USER_ID = uid
        data = load_data()
        data.setdefault("meta", {})["primary_uid"] = uid
        save_data(data)

    # ── State machine: check for pending flows ──────────────────────────────
    bot_data = context.bot_data

    # EOD response flow
    eod_state = bot_data.get("awaiting_eod")
    if eod_state and eod_state[0] == uid:
        _, expire_ts = eod_state
        if time.time() < expire_ts:
            bot_data.pop("awaiting_eod", None)
            await _handle_eod_response(update, context, uid, text)
            return
        else:
            bot_data.pop("awaiting_eod", None)

    # Mark-done flow
    done_state = bot_data.get("awaiting_done")
    if done_state and done_state[0] == uid:
        _, expire_ts = done_state
        if time.time() < expire_ts:
            bot_data.pop("awaiting_done", None)
            await _handle_mark_done(update, context, uid, text)
            return
        else:
            bot_data.pop("awaiting_done", None)

    # ── Button mappings ─────────────────────────────────────────────────────
    button_map = {
        "➡️ What should I do now?": "What is the single most important thing I should do right now? Pick one from my list.",
        "📋 Show my task list":      "Show me everything on my current list, grouped by priority. Skip archived tasks.",
        "😮‍💨 I'm overwhelmed":       "I'm feeling really overwhelmed right now. Help me calm down and figure out just one thing to do.",
    }

    if text == "🗑 Clear & start fresh":
        reset_chat(uid)
        data = load_data()
        udata = get_user_data(data, uid)
        # Archive all undone tasks instead of deleting
        for t in udata["tasks"]:
            if not t["done"]:
                t["archived"] = True
        save_data(data)
        await update.message.reply_text("✅ Done — fresh start! What's on your mind?")
        return

    if text == "✅ Mark task done":
        expire_ts = time.time() + 1800  # 30 min window
        bot_data["awaiting_done"] = (uid, expire_ts)
        await update.message.reply_text(
            "Which task did you finish? Type the name (or part of it)."
        )
        return

    text = button_map.get(text, text)

    # Show typing indicator
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action="typing"
    )

    try:
        reply = await ask_gemini(uid, text)
        await update.message.reply_text(reply, parse_mode="Markdown")

        # Extract and persist tasks from this exchange
        new_tasks = await extract_tasks_from_reply(uid, text, reply)
        if new_tasks:
            data = load_data()
            udata = get_user_data(data, uid)
            upsert_tasks(udata["tasks"], new_tasks)
            save_data(data)

    except Exception as e:
        print(f"Error for user {uid}: {e}")
        await update.message.reply_text(
            "Something went wrong — try again in a moment. "
            "If it keeps happening, tap *Clear & start fresh*.",
            parse_mode="Markdown"
        )

async def _handle_mark_done(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: int, text: str):
    """Handle the 'mark task done' flow response."""
    data = load_data()
    udata = get_user_data(data, uid)
    before_count = sum(1 for t in udata["tasks"] if t["done"])
    mark_tasks_done_by_text(udata["tasks"], text)
    after_count = sum(1 for t in udata["tasks"] if t["done"])
    newly_done = after_count - before_count
    save_data(data)

    if newly_done > 0:
        # Also tell Gemini so it stays in sync
        await ask_gemini(uid, f"I just marked as done: {text}")
        await update.message.reply_text(
            f"✅ Marked {newly_done} task(s) as done. Nice work!",
        )
    else:
        await update.message.reply_text(
            "I couldn't find that task in your list. Try typing part of the task name exactly as you added it.",
        )

async def _handle_eod_response(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: int, text: str):
    """Handle the end-of-day review response."""
    data = load_data()
    udata = get_user_data(data, uid)

    mark_tasks_done_by_text(udata["tasks"], text)
    save_data(data)

    active = get_active_tasks(udata["tasks"])
    active_text = format_tasks_for_gemini(active)

    prompt = (
        f"END OF DAY MODE.\n"
        f"The user just reported what they completed: \"{text}\"\n"
        f"Remaining active tasks:\n{active_text}\n\n"
        f"Acknowledge warmly, note what carries over to tomorrow. Under 100 words."
    )
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    try:
        reply = await ask_gemini(uid, prompt)
        await update.message.reply_text(reply, parse_mode="Markdown")
    except Exception as e:
        print(f"EOD error for user {uid}: {e}")
        await update.message.reply_text("Great work today! See you tomorrow.")

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    global PRIMARY_USER_ID

    if "YOUR_" in BOT_TOKEN or "YOUR_" in GEMINI_KEY:
        print("\n⚠️  Paste your keys at the top of this file first.\n")
        return

    # Restore primary user ID from disk
    data = load_data()
    PRIMARY_USER_ID = data.get("meta", {}).get("primary_uid")

    print("🤖 Stress bot is running (Gemini). Press Ctrl+C to stop.")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help",  help_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
