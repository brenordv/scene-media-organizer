import psycopg
import pytest

from src.data.activity_logger import ActivityTracker
from tests.fakes import FakeDatabase


def _activity_inserts(fake_db: FakeDatabase) -> list[str]:
    return [params[0] for query, params in fake_db.executed if query.startswith("INSERT INTO activity_tracker")]


def test_activity_tracker_writes_lines_at_or_above_its_level(fake_db: FakeDatabase) -> None:
    tracker = ActivityTracker("Test Tracker", "INFO")
    fake_db.reset()

    tracker.debug("debug line")
    tracker.info("info line")
    tracker.error("error line")

    assert _activity_inserts(fake_db) == ["info line", "error line"]


def test_activity_tracker_debug_level_writes_everything(fake_db: FakeDatabase) -> None:
    tracker = ActivityTracker("Test Tracker Debug", "DEBUG")
    fake_db.reset()

    tracker.debug("debug line")
    tracker.warning("warning line")

    assert _activity_inserts(fake_db) == ["debug line", "warning line"]


def test_activity_tracker_database_error_raises_runtime_error(fake_db: FakeDatabase) -> None:
    tracker = ActivityTracker("Test Tracker Failing", "DEBUG")
    fake_db.reset()
    fake_db.fail_with = psycopg.OperationalError("connection lost")

    with pytest.raises(RuntimeError, match="Error logging activity"):
        tracker.info("will fail")
