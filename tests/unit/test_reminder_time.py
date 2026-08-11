from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from tasknotes_ntfy.domain import Reminder, ReminderType, Task
from tasknotes_ntfy.reminder_time import ReminderResolutionError, parse_anchor, resolve_reminder

NY = ZoneInfo("America/New_York")


def task(*, due=None, scheduled=None) -> Task:
    return Task("Task.md", "Task", "To-do", "None", due, scheduled, False, "", (), "hash")


def relative(related_to: str, offset: timedelta) -> Reminder:
    return Reminder("r1", ReminderType.RELATIVE, related_to, "duration", offset)


@pytest.mark.parametrize(
    ("anchor", "related_to", "offset", "expected"),
    [
        (date(2026, 8, 12), "due", timedelta(), datetime(2026, 8, 12, 11, tzinfo=UTC)),
        ("2026-08-14", "scheduled", timedelta(), datetime(2026, 8, 14, 11, tzinfo=UTC)),
        ("2026-08-13", "due", timedelta(minutes=-15), datetime(2026, 8, 13, 10, 45, tzinfo=UTC)),
        ("2026-08-11T19:27", "due", timedelta(), datetime(2026, 8, 11, 23, 27, tzinfo=UTC)),
        ("2026-08-11T19:27-05:00", "due", timedelta(), datetime(2026, 8, 12, 0, 27, tzinfo=UTC)),
        ("2026-08-11T19:27", "due", timedelta(hours=2), datetime(2026, 8, 12, 1, 27, tzinfo=UTC)),
    ],
)
def test_relative_resolution(anchor, related_to, offset, expected) -> None:
    value = task(due=anchor) if related_to == "due" else task(scheduled=anchor)
    assert resolve_reminder(value, relative(related_to, offset), NY, time(7)) == expected


def test_absolute_naive_and_offset() -> None:
    naive = Reminder("a", ReminderType.ABSOLUTE, absolute_time="2026-08-11T19:27")
    aware = Reminder("b", ReminderType.ABSOLUTE, absolute_time="2026-08-11T19:27+02:00")
    assert resolve_reminder(task(), naive, NY, time(7)) == datetime(2026, 8, 11, 23, 27, tzinfo=UTC)
    assert resolve_reminder(task(), aware, NY, time(7)) == datetime(2026, 8, 11, 17, 27, tzinfo=UTC)


def test_missing_anchor_is_invalid() -> None:
    with pytest.raises(ReminderResolutionError, match="missing due"):
        resolve_reminder(task(), relative("due", timedelta()), NY, time(7))


def test_dst_spring_nonexistent_time_is_rejected() -> None:
    with pytest.raises(ReminderResolutionError, match="does not exist"):
        parse_anchor("2026-03-08T02:30", NY, time(7))


def test_dst_fall_ambiguous_time_chooses_first_fold() -> None:
    resolved = parse_anchor("2026-11-01T01:30", NY, time(7))
    assert resolved.fold == 0
    assert resolved.astimezone(UTC) == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
