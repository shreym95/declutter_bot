import datetime
import json
import logging
import os
import time

log = logging.getLogger("declutter_bot.metrics")

METRICS_FILE = "metrics.json"
_CALL_TYPES = ("chat", "extract_tasks", "parse_snooze")
_LATENCY_WINDOW = 100  # keep last N samples per call type


# ── I/O ───────────────────────────────────────────────────────────────────────

def _default() -> dict:
    return {
        "gemini": {
            ct: {"calls": 0, "errors": 0, "total_ms": 0.0, "latencies": []}
            for ct in _CALL_TYPES
        },
        "tasks": {"created": 0, "done": 0, "archived": 0, "snoozed": 0},
        "messages": {"total": 0, "by_day": {}},
        "last_reset": datetime.datetime.now().isoformat(timespec="seconds"),
    }


def load() -> dict:
    try:
        with open(METRICS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Back-fill any missing keys (e.g. after adding a new call type)
        for ct in _CALL_TYPES:
            data["gemini"].setdefault(ct, {"calls": 0, "errors": 0, "total_ms": 0.0, "latencies": []})
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        return _default()


def save(data: dict) -> None:
    tmp = METRICS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, METRICS_FILE)


# ── Recorders ─────────────────────────────────────────────────────────────────

def record_gemini_call(call_type: str, duration_ms: float, error: bool = False) -> None:
    try:
        data = load()
        entry = data["gemini"].setdefault(
            call_type, {"calls": 0, "errors": 0, "total_ms": 0.0, "latencies": []}
        )
        entry["calls"] += 1
        entry["total_ms"] += duration_ms
        if error:
            entry["errors"] += 1
        lats = entry["latencies"]
        lats.append(round(duration_ms, 1))
        if len(lats) > _LATENCY_WINDOW:
            entry["latencies"] = lats[-_LATENCY_WINDOW:]
        save(data)
    except Exception:
        log.exception("record_gemini_call failed (non-fatal)")


def record_task_event(event: str) -> None:
    """event: 'created' | 'done' | 'archived' | 'snoozed'"""
    try:
        data = load()
        data["tasks"][event] = data["tasks"].get(event, 0) + 1
        save(data)
    except Exception:
        log.exception("record_task_event failed (non-fatal)")


def record_message() -> None:
    try:
        data = load()
        data["messages"]["total"] = data["messages"].get("total", 0) + 1
        today = datetime.date.today().isoformat()
        data["messages"]["by_day"][today] = data["messages"]["by_day"].get(today, 0) + 1
        save(data)
    except Exception:
        log.exception("record_message failed (non-fatal)")


# ── Formatting ─────────────────────────────────────────────────────────────────

def _p95(latencies: list) -> float:
    if not latencies:
        return 0.0
    sorted_lats = sorted(latencies)
    idx = max(0, int(len(sorted_lats) * 0.95) - 1)
    return sorted_lats[idx]


def format_stats_message() -> str:
    data = load()
    g = data["gemini"]
    t = data["tasks"]
    m = data["messages"]

    lines = ["📊 *Bot Stats*\n"]

    # Gemini calls
    lines.append("*Gemini API*")
    total_calls = sum(g[ct]["calls"] for ct in _CALL_TYPES)
    total_errors = sum(g[ct]["errors"] for ct in _CALL_TYPES)
    lines.append(f"Total calls: `{total_calls}` | Errors: `{total_errors}`")
    lines.append("")

    label = {"chat": "Chat", "extract_tasks": "Extract tasks", "parse_snooze": "Parse snooze"}
    for ct in _CALL_TYPES:
        entry = g[ct]
        calls = entry["calls"]
        if calls == 0:
            lines.append(f"_{label[ct]}_: no calls yet")
            continue
        avg = entry["total_ms"] / calls
        p95 = _p95(entry["latencies"])
        err_pct = (entry["errors"] / calls) * 100
        lines.append(
            f"_{label[ct]}_: `{calls}` calls | avg `{avg:.0f}ms` | p95 `{p95:.0f}ms` | err `{err_pct:.1f}%`"
        )

    # Tasks
    lines.append("")
    lines.append("*Tasks*")
    lines.append(
        f"Created: `{t.get('created', 0)}` | Done: `{t.get('done', 0)}` | "
        f"Archived: `{t.get('archived', 0)}` | Snoozed: `{t.get('snoozed', 0)}`"
    )

    # Messages
    lines.append("")
    lines.append("*Messages*")
    lines.append(f"Total: `{m.get('total', 0)}`")
    by_day = m.get("by_day", {})
    if by_day:
        recent = sorted(by_day.items())[-7:]  # last 7 days
        lines.append("Last 7 days: " + ", ".join(f"`{d[-5:]}:{n}`" for d, n in recent))

    # Since
    lines.append("")
    lines.append(f"_Since: {data.get('last_reset', 'unknown')}_")

    return "\n".join(lines)
