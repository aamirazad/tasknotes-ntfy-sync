from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tasknotes_ntfy.config import Settings
from tasknotes_ntfy.domain import ClaimedOccurrence, ReminderOccurrence, Task
from tasknotes_ntfy.ntfy import PermanentDeliveryError, TransientDeliveryError
from tasknotes_ntfy.repository import Repository
from tasknotes_ntfy.scheduler import Scheduler

NOW = datetime(2026, 8, 11, 12, tzinfo=UTC)


def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        data_root=tmp_path,
        vault_root=tmp_path / "vault",
        database_path=tmp_path / "notifier" / "db.sqlite3",
        health_path=tmp_path / "notifier" / "health.json",
        obsidian_remote_vault="Remote",
        obsidian_deep_link_vault="Phone",
        obsidian_auth_token="token",
        ntfy_topic="topic",
    )


def seed(repository: Repository, effective: datetime = NOW) -> None:
    task = Task("Task.md", "Task", "To-do", "None", None, None, False, "", (), "hash")
    occurrence = ReminderOccurrence(
        "occurrence",
        task.path,
        "reminder",
        effective,
        "payload",
        "Title",
        "Message",
        "obsidian://open",
        3,
        "tn-stable",
    )
    repository.reconcile_task(
        task,
        [occurrence],
        active=True,
        scan_id=None,
        now=NOW - timedelta(minutes=1),
        grace=timedelta(minutes=15),
    )


class FakePublisher:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[ClaimedOccurrence] = []

    async def publish(self, occurrence: ClaimedOccurrence) -> None:
        self.calls.append(occurrence)
        if self.error:
            raise self.error


@pytest.fixture
def repository(tmp_path: Path):
    value = Repository(tmp_path / "notifier" / "db.sqlite3")
    seed(value)
    yield value
    value.close()


@pytest.mark.asyncio
async def test_success_claims_once_and_marks_sent(tmp_path: Path, repository: Repository) -> None:
    publisher = FakePublisher()
    scheduler = Scheduler(settings(tmp_path), repository, publisher, clock=lambda: NOW)
    assert await scheduler.run_once() == 1
    assert await scheduler.run_once() == 0
    row = repository.list_occurrences()[0]
    assert row["state"] == "sent"
    assert row["attempt_count"] == 1


@pytest.mark.asyncio
async def test_transient_failure_schedules_retry(tmp_path: Path, repository: Repository) -> None:
    publisher = FakePublisher(TransientDeliveryError("HTTP 429", 17))
    scheduler = Scheduler(settings(tmp_path), repository, publisher, clock=lambda: NOW)
    await scheduler.run_once()
    row = repository.list_occurrences()[0]
    assert row["state"] == "retry"
    assert row["next_attempt_at_utc"] == (NOW + timedelta(seconds=17)).isoformat()


@pytest.mark.asyncio
async def test_permanent_failure_is_terminal(tmp_path: Path, repository: Repository) -> None:
    publisher = FakePublisher(PermanentDeliveryError("HTTP 401"))
    scheduler = Scheduler(settings(tmp_path), repository, publisher, clock=lambda: NOW)
    await scheduler.run_once()
    row = repository.list_occurrences()[0]
    assert row["state"] == "failed"
    assert row["last_error"] == "HTTP 401"


def test_stale_claim_recovery_reuses_stable_sequence(repository: Repository) -> None:
    first = repository.claim_due(
        NOW,
        grace=timedelta(minutes=15),
        lease=timedelta(minutes=2),
    )
    assert len(first) == 1
    # Simulate ntfy accepting the request followed by a process crash before mark_sent.
    second = repository.claim_due(
        NOW + timedelta(minutes=3),
        grace=timedelta(minutes=15),
        lease=timedelta(minutes=2),
    )
    assert len(second) == 1
    assert second[0].ntfy_message_id == first[0].ntfy_message_id
    assert second[0].attempt_count == 2


def test_old_scheduled_occurrence_expires(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "old.sqlite3")
    seed(repository, NOW - timedelta(minutes=16))
    claimed = repository.claim_due(
        NOW,
        grace=timedelta(minutes=15),
        lease=timedelta(minutes=2),
    )
    assert claimed == []
    assert repository.list_occurrences()[0]["state"] == "expired"
    repository.close()
