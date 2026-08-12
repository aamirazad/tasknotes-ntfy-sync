"""Build immutable notification payload snapshots and occurrence identities."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import PurePosixPath
from urllib.parse import quote, urlencode

from .domain import Reminder, ReminderOccurrence, ReminderType, Task
from .reminder_time import is_date_only


def obsidian_url(vault_name: str, task_path: str) -> str:
    path = PurePosixPath(task_path)
    if path.suffix.lower() == ".md":
        path = path.with_suffix("")
    query = urlencode(
        {"vault": vault_name, "file": path.as_posix()},
        quote_via=quote,
        safe="",
    )
    return f"obsidian://open?{query}"


def truncate_utf8(body: str, max_bytes: int) -> str:
    cleaned = body.strip()
    if not cleaned:
        return ""
    encoded = cleaned.encode("utf-8")
    if len(encoded) <= max_bytes:
        return cleaned
    ellipsis = "…".encode()
    prefix = encoded[: max_bytes - len(ellipsis)]
    return prefix.decode("utf-8", errors="ignore") + "…"


def humanize_duration(duration: timedelta) -> str:
    seconds = abs(int(duration.total_seconds()))
    units = ((604800, "week"), (86400, "day"), (3600, "hour"), (60, "minute"))
    for unit_seconds, label in units:
        if seconds >= unit_seconds and seconds % unit_seconds == 0:
            amount = seconds // unit_seconds
            return f"{amount} {label}{'' if amount == 1 else 's'}"
    return f"{seconds} second{'' if seconds == 1 else 's'}"


def notification_title(task: Task, reminder: Reminder) -> str:
    if reminder.type is ReminderType.ABSOLUTE:
        return f"{task.title} reminder"
    assert reminder.offset is not None
    assert reminder.related_to is not None
    anchor = task.due if reminder.related_to == "due" else task.scheduled
    context = "due" if reminder.related_to == "due" else "scheduled"
    if reminder.offset == timedelta(0):
        when = "today" if anchor is not None and is_date_only(anchor) else "now"
        return f"{task.title} is {context} {when}"
    humanized = humanize_duration(reminder.offset)
    if reminder.offset < timedelta(0):
        return f"{task.title} is {context} in {humanized}"
    return f"{task.title} was {context} {humanized} ago"


def make_occurrence(
    task: Task,
    reminder: Reminder,
    effective_at_utc: datetime,
    *,
    vault_identity: str,
    body_max_bytes: int,
    priority_map: dict[str, int],
) -> ReminderOccurrence:
    identity = "\0".join(
        (vault_identity, task.path, reminder.id, effective_at_utc.isoformat())
    ).encode()
    occurrence_id = hashlib.sha256(identity).hexdigest()
    title = notification_title(task, reminder)
    message = truncate_utf8(task.body, body_max_bytes)
    click_url = obsidian_url(vault_identity, task.path)
    priority = priority_map.get(task.priority, 3)
    payload = json.dumps(
        {
            "title": title,
            "message": message,
            "click": click_url,
            "priority": priority,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return ReminderOccurrence(
        occurrence_id=occurrence_id,
        task_path=task.path,
        reminder_id=reminder.id,
        effective_at_utc=effective_at_utc,
        payload_hash=hashlib.sha256(payload).hexdigest(),
        title=title,
        message=message,
        click_url=click_url,
        ntfy_priority=priority,
        ntfy_message_id=f"tn-{occurrence_id[:32]}",
    )
