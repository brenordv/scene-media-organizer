from pathlib import Path

import pytest

from src import catch_up as catch_up_module
from src.catch_up import queue_untracked_files, run_catch_up_scan
from tests.fakes import RecordingLogger, as_traced


@pytest.fixture
def prepared(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    queued: list[str] = []
    monkeypatch.setattr(catch_up_module, "prepare_file_for_processing", queued.append)
    return queued


@pytest.fixture
def watch_folder(tmp_path: Path) -> Path:
    (tmp_path / "Show.S01").mkdir()
    (tmp_path / "Show.S01" / "episode1.mkv").write_bytes(b"1")
    (tmp_path / "Show.S01" / "episode2.mkv").write_bytes(b"2")
    (tmp_path / "movie.mkv").write_bytes(b"m")
    return tmp_path


def test_queue_untracked_files_queues_only_unknown_names(
    monkeypatch: pytest.MonkeyPatch, watch_folder: Path, prepared: list[str]
) -> None:
    monkeypatch.setattr(
        catch_up_module._work_queue_manager, "filter_only_existing_filenames", lambda names: ["episode1.mkv"]
    )

    queued = queue_untracked_files(watch_folder)

    assert queued == 2
    assert sorted(Path(path).name for path in prepared) == ["episode2.mkv", "movie.mkv"]


def test_run_catch_up_scan_logs_the_count(
    monkeypatch: pytest.MonkeyPatch, watch_folder: Path, prepared: list[str]
) -> None:
    monkeypatch.setattr(catch_up_module._work_queue_manager, "filter_only_existing_filenames", lambda names: [])
    logger = RecordingLogger()

    run_catch_up_scan(watch_folder, as_traced(logger))

    assert logger.lines == [("info", "Startup catch-up scan queued 3 file(s) that were not in the work queue.")]


def test_run_catch_up_scan_failure_is_logged_not_raised(
    monkeypatch: pytest.MonkeyPatch, watch_folder: Path, prepared: list[str]
) -> None:
    def failing_lookup(names: list[str]) -> list[str]:
        raise RuntimeError("Error checking database for filenames")

    monkeypatch.setattr(catch_up_module._work_queue_manager, "filter_only_existing_filenames", failing_lookup)
    logger = RecordingLogger()

    run_catch_up_scan(watch_folder, as_traced(logger))

    assert logger.lines[0][0] == "exception"
    assert "on_demand.py missing" in logger.lines[0][1]
    assert prepared == []
