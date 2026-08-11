"""Command-line entry point."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from . import __version__
from .config import Settings
from .frontmatter import parse_task_file
from .healthcheck import check_health
from .reconcile import Reconciler
from .reminder_time import ReminderResolutionError, resolve_reminder
from .repository import Repository
from .service import run_service
from .supervisor import supervise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tasknotes-ntfy")
    parser.add_argument("--version", action="version", version=__version__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("run", help="run the notifier service")
    scan = subcommands.add_parser("scan", help="reconcile the task directory")
    scan.add_argument("--dry-run", action="store_true")
    listing = subcommands.add_parser("list", help="list reminder occurrences")
    listing.add_argument("--state")
    explain = subcommands.add_parser("explain", help="explain reminder times for one task")
    explain.add_argument("path")
    subcommands.add_parser("health", help="check service health")
    subcommands.add_parser("supervise", help="initialize and supervise the container")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        settings = Settings()  # type: ignore[call-arg]  # environment-backed settings
    except ValidationError as exc:
        errors = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        print(f"configuration error: {errors}", file=sys.stderr)
        return 2
    if args.command == "run":
        asyncio.run(run_service(settings))
        return 0
    if args.command == "health":
        result = check_health(settings)
        print(json.dumps(result, separators=(",", ":"), default=str))
        return 0 if result["healthy"] else 1
    if args.command == "list":
        repository = Repository(settings.database_path)
        try:
            for row in repository.list_occurrences(args.state):
                print(
                    json.dumps(
                        {
                            "occurrence_id": row["occurrence_id"],
                            "task_path": row["task_path"],
                            "reminder_id": row["reminder_id"],
                            "effective_at_utc": row["effective_at_utc"],
                            "state": row["state"],
                            "attempt_count": row["attempt_count"],
                            "next_attempt_at_utc": row["next_attempt_at_utc"],
                            "last_error": row["last_error"],
                        },
                        separators=(",", ":"),
                    )
                )
        finally:
            repository.close()
        return 0
    if args.command == "scan":
        if args.dry_run:
            with tempfile.TemporaryDirectory(prefix="tasknotes-ntfy-") as directory:
                repository = Repository(Path(directory) / "dry-run.sqlite3")
                try:
                    reconciler = Reconciler(settings, repository)
                    reconciler.full_scan()
                    rows = repository.list_occurrences()
                    print(json.dumps({"occurrences": len(rows), "dry_run": True}))
                finally:
                    repository.close()
        else:
            repository = Repository(settings.database_path)
            try:
                scan_id = Reconciler(settings, repository).full_scan()
                print(json.dumps({"scan_id": scan_id, "dry_run": False}))
            finally:
                repository.close()
        return 0
    if args.command == "explain":
        relative = PurePosixPath(args.path)
        if relative.is_absolute() or ".." in relative.parts:
            print("task path must be vault-relative and remain inside TASKS_PATH", file=sys.stderr)
            return 2
        path = settings.vault_root.joinpath(*relative.parts)
        try:
            path.relative_to(settings.task_directory)
        except ValueError:
            print("task path must be inside TASKS_PATH", file=sys.stderr)
            return 2
        task = parse_task_file(
            path,
            relative.as_posix(),
            property_name=settings.task_property_name,
            property_value=settings.task_property_value,
            max_file_bytes=settings.max_file_bytes,
        )
        reminders: list[dict[str, object]] = []
        for reminder in task.reminders:
            item: dict[str, object] = {
                "id": reminder.id,
                "type": reminder.type,
                "related_to": reminder.related_to,
                "offset": reminder.offset_raw,
                "absolute_time": reminder.absolute_time,
            }
            try:
                item["effective_at_utc"] = resolve_reminder(
                    task, reminder, settings.timezone, settings.date_only_time
                ).isoformat()
            except ReminderResolutionError as exc:
                item["error"] = str(exc)
            reminders.append(item)
        print(
            json.dumps(
                {
                    "path": task.path,
                    "title": task.title,
                    "due": task.due,
                    "scheduled": task.scheduled,
                    "reminders": reminders,
                },
                default=str,
                separators=(",", ":"),
            )
        )
        return 0
    if args.command == "supervise":
        return asyncio.run(supervise(settings))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
