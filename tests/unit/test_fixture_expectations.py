from datetime import UTC, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from tasknotes_ntfy.frontmatter import parse_task_file
from tasknotes_ntfy.reminder_time import resolve_reminder

FIXTURES = Path(__file__).parents[1] / "fixtures" / "tasks"
NY = ZoneInfo("America/New_York")


def load(name: str):
    path = FIXTURES / name
    return parse_task_file(
        path,
        f"Efforts/Tasks/{name}",
        property_name="base",
        property_value="[[Tasks.base]]",
        max_file_bytes=1_000_000,
    )


def test_fixture_expected_times() -> None:
    expected = {
        "Example task due tomorrow.md": datetime(2026, 8, 12, 11, tzinfo=UTC),
        "Example task schedued wednesday but due thursday with notifications.md": datetime(
            2026, 8, 13, 10, 45, tzinfo=UTC
        ),
        "Example task schedued.md": datetime(2026, 8, 14, 11, tzinfo=UTC),
        "Task with a due time and set amount of time.md": datetime(2026, 8, 11, 23, 27, tzinfo=UTC),
    }
    for name, instant in expected.items():
        task = load(name)
        assert resolve_reminder(task, task.reminders[0], NY, time(7)) == instant


def test_fixture_files_without_reminders_schedule_nothing() -> None:
    assert load("Example task without notifications.md").reminders == ()
    assert load("Regular Task.md").reminders == ()
