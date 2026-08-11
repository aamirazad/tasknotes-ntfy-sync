"""Database-driven due loop and delivery state transitions."""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Protocol

from .config import Settings
from .domain import ClaimedOccurrence
from .logging import log_event
from .ntfy import PermanentDeliveryError, TransientDeliveryError
from .repository import Repository

logger = logging.getLogger(__name__)
BACKOFF_SECONDS = (10, 30, 120, 600, 1800)


class Publisher(Protocol):
    async def publish(self, occurrence: ClaimedOccurrence) -> None: ...


class Scheduler:
    def __init__(
        self,
        settings: Settings,
        repository: Repository,
        publisher: Publisher,
        *,
        clock: Callable[[], datetime] | None = None,
        jitter: Callable[[float, float], float] | None = None,
        heartbeat: Callable[[datetime], None] | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.publisher = publisher
        self.clock = clock or (lambda: datetime.now(UTC))
        self.jitter = jitter or random.uniform
        self.heartbeat = heartbeat

    def _retry_delay(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return retry_after
        base = BACKOFF_SECONDS[min(max(attempt - 1, 0), len(BACKOFF_SECONDS) - 1)]
        return self.jitter(float(base), float(base) * 1.2)

    async def run_once(self) -> int:
        now = self.clock()
        claimed = self.repository.claim_due(
            now,
            grace=timedelta(seconds=self.settings.missed_reminder_grace_seconds),
            lease=timedelta(seconds=self.settings.delivery_lease_seconds),
        )
        for occurrence in claimed:
            log_event(
                logger,
                logging.INFO,
                "notification_claimed",
                occurrence_id=occurrence.occurrence_id[:12],
                attempt=occurrence.attempt_count,
            )
            try:
                await self.publisher.publish(occurrence)
            except TransientDeliveryError as exc:
                transition_time = self.clock()
                delay = self._retry_delay(occurrence.attempt_count, exc.retry_after_seconds)
                next_attempt = transition_time + timedelta(seconds=delay)
                self.repository.mark_retry(
                    occurrence.occurrence_id,
                    transition_time,
                    next_attempt,
                    str(exc),
                )
                log_event(
                    logger,
                    logging.WARNING,
                    "notification_retry",
                    occurrence_id=occurrence.occurrence_id[:12],
                    attempt=occurrence.attempt_count,
                    next_attempt_at_utc=next_attempt.isoformat(),
                    error=str(exc),
                )
            except PermanentDeliveryError as exc:
                self.repository.mark_failed(occurrence.occurrence_id, self.clock(), str(exc))
                log_event(
                    logger,
                    logging.ERROR,
                    "notification_failed",
                    occurrence_id=occurrence.occurrence_id[:12],
                    attempt=occurrence.attempt_count,
                    error=str(exc),
                )
            else:
                sent_at = self.clock()
                self.repository.mark_sent(occurrence.occurrence_id, sent_at)
                log_event(
                    logger,
                    logging.INFO,
                    "notification_sent",
                    occurrence_id=occurrence.occurrence_id[:12],
                    attempt=occurrence.attempt_count,
                )
        if self.heartbeat is not None:
            self.heartbeat(self.clock())
        return len(claimed)

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await self.run_once()
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self.settings.due_poll_seconds)
