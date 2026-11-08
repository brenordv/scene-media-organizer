import psycopg
import pytest

from src.data.activity_logger import ActivityTracker, database_log_level
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


@pytest.mark.parametrize(
    ("knob", "tracker_level", "expected"),
    [(None, "DEBUG", "DEBUG"), ("INFO", "DEBUG", "INFO"), ("info", "WARNING", "WARNING"), ("ERROR", "INFO", "ERROR")],
)
def test_database_log_level_is_the_higher_of_knob_and_tracker(
    monkeypatch: pytest.MonkeyPatch, knob: str | None, tracker_level: str, expected: str
) -> None:
    if knob is None:
        monkeypatch.delenv("ACTIVITY_DB_LOG_LEVEL", raising=False)
    else:
        monkeypatch.setenv("ACTIVITY_DB_LOG_LEVEL", knob)

    assert database_log_level(tracker_level) == expected


def test_database_log_level_unknown_knob_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACTIVITY_DB_LOG_LEVEL", "VERBOSE")

    with pytest.raises(ValueError, match="ACTIVITY_DB_LOG_LEVEL"):
        database_log_level("DEBUG")


def test_activity_tracker_knob_stops_debug_rows(monkeypatch: pytest.MonkeyPatch, fake_db: FakeDatabase) -> None:
    monkeypatch.setenv("ACTIVITY_DB_LOG_LEVEL", "INFO")
    tracker = ActivityTracker("Test Tracker Knob", "DEBUG")
    fake_db.reset()

    tracker.debug("debug line")
    tracker.info("info line")

    assert _activity_inserts(fake_db) == ["info line"]


def test_activity_tracker_database_error_raises_runtime_error(fake_db: FakeDatabase) -> None:
    tracker = ActivityTracker("Test Tracker Failing", "DEBUG")
    fake_db.reset()
    fake_db.fail_with = psycopg.OperationalError("connection lost")

    with pytest.raises(RuntimeError, match="Error logging activity"):
        tracker.info("will fail")
