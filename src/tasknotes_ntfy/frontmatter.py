"""TaskNotes Markdown and YAML frontmatter parsing."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from pathlib import Path

import isodate
import yaml

from .domain import InvalidReminder, Reminder, ReminderType, Task, YamlTime


class TaskParseError(ValueError):
    """The file cannot be safely parsed as a TaskNotes task."""


class NotTaskError(TaskParseError):
    """The Markdown file does not match the configured task identity."""


def _split_frontmatter(text: str) -> tuple[str, str]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise NotTaskError("missing YAML frontmatter")
    for index, line in enumerate(lines[1:], start=1):
        if line.rstrip("\r\n") == "---":
            return "".join(lines[1:index]), "".join(lines[index + 1 :]).strip("\r\n")
    raise TaskParseError("incomplete YAML frontmatter")


def _yaml_time(value: object, field_name: str) -> YamlTime | None:
    if value is None:
        return None
    if isinstance(value, str | date | datetime):
        return value
    raise TaskParseError(f"{field_name} must be a date or date-time")


def _bool_value(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1"}
    return bool(value) if value is not None else False


def _parse_reminder(value: object, index: int) -> Reminder | InvalidReminder:
    if not isinstance(value, Mapping):
        return InvalidReminder(id=f"index-{index}", reason="reminder must be a mapping")

    reminder_id = str(value.get("id") or f"index-{index}")
    reminder_type_raw = value.get("type")
    try:
        reminder_type = ReminderType(str(reminder_type_raw))
    except ValueError:
        return InvalidReminder(id=reminder_id, reason="type must be relative or absolute")

    description = value.get("description")
    description_text = str(description) if description is not None else None
    if reminder_type is ReminderType.RELATIVE:
        related_to = value.get("relatedTo")
        if related_to not in {"due", "scheduled"}:
            return InvalidReminder(id=reminder_id, reason="relatedTo must be due or scheduled")
        offset_raw = value.get("offset")
        if not isinstance(offset_raw, str):
            return InvalidReminder(id=reminder_id, reason="relative reminder requires offset")
        try:
            parsed_offset = isodate.parse_duration(offset_raw)
        except (ValueError, TypeError, isodate.ISO8601Error):
            return InvalidReminder(id=reminder_id, reason="offset is not an ISO-8601 duration")
        if not isinstance(parsed_offset, timedelta):
            return InvalidReminder(
                id=reminder_id, reason="calendar month/year offsets are unsupported"
            )
        return Reminder(
            id=reminder_id,
            type=reminder_type,
            related_to=str(related_to),
            offset_raw=offset_raw,
            offset=parsed_offset,
            description=description_text,
        )

    absolute_time = value.get("absoluteTime")
    if absolute_time is None:
        return InvalidReminder(id=reminder_id, reason="absolute reminder requires absoluteTime")
    if not isinstance(absolute_time, str | date | datetime):
        return InvalidReminder(id=reminder_id, reason="absoluteTime must be a date-time")
    return Reminder(
        id=reminder_id,
        type=reminder_type,
        absolute_time=absolute_time,
        description=description_text,
    )


def parse_task_text(
    text: str,
    relative_path: str,
    *,
    property_name: str,
    property_value: str,
) -> Task:
    """Parse one already-size-checked Markdown file."""

    frontmatter_text, body = _split_frontmatter(text)
    try:
        loaded = yaml.safe_load(frontmatter_text)
    except yaml.YAMLError as exc:
        raise TaskParseError("invalid YAML frontmatter") from exc
    if not isinstance(loaded, Mapping):
        raise TaskParseError("frontmatter must be a mapping")
    if loaded.get(property_name) != property_value:
        raise NotTaskError("task identification property does not match")

    parsed: list[Reminder] = []
    invalid: list[InvalidReminder] = []
    reminder_values = loaded.get("reminders") or []
    if not isinstance(reminder_values, list):
        invalid.append(InvalidReminder(id="reminders", reason="reminders must be a list"))
    else:
        for index, raw_reminder in enumerate(reminder_values):
            reminder = _parse_reminder(raw_reminder, index)
            if isinstance(reminder, InvalidReminder):
                invalid.append(reminder)
            else:
                parsed.append(reminder)

    title_value = loaded.get("title")
    title = str(title_value).strip() if title_value is not None else Path(relative_path).stem
    if not title:
        title = Path(relative_path).stem
    return Task(
        path=relative_path,
        title=title,
        status=str(loaded.get("status") or ""),
        priority=str(loaded.get("priority") or "None"),
        due=_yaml_time(loaded.get("due"), "due"),
        scheduled=_yaml_time(loaded.get("scheduled"), "scheduled"),
        archived=_bool_value(loaded.get("archived")),
        body=body,
        reminders=tuple(parsed),
        invalid_reminders=tuple(invalid),
        source_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def parse_task_file(
    path: Path,
    relative_path: str,
    *,
    property_name: str,
    property_value: str,
    max_file_bytes: int,
) -> Task:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise TaskParseError("file metadata is unavailable") from exc
    if size > max_file_bytes:
        raise TaskParseError(f"file exceeds {max_file_bytes} bytes")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise TaskParseError("file is not readable UTF-8") from exc
    return parse_task_text(
        text,
        relative_path,
        property_name=property_name,
        property_value=property_value,
    )
