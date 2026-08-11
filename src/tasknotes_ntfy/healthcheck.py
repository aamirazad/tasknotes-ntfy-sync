"""Health state writer and read-only Docker health check."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .config import Settings


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


class HealthReporter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.state: dict[str, Any] = {
            "status": "starting",
            "main_heartbeat_at_utc": None,
            "scheduler_heartbeat_at_utc": None,
            "last_scan_at_utc": None,
        }

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.state, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)

    def starting(self, now: datetime, reason: str) -> None:
        self.state.update(
            status="starting",
            reason=reason,
            main_heartbeat_at_utc=now.astimezone(UTC).isoformat(),
        )
        self._write()

    def heartbeat(self, now: datetime) -> None:
        self.state.update(
            status="running",
            reason=None,
            main_heartbeat_at_utc=now.astimezone(UTC).isoformat(),
        )
        self._write()

    def scheduler_heartbeat(self, now: datetime) -> None:
        self.state["scheduler_heartbeat_at_utc"] = now.astimezone(UTC).isoformat()
        self._write()

    def scan_completed(self, now: datetime) -> None:
        self.state["last_scan_at_utc"] = now.astimezone(UTC).isoformat()
        self._write()


def check_health(settings: Settings, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    reasons: list[str] = []
    try:
        state = json.loads(settings.health_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {}
        reasons.append("health state is missing or invalid")

    stale = timedelta(seconds=settings.health_stale_seconds)
    main_heartbeat = _parse_time(state.get("main_heartbeat_at_utc"))
    scheduler_heartbeat = _parse_time(state.get("scheduler_heartbeat_at_utc"))
    last_scan = _parse_time(state.get("last_scan_at_utc"))
    if main_heartbeat is None or now - main_heartbeat > stale:
        reasons.append("main heartbeat is stale")
    if scheduler_heartbeat is None or now - scheduler_heartbeat > stale:
        reasons.append("scheduler heartbeat is stale")
    scan_stale = timedelta(
        seconds=max(settings.health_stale_seconds, settings.reconcile_interval_seconds * 3)
    )
    if last_scan is None or now - last_scan > scan_stale:
        reasons.append("full reconciliation is stale")
    if not settings.task_directory.is_dir() or not os.access(settings.task_directory, os.R_OK):
        reasons.append("task directory is not readable")
    try:
        uri = f"file:{settings.database_path}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            connection.execute("SELECT version FROM schema_version").fetchone()
    except sqlite3.Error:
        reasons.append("SQLite database is not queryable")
    if settings.sync_health_path.exists():
        try:
            sync_state = json.loads(settings.sync_health_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            reasons.append("sync health state is invalid")
        else:
            if not sync_state.get("healthy"):
                reasons.append("Obsidian Sync is not ready")
    return {
        "healthy": not reasons,
        "status": "healthy" if not reasons else "unhealthy",
        "reasons": reasons,
        "state": state,
    }


def main() -> int:
    try:
        settings = Settings()  # type: ignore[call-arg]
        result = check_health(settings)
    except Exception as exc:
        result = {"healthy": False, "status": "unhealthy", "reasons": [str(exc)]}
    print(json.dumps(result, separators=(",", ":"), default=str))
    return 0 if result["healthy"] else 1


if __name__ == "__main__":
    sys.exit(main())
