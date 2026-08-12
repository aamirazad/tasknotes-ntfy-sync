from datetime import UTC, date, datetime, timedelta

from tasknotes_ntfy.domain import Reminder, ReminderType, Task
from tasknotes_ntfy.notification import (
    make_occurrence,
    notification_title,
    obsidian_url,
    truncate_utf8,
)


def task(**changes) -> Task:
    values = {
        "path": "Efforts/Tasks/Café #1?.md",
        "title": "Write report",
        "status": "To-do",
        "priority": "High",
        "due": date(2026, 8, 12),
        "scheduled": None,
        "archived": False,
        "body": "Body",
        "reminders": (),
        "source_hash": "hash",
    }
    values.update(changes)
    return Task(**values)


def reminder(related: str, offset: timedelta) -> Reminder:
    return Reminder("r", ReminderType.RELATIVE, related, "offset", offset)


def test_contextual_titles() -> None:
    assert notification_title(task(), reminder("due", timedelta())) == "Write report is due today"
    assert (
        notification_title(task(due="2026-08-12T09:00"), reminder("due", timedelta()))
        == "Write report is due now"
    )
    assert (
        notification_title(task(scheduled=date(2026, 8, 12)), reminder("scheduled", timedelta()))
        == "Write report is scheduled today"
    )
    assert (
        notification_title(task(), reminder("due", timedelta(minutes=-15)))
        == "Write report is due in 15 minutes"
    )
    assert (
        notification_title(task(), reminder("due", timedelta(hours=2)))
        == "Write report was due 2 hours ago"
    )
    absolute = Reminder("a", ReminderType.ABSOLUTE, absolute_time="2026-08-12T10:00")
    assert notification_title(task(), absolute) == "Write report reminder"


def test_utf8_byte_safe_truncation_and_empty_body() -> None:
    value = truncate_utf8("é" * 100, 10)
    assert value == "ééé…"
    assert len(value.encode()) <= 10
    assert truncate_utf8(" \n ", 100) == ""


def test_obsidian_url_encoding() -> None:
    assert obsidian_url("NTFY Test", "Efforts/Tasks/Café #1?.md") == (
        "obsidian://open?vault=NTFY%20Test&file=Efforts%2FTasks%2FCaf%C3%A9%20%231%3F"
    )


def test_occurrence_is_stable_and_payload_changes_are_distinct() -> None:
    effective = datetime(2026, 8, 12, 11, tzinfo=UTC)
    first = make_occurrence(
        task(),
        reminder("due", timedelta()),
        effective,
        vault_identity="NTFY Test",
        body_max_bytes=1000,
        priority_map={"High": 4},
    )
    edited = make_occurrence(
        task(body="Edited"),
        reminder("due", timedelta()),
        effective,
        vault_identity="NTFY Test",
        body_max_bytes=1000,
        priority_map={"High": 4},
    )
    assert first.occurrence_id == edited.occurrence_id
    assert first.payload_hash != edited.payload_hash
    assert first.ntfy_priority == 4
    assert first.ntfy_message_id.startswith("tn-")
