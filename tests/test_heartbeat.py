import json
import time
from pathlib import Path

from src.heartbeat import HEARTBEAT_FILE_NAME, HealthState, beat, report_transitions, write_heartbeat
from tests.fakes import RecordingLogger, as_traced

NOW = time.time()


def test_write_heartbeat_replaces_the_file_with_the_state(tmp_path: Path) -> None:
    path = tmp_path / HEARTBEAT_FILE_NAME
    path.write_text("old")

    write_heartbeat(HealthState(postgres=True, mqtt=False), path, NOW)

    assert json.loads(path.read_text()) == {"time": NOW, "postgres": True, "mqtt": False}
    assert not path.with_name(f"{path.name}.tmp").exists()


def test_report_transitions_logs_only_changes() -> None:
    logger = RecordingLogger()
    healthy = HealthState(postgres=True, mqtt=True)
    db_down = HealthState(postgres=False, mqtt=True)

    report_transitions(None, healthy, as_traced(logger))
    report_transitions(healthy, healthy, as_traced(logger))
    report_transitions(healthy, db_down, as_traced(logger))
    report_transitions(db_down, db_down, as_traced(logger))
    report_transitions(db_down, healthy, as_traced(logger))

    assert logger.lines == [
        ("warning", "Health check: Postgres is unreachable."),
        ("info", "Health check: Postgres is reachable again."),
    ]


def test_beat_writes_the_probed_state(tmp_path: Path) -> None:
    path = tmp_path / HEARTBEAT_FILE_NAME

    state = beat(None, lambda: False, lambda: True, path, NOW, as_traced(RecordingLogger()))

    assert state == HealthState(postgres=False, mqtt=True)
    assert json.loads(path.read_text())["postgres"] is False


def test_beat_write_failure_is_logged_and_the_state_still_returned(tmp_path: Path) -> None:
    logger = RecordingLogger()
    missing_folder = tmp_path / "missing" / HEARTBEAT_FILE_NAME

    state = beat(None, lambda: True, lambda: True, missing_folder, NOW, as_traced(logger))

    assert state == HealthState(postgres=True, mqtt=True)
    assert logger.lines[-1][0] == "warning"
    assert "heartbeat" in logger.lines[-1][1]
