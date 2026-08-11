import json
import subprocess
from pathlib import Path

import pytest

from tasknotes_ntfy.config import Settings
from tasknotes_ntfy.headless import HeadlessError, HeadlessManager
from tasknotes_ntfy.supervisor import notifier_environment, sync_environment


def settings(tmp_path: Path, **values) -> Settings:
    configured = {
        "data_root": tmp_path,
        "vault_root": tmp_path / "vault",
        "database_path": tmp_path / "notifier" / "db.sqlite3",
        "health_path": tmp_path / "notifier" / "health.json",
        "obsidian_remote_vault": "Remote Vault",
        "obsidian_deep_link_vault": "Phone",
        "obsidian_auth_token": "auth-secret",
        "obsidian_e2ee_password": "e2ee-secret",
        "ntfy_topic": "topic-secret",
        **values,
    }
    return Settings(_env_file=None, **configured)


class FakeRunner:
    def __init__(self, responses: list[tuple[int, object]]) -> None:
        self.responses = responses
        self.calls: list[list[str]] = []

    def __call__(self, arguments, environment) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(arguments))
        returncode, output = self.responses.pop(0)
        stdout = output if isinstance(output, str) else json.dumps(output)
        return subprocess.CompletedProcess(arguments, returncode, stdout, "")


def test_first_setup_resolves_unique_vault_and_enforces_pull_only(tmp_path: Path) -> None:
    configured = settings(tmp_path)
    runner = FakeRunner(
        [
            (3, ""),
            (0, {"vaults": [{"id": "id-1", "name": "Remote Vault"}], "shared": []}),
            (0, {}),
            (0, {}),
            (
                0,
                {
                    "vaultId": "id-1",
                    "vaultName": "Remote Vault",
                    "syncMode": "pull-only",
                },
            ),
        ]
    )
    status = HeadlessManager(configured, runner, {}).ensure_configured()
    assert status["syncMode"] == "pull-only"
    setup = runner.calls[2]
    assert setup[:3] == ["ob", "sync-setup", "--vault"]
    assert "id-1" in setup
    assert "e2ee-secret" in setup
    config = runner.calls[3]
    assert config[config.index("--mode") + 1] == "pull-only"
    assert config[config.index("--configs") + 1] == ""
    assert config[config.index("--file-types") + 1] == ""


def test_existing_configuration_is_reused_without_password(tmp_path: Path) -> None:
    configured = settings(tmp_path, obsidian_e2ee_password=None)
    existing = {"vaultId": "id-1", "vaultName": "Remote Vault", "syncMode": "bidirectional"}
    verified = {**existing, "syncMode": "pull-only"}
    runner = FakeRunner([(0, existing), (0, {}), (0, verified)])
    HeadlessManager(configured, runner, {}).ensure_configured()
    assert all("sync-setup" not in call for call in runner.calls)


def test_mismatched_persisted_vault_fails_without_unlink(tmp_path: Path) -> None:
    configured = settings(tmp_path)
    runner = FakeRunner([(0, {"vaultId": "other", "vaultName": "Other", "syncMode": "pull-only"})])
    with pytest.raises(HeadlessError, match="different remote vault"):
        HeadlessManager(configured, runner, {}).ensure_configured()
    assert all("sync-unlink" not in call for call in runner.calls)


def test_first_setup_requires_password(tmp_path: Path) -> None:
    configured = settings(tmp_path, obsidian_e2ee_password=None)
    with pytest.raises(HeadlessError, match="required"):
        HeadlessManager(configured, FakeRunner([(3, "")]), {}).ensure_configured()


def test_child_environments_separate_secrets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configured = settings(tmp_path)
    monkeypatch.setenv("NTFY_TOPIC", "topic-secret")
    monkeypatch.setenv("OBSIDIAN_E2EE_PASSWORD", "e2ee-secret")
    sync_env = sync_environment(configured)
    assert sync_env["OBSIDIAN_AUTH_TOKEN"] == "auth-secret"
    assert "NTFY_TOPIC" not in sync_env
    assert "OBSIDIAN_E2EE_PASSWORD" not in sync_env
    notify_env = notifier_environment()
    assert notify_env["OBSIDIAN_AUTH_TOKEN"] == ""
    assert "OBSIDIAN_E2EE_PASSWORD" not in notify_env
    assert notify_env["NTFY_TOPIC"] == "topic-secret"
