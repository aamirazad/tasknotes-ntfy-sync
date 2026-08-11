"""Opt-in tests for the built amd64 runtime image.

Run with RUN_CONTAINER_TESTS=1 pytest tests/container.
"""

import json
import os
import subprocess
import time
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_CONTAINER_TESTS") != "1",
    reason="set RUN_CONTAINER_TESTS=1 to build and inspect the runtime image",
)

IMAGE = "tasknotes-ntfy:container-test"


@pytest.fixture(scope="session", autouse=True)
def image() -> str:
    subprocess.run(
        ["docker", "build", "--platform", "linux/amd64", "-t", IMAGE, "."],
        check=True,
    )
    return IMAGE


def test_image_has_no_exposed_ports_and_uses_tini(image: str) -> None:
    output = subprocess.check_output(["docker", "image", "inspect", image], text=True)
    inspected = json.loads(output)[0]
    assert not inspected["Config"].get("ExposedPorts")
    assert inspected["Config"]["Entrypoint"][:2] == ["/usr/bin/tini", "--"]
    assert inspected["Architecture"] == "amd64"


def test_runtime_contains_dedicated_users_and_pinned_headless(image: str) -> None:
    output = subprocess.check_output(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "/bin/sh",
            image,
            "-c",
            "id obsync && id notifier && ob --version && python --version",
        ],
        text=True,
    )
    assert "uid=10001(obsync)" in output
    assert "uid=10002(notifier)" in output
    assert "0.0.14" in output
    assert "Python 3.13" in output


def test_notifier_cannot_mutate_vault(image: str) -> None:
    probe = """
import os, pwd
from pathlib import Path
from tasknotes_ntfy.config import Settings
from tasknotes_ntfy.supervisor import initialize_directories
s = Settings()
initialize_directories(s)
source = s.vault_root / 'Existing.md'
source.write_text('original')
initialize_directories(s)
user = pwd.getpwnam('notifier')
os.initgroups('notifier', user.pw_gid)
os.setgid(user.pw_gid)
os.setuid(user.pw_uid)
operations = [
    lambda: (s.vault_root / 'Created.md').write_text('new'),
    lambda: source.write_text('edited'),
    lambda: source.rename(s.vault_root / 'Renamed.md'),
    lambda: source.unlink(),
]
for operation in operations:
    try:
        operation()
    except PermissionError:
        pass
    else:
        raise SystemExit('notifier unexpectedly mutated the vault')
"""
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "python",
            "-e",
            "OBSIDIAN_REMOTE_VAULT=Remote",
            "-e",
            "OBSIDIAN_DEEP_LINK_VAULT=Phone",
            "-e",
            "OBSIDIAN_AUTH_TOKEN=token",
            "-e",
            "NTFY_TOPIC=topic",
            image,
            "-c",
            probe,
        ],
        check=True,
    )


def test_supervisor_initializes_and_reuses_persistent_state(image: str, tmp_path: Path) -> None:
    mock_directory = tmp_path / "mock"
    mock_directory.mkdir()
    mock = mock_directory / "ob"
    mock.write_text(
        """#!/bin/sh
set -eu
command_name="$1"
shift
case "$command_name" in
  sync-status)
    if [ ! -f /data/config/mock-configured ]; then exit 3; fi
    printf '%s%s\\n' \
      '{"vaultId":"remote-id","vaultName":"Remote","vaultPath":"/data/vault"' \
      ',"syncMode":"pull-only"}'
    ;;
  sync-list-remote)
    printf '%s\\n' '{"vaults":[{"id":"remote-id","name":"Remote","region":"us"}],"shared":[]}'
    ;;
  sync-setup)
    mkdir -p /data/vault/Efforts/Tasks
    touch /data/config/mock-configured
    printf '%s\\n' '{}'
    ;;
  sync-config)
    printf '%s\\n' '{}'
    ;;
  sync)
    trap 'exit 0' INT TERM
    printf '%s\\n' 'Fully synced'
    while :; do sleep 1; done
    ;;
  *) exit 2 ;;
esac
""",
        encoding="utf-8",
    )
    mock.chmod(0o755)
    suffix = uuid.uuid4().hex[:12]
    volume = f"tasknotes-ntfy-test-{suffix}"
    base_name = f"tasknotes-ntfy-test-{suffix}"
    subprocess.run(["docker", "volume", "create", volume], check=True, capture_output=True)

    def start(name: str, include_password: bool) -> str:
        command = [
            "docker",
            "run",
            "--detach",
            "--name",
            name,
            "--read-only",
            "--security-opt",
            "no-new-privileges:true",
            "--cap-drop",
            "ALL",
            "--cap-add",
            "CHOWN",
            "--cap-add",
            "DAC_OVERRIDE",
            "--cap-add",
            "FOWNER",
            "--cap-add",
            "KILL",
            "--cap-add",
            "SETGID",
            "--cap-add",
            "SETUID",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,noexec,size=64m",
            "-v",
            f"{volume}:/data",
            "-v",
            f"{mock_directory}:/mock:ro",
            "-e",
            "PATH=/mock:/opt/venv/bin:/opt/obsidian/bin:/usr/local/bin:/usr/bin:/bin",
            "-e",
            "OBSIDIAN_REMOTE_VAULT=Remote",
            "-e",
            "OBSIDIAN_DEEP_LINK_VAULT=Phone",
            "-e",
            "OBSIDIAN_AUTH_TOKEN=token",
            "-e",
            "NTFY_TOPIC=topic",
        ]
        if include_password:
            command.extend(["-e", "OBSIDIAN_E2EE_PASSWORD=password"])
        command.append(image)
        return subprocess.check_output(command, text=True).strip()

    try:
        first = f"{base_name}-first"
        start(first, True)
        for _ in range(30):
            health = subprocess.run(
                ["docker", "exec", first, "tasknotes-ntfy", "health"],
                text=True,
                capture_output=True,
            )
            if health.returncode == 0:
                break
            time.sleep(0.5)
        else:
            logs = subprocess.check_output(["docker", "logs", first], text=True)
            pytest.fail(
                f"container did not become healthy:\n{logs}\n{health.stdout}{health.stderr}"
            )
        processes = subprocess.check_output(
            ["docker", "top", first, "-eo", "user,pid,args"], text=True
        )
        assert "10001" in processes
        assert "10002" in processes
        assert "ob sync --path /data/vault --continuous" in processes
        subprocess.run(["docker", "stop", "--time", "10", first], check=True, capture_output=True)
        subprocess.run(["docker", "rm", first], check=True, capture_output=True)

        second = f"{base_name}-second"
        start(second, False)
        for _ in range(20):
            if (
                subprocess.run(
                    ["docker", "exec", second, "test", "-f", "/data/config/mock-configured"]
                ).returncode
                == 0
            ):
                break
            time.sleep(0.25)
        assert (
            subprocess.check_output(
                ["docker", "inspect", "-f", "{{.State.Running}}", second], text=True
            ).strip()
            == "true"
        )
        subprocess.run(["docker", "stop", "--time", "10", second], check=True, capture_output=True)
        subprocess.run(["docker", "rm", second], check=True, capture_output=True)
    finally:
        for name in (f"{base_name}-first", f"{base_name}-second"):
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        subprocess.run(["docker", "volume", "rm", "-f", volume], capture_output=True)
