import datetime

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

import jobs
import storage
from config import (
    BOT_TOKEN,
    EVENING_REVIEW_HOUR,
    EVENING_REVIEW_MINUTE,
    IST,
    MORNING_SUMMARY_HOUR,
    MORNING_SUMMARY_MINUTE,
    setup_logging,
)
from handlers.callbacks import handle_archive
from handlers.commands import start, help_cmd
from handlers.messages import handle_message

log = setup_logging()


def main() -> None:
    log.info("Bot starting up")

    data = storage.load()
    primary_uid = storage.get_primary_uid(data)
    if primary_uid:
        log.info("Restored primary_uid=%s from disk", primary_uid)

    app = Application.builder().token(BOT_TOKEN).build()

    # Propagate primary_uid into bot_data for scheduled jobs
    app.bot_data["primary_uid"] = primary_uid

    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_archive, pattern="^archive:"))

    # Scheduled jobs — pass primary_uid via job data so jobs don't read globals
    jq = app.job_queue
    jq.run_daily(
        jobs.morning_summary,
        time=datetime.time(hour=MORNING_SUMMARY_HOUR, minute=MORNING_SUMMARY_MINUTE, tzinfo=IST),
        name="morning_summary",
        data={"primary_uid": primary_uid},
    )
    jq.run_daily(
        jobs.evening_review,
        time=datetime.time(hour=EVENING_REVIEW_HOUR, minute=EVENING_REVIEW_MINUTE, tzinfo=IST),
        name="evening_review",
        data={"primary_uid": primary_uid},
    )
    log.info(
        "Scheduled: morning summary %02d:%02d IST, evening review %02d:%02d IST",
        MORNING_SUMMARY_HOUR, MORNING_SUMMARY_MINUTE,
        EVENING_REVIEW_HOUR, EVENING_REVIEW_MINUTE,
    )

    # Restore snoozed reminders that survived a restart
    jobs.restore_snoozed_jobs(jq, data)

    log.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
