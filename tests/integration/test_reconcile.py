from datetime import UTC, datetime
from pathlib import Path

import pytest

from tasknotes_ntfy.config import Settings
from tasknotes_ntfy.reconcile import Reconciler, ScanError
from tasknotes_ntfy.repository import Repository

NOW = datetime(2026, 8, 11, 12, tzinfo=UTC)


def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        data_root=tmp_path,
        vault_root=tmp_path / "vault",
        database_path=tmp_path / "notifier" / "db.sqlite3",
        health_path=tmp_path / "notifier" / "health.json",
        obsidian_remote_vault="Remote",
        obsidian_deep_link_vault="Phone Vault",
        obsidian_auth_token="token",
        ntfy_topic="topic",
        tasks_path="Tasks",
    )


def markdown(*, due: str = "2026-08-12", status: str = "To-do", body: str = "Body") -> str:
    return f"""---
base: "[[Tasks.base]]"
status: {status}
priority: High
due: {due}
reminders:
  - id: r1
    type: relative
    relatedTo: due
    offset: PT0M
---
{body}
"""


@pytest.fixture
def service(tmp_path: Path):
    configured = settings(tmp_path)
    configured.task_directory.mkdir(parents=True)
    repository = Repository(configured.database_path)
    reconciler = Reconciler(configured, repository, clock=lambda: NOW)
    yield configured, repository, reconciler
    repository.close()


def test_full_scan_edit_reschedule_complete_delete(service) -> None:
    configured, repository, reconciler = service
    path = configured.task_directory / "One.md"
    path.write_text(markdown(), encoding="utf-8")
    reconciler.full_scan()
    assert [row["state"] for row in repository.list_occurrences()] == ["scheduled"]

    path.write_text(markdown(body="Edited body"), encoding="utf-8")
    reconciler.full_scan()
    rows = repository.list_occurrences()
    assert len(rows) == 1
    assert rows[0]["notification_message"] == "Edited body"

    path.write_text(markdown(due="2026-08-13"), encoding="utf-8")
    reconciler.full_scan()
    assert sorted(row["state"] for row in repository.list_occurrences()) == [
        "canceled",
        "scheduled",
    ]

    path.write_text(markdown(due="2026-08-13", status="Done"), encoding="utf-8")
    reconciler.full_scan()
    assert all(row["state"] == "canceled" for row in repository.list_occurrences())

    path.write_text(markdown(due="2026-08-13"), encoding="utf-8")
    reconciler.full_scan()
    assert any(row["state"] == "scheduled" for row in repository.list_occurrences())

    path.unlink()
    reconciler.full_scan()
    assert all(row["state"] == "canceled" for row in repository.list_occurrences())


def test_failed_partial_scan_does_not_cancel_unseen_task(service) -> None:
    configured, repository, reconciler = service
    good = configured.task_directory / "Good.md"
    good.write_text(markdown(), encoding="utf-8")
    reconciler.full_scan()
    good.unlink()
    for number in range(3):
        (configured.task_directory / f"Broken{number}.md").write_text(
            "---\nbase: x", encoding="utf-8"
        )
    with pytest.raises(ScanError):
        reconciler.full_scan()
    assert repository.list_occurrences()[0]["state"] == "scheduled"


def test_recurring_date_advance_reuses_reminder_id_without_duplicate(service) -> None:
    configured, repository, reconciler = service
    path = configured.task_directory / "Recurring.md"
    path.write_text(markdown(due="2026-08-12"), encoding="utf-8")
    reconciler.full_scan()
    first = repository.list_occurrences()[0]
    repository.connection.execute(
        "UPDATE reminder_occurrences SET state='sent' WHERE occurrence_id=?",
        (first["occurrence_id"],),
    )
    path.write_text(markdown(due="2026-08-19"), encoding="utf-8")
    reconciler.full_scan()
    rows = repository.list_occurrences()
    assert [row["state"] for row in rows] == ["sent", "scheduled"]
    assert rows[0]["reminder_id"] == rows[1]["reminder_id"] == "r1"
