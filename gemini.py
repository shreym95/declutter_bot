import datetime
import json
import logging

from google import genai
from google.genai import types

import storage
from config import GEMINI_KEY, GEMINI_MODEL, SYSTEM_PROMPT

log = logging.getLogger("declutter_bot.gemini")

_client = genai.Client(api_key=GEMINI_KEY)
_sessions: dict[int, object] = {}


# ── Chat session management ────────────────────────────────────────────────────

def _get_session(uid: int):
    if uid not in _sessions:
        _sessions[uid] = _client.chats.create(
            model=GEMINI_MODEL,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                max_output_tokens=600,
            ),
        )
    return _sessions[uid]


def reset_session(uid: int) -> None:
    _sessions.pop(uid, None)


async def chat(uid: int, message: str) -> str:
    session = _get_session(uid)
    response = session.send_message(message)
    return response.text


# ── One-shot calls ─────────────────────────────────────────────────────────────

def _one_shot(prompt: str, max_tokens: int = 300) -> str:
    response = _client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(max_output_tokens=max_tokens),
    )
    return response.text.strip().strip("```json").strip("```").strip()


async def extract_tasks(user_message: str, gemini_reply: str) -> list[dict]:
    """Parse structured tasks from a Gemini sorted-reply."""
    prompt = (
        f"The user sent this message:\n{user_message}\n\n"
        f"The assistant sorted it like this:\n{gemini_reply}\n\n"
        f"Extract the tasks and their priorities. "
        f"Reply ONLY with a JSON array, no markdown, no explanation. Format:\n"
        f'[{{"text": "task name", "priority": "today|week|whenever"}}, ...]\n'
        f"If there are no tasks to extract (e.g. the user just chatted), reply with: []"
    )
    try:
        raw = _one_shot(prompt, max_tokens=300)
        items = json.loads(raw)
        if not isinstance(items, list):
            return []
        return [
            storage.make_task(
                item["text"],
                item.get("priority", "whenever"),
            )
            for item in items
            if isinstance(item, dict) and "text" in item
        ]
    except Exception:
        log.exception("extract_tasks failed")
        return []


async def parse_snooze(text: str) -> dict:
    """Parse a snooze request into {"task": str, "date": "YYYY-MM-DD"} or {"error": str}."""
    today = datetime.date.today().isoformat()
    prompt = (
        f"Today is {today}.\n"
        f"The user wants to snooze a task. Their message: \"{text}\"\n\n"
        f"Extract the task name and the date they want to be reminded.\n"
        f"Reply ONLY with valid JSON, no markdown, no explanation.\n"
        f'Format: {{"task": "task name here", "date": "YYYY-MM-DD"}}\n'
        f'If the date or task is unclear, reply: {{"error": "unclear"}}\n'
        f"Interpret relative dates: 'tomorrow', 'next Monday', 'in 3 days', etc."
    )
    try:
        raw = _one_shot(prompt, max_tokens=100)
        result = json.loads(raw)
        if "error" in result or "task" not in result or "date" not in result:
            return {"error": "unclear"}
        datetime.date.fromisoformat(result["date"])  # validate
        return result
    except Exception:
        log.exception("parse_snooze failed")
        return {"error": "unclear"}
