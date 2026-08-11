from datetime import date
from pathlib import Path

import pytest

from tasknotes_ntfy.domain import ReminderType
from tasknotes_ntfy.frontmatter import (
    NotTaskError,
    TaskParseError,
    parse_task_file,
    parse_task_text,
)


def parse(text: str, path: str = "Tasks/Filename.md"):
    return parse_task_text(
        text,
        path,
        property_name="base",
        property_value="[[Tasks.base]]",
    )


def test_title_body_dates_and_multiple_reminders() -> None:
    task = parse(
        """---
base: "[[Tasks.base]]"
title: Explicit title
status: To-do
priority: High
due: 2026-08-12
archived: false
reminders:
  - id: good
    type: relative
    relatedTo: due
    offset: -PT15M
  - id: bad
    type: relative
    relatedTo: nowhere
    offset: PT0M
---

Hello 🌎
"""
    )
    assert task.title == "Explicit title"
    assert task.body == "Hello 🌎"
    assert task.due == date(2026, 8, 12)
    assert task.reminders[0].type is ReminderType.RELATIVE
    assert task.invalid_reminders[0].id == "bad"


def test_filename_fallback_and_empty_body() -> None:
    task = parse("---\nbase: '[[Tasks.base]]'\n---\n", "Nested/Fallback.md")
    assert task.title == "Fallback"
    assert task.body == ""


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("plain markdown", NotTaskError),
        ("---\nbase: x", TaskParseError),
        ("---\nbase: other\n---\n", NotTaskError),
        ("---\n: broken\n---\n", TaskParseError),
    ],
)
def test_non_tasks_and_malformed_frontmatter(text: str, error: type[Exception]) -> None:
    with pytest.raises(error):
        parse(text)


def test_file_size_is_enforced(tmp_path: Path) -> None:
    path = tmp_path / "Large.md"
    path.write_text("---\nbase: '[[Tasks.base]]'\n---\nbody", encoding="utf-8")
    with pytest.raises(TaskParseError, match="exceeds"):
        parse_task_file(
            path,
            "Large.md",
            property_name="base",
            property_value="[[Tasks.base]]",
            max_file_bytes=10,
        )
