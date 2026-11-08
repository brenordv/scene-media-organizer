from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from src.helpers import (
    Destination,
    parse_work_item_row,
    partition_open_batches,
    resolve_destination,
    select_new_files,
)

MOVIES = Path("/movies")
SERIES = Path("/series")


@pytest.mark.parametrize(
    ("media_info", "expected"),
    [
        ({"media_type": "movie", "title": "The Matrix", "year": 1999}, MOVIES / "The-Matrix--1999"),
        ({"media_type": "movie", "title": "The Matrix", "year": None}, MOVIES / "The-Matrix"),
        (
            {"media_type": "tv", "title": "Slow Horses", "season": 6, "tmdb_series_id": 1},
            SERIES / "Slow-Horses/Season06",
        ),
        ({"media_type": "tv", "title": "Doctor Who", "season": 0}, SERIES / "Doctor-Who/Season00"),
    ],
)
def test_resolve_destination_places_movies_and_episodes(media_info: dict[str, Any], expected: Path) -> None:
    assert resolve_destination(media_info, MOVIES, SERIES) == Destination(expected)


@pytest.mark.parametrize(
    ("media_info", "reason_fragment"),
    [
        ({"media_type": "unknown", "title": "A"}, "media type"),
        ({"media_type": None, "title": "A"}, "media type"),
        ({"media_type": "movie", "title": None}, "no title"),
        ({"media_type": "movie", "title": "???"}, "no character usable"),
        ({"media_type": "tv", "title": "Show", "season": None}, "no season"),
    ],
)
def test_resolve_destination_unusable_record_gives_a_reason(media_info: dict[str, Any], reason_fragment: str) -> None:
    destination = resolve_destination(media_info, MOVIES, SERIES)

    assert destination.path is None
    assert destination.reason is not None
    assert reason_fragment in destination.reason


def test_select_new_files_partial_overlap() -> None:
    files_found = [("/a/x.mkv", "x.mkv"), ("/a/y.mkv", "y.mkv")]
    assert select_new_files(files_found, ["x.mkv"]) == ["/a/y.mkv"]


def test_select_new_files_no_overlap() -> None:
    files_found = [("/a/x.mkv", "x.mkv"), ("/a/y.mkv", "y.mkv")]
    assert select_new_files(files_found, []) == ["/a/x.mkv", "/a/y.mkv"]


def test_select_new_files_all_existing() -> None:
    files_found = [("/a/x.mkv", "x.mkv"), ("/a/y.mkv", "y.mkv")]
    assert select_new_files(files_found, ["x.mkv", "y.mkv"]) == []


def test_select_new_files_empty_inputs() -> None:
    assert select_new_files([], []) == []
    assert select_new_files([], ["x.mkv"]) == []


def test_parse_work_item_row_maps_twelve_columns_including_attempts() -> None:
    row = (
        "id-1",
        "/a/x.mkv",
        "x.mkv",
        "/a",
        "/dest",
        "WORKING",
        True,
        False,
        "2026-01-01",
        "2026-01-02",
        "cache-1",
        3,
    )
    assert parse_work_item_row(row) == {
        "id": "id-1",
        "full_path": "/a/x.mkv",
        "filename": "x.mkv",
        "parent": "/a",
        "target_path": "/dest",
        "status": "WORKING",
        "is_archive": True,
        "is_main_archive_file": False,
        "created_at": "2026-01-01",
        "modified_at": "2026-01-02",
        "media_info_cache_id": "cache-1",
        "attempts": 3,
    }


def test_partition_open_batches_empty_input() -> None:
    assert partition_open_batches([]) == (None, [])


def test_partition_open_batches_single_batch() -> None:
    row = ("batch-1", datetime(2026, 1, 1, 10, 0, 0), datetime(2026, 1, 1, 10, 5, 0), 3)
    resume_row, stale_batch_ids = partition_open_batches([row])
    assert resume_row == row
    assert stale_batch_ids == []


def test_partition_open_batches_most_recent_wins() -> None:
    older = ("batch-old", datetime(2026, 1, 1, 9, 0, 0), datetime(2026, 1, 1, 9, 30, 0), 2)
    newer = ("batch-new", datetime(2026, 1, 1, 12, 0, 0), datetime(2026, 1, 1, 12, 1, 0), 5)
    resume_row, stale_batch_ids = partition_open_batches([older, newer])
    assert resume_row == newer
    assert stale_batch_ids == ["batch-old"]


def test_partition_open_batches_tie_broken_by_batch_id() -> None:
    created = datetime(2026, 1, 1, 10, 0, 0)
    row_a = ("batch-aaa", created, created, 1)
    row_b = ("batch-bbb", created, created, 1)
    resume_row, stale_batch_ids = partition_open_batches([row_a, row_b])
    assert resume_row == row_b
    assert stale_batch_ids == ["batch-aaa"]
