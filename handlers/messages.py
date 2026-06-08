import datetime
import logging
import time

from telegram import Update
from telegram.ext import ContextTypes

import gemini
import metrics
import storage
from config import IST, FLOW_EXPIRY_SECONDS
from jobs import register_snooze

log = logging.getLogger("declutter_bot.handlers.messages")


# ── Flow state helpers ─────────────────────────────────────────────────────────

def _pop_if_valid(bot_data: dict, key: str, uid: int) -> bool:
    """Return True and clear the flag if it belongs to uid and hasn't expired."""
    state = bot_data.get(key)
    if state and state[0] == uid:
        _, expire_ts = state
        bot_data.pop(key, None)
        return time.time() < expire_ts
    return False


def _set_flow(bot_data: dict, key: str, uid: int) -> None:
    bot_data[key] = (uid, time.time() + FLOW_EXPIRY_SECONDS)


# ── Flow handlers ──────────────────────────────────────────────────────────────

async def _on_mark_done(update: Update, uid: int, text: str) -> None:
    data = storage.load()
    udata = storage.get_user(data, uid)
    count = storage.mark_done_by_text(udata["tasks"], text)
    storage.save(data)

    if count:
        await gemini.chat(uid, f"I just marked as done: {text}")
        await update.message.reply_text(f"✅ Marked {count} task(s) as done. Nice work!")
    else:
        await update.message.reply_text(
            "I couldn't find that task. Try typing part of the name exactly as you added it."
        )


async def _on_eod_response(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: int, text: str) -> None:
    data = storage.load()
    udata = storage.get_user(data, uid)
    storage.mark_done_by_text(udata["tasks"], text)
    storage.save(data)

    active = storage.active_tasks(udata["tasks"])
    prompt = (
        f"END OF DAY MODE.\n"
        f"The user just reported what they completed: \"{text}\"\n"
        f"Remaining active tasks:\n{storage.format_for_prompt(active)}\n\n"
        f"Acknowledge warmly, note what carries over to tomorrow. Under 100 words."
    )
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    try:
        reply = await gemini.chat(uid, prompt)
        await update.message.reply_text(reply, parse_mode="Markdown")
    except Exception:
        log.exception("EOD response error for user %s", uid)
        await update.message.reply_text("Great work today! See you tomorrow.")


async def _on_snooze(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: int, text: str) -> None:
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    result = await gemini.parse_snooze(text)

    if "error" in result:
        await update.message.reply_text(
            "I couldn't work out which task or when. Try something like:\n"
            "_\"Snooze 'finish report' to next Monday\"_ or _\"remind me about groceries tomorrow\"_",
            parse_mode="Markdown",
        )
        return

    task_name: str = result["task"]
    snooze_date: str = result["date"]
    remind_dt = IST.localize(datetime.datetime.fromisoformat(f"{snooze_date}T{SNOOZE_HOUR:02d}:00:00"))

    if remind_dt <= datetime.datetime.now(tz=IST):
        await update.message.reply_text(f"That date ({snooze_date}) is in the past. Try a future date.")
        return

    data = storage.load()
    udata = storage.get_user(data, uid)
    display_name = task_name
    for t in udata["tasks"]:
        if not t["done"] and not t["archived"] and task_name.lower() in t["text"].lower():
            t["snoozed_until"] = remind_dt.timestamp()
            display_name = t["text"]
            storage.save(data)
            metrics.record_task_event("snoozed")
            log.info("Task snoozed for user %s until %s: %s", uid, snooze_date, display_name)
            break
    else:
        log.info("Snooze scheduled (no JSON match) for user %s: %s on %s", uid, task_name, snooze_date)

    register_snooze(context.job_queue, uid, display_name, remind_dt)
    await update.message.reply_text(
        f"⏰ Got it — I'll remind you about *{display_name}* on {snooze_date} at {SNOOZE_HOUR} AM.",
        parse_mode="Markdown",
    )


# Import here to avoid a circular import with config
from config import SNOOZE_REMINDER_HOUR as SNOOZE_HOUR


# ── Main handler ───────────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    text = update.message.text.strip()
    bot_data = context.bot_data

    metrics.record_message()

    # Seed primary_uid if the user never ran /start
    if not bot_data.get("primary_uid"):
        data = storage.load()
        storage.set_primary_uid(data, uid)
        storage.save(data)
        bot_data["primary_uid"] = uid

    # ── Active flow routing ────────────────────────────────────────────────────
    if _pop_if_valid(bot_data, "awaiting_eod", uid):
        await _on_eod_response(update, context, uid, text)
        return

    if _pop_if_valid(bot_data, "awaiting_done", uid):
        await _on_mark_done(update, uid, text)
        return

    if _pop_if_valid(bot_data, "awaiting_snooze", uid):
        await _on_snooze(update, context, uid, text)
        return

    # ── Button: show task list (no Gemini — renders from storage) ─────────────
    if text == "📋 Show my task list":
        data = storage.load()
        active = storage.active_tasks(storage.get_user(data, uid)["tasks"])
        if not active:
            await update.message.reply_text("No tasks yet — just send me anything on your mind!")
        else:
            await update.message.reply_text(storage.render_grouped(active), parse_mode="Markdown")
        return

    # ── Button: what to do now (injects real task list into prompt) ────────────
    if text == "➡️ What should I do now?":
        data = storage.load()
        active = storage.active_tasks(storage.get_user(data, uid)["tasks"])
        if not active:
            await update.message.reply_text("No tasks yet — just send me anything on your mind!")
            return
        text = (
            f"Here is my current task list:\n{storage.format_for_prompt(active)}\n\n"
            f"What is the single most important thing I should do right now? Pick one."
        )

    # ── Button: clear ──────────────────────────────────────────────────────────
    if text == "🗑 Clear & start fresh":
        gemini.reset_session(uid)
        data = storage.load()
        udata = storage.get_user(data, uid)
        storage.archive_all_active(udata["tasks"])
        storage.save(data)
        await update.message.reply_text("✅ Done — fresh start! What's on your mind?")
        return

    # ── Button: mark done ──────────────────────────────────────────────────────
    if text == "✅ Mark task done":
        _set_flow(bot_data, "awaiting_done", uid)
        await update.message.reply_text("Which task did you finish? Type the name (or part of it).")
        return

    # ── Button: snooze ────────────────────────────────────────────────────────
    if text == "⏰ Snooze a task":
        _set_flow(bot_data, "awaiting_snooze", uid)
        await update.message.reply_text(
            "Which task and when? For example:\n"
            "_\"Snooze 'call accountant' to next Friday\"_\n"
            "_\"Remind me about groceries tomorrow\"_",
            parse_mode="Markdown",
        )
        return

    # ── Button: overwhelmed ───────────────────────────────────────────────────
    if text == "😮‍💨 I'm overwhelmed":
        text = "I'm feeling really overwhelmed right now. Help me calm down and figure out just one thing to do."

    # ── Default: send to Gemini ────────────────────────────────────────────────
    log.info("Message from user %s: %.80s", uid, text)
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        reply = await gemini.chat(uid, text)
        await update.message.reply_text(reply, parse_mode="Markdown")

        new_tasks = await gemini.extract_tasks(text, reply)
        if new_tasks:
            data = storage.load()
            udata = storage.get_user(data, uid)
            storage.upsert(udata["tasks"], new_tasks)
            storage.save(data)
            log.info("Saved %d new task(s) for user %s", len(new_tasks), uid)

    except Exception:
        log.exception("Error handling message for user %s", uid)
        await update.message.reply_text(
            "Something went wrong — try again in a moment. "
            "If it keeps happening, tap *Clear & start fresh*.",
            parse_mode="Markdown",
        )
