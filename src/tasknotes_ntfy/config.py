"""Validated environment configuration."""

from __future__ import annotations

import json
from datetime import time
from pathlib import Path, PurePosixPath
from typing import Annotated, Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

DEFAULT_PRIORITY_MAP = {"None": 3, "Low": 2, "Medium": 3, "High": 4, "Urgent": 5}


class Settings(BaseSettings):
    """All runtime settings. Environment names intentionally match field aliases."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        populate_by_name=True,
    )

    data_root: Path = Field(default=Path("/data"), alias="DATA_ROOT")
    vault_root: Path = Field(default=Path("/data/vault"), alias="VAULT_ROOT")
    obsidian_remote_vault: str = Field(alias="OBSIDIAN_REMOTE_VAULT")
    obsidian_deep_link_vault: str = Field(alias="OBSIDIAN_DEEP_LINK_VAULT")
    obsidian_auth_token: SecretStr = Field(alias="OBSIDIAN_AUTH_TOKEN")
    obsidian_e2ee_password: SecretStr | None = Field(default=None, alias="OBSIDIAN_E2EE_PASSWORD")
    obsidian_device_name: str = Field(default="tasknotes-ntfy", alias="OBSIDIAN_DEVICE_NAME")
    tasks_path: str = Field(default="Efforts/Tasks", alias="TASKS_PATH")
    task_property_name: str = Field(default="base", alias="TASK_PROPERTY_NAME")
    task_property_value: str = Field(default="[[Tasks.base]]", alias="TASK_PROPERTY_VALUE")
    timezone_name: str = Field(default="America/New_York", alias="TIMEZONE")
    date_only_time: time = Field(default=time(7, 0), alias="DATE_ONLY_TIME")
    completed_statuses: Annotated[frozenset[str], NoDecode] = Field(
        default=frozenset({"Done", "Not doing"}), alias="COMPLETED_STATUSES"
    )
    priority_map: dict[str, int] = Field(
        default_factory=lambda: DEFAULT_PRIORITY_MAP.copy(), alias="PRIORITY_MAP_JSON"
    )
    ntfy_base_url: str = Field(default="https://ntfy.sh", alias="NTFY_BASE_URL")
    ntfy_topic: SecretStr = Field(alias="NTFY_TOPIC")
    ntfy_access_token: SecretStr | None = Field(default=None, alias="NTFY_ACCESS_TOKEN")
    notification_tag: str = Field(default="calendar", alias="NOTIFICATION_TAG")
    body_max_bytes: int = Field(default=1000, ge=64, le=4096, alias="BODY_MAX_BYTES")
    max_file_bytes: int = Field(default=2_097_152, ge=1024, alias="MAX_FILE_BYTES")
    reconcile_interval_seconds: float = Field(default=60, gt=0, alias="RECONCILE_INTERVAL_SECONDS")
    watch_debounce_ms: int = Field(default=500, ge=50, alias="WATCH_DEBOUNCE_MS")
    due_poll_seconds: float = Field(default=5, gt=0, alias="DUE_POLL_SECONDS")
    missed_reminder_grace_seconds: int = Field(
        default=900, ge=0, alias="MISSED_REMINDER_GRACE_SECONDS"
    )
    ntfy_timeout_seconds: float = Field(default=10, gt=0, alias="NTFY_TIMEOUT_SECONDS")
    delivery_lease_seconds: int = Field(default=120, ge=10, alias="DELIVERY_LEASE_SECONDS")
    health_stale_seconds: int = Field(default=180, ge=10, alias="HEALTH_STALE_SECONDS")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    database_path: Path = Field(
        default=Path("/data/notifier/reminders.sqlite3"), alias="DATABASE_PATH"
    )
    health_path: Path = Field(default=Path("/data/notifier/health.json"), alias="HEALTH_PATH")
    sync_health_path: Path = Field(
        default=Path("/data/notifier/sync-health.json"), alias="SYNC_HEALTH_PATH"
    )

    @field_validator("completed_statuses", mode="before")
    @classmethod
    def parse_completed_statuses(cls, value: Any) -> Any:
        if isinstance(value, str):
            return frozenset(item.strip() for item in value.split(",") if item.strip())
        return value

    @field_validator("priority_map", mode="before")
    @classmethod
    def parse_priority_map(cls, value: Any) -> Any:
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError("must be valid JSON") from exc
        return value

    @field_validator("priority_map")
    @classmethod
    def validate_priority_map(cls, value: dict[str, int]) -> dict[str, int]:
        if any(
            not isinstance(priority, int) or priority < 1 or priority > 5
            for priority in value.values()
        ):
            raise ValueError("values must be integer ntfy priorities from 1 through 5")
        return value

    @field_validator("tasks_path")
    @classmethod
    def validate_tasks_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or value in {"", "."}:
            raise ValueError("must be a non-empty vault-relative path without '..'")
        return path.as_posix()

    @field_validator("timezone_name")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("must be a valid IANA time zone") from exc
        return value

    @field_validator("ntfy_base_url")
    @classmethod
    def validate_ntfy_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("must be an absolute HTTP(S) URL")
        return value.rstrip("/")

    @field_validator(
        "obsidian_remote_vault",
        "obsidian_deep_link_vault",
        "obsidian_device_name",
        "task_property_name",
        "task_property_value",
        "notification_tag",
    )
    @classmethod
    def require_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value.strip()

    @model_validator(mode="after")
    def validate_paths(self) -> Settings:
        if self.sync_health_path == Path("/data/notifier/sync-health.json"):
            self.sync_health_path = self.data_root / "notifier" / "sync-health.json"
        try:
            self.vault_root.relative_to(self.data_root)
            self.database_path.relative_to(self.data_root)
            self.health_path.relative_to(self.data_root)
            self.sync_health_path.relative_to(self.data_root)
        except ValueError as exc:
            raise ValueError(
                "VAULT_ROOT, DATABASE_PATH, HEALTH_PATH, and SYNC_HEALTH_PATH "
                "must be under DATA_ROOT"
            ) from exc
        return self

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    @property
    def task_directory(self) -> Path:
        return self.vault_root.joinpath(*PurePosixPath(self.tasks_path).parts)
