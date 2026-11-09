from pathlib import Path

import pytest

from src import catch_up as catch_up_module
from src.catch_up import ScanCounts, log_scan_counts, queue_untracked_files, run_catch_up_scan
from tests.fakes import RecordingLogger, RecordingSpan, as_traced


@pytest.fixture
def prepared(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    queued: list[str] = []

    def fake_prepare(filename: str) -> str:
        queued.append(filename)
        return "PENDING"

    monkeypatch.setattr(catch_up_module, "prepare_file_for_processing", fake_prepare)
    return queued


@pytest.fixture
def watch_folder(tmp_path: Path) -> Path:
    (tmp_path / "Show.S01").mkdir()
    (tmp_path / "Show.S01" / "episode1.mkv").write_bytes(b"1")
    (tmp_path / "Show.S01" / "episode2.mkv").write_bytes(b"2")
    (tmp_path / "movie.mkv").write_bytes(b"m")
    return tmp_path


def _known_rows(monkeypatch: pytest.MonkeyPatch, rows: list[tuple[str, str]]) -> None:
    def rows_with_those_names(names: list[str]) -> list[tuple[str, str]]:
        return [row for row in rows if row[1] in names]

    monkeypatch.setattr(catch_up_module._work_queue_manager, "get_known_paths", rows_with_those_names)


def test_queue_untracked_files_queues_only_paths_the_queue_does_not_track(
    monkeypatch: pytest.MonkeyPatch, watch_folder: Path, prepared: list[str]
) -> None:
    _known_rows(monkeypatch, [(str(watch_folder / "Show.S01" / "episode1.mkv"), "episode1.mkv")])

    counts = queue_untracked_files(watch_folder)

    assert counts == ScanCounts(found=3, known=1, fallback_rows=0, new=2, pending=2, ignored=0)
    assert sorted(Path(path).name for path in prepared) == ["episode2.mkv", "movie.mkv"]


def test_queue_untracked_files_queues_a_name_the_queue_only_knows_in_another_folder(
    monkeypatch: pytest.MonkeyPatch, watch_folder: Path, prepared: list[str]
) -> None:
    (watch_folder / "Show.S02").mkdir()
    same_name = watch_folder / "Show.S02" / "episode1.mkv"
    same_name.write_bytes(b"different content")
    _known_rows(
        monkeypatch,
        [
            ("/watch/Show.S01/episode1.mkv", "episode1.mkv"),
            ("/watch/Show.S01/episode2.mkv", "episode2.mkv"),
            ("/watch/movie.mkv", "movie.mkv"),
        ],
    )

    counts = queue_untracked_files(watch_folder)

    assert prepared == [str(same_name.resolve())]
    assert counts == ScanCounts(found=4, known=3, fallback_rows=0, new=1, pending=1, ignored=0)


def test_queue_untracked_files_counts_pending_and_ignored_separately(
    monkeypatch: pytest.MonkeyPatch, watch_folder: Path
) -> None:
    _known_rows(monkeypatch, [])
    statuses = iter(["PENDING", "IGNORED", "IGNORED"])
    monkeypatch.setattr(catch_up_module, "prepare_file_for_processing", lambda filename: next(statuses))

    counts = queue_untracked_files(watch_folder)

    assert counts == ScanCounts(found=3, known=0, fallback_rows=0, new=3, pending=1, ignored=2)


def test_queue_untracked_files_row_outside_the_watch_folder_matches_by_name(
    monkeypatch: pytest.MonkeyPatch, watch_folder: Path, prepared: list[str]
) -> None:
    _known_rows(monkeypatch, [("/old-mount/Show.S01/episode1.mkv", "episode1.mkv")])

    counts = queue_untracked_files(watch_folder)

    assert counts.fallback_rows == 1
    assert sorted(Path(path).name for path in prepared) == ["episode2.mkv", "movie.mkv"]


def test_queue_untracked_files_file_resolving_outside_the_folder_does_not_raise(
    monkeypatch: pytest.MonkeyPatch, watch_folder: Path, tmp_path_factory: pytest.TempPathFactory, prepared: list[str]
) -> None:
    outside = tmp_path_factory.mktemp("outside")
    _known_rows(monkeypatch, [])
    monkeypatch.setattr(Path, "resolve", lambda self, strict=False: outside / self.name)

    counts = queue_untracked_files(watch_folder)

    assert counts.new == 3
    assert str(outside / "movie.mkv") in prepared


def _link_inside_the_folder(monkeypatch: pytest.MonkeyPatch, link: Path, target: Path) -> None:
    monkeypatch.setattr(Path, "resolve", lambda self, strict=False: target if self == link else self)


def test_queue_untracked_files_symlink_to_a_tracked_file_in_the_folder_is_known(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, prepared: list[str]
) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    link = tmp_path / "a" / "x.mkv"
    target = tmp_path / "b" / "real.mkv"
    link.write_bytes(b"1")
    target.write_bytes(b"1")
    _link_inside_the_folder(monkeypatch, link, target)
    _known_rows(monkeypatch, [(str(target), "real.mkv")])

    counts = queue_untracked_files(tmp_path)

    assert prepared == []
    assert counts == ScanCounts(found=1, known=1, fallback_rows=0, new=0, pending=0, ignored=0)


def test_queue_untracked_files_symlink_and_its_target_are_queued_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, prepared: list[str]
) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    link = tmp_path / "a" / "x.mkv"
    target = tmp_path / "b" / "real.mkv"
    link.write_bytes(b"1")
    target.write_bytes(b"1")
    _link_inside_the_folder(monkeypatch, link, target)
    _known_rows(monkeypatch, [])

    counts = queue_untracked_files(tmp_path)

    assert prepared == [str(target)]
    assert counts.new == 1


def test_queue_untracked_files_records_the_counts_on_the_span(
    monkeypatch: pytest.MonkeyPatch, watch_folder: Path, prepared: list[str], recording_span: RecordingSpan
) -> None:
    _known_rows(monkeypatch, [])

    queue_untracked_files(watch_folder)

    assert recording_span.attributes == {
        "catch_up.found": 3,
        "catch_up.known": 0,
        "catch_up.fallback_rows": 0,
        "catch_up.new": 3,
        "catch_up.pending": 3,
        "catch_up.ignored": 0,
    }


def test_log_scan_counts_names_every_count() -> None:
    logger = RecordingLogger()

    log_scan_counts(ScanCounts(found=9, known=5, fallback_rows=0, new=4, pending=3, ignored=1), as_traced(logger))

    assert logger.lines == [
        ("info", "Watch folder scan: 9 file(s) found, 5 already known, 4 new (3 to process, 1 ignored).")
    ]


def test_log_scan_counts_warns_about_rows_matched_by_name() -> None:
    logger = RecordingLogger()

    log_scan_counts(ScanCounts(found=1, known=1, fallback_rows=7, new=0, pending=0, ignored=0), as_traced(logger))

    assert logger.lines[1][0] == "warning"
    assert logger.lines[1][1].startswith("7 work queue row(s) have a path outside the watch folder")


def test_run_catch_up_scan_logs_the_counts(
    monkeypatch: pytest.MonkeyPatch, watch_folder: Path, prepared: list[str]
) -> None:
    _known_rows(monkeypatch, [])
    logger = RecordingLogger()

    run_catch_up_scan(watch_folder, as_traced(logger))

    assert logger.lines == [
        ("info", "Watch folder scan: 3 file(s) found, 0 already known, 3 new (3 to process, 0 ignored).")
    ]


def test_run_catch_up_scan_failure_is_logged_not_raised(
    monkeypatch: pytest.MonkeyPatch, watch_folder: Path, prepared: list[str]
) -> None:
    def failing_lookup(names: list[str]) -> list[tuple[str, str]]:
        raise RuntimeError("Error checking database for filenames")

    monkeypatch.setattr(catch_up_module._work_queue_manager, "get_known_paths", failing_lookup)
    logger = RecordingLogger()

    run_catch_up_scan(watch_folder, as_traced(logger))

    assert logger.lines[0][0] == "exception"
    assert "on_demand.py missing" in logger.lines[0][1]
    assert prepared == []
