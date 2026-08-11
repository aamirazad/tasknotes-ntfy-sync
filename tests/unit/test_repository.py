from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tasknotes_ntfy.domain import ReminderOccurrence, Task
from tasknotes_ntfy.repository import Repository

NOW = datetime(2026, 8, 11, 12, tzinfo=UTC)


def task(*, status: str = "To-do", archived: bool = False, body_hash: str = "h") -> Task:
    return Task("Tasks/One.md", "One", status, "High", None, None, archived, "", (), body_hash)


def occurrence(identifier: str = "one", effective: datetime | None = None) -> ReminderOccurrence:
    return ReminderOccurrence(
        identifier,
        "Tasks/One.md",
        "r1",
        effective or NOW + timedelta(hours=1),
        f"payload-{identifier}",
        "Title",
        "Body",
        "obsidian://open",
        4,
        f"tn-{identifier}",
    )


@pytest.fixture
def repository(tmp_path: Path):
    value = Repository(tmp_path / "db.sqlite3")
    yield value
    value.close()


def reconcile(
    repository: Repository,
    value: Task,
    desired: list[ReminderOccurrence],
    *,
    active: bool = True,
) -> None:
    repository.reconcile_task(
        value,
        desired,
        active=active,
        scan_id=None,
        now=NOW,
        grace=timedelta(minutes=15),
    )


def test_create_is_idempotent_and_pending_payload_updates(repository: Repository) -> None:
    first = occurrence()
    reconcile(repository, task(), [first])
    reconcile(repository, task(body_hash="changed"), [first])
    rows = repository.list_occurrences()
    assert len(rows) == 1
    assert rows[0]["state"] == "scheduled"

    edited = replace(first, payload_hash="new", message="Edited")
    reconcile(repository, task(), [edited])
    assert repository.list_occurrences()[0]["notification_message"] == "Edited"


def test_reschedule_cancels_old_and_schedules_new(repository: Repository) -> None:
    reconcile(repository, task(), [occurrence("old")])
    reconcile(repository, task(), [occurrence("new", NOW + timedelta(hours=2))])
    states = {row["occurrence_id"]: row["state"] for row in repository.list_occurrences()}
    assert states == {"old": "canceled", "new": "scheduled"}


def test_complete_archive_and_reopen(repository: Repository) -> None:
    item = occurrence()
    reconcile(repository, task(), [item])
    reconcile(repository, task(status="Done"), [item], active=False)
    assert repository.list_occurrences()[0]["state"] == "canceled"
    reconcile(repository, task(), [item])
    assert repository.list_occurrences()[0]["state"] == "scheduled"
    reconcile(repository, task(archived=True), [item], active=False)
    assert repository.list_occurrences()[0]["state"] == "canceled"


def test_outside_grace_expires_but_sent_history_is_preserved(repository: Repository) -> None:
    stale = occurrence(effective=NOW - timedelta(minutes=16))
    reconcile(repository, task(), [stale])
    assert repository.list_occurrences()[0]["state"] == "expired"

    repository.connection.execute(
        "UPDATE reminder_occurrences SET state='sent' WHERE occurrence_id=?", (stale.occurrence_id,)
    )
    reconcile(repository, task(body_hash="edited"), [stale])
    assert repository.list_occurrences()[0]["state"] == "sent"


def test_successful_scan_cancels_unseen_but_failed_scan_does_not(repository: Repository) -> None:
    scan_one = repository.start_scan(NOW)
    repository.reconcile_task(
        task(),
        [occurrence()],
        active=True,
        scan_id=scan_one,
        now=NOW,
        grace=timedelta(minutes=15),
    )
    repository.complete_scan(scan_one, NOW, file_count=1, valid_task_count=1, error_count=0)

    failed = repository.start_scan(NOW)
    repository.fail_scan(
        failed,
        NOW,
        file_count=3,
        valid_task_count=0,
        error_count=3,
        error="unstable volume",
    )
    assert repository.list_occurrences()[0]["state"] == "scheduled"

    empty = repository.start_scan(NOW)
    repository.complete_scan(empty, NOW, file_count=0, valid_task_count=0, error_count=0)
    assert repository.list_occurrences()[0]["state"] == "canceled"


def test_rejects_database_from_newer_application(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "db.sqlite3")
    repository.connection.execute("UPDATE schema_version SET version=999")
    repository.close()
    with pytest.raises(RuntimeError, match="newer"):
        Repository(tmp_path / "db.sqlite3")
