"""Command-line entry point."""

from __future__ import annotations

import argparse
import sys

from pydantic import ValidationError

from . import __version__
from .config import Settings


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
        Settings()  # type: ignore[call-arg]  # populated by pydantic-settings from the environment
    except ValidationError as exc:
        errors = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        print(f"configuration error: {errors}", file=sys.stderr)
        return 2
    print(
        f"command {args.command!r} is not available until its implementation phase",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
