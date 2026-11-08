import json
import time
from pathlib import Path

import pytest

import healthcheck
from src.heartbeat import HEARTBEAT_FILE_NAME, HealthState, beat, heartbeat_path, report_transitions, write_heartbeat
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


def test_healthcheck_reads_the_file_the_service_writes() -> None:
    assert healthcheck.HEARTBEAT_FILE_NAME == HEARTBEAT_FILE_NAME
    assert heartbeat_path().name == HEARTBEAT_FILE_NAME


def _heartbeat(tmp_path: Path, **values: object) -> Path:
    path = tmp_path / HEARTBEAT_FILE_NAME
    path.write_text(json.dumps({"time": NOW, "postgres": True, "mqtt": True, **values}))
    return path


def test_find_problems_healthy_service_has_none(tmp_path: Path) -> None:
    assert healthcheck.find_problems(str(tmp_path), _heartbeat(tmp_path), NOW + 30) == []


@pytest.mark.parametrize(
    ("values", "now", "problem"),
    [
        ({"postgres": False}, NOW, "Postgres unreachable"),
        ({"mqtt": False}, NOW, "MQTT unreachable"),
        ({}, NOW + 181, "Heartbeat is 181 s old"),
    ],
)
def test_find_problems_reports_each_failure(
    tmp_path: Path, values: dict[str, object], now: float, problem: str
) -> None:
    assert healthcheck.find_problems(str(tmp_path), _heartbeat(tmp_path, **values), now) == [problem]


def test_find_problems_without_heartbeat_or_watch_folder(tmp_path: Path) -> None:
    problems = healthcheck.find_problems(str(tmp_path / "missing"), tmp_path / HEARTBEAT_FILE_NAME, NOW)

    assert problems[0].startswith("WATCH_FOLDER missing")
    assert problems[1] == "No readable heartbeat from the service yet"


def test_find_problems_malformed_heartbeat(tmp_path: Path) -> None:
    path = tmp_path / HEARTBEAT_FILE_NAME
    path.write_text(json.dumps(["not", "an", "object"]))

    assert healthcheck.find_problems(str(tmp_path), path, NOW) == ["Heartbeat file is malformed"]


def test_healthcheck_main_returns_1_without_heartbeat(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    monkeypatch.setenv("WATCH_FOLDER", str(tmp_path))

    assert healthcheck.main() == 1
    assert "No readable heartbeat" in capsys.readouterr().out
