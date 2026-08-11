from datetime import UTC, datetime, timedelta
from pathlib import Path

from tasknotes_ntfy.config import Settings
from tasknotes_ntfy.healthcheck import HealthReporter, check_health
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
        obsidian_deep_link_vault="Phone",
        obsidian_auth_token="token",
        ntfy_topic="topic",
        tasks_path="Tasks",
    )


def test_health_requires_recent_loops_scan_directory_and_database(tmp_path: Path) -> None:
    configured = settings(tmp_path)
    configured.task_directory.mkdir(parents=True)
    repository = Repository(configured.database_path)
    reporter = HealthReporter(configured.health_path)
    reporter.heartbeat(NOW)
    reporter.scheduler_heartbeat(NOW)
    reporter.scan_completed(NOW)
    assert check_health(configured, NOW)["healthy"] is True
    repository.close()

    result = check_health(configured, NOW + timedelta(minutes=4))
    assert result["healthy"] is False
    assert "main heartbeat is stale" in result["reasons"]


def test_missing_state_is_unhealthy(tmp_path: Path) -> None:
    configured = settings(tmp_path)
    result = check_health(configured, NOW)
    assert result["healthy"] is False
    assert "health state is missing or invalid" in result["reasons"]
