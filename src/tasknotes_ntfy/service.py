"""Notifier process orchestration."""

from __future__ import annotations

import asyncio
import logging
import signal
from contextlib import suppress
from datetime import UTC, datetime

from .config import Settings
from .healthcheck import HealthReporter
from .logging import log_event
from .ntfy import NtfyPublisher
from .reconcile import Reconciler
from .repository import Repository
from .scheduler import Scheduler
from .watcher import Watcher

logger = logging.getLogger(__name__)


async def _heartbeat_loop(
    reporter: HealthReporter, stop: asyncio.Event, interval: float = 10
) -> None:
    while not stop.is_set():
        reporter.heartbeat(datetime.now(UTC))
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)


async def run_service(settings: Settings) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        with suppress(NotImplementedError):
            loop.add_signal_handler(signum, stop.set)

    reporter = HealthReporter(settings.health_path)
    while not settings.task_directory.is_dir() and not stop.is_set():
        reporter.starting(datetime.now(UTC), "waiting for initial vault sync")
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=5)
    if stop.is_set():
        return

    repository = Repository(settings.database_path)
    publisher = NtfyPublisher(settings)
    reconciler = Reconciler(
        settings,
        repository,
        scan_completed=reporter.scan_completed,
    )
    watcher = Watcher(settings, reconciler)
    scheduler = Scheduler(
        settings,
        repository,
        publisher,
        heartbeat=reporter.scheduler_heartbeat,
    )
    try:
        reconciler.full_scan()
        tasks = [
            asyncio.create_task(scheduler.run(stop), name="scheduler"),
            asyncio.create_task(watcher.run(stop), name="watcher"),
            asyncio.create_task(watcher.periodic_reconcile(stop), name="reconciler"),
            asyncio.create_task(_heartbeat_loop(reporter, stop), name="heartbeat"),
        ]
        log_event(logger, logging.INFO, "startup_complete")
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        unexpected = [task for task in done if not stop.is_set()]
        stop.set()
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if unexpected:
            await unexpected[0]
    finally:
        await publisher.close()
        repository.close()
