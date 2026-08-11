import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from tasknotes_ntfy.config import Settings
from tasknotes_ntfy.ntfy import NtfyPublisher
from tasknotes_ntfy.reconcile import Reconciler
from tasknotes_ntfy.repository import Repository
from tasknotes_ntfy.scheduler import Scheduler


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
        ntfy_topic="secret-topic",
        ntfy_base_url="https://ntfy.example",
    )


@pytest.mark.asyncio
async def test_vault_to_mock_ntfy_is_once_across_restart(tmp_path: Path) -> None:
    configured = settings(tmp_path)
    configured.task_directory.mkdir(parents=True)
    (configured.task_directory / "Notify.md").write_text(
        """---
base: "[[Tasks.base]]"
status: To-do
priority: Medium
due: 2026-08-11T08:00
reminders:
  - id: reminder
    type: relative
    relatedTo: due
    offset: PT0M
---
Integration body
""",
        encoding="utf-8",
    )
    now = datetime(2026, 8, 11, 12, tzinfo=UTC)
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.read()))
        return httpx.Response(200, json={"id": "server-id"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    repository = Repository(configured.database_path)
    Reconciler(configured, repository, clock=lambda: now).full_scan()
    publisher = NtfyPublisher(configured, client)
    scheduler = Scheduler(configured, repository, publisher, clock=lambda: now)
    assert await scheduler.run_once() == 1
    repository.close()

    restarted = Repository(configured.database_path)
    restarted_scheduler = Scheduler(
        configured, restarted, publisher, clock=lambda: now + timedelta(seconds=5)
    )
    assert await restarted_scheduler.run_once() == 0
    assert len(requests) == 1
    assert str(requests[0]["sequence_id"]).startswith("tn-")
    assert requests[0]["message"] == "Integration body"
    restarted.close()
    await client.aclose()
