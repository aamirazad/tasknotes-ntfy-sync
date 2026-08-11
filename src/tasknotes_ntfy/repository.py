"""SQLite persistence and reconciliation state transitions."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from .domain import OccurrenceState, ReminderOccurrence, Task

SCHEMA_VERSION = 1

MIGRATION_1 = """
CREATE TABLE schema_version (
    version INTEGER NOT NULL
);
INSERT INTO schema_version(version) VALUES (1);

CREATE TABLE tasks (
    path TEXT PRIMARY KEY,
    source_hash TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    priority TEXT NOT NULL,
    archived INTEGER NOT NULL CHECK (archived IN (0, 1)),
    last_seen_scan_id INTEGER,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE reminder_occurrences (
    occurrence_id TEXT PRIMARY KEY,
    task_path TEXT NOT NULL REFERENCES tasks(path),
    reminder_id TEXT NOT NULL,
    effective_at_utc TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    notification_title TEXT NOT NULL,
    notification_message TEXT NOT NULL,
    click_url TEXT NOT NULL,
    ntfy_priority INTEGER NOT NULL CHECK (ntfy_priority BETWEEN 1 AND 5),
    ntfy_message_id TEXT NOT NULL,
    state TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at_utc TEXT,
    claimed_at_utc TEXT,
    sent_at_utc TEXT,
    last_error TEXT,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);
CREATE INDEX occurrence_due_idx
    ON reminder_occurrences(state, effective_at_utc);
CREATE INDEX occurrence_retry_idx
    ON reminder_occurrences(state, next_attempt_at_utc);
CREATE INDEX occurrence_task_reminder_idx
    ON reminder_occurrences(task_path, reminder_id);

CREATE TABLE scan_runs (
    scan_id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at_utc TEXT NOT NULL,
    completed_at_utc TEXT,
    file_count INTEGER NOT NULL DEFAULT 0,
    valid_task_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    error TEXT
);
"""


def utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("UTC timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat()


class Repository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(database_path, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=5000")
        self._migrate()

    def close(self) -> None:
        self.connection.close()

    def _migrate(self) -> None:
        exists = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'"
        ).fetchone()
        if not exists:
            try:
                self.connection.executescript(f"BEGIN IMMEDIATE;\n{MIGRATION_1}\nCOMMIT;")
            except BaseException:
                if self.connection.in_transaction:
                    self.connection.execute("ROLLBACK")
                raise
            return
        version = int(self.connection.execute("SELECT version FROM schema_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"database schema {version} is newer than supported schema {SCHEMA_VERSION}"
            )
        if version < SCHEMA_VERSION:
            raise RuntimeError(f"no migration from schema {version} to {SCHEMA_VERSION}")

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        else:
            self.connection.execute("COMMIT")

    def start_scan(self, now: datetime) -> int:
        cursor = self.connection.execute(
            "INSERT INTO scan_runs(started_at_utc, status) VALUES (?, 'running')",
            (utc_text(now),),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("SQLite did not allocate a scan ID")
        return cursor.lastrowid

    def fail_scan(
        self,
        scan_id: int,
        now: datetime,
        *,
        file_count: int,
        valid_task_count: int,
        error_count: int,
        error: str,
    ) -> None:
        self.connection.execute(
            """
            UPDATE scan_runs SET completed_at_utc=?, file_count=?, valid_task_count=?,
                error_count=?, status='failed', error=? WHERE scan_id=?
            """,
            (
                utc_text(now),
                file_count,
                valid_task_count,
                error_count,
                error[:500],
                scan_id,
            ),
        )

    def complete_scan(
        self,
        scan_id: int,
        now: datetime,
        *,
        file_count: int,
        valid_task_count: int,
        error_count: int,
    ) -> None:
        timestamp = utc_text(now)
        with self.transaction():
            self.connection.execute(
                """
                UPDATE reminder_occurrences SET state='canceled', updated_at_utc=?
                WHERE task_path IN (
                    SELECT path FROM tasks
                    WHERE last_seen_scan_id IS NULL OR last_seen_scan_id != ?
                ) AND state IN ('scheduled', 'retry')
                """,
                (timestamp, scan_id),
            )
            self.connection.execute(
                """
                UPDATE scan_runs SET completed_at_utc=?, file_count=?, valid_task_count=?,
                    error_count=?, status='completed' WHERE scan_id=?
                """,
                (timestamp, file_count, valid_task_count, error_count, scan_id),
            )

    def touch_task(self, path: str, scan_id: int) -> None:
        self.connection.execute(
            "UPDATE tasks SET last_seen_scan_id=? WHERE path=?", (scan_id, path)
        )

    def mark_not_task(self, path: str, scan_id: int | None, now: datetime) -> None:
        timestamp = utc_text(now)
        with self.transaction():
            if scan_id is not None:
                self.connection.execute(
                    "UPDATE tasks SET last_seen_scan_id=?, updated_at_utc=? WHERE path=?",
                    (scan_id, timestamp, path),
                )
            self.connection.execute(
                """
                UPDATE reminder_occurrences SET state='canceled', updated_at_utc=?
                WHERE task_path=? AND state IN ('scheduled', 'retry')
                """,
                (timestamp, path),
            )

    def reconcile_task(
        self,
        task: Task,
        desired: Sequence[ReminderOccurrence],
        *,
        active: bool,
        scan_id: int | None,
        now: datetime,
        grace: timedelta,
    ) -> None:
        timestamp = utc_text(now)
        desired_ids = {item.occurrence_id for item in desired}
        with self.transaction():
            self.connection.execute(
                """
                INSERT INTO tasks(
                    path, source_hash, title, status, priority, archived,
                    last_seen_scan_id, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    source_hash=excluded.source_hash,
                    title=excluded.title,
                    status=excluded.status,
                    priority=excluded.priority,
                    archived=excluded.archived,
                    last_seen_scan_id=COALESCE(excluded.last_seen_scan_id, tasks.last_seen_scan_id),
                    updated_at_utc=excluded.updated_at_utc
                """,
                (
                    task.path,
                    task.source_hash,
                    task.title,
                    task.status,
                    task.priority,
                    int(task.archived),
                    scan_id,
                    timestamp,
                ),
            )
            if active:
                for occurrence in desired:
                    initial_state = (
                        OccurrenceState.EXPIRED
                        if occurrence.effective_at_utc < now - grace
                        else OccurrenceState.SCHEDULED
                    )
                    self._upsert_occurrence(occurrence, initial_state, timestamp)

            params: list[object] = [timestamp, task.path]
            not_in = ""
            if desired_ids and active:
                placeholders = ",".join("?" for _ in desired_ids)
                not_in = f" AND occurrence_id NOT IN ({placeholders})"
                params.extend(sorted(desired_ids))
            self.connection.execute(
                """
                UPDATE reminder_occurrences SET state='canceled', updated_at_utc=?
                WHERE task_path=? AND state IN ('scheduled', 'retry')
                """
                + not_in,
                params,
            )

    def _upsert_occurrence(
        self,
        occurrence: ReminderOccurrence,
        initial_state: OccurrenceState,
        timestamp: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO reminder_occurrences(
                occurrence_id, task_path, reminder_id, effective_at_utc, payload_hash,
                notification_title, notification_message, click_url, ntfy_priority,
                ntfy_message_id, state, created_at_utc, updated_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(occurrence_id) DO UPDATE SET
                payload_hash=excluded.payload_hash,
                notification_title=excluded.notification_title,
                notification_message=excluded.notification_message,
                click_url=excluded.click_url,
                ntfy_priority=excluded.ntfy_priority,
                state=CASE
                    WHEN reminder_occurrences.state IN ('sent', 'sending', 'failed')
                        THEN reminder_occurrences.state
                    WHEN excluded.state='expired' THEN 'expired'
                    WHEN reminder_occurrences.state IN ('canceled', 'expired')
                        THEN excluded.state
                    ELSE reminder_occurrences.state
                END,
                updated_at_utc=excluded.updated_at_utc
            """,
            (
                occurrence.occurrence_id,
                occurrence.task_path,
                occurrence.reminder_id,
                utc_text(occurrence.effective_at_utc),
                occurrence.payload_hash,
                occurrence.title,
                occurrence.message,
                occurrence.click_url,
                occurrence.ntfy_priority,
                occurrence.ntfy_message_id,
                initial_state,
                timestamp,
                timestamp,
            ),
        )

    def list_occurrences(self, state: str | None = None) -> list[sqlite3.Row]:
        if state is None:
            return list(
                self.connection.execute(
                    "SELECT * FROM reminder_occurrences ORDER BY effective_at_utc, occurrence_id"
                )
            )
        return list(
            self.connection.execute(
                """
                SELECT * FROM reminder_occurrences WHERE state=?
                ORDER BY effective_at_utc, occurrence_id
                """,
                (state,),
            )
        )

    def get_task(self, path: str) -> sqlite3.Row | None:
        return cast(
            sqlite3.Row | None,
            self.connection.execute("SELECT * FROM tasks WHERE path=?", (path,)).fetchone(),
        )

    def last_successful_scan(self) -> sqlite3.Row | None:
        return cast(
            sqlite3.Row | None,
            self.connection.execute(
                """
                SELECT * FROM scan_runs WHERE status='completed'
                ORDER BY completed_at_utc DESC LIMIT 1
                """
            ).fetchone(),
        )
