import json
import time
from pathlib import Path

import pytest

import healthcheck
from src.heartbeat import HEARTBEAT_FILE_NAME, heartbeat_path

NOW = time.time()


def _heartbeat(tmp_path: Path, **values: object) -> Path:
    path = tmp_path / HEARTBEAT_FILE_NAME
    path.write_text(json.dumps({"time": NOW, "postgres": True, "mqtt": True, **values}))
    return path


def test_healthcheck_reads_the_file_the_service_writes() -> None:
    assert healthcheck.HEARTBEAT_FILE_NAME == HEARTBEAT_FILE_NAME
    assert heartbeat_path().name == HEARTBEAT_FILE_NAME


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


def test_find_problems_truncated_heartbeat_counts_as_unreadable(tmp_path: Path) -> None:
    path = tmp_path / HEARTBEAT_FILE_NAME
    path.write_text("{")

    assert healthcheck.find_problems(str(tmp_path), path, NOW) == ["No readable heartbeat from the service yet"]


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


def test_healthcheck_main_returns_0_when_healthy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    monkeypatch.setenv("WATCH_FOLDER", str(tmp_path))
    path = tmp_path / HEARTBEAT_FILE_NAME
    path.write_text(json.dumps({"time": time.time(), "postgres": True, "mqtt": True}))

    assert healthcheck.main() == 0
    assert capsys.readouterr().out.strip() == "OK"
