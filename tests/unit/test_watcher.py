from pathlib import Path

import pytest
from watchfiles import Change

from tasknotes_ntfy.config import Settings
from tasknotes_ntfy.frontmatter import TaskParseError
from tasknotes_ntfy.watcher import Watcher


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
        tasks_path="Tasks",
    )


class FakeReconciler:
    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.paths: list[Path] = []
        self.scans = 0

    def reconcile_path(self, path: Path) -> None:
        self.paths.append(path)
        if len(self.paths) <= self.failures:
            raise TaskParseError("partial file")

    def full_scan(self) -> int:
        self.scans += 1
        return self.scans


@pytest.mark.asyncio
async def test_collapses_modify_events_and_ignores_non_markdown(tmp_path: Path) -> None:
    configured = settings(tmp_path)
    configured.task_directory.mkdir(parents=True)
    path = configured.task_directory / "One.md"
    path.write_text("content", encoding="utf-8")
    reconciler = FakeReconciler()
    watcher = Watcher(configured, reconciler)  # type: ignore[arg-type]
    await watcher.handle_changes(
        {
            (Change.modified, str(path)),
            (Change.added, str(path)),
            (Change.added, str(path.with_suffix(".txt"))),
        }
    )
    assert reconciler.paths == [path]
    assert reconciler.scans == 0


@pytest.mark.asyncio
async def test_delete_triggers_authoritative_full_scan(tmp_path: Path) -> None:
    configured = settings(tmp_path)
    reconciler = FakeReconciler()
    watcher = Watcher(configured, reconciler)  # type: ignore[arg-type]
    await watcher.handle_changes({(Change.deleted, str(configured.task_directory / "Gone.md"))})
    assert reconciler.scans == 1


@pytest.mark.asyncio
async def test_partial_file_is_retried(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    configured = settings(tmp_path)
    configured.task_directory.mkdir(parents=True)
    path = configured.task_directory / "Partial.md"
    path.write_text("content", encoding="utf-8")
    reconciler = FakeReconciler(failures=2)

    async def no_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr("tasknotes_ntfy.watcher.asyncio.sleep", no_sleep)
    watcher = Watcher(configured, reconciler)  # type: ignore[arg-type]
    await watcher.handle_changes({(Change.modified, str(path))})
    assert reconciler.paths == [path, path, path]
