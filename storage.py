import json
import os
import time
from typing import Optional

from config import DATA_FILE

_PRIORITY_LABELS = {
    "today": "🔴 Do today",
    "week": "🟡 This week",
    "whenever": "⚪ Whenever",
}
VALID_PRIORITIES = frozenset(_PRIORITY_LABELS)


# ── I/O ───────────────────────────────────────────────────────────────────────

def load() -> dict:
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save(data: dict) -> None:
    """Atomic write: write to a temp file then rename to avoid corruption on crash."""
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, DATA_FILE)


# ── User namespace ─────────────────────────────────────────────────────────────

def get_user(data: dict, uid: int) -> dict:
    key = str(uid)
    if key not in data:
        data[key] = {"tasks": []}
    return data[key]


def get_primary_uid(data: dict) -> Optional[int]:
    return data.get("meta", {}).get("primary_uid")


def set_primary_uid(data: dict, uid: int) -> None:
    data.setdefault("meta", {})["primary_uid"] = uid


# ── Task constructors ──────────────────────────────────────────────────────────

def make_task(text: str, priority: str = "whenever") -> dict:
    if priority not in VALID_PRIORITIES:
        priority = "whenever"
    ts = int(time.time())
    return {
        "id": f"t_{ts}_{hash(text) % 10000:04d}",
        "text": text,
        "priority": priority,
        "created_at": ts,
        "snoozed_until": None,
        "done": False,
        "archived": False,
    }


# ── Task queries ───────────────────────────────────────────────────────────────

def active_tasks(tasks: list) -> list:
    now = time.time()
    return [
        t for t in tasks
        if not t["done"]
        and not t["archived"]
        and (t["snoozed_until"] is None or t["snoozed_until"] <= now)
    ]


def stale_tasks(tasks: list, days: int = 7) -> list:
    cutoff = time.time() - (days * 86400)
    return [
        t for t in tasks
        if not t["done"]
        and not t["archived"]
        and t["snoozed_until"] is None
        and t["created_at"] < cutoff
    ]


def format_for_prompt(tasks: list) -> str:
    if not tasks:
        return "(no tasks)"
    return "\n".join(
        f"[{_PRIORITY_LABELS.get(t['priority'], '⚪ Whenever')}] {t['text']}"
        for t in tasks
    )


def render_grouped(tasks: list) -> str:
    """Return a Markdown-formatted grouped task list for display to the user."""
    groups: dict[str, list[str]] = {"today": [], "week": [], "whenever": []}
    for t in tasks:
        groups.setdefault(t["priority"], []).append(t["text"])

    lines = []
    for key, heading in [
        ("today", "🔴 *Do today*"),
        ("week", "🟡 *This week*"),
        ("whenever", "⚪ *Whenever*"),
    ]:
        if groups[key]:
            lines.append(heading)
            lines.extend(f"• {item}" for item in groups[key])
    return "\n".join(lines)


# ── Task mutations ─────────────────────────────────────────────────────────────

def upsert(existing: list, new_tasks: list) -> None:
    """Append tasks not already present (case-insensitive text match)."""
    seen = {t["text"].lower() for t in existing if not t["archived"]}
    for t in new_tasks:
        key = t["text"].lower()
        if key not in seen:
            existing.append(t)
            seen.add(key)


def mark_done_by_text(tasks: list, done_text: str) -> int:
    """Mark tasks whose text appears in done_text. Returns count marked."""
    done_lower = done_text.lower()
    count = 0
    for t in tasks:
        if not t["done"] and t["text"].lower() in done_lower:
            t["done"] = True
            count += 1
    return count


def archive_by_id(tasks: list, task_id: str) -> Optional[str]:
    """Set archived+done on matching task. Returns task text or None if not found."""
    for t in tasks:
        if t["id"] == task_id:
            t["archived"] = True
            t["done"] = True
            return t["text"]
    return None


def archive_all_active(tasks: list) -> None:
    for t in tasks:
        if not t["done"]:
            t["archived"] = True
