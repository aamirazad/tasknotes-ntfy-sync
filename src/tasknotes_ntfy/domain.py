"""Side-effect-free domain types shared by parser, reconciliation, and delivery."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum

YamlTime = str | date | datetime


class ReminderType(StrEnum):
    RELATIVE = "relative"
    ABSOLUTE = "absolute"


class OccurrenceState(StrEnum):
    SCHEDULED = "scheduled"
    SENDING = "sending"
    RETRY = "retry"
    SENT = "sent"
    CANCELED = "canceled"
    EXPIRED = "expired"
    INVALID = "invalid"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Reminder:
    id: str
    type: ReminderType
    related_to: str | None = None
    offset_raw: str | None = None
    offset: timedelta | None = None
    absolute_time: YamlTime | None = None
    description: str | None = None


@dataclass(frozen=True, slots=True)
class InvalidReminder:
    id: str
    reason: str


@dataclass(frozen=True, slots=True)
class Task:
    path: str
    title: str
    status: str
    priority: str
    due: YamlTime | None
    scheduled: YamlTime | None
    archived: bool
    body: str
    reminders: tuple[Reminder, ...]
    source_hash: str
    invalid_reminders: tuple[InvalidReminder, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ReminderOccurrence:
    occurrence_id: str
    task_path: str
    reminder_id: str
    effective_at_utc: datetime
    payload_hash: str
    title: str
    message: str
    click_url: str
    ntfy_priority: int
    ntfy_message_id: str


@dataclass(frozen=True, slots=True)
class ClaimedOccurrence:
    occurrence_id: str
    title: str
    message: str
    click_url: str
    ntfy_priority: int
    ntfy_message_id: str
    attempt_count: int
