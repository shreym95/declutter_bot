import logging

from telegram import Update
from telegram.ext import ContextTypes

import storage

log = logging.getLogger("declutter_bot.handlers.callbacks")


async def handle_archive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    task_id = query.data.split(":", 1)[1]
    uid = query.from_user.id

    data = storage.load()
    udata = storage.get_user(data, uid)
    task_name = storage.archive_by_id(udata["tasks"], task_id)

    if task_name:
        storage.save(data)
        log.info("User %s archived stale task: %s", uid, task_name)
        await query.edit_message_text(
            f"🗃 Archived: _{task_name}_\n\nLet it go — you made a call.",
            parse_mode="Markdown",
        )
    else:
        await query.edit_message_text("Task not found — may have already been archived.")
