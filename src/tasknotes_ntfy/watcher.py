"""Debounced filesystem events with periodic authoritative reconciliation."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from pathlib import Path

from watchfiles import Change, awatch

from .config import Settings
from .frontmatter import TaskParseError
from .logging import log_event
from .reconcile import Reconciler, ScanError

logger = logging.getLogger(__name__)


class Watcher:
    def __init__(self, settings: Settings, reconciler: Reconciler) -> None:
        self.settings = settings
        self.reconciler = reconciler

    async def handle_changes(self, changes: set[tuple[Change, str]]) -> None:
        markdown_changes = {
            (change, Path(raw_path))
            for change, raw_path in changes
            if Path(raw_path).suffix.lower() == ".md"
        }
        if not markdown_changes:
            return
        if any(change == Change.deleted for change, _ in markdown_changes):
            self.reconciler.full_scan()
            return
        latest = {path for _, path in markdown_changes}
        for path in sorted(latest):
            if not path.exists():
                self.reconciler.full_scan()
                continue
            delays = (0.1, 0.25, 0.5)
            for attempt, delay in enumerate(delays, start=1):
                try:
                    self.reconciler.reconcile_path(path)
                    break
                except TaskParseError as exc:
                    if attempt == len(delays):
                        log_event(
                            logger,
                            logging.WARNING,
                            "watcher_error",
                            task_path=path.relative_to(self.settings.vault_root).as_posix(),
                            error=str(exc),
                        )
                        break
                    await asyncio.sleep(delay)
                except FileNotFoundError:
                    self.reconciler.full_scan()
                    break

    async def run(self, stop: asyncio.Event) -> None:
        async for changes in awatch(
            self.settings.task_directory,
            debounce=self.settings.watch_debounce_ms,
            stop_event=stop,
            recursive=True,
        ):
            await self.handle_changes(changes)
            if stop.is_set():
                break

    async def periodic_reconcile(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    stop.wait(), timeout=self.settings.reconcile_interval_seconds
                )
            if not stop.is_set():
                with suppress(ScanError):
                    self.reconciler.full_scan()
