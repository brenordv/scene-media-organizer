import os
from pathlib import Path
from typing import Any

import pytest

from src import batch_processor as processor_module
from src.batch_processor import _process_batch_item

MOVIES = Path(os.environ["MOVIES_BASE_FOLDER"])
SERIES = Path(os.environ["SERIES_BASE_FOLDER"])


class _Recorder:
    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []
        self.copies: list[tuple[str, Path]] = []


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    recorded = _Recorder()
    monkeypatch.setattr(
        processor_module._work_queue_manager, "update", lambda item: recorded.updates.append(dict(item))
    )
    monkeypatch.setattr(processor_module, "check_is_file_stable", lambda path: True)

    def fake_copy(source: str, destination: Path) -> bool:
        recorded.copies.append((source, destination))
        return True

    monkeypatch.setattr(processor_module, "copy_file", fake_copy)
    return recorded


@pytest.fixture
def media_file(tmp_path: Path) -> Path:
    path = tmp_path / "Movie.2020.1080p.mkv"
    path.write_bytes(b"video")
    return path


def _item(path: Path, is_archive: bool = False, attempts: int = 1) -> dict[str, Any]:
    return {"id": "item-1", "full_path": str(path), "is_archive": is_archive, "attempts": attempts, "status": "WORKING"}


def _identify_returns(monkeypatch: pytest.MonkeyPatch, media_info: dict[str, Any] | None) -> None:
    monkeypatch.setattr(processor_module, "identify_file", lambda path: media_info)


def test_missing_file_is_marked_failed_missing(recorder: _Recorder, tmp_path: Path) -> None:
    assert _process_batch_item(_item(tmp_path / "gone.mkv")) is None
    assert recorder.updates[-1]["status"] == "FAILED_MISSING"


def test_unstable_file_is_retried_with_attempt_refunded(
    recorder: _Recorder, media_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(processor_module, "check_is_file_stable", lambda path: False)

    result = _process_batch_item(_item(media_file, attempts=2))

    assert result is not None
    assert recorder.updates[-1]["attempts"] == 1


def test_unidentified_file_is_marked_failed_id(
    recorder: _Recorder, media_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _identify_returns(monkeypatch, None)

    assert _process_batch_item(_item(media_file)) is None
    assert recorder.updates[-1]["status"] == "FAILED_ID"
    assert recorder.copies == []


def test_movie_is_copied_to_title_and_year_folder(
    recorder: _Recorder, media_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _identify_returns(monkeypatch, {"id": "cache-1", "media_type": "movie", "title": "The Matrix", "year": 1999})

    assert _process_batch_item(_item(media_file)) is None
    assert recorder.copies == [(str(media_file), MOVIES / "The-Matrix--1999")]
    assert recorder.updates[-1]["status"] == "DONE"
    assert recorder.updates[-1]["media_info_cache_id"] == "cache-1"


def test_movie_without_year_uses_title_only(
    recorder: _Recorder, media_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _identify_returns(monkeypatch, {"id": "cache-1", "media_type": "movie", "title": "The Matrix", "year": None})

    _process_batch_item(_item(media_file))

    assert recorder.copies[0][1] == MOVIES / "The-Matrix"


@pytest.mark.parametrize(("season", "folder"), [(2, "Season02"), (0, "Season00")])
def test_episode_is_copied_to_series_season_folder(
    recorder: _Recorder, media_file: Path, monkeypatch: pytest.MonkeyPatch, season: int, folder: str
) -> None:
    media_info = {"id": "cache-2", "media_type": "tv", "title": "Slow Horses", "season": season, "tmdb_id": 99}
    _identify_returns(monkeypatch, media_info)

    _process_batch_item(_item(media_file))

    assert recorder.copies[0][1] == SERIES / "Slow-Horses" / folder


@pytest.mark.parametrize(
    "media_info",
    [
        {"id": None, "media_type": "movie", "title": "A"},
        {"id": "cache-3", "media_type": "unknown", "title": "A"},
        {"id": "cache-3", "media_type": "movie", "title": None},
        {"id": "cache-3", "media_type": "movie", "title": "???"},
        {"id": "cache-3", "media_type": "tv", "title": "Show", "season": None},
    ],
)
def test_unusable_metadata_is_marked_failed_id_without_copying(
    recorder: _Recorder, media_file: Path, monkeypatch: pytest.MonkeyPatch, media_info: dict[str, Any]
) -> None:
    _identify_returns(monkeypatch, media_info)

    assert _process_batch_item(_item(media_file)) is None
    assert recorder.updates[-1]["status"] == "FAILED_ID"
    assert recorder.copies == []


def test_archive_is_decompressed_and_marked_done(
    recorder: _Recorder, media_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _identify_returns(monkeypatch, {"id": "cache-1", "media_type": "movie", "title": "A"})
    monkeypatch.setattr(processor_module, "decompress_file", lambda path: True)

    assert _process_batch_item(_item(media_file, is_archive=True)) is None
    assert recorder.updates[-1]["status"] == "DONE"


def test_archive_that_fails_to_decompress_is_retried(
    recorder: _Recorder, media_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _identify_returns(monkeypatch, {"id": "cache-1", "media_type": "movie", "title": "A"})
    monkeypatch.setattr(processor_module, "decompress_file", lambda path: False)

    assert _process_batch_item(_item(media_file, is_archive=True)) is not None


def test_failed_copy_leaves_item_unfinished(
    recorder: _Recorder, media_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _identify_returns(monkeypatch, {"id": "cache-1", "media_type": "movie", "title": "A", "year": 2001})
    monkeypatch.setattr(processor_module, "copy_file", lambda source, destination: False)

    _process_batch_item(_item(media_file))

    assert recorder.updates[-1]["status"] == "WORKING"
