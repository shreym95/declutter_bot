import datetime
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import gemini
import storage
from config import IST, FLOW_EXPIRY_SECONDS, SNOOZE_REMINDER_HOUR
import time

log = logging.getLogger("declutter_bot.jobs")


async def morning_summary(context: ContextTypes.DEFAULT_TYPE) -> None:
    uid: int | None = context.job.data.get("primary_uid")
    if uid is None:
        log.warning("morning_summary fired but primary_uid is not set")
        return

    data = storage.load()
    udata = storage.get_user(data, uid)
    active = storage.active_tasks(udata["tasks"])

    if not active:
        await context.bot.send_message(uid, "☀️ Good morning! No pending tasks — enjoy the clear head.")
        return

    stale = storage.stale_tasks(udata["tasks"])
    task_text = storage.format_for_prompt(active)
    stale_note = ""
    if stale:
        stale_names = ", ".join(t["text"] for t in stale[:5])
        stale_note = (
            f"\n\nAlso flag these tasks as older than 7 days (mention them briefly): {stale_names}"
        )

    prompt = (
        f"MORNING SUMMARY MODE.\n"
        f"Current task list:\n{task_text}\n\n"
        f"Give me:\n"
        f"1. Top 3 priorities for today (pick from 🔴 first, then 🟡)\n"
        f"2. One sentence of encouragement\n"
        f"Keep it under 150 words.{stale_note}"
    )

    try:
        reply = await gemini.chat(uid, prompt)
        await context.bot.send_message(
            uid, f"☀️ *Morning check-in*\n\n{reply}", parse_mode="Markdown"
        )
        if stale:
            buttons = [
                [InlineKeyboardButton(f"🗃 Archive: {t['text'][:40]}", callback_data=f"archive:{t['id']}")]
                for t in stale[:5]
            ]
            await context.bot.send_message(
                uid,
                "These tasks have been sitting for 7+ days. Archive the ones you're letting go of:",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        log.info("Morning summary sent to user %s (%d stale tasks)", uid, len(stale))
    except Exception:
        log.exception("Morning summary failed for user %s", uid)


async def evening_review(context: ContextTypes.DEFAULT_TYPE) -> None:
    uid: int | None = context.job.data.get("primary_uid")
    if uid is None:
        log.warning("evening_review fired but primary_uid is not set")
        return

    expire_ts = time.time() + FLOW_EXPIRY_SECONDS
    context.bot_data["awaiting_eod"] = (uid, expire_ts)

    try:
        await context.bot.send_message(
            uid,
            "🌙 *Evening check-in*\n\n"
            "What did you get done today? Just tell me — one thing or many.\n\n"
            "_Say 'nothing' if it was one of those days. No judgment._",
            parse_mode="Markdown",
        )
        log.info("Evening review prompt sent to user %s", uid)
    except Exception:
        log.exception("Evening review failed for user %s", uid)


async def snooze_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    uid: int = context.job.data["uid"]
    task_text: str = context.job.data["task"]
    try:
        await context.bot.send_message(
            uid,
            f"⏰ *Snooze reminder*\n\nTime to look at: _{task_text}_\n\nWhat do you want to do with this?",
            parse_mode="Markdown",
        )
        log.info("Snooze reminder sent to user %s for task: %s", uid, task_text)
    except Exception:
        log.exception("Snooze reminder failed for user %s", uid)


def register_snooze(job_queue, uid: int, task_text: str, remind_dt: datetime.datetime) -> None:
    job_queue.run_once(
        snooze_reminder,
        when=remind_dt,
        data={"uid": uid, "task": task_text},
        name=f"snooze_{uid}_{task_text[:20]}",
    )


def restore_snoozed_jobs(job_queue, data: dict) -> None:
    """Re-register pending snooze jobs from tasks.json after a restart."""
    now_ts = time.time()
    count = 0
    for uid_key, udata in data.items():
        if uid_key == "meta" or not isinstance(udata, dict):
            continue
        uid_int = int(uid_key)
        for t in udata.get("tasks", []):
            snooze_ts = t.get("snoozed_until")
            if snooze_ts and snooze_ts > now_ts and not t["done"] and not t["archived"]:
                remind_dt = datetime.datetime.fromtimestamp(snooze_ts, tz=IST)
                register_snooze(job_queue, uid_int, t["text"], remind_dt)
                count += 1
                log.info(
                    "Re-registered snooze for user %s: '%s' at %s",
                    uid_int, t["text"], remind_dt.isoformat(),
                )
    if count:
        log.info("Restored %d snoozed reminder(s) from disk", count)
