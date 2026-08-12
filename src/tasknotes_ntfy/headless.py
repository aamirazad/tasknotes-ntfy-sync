"""Safe, idempotent Obsidian Headless configuration."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .config import Settings


class HeadlessError(RuntimeError):
    pass


Runner = Callable[[Sequence[str], Mapping[str, str]], subprocess.CompletedProcess[str]]


@dataclass(frozen=True, slots=True)
class RemoteVault:
    id: str
    name: str


class HeadlessManager:
    def __init__(self, settings: Settings, runner: Runner, environment: Mapping[str, str]) -> None:
        self.settings = settings
        self.runner = runner
        self.environment = environment

    def _run_json(self, arguments: Sequence[str], allowed: set[int] | None = None) -> Any:
        result = self.runner(["ob", *arguments], self.environment)
        if result.returncode not in (allowed or {0}):
            raise HeadlessError(f"ob {arguments[0]} failed with exit code {result.returncode}")
        if not result.stdout.strip():
            return None
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise HeadlessError(f"ob {arguments[0]} returned invalid JSON") from exc

    def status(self) -> dict[str, Any] | None:
        result = self.runner(
            ["ob", "sync-status", "--path", str(self.settings.vault_root), "--json"],
            self.environment,
        )
        if result.returncode == 3:
            return None
        if result.returncode != 0:
            raise HeadlessError(f"ob sync-status failed with exit code {result.returncode}")
        try:
            parsed = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise HeadlessError("ob sync-status returned invalid JSON") from exc
        if not isinstance(parsed, dict):
            raise HeadlessError("ob sync-status returned an unexpected response")
        return parsed

    def remote_vault(self) -> RemoteVault:
        response = self._run_json(["sync-list-remote", "--json"])
        if not isinstance(response, dict):
            raise HeadlessError("ob sync-list-remote returned an unexpected response")
        raw_vaults = [*(response.get("vaults") or []), *(response.get("shared") or [])]
        matches = [
            item
            for item in raw_vaults
            if isinstance(item, dict)
            and (
                item.get("id") == self.settings.obsidian_remote_vault
                or item.get("name") == self.settings.obsidian_remote_vault
            )
        ]
        if len(matches) != 1:
            raise HeadlessError(
                "configured remote vault must match exactly one accessible vault by name or ID"
            )
        return RemoteVault(id=str(matches[0]["id"]), name=str(matches[0]["name"]))

    def ensure_configured(self) -> dict[str, Any]:
        status = self.status()
        if status is not None:
            matches = self.settings.obsidian_remote_vault in {
                status.get("vaultId"),
                status.get("vaultName"),
            }
            if not matches:
                raise HeadlessError(
                    "persisted sync state belongs to a different remote vault; "
                    "refusing to unlink or replace it"
                )
        else:
            password = self.settings.obsidian_e2ee_password
            if password is None or not password.get_secret_value():
                raise HeadlessError("OBSIDIAN_E2EE_PASSWORD is required for first-time setup")
            remote = self.remote_vault()
            self._run_json(
                [
                    "sync-setup",
                    "--vault",
                    remote.id,
                    "--path",
                    str(self.settings.vault_root),
                    "--password",
                    password.get_secret_value(),
                    "--device-name",
                    self.settings.obsidian_device_name,
                    "--json",
                ]
            )

        self._run_json(
            [
                "sync-config",
                "--path",
                str(self.settings.vault_root),
                "--mode",
                "pull-only",
                "--configs",
                "",
                "--file-types",
                "",
                "--excluded-folders",
                ",".join(self.settings.obsidian_excluded_folders),
                "--json",
            ]
        )
        verified = self.status()
        if verified is None or verified.get("syncMode") != "pull-only":
            raise HeadlessError("Obsidian Sync pull-only verification failed")
        return verified
