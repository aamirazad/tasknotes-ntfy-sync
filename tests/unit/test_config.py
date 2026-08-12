from pathlib import Path

import pytest
from pydantic import ValidationError

from tasknotes_ntfy.config import Settings

BASE_ENV = {
    "OBSIDIAN_REMOTE_VAULT": "Remote Vault",
    "OBSIDIAN_DEEP_LINK_VAULT": "Phone Vault",
    "OBSIDIAN_AUTH_TOKEN": "secret-token",
    "NTFY_TOPIC": "high-entropy-topic",
}


def make_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **overrides: str) -> Settings:
    values = {
        **BASE_ENV,
        "DATA_ROOT": str(tmp_path),
        "VAULT_ROOT": str(tmp_path / "vault"),
        "DATABASE_PATH": str(tmp_path / "notifier" / "db.sqlite3"),
        "HEALTH_PATH": str(tmp_path / "notifier" / "health.json"),
        **overrides,
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def test_defaults_and_csv_parsing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = make_settings(monkeypatch, tmp_path, COMPLETED_STATUSES="Done, Not doing")
    assert settings.completed_statuses == frozenset({"Done", "Not doing"})
    assert settings.obsidian_excluded_folders == ()
    assert settings.date_only_time.isoformat() == "07:00:00"
    assert settings.priority_map["High"] == 4


def test_parses_obsidian_excluded_folders(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = make_settings(
        monkeypatch, tmp_path, OBSIDIAN_EXCLUDED_FOLDERS="Archive, Attachments/Private,  Logs "
    )
    assert settings.obsidian_excluded_folders == ("Archive", "Attachments/Private", "Logs")


def test_rejects_path_escape(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="vault-relative"):
        make_settings(monkeypatch, tmp_path, TASKS_PATH="../elsewhere")


def test_rejects_invalid_timezone(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="IANA"):
        make_settings(monkeypatch, tmp_path, TIMEZONE="Mars/Olympus")


def test_secrets_are_redacted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = make_settings(monkeypatch, tmp_path)
    assert "secret-token" not in repr(settings)
    assert "high-entropy-topic" not in repr(settings)
