"""Root container supervisor for Obsidian Headless and the notifier process."""

from __future__ import annotations

import asyncio
import json
import os
import pwd
import signal
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

from .config import Settings
from .headless import HeadlessManager


def _identity(username: str) -> tuple[int, int]:
    user = pwd.getpwnam(username)
    return user.pw_uid, user.pw_gid


def _demote(username: str, umask: int = 0o027) -> Callable[[], None]:
    uid, gid = _identity(username)

    def apply() -> None:
        os.initgroups(username, gid)
        os.setgid(gid)
        os.setuid(uid)
        os.umask(umask)

    return apply


def initialize_directories(settings: Settings) -> None:
    sync_uid, sync_gid = _identity("obsync")
    notifier_uid, notifier_gid = _identity("notifier")
    paths = (
        (settings.data_root, 0, sync_gid, 0o750),
        (settings.data_root / "config", sync_uid, sync_gid, 0o700),
        (settings.vault_root, sync_uid, sync_gid, 0o750),
        (settings.database_path.parent, notifier_uid, notifier_gid, 0o700),
    )
    for path, uid, gid, mode in paths:
        path.mkdir(parents=True, exist_ok=True)
        os.chown(path, uid, gid)
        os.chmod(path, mode)
    # Repair ownership and strip notifier write access after volume restoration.
    for root, directories, files in os.walk(settings.vault_root):
        root_path = Path(root)
        os.chown(root_path, sync_uid, sync_gid)
        os.chmod(root_path, 0o750)
        for name in directories:
            path = root_path / name
            os.chown(path, sync_uid, sync_gid)
            os.chmod(path, 0o750)
        for name in files:
            path = root_path / name
            os.chown(path, sync_uid, sync_gid)
            os.chmod(path, 0o640)


def sync_environment(settings: Settings) -> dict[str, str]:
    environment = dict(os.environ)
    environment["XDG_CONFIG_HOME"] = str(settings.data_root / "config")
    environment["OBSIDIAN_AUTH_TOKEN"] = settings.obsidian_auth_token.get_secret_value()
    environment.pop("OBSIDIAN_E2EE_PASSWORD", None)
    environment.pop("NTFY_TOPIC", None)
    environment.pop("NTFY_ACCESS_TOKEN", None)
    return environment


def notifier_environment() -> dict[str, str]:
    environment = dict(os.environ)
    # The notifier validates the shared settings model but never receives Obsidian secrets.
    environment["OBSIDIAN_AUTH_TOKEN"] = ""
    environment.pop("OBSIDIAN_E2EE_PASSWORD", None)
    return environment


def _sync_runner(
    arguments: Sequence[str], environment: Mapping[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        capture_output=True,
        text=True,
        check=False,
        env=environment,
        preexec_fn=_demote("obsync"),
    )


def _write_sync_health(settings: Settings, healthy: bool, state: str) -> None:
    payload = {
        "healthy": healthy,
        "state": state,
        "updated_at_utc": datetime.now(UTC).isoformat(),
    }
    temporary = settings.sync_health_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    os.chown(temporary, *_identity("notifier"))
    os.replace(temporary, settings.sync_health_path)


async def _pipe_output(
    process: asyncio.subprocess.Process, child: str, settings: Settings | None = None
) -> None:
    if process.stdout is None:
        return
    while line := await process.stdout.readline():
        message = line.decode(errors="replace").rstrip()
        print(json.dumps({"event": "child_log", "child": child, "message": message}), flush=True)
        if settings is not None and "Fully synced" in message:
            _write_sync_health(settings, True, "fully-synced")


async def _start_child(
    arguments: Sequence[str], username: str, environment: Mapping[str, str]
) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        *arguments,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=environment,
        preexec_fn=_demote(username),
    )


async def supervise(settings: Settings) -> int:
    if os.geteuid() != 0:
        raise RuntimeError("container supervisor must start as root")
    initialize_directories(settings)
    manager = HeadlessManager(settings, _sync_runner, sync_environment(settings))
    manager.ensure_configured()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)

    notifier = await _start_child(
        [sys.executable, "-m", "tasknotes_ntfy", "run"],
        "notifier",
        notifier_environment(),
    )
    notifier_log = asyncio.create_task(_pipe_output(notifier, "notifier"))

    async def run_sync() -> None:
        backoffs = (1, 2, 5, 10, 30)
        failures = 0
        while not stop.is_set():
            _write_sync_health(settings, False, "starting" if failures == 0 else "restarting")
            process = await _start_child(
                ["ob", "sync", "--path", str(settings.vault_root), "--continuous"],
                "obsync",
                sync_environment(settings),
            )
            output = asyncio.create_task(_pipe_output(process, "sync", settings))
            stopped = asyncio.create_task(stop.wait())
            exited = asyncio.create_task(process.wait())
            done, _ = await asyncio.wait({stopped, exited}, return_when=asyncio.FIRST_COMPLETED)
            if stopped in done:
                process.terminate()
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(process.wait(), timeout=20)
                if process.returncode is None:
                    process.kill()
                    await process.wait()
                exited.cancel()
                await asyncio.gather(exited, return_exceptions=True)
                await output
                return
            stopped.cancel()
            await asyncio.gather(stopped, return_exceptions=True)
            await output
            failures += 1
            _write_sync_health(settings, False, "restarting")
            delay = backoffs[min(failures - 1, len(backoffs) - 1)]
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=delay)

    sync_task = asyncio.create_task(run_sync(), name="sync-supervisor")
    stop_task = asyncio.create_task(stop.wait())
    notifier_wait = asyncio.create_task(notifier.wait())
    done, _ = await asyncio.wait(
        {stop_task, notifier_wait, sync_task}, return_when=asyncio.FIRST_COMPLETED
    )
    exit_code = 0
    if notifier_wait in done and not stop.is_set():
        exit_code = notifier.returncode or 1
        stop.set()
    elif sync_task in done and not stop.is_set():
        exception = sync_task.exception()
        if exception:
            raise exception
        exit_code = 1
        stop.set()
    if notifier.returncode is None:
        notifier.terminate()
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(notifier.wait(), timeout=20)
        if notifier.returncode is None:
            notifier.kill()
            await notifier.wait()
    stop.set()
    await asyncio.gather(sync_task, return_exceptions=True)
    stop_task.cancel()
    notifier_wait.cancel()
    await asyncio.gather(stop_task, notifier_wait, return_exceptions=True)
    await notifier_log
    return exit_code
