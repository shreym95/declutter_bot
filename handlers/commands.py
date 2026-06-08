import logging

from telegram import KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.ext import ContextTypes

import gemini
import metrics
import storage

log = logging.getLogger("declutter_bot.handlers.commands")

KEYBOARD = ReplyKeyboardMarkup(
    [
        [KeyboardButton("➡️ What should I do now?"), KeyboardButton("📋 Show my task list")],
        [KeyboardButton("✅ Mark task done"),         KeyboardButton("⏰ Snooze a task")],
        [KeyboardButton("🗑 Clear & start fresh"),   KeyboardButton("😮‍💨 I'm overwhelmed")],
    ],
    resize_keyboard=True,
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    name = update.effective_user.first_name or "there"
    log.info("User %s (%s) started a session", uid, name)

    gemini.reset_session(uid)

    data = storage.load()
    storage.set_primary_uid(data, uid)
    storage.save(data)

    # Propagate to job data so scheduled jobs always have the latest uid
    context.bot_data["primary_uid"] = uid

    await update.message.reply_text(
        f"Hey {name} 👋\n\n"
        "I'm your personal task sorter. Whenever something pops into your head, "
        "just send it to me — one task or ten at once.\n\n"
        "I'll tell you what needs to happen *today*, what can wait, "
        "and exactly *what to do next*. No forms, no apps — just this chat.\n\n"
        "Go ahead — what's on your mind right now?",
        parse_mode="Markdown",
        reply_markup=KEYBOARD,
    )


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(metrics.format_stats_message(), parse_mode="Markdown")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "*How to use this bot:*\n\n"
        "• Type any task(s) — one per line or all at once\n"
        "• Ask *'what should I do now?'* anytime\n"
        "• Tap *✅ Mark task done* and tell me what you finished\n"
        "• Tap *⏰ Snooze a task* to defer with a natural-language date\n"
        "• Say *'clear'* or tap the button to start fresh\n"
        "• Just vent if you're overwhelmed — I'll help untangle it\n\n"
        "_Powered by Google Gemini. Tasks saved to tasks.json._",
        parse_mode="Markdown",
    )
