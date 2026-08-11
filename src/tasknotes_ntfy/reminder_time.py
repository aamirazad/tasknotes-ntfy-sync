"""Resolve TaskNotes anchor values and reminders into aware UTC instants."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from .domain import Reminder, ReminderType, Task, YamlTime


class ReminderResolutionError(ValueError):
    pass


def is_date_only(value: YamlTime) -> bool:
    if isinstance(value, datetime):
        return False
    if isinstance(value, date):
        return True
    try:
        date.fromisoformat(value)
        return "T" not in value and " " not in value
    except ValueError:
        return False


def _localize(naive: datetime, timezone: ZoneInfo) -> datetime:
    """Choose fold 0 for ambiguous times and reject nonexistent wall times."""

    localized = naive.replace(tzinfo=timezone, fold=0)
    round_trip = localized.astimezone(UTC).astimezone(timezone).replace(tzinfo=None)
    if round_trip != naive:
        raise ReminderResolutionError("local date-time does not exist because of a DST transition")
    return localized


def parse_anchor(value: YamlTime, timezone: ZoneInfo, date_only_time: time) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, date_only_time)
    elif is_date_only(value):
        parsed = datetime.combine(date.fromisoformat(value), date_only_time)
    else:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise ReminderResolutionError("invalid ISO date-time") from exc
    if parsed.tzinfo is None:
        parsed = _localize(parsed, timezone)
    return parsed


def resolve_reminder(
    task: Task,
    reminder: Reminder,
    timezone: ZoneInfo,
    date_only_time: time,
) -> datetime:
    if reminder.type is ReminderType.ABSOLUTE:
        if reminder.absolute_time is None or is_date_only(reminder.absolute_time):
            raise ReminderResolutionError("absolute reminder requires a date-time")
        return parse_anchor(reminder.absolute_time, timezone, date_only_time).astimezone(UTC)

    if reminder.related_to not in {"due", "scheduled"} or reminder.offset is None:
        raise ReminderResolutionError("invalid relative reminder")
    anchor = task.due if reminder.related_to == "due" else task.scheduled
    if anchor is None:
        raise ReminderResolutionError(f"missing {reminder.related_to} anchor")
    return (parse_anchor(anchor, timezone, date_only_time) + reminder.offset).astimezone(UTC)
