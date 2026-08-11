"""Filesystem-to-SQLite reconciliation."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .config import Settings
from .domain import ReminderOccurrence, Task
from .frontmatter import NotTaskError, TaskParseError, parse_task_file
from .logging import log_event
from .notification import make_occurrence
from .reminder_time import ReminderResolutionError, resolve_reminder
from .repository import Repository

logger = logging.getLogger(__name__)


class ScanError(RuntimeError):
    pass


class Reconciler:
    def __init__(
        self,
        settings: Settings,
        repository: Repository,
        *,
        clock: Callable[[], datetime] | None = None,
        scan_completed: Callable[[datetime], None] | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.clock = clock or (lambda: datetime.now(UTC))
        self.scan_completed = scan_completed

    def desired_occurrences(self, task: Task) -> list[ReminderOccurrence]:
        desired: list[ReminderOccurrence] = []
        for invalid in task.invalid_reminders:
            log_event(
                logger,
                logging.WARNING,
                "reminder_invalid",
                task_path=task.path,
                reminder_id=invalid.id,
                error=invalid.reason,
            )
        for reminder in task.reminders:
            try:
                effective = resolve_reminder(
                    task,
                    reminder,
                    self.settings.timezone,
                    self.settings.date_only_time,
                )
            except ReminderResolutionError as exc:
                log_event(
                    logger,
                    logging.WARNING,
                    "reminder_invalid",
                    task_path=task.path,
                    reminder_id=reminder.id,
                    error=str(exc),
                )
                continue
            desired.append(
                make_occurrence(
                    task,
                    reminder,
                    effective,
                    vault_identity=self.settings.obsidian_deep_link_vault,
                    body_max_bytes=self.settings.body_max_bytes,
                    priority_map=self.settings.priority_map,
                )
            )
        return desired

    def reconcile_task(self, task: Task, scan_id: int | None = None) -> list[ReminderOccurrence]:
        desired = self.desired_occurrences(task)
        active = task.status not in self.settings.completed_statuses and not task.archived
        self.repository.reconcile_task(
            task,
            desired,
            active=active,
            scan_id=scan_id,
            now=self.clock(),
            grace=timedelta(seconds=self.settings.missed_reminder_grace_seconds),
        )
        return desired

    def reconcile_path(self, path: Path, scan_id: int | None = None) -> Task | None:
        relative = path.relative_to(self.settings.vault_root).as_posix()
        try:
            task = parse_task_file(
                path,
                relative,
                property_name=self.settings.task_property_name,
                property_value=self.settings.task_property_value,
                max_file_bytes=self.settings.max_file_bytes,
            )
        except NotTaskError:
            self.repository.mark_not_task(relative, scan_id, self.clock())
            return None
        self.reconcile_task(task, scan_id)
        return task

    def full_scan(self) -> int:
        started = self.clock()
        scan_id = self.repository.start_scan(started)
        log_event(logger, logging.INFO, "scan_started", scan_id=scan_id)
        file_count = 0
        valid_count = 0
        error_count = 0
        try:
            paths = sorted(self.settings.task_directory.rglob("*.md"))
        except OSError as exc:
            self.repository.fail_scan(
                scan_id,
                self.clock(),
                file_count=0,
                valid_task_count=0,
                error_count=1,
                error="task directory enumeration failed",
            )
            raise ScanError("task directory enumeration failed") from exc

        for path in paths:
            file_count += 1
            try:
                task = self.reconcile_path(path, scan_id)
                valid_count += int(task is not None)
            except TaskParseError as exc:
                error_count += 1
                relative = path.relative_to(self.settings.vault_root).as_posix()
                self.repository.touch_task(relative, scan_id)
                log_event(
                    logger,
                    logging.WARNING,
                    "task_invalid",
                    task_path=relative,
                    error=str(exc),
                )

        failure_threshold = max(3, (file_count + 4) // 5)
        if error_count >= failure_threshold:
            self.repository.fail_scan(
                scan_id,
                self.clock(),
                file_count=file_count,
                valid_task_count=valid_count,
                error_count=error_count,
                error="too many task files failed to parse",
            )
            raise ScanError("too many task files failed to parse")

        finished = self.clock()
        self.repository.complete_scan(
            scan_id,
            finished,
            file_count=file_count,
            valid_task_count=valid_count,
            error_count=error_count,
        )
        if self.scan_completed is not None:
            self.scan_completed(finished)
        duration_ms = int((finished - started).total_seconds() * 1000)
        log_event(
            logger,
            logging.INFO,
            "scan_completed",
            scan_id=scan_id,
            file_count=file_count,
            valid_task_count=valid_count,
            error_count=error_count,
            duration_ms=duration_ms,
        )
        return scan_id
