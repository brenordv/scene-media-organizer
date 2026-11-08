from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from src.helpers import (
    Destination,
    library_root_pairs,
    locate_in_library,
    parse_work_item_row,
    partition_open_batches,
    resolve_destination,
    select_new_files,
    watch_folder_roots,
)

MOVIES = Path("/movies")
SERIES = Path("/series")
WATCH = Path("/data/watch")
WATCH_ROOTS = [WATCH, Path("/watch")]


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


def _found(relative_path: str) -> tuple[str, str, Path]:
    relative = Path(relative_path)
    return (str(WATCH / relative), relative.name, relative)


def test_select_new_files_same_name_in_another_folder_is_new() -> None:
    found = [_found("show.s01/episode.mkv"), _found("show.s02/episode.mkv")]
    known_rows = [("/data/watch/show.s01/episode.mkv", "episode.mkv")]

    new_files, fallback_rows = select_new_files(found, known_rows, WATCH_ROOTS)

    assert new_files == [str(WATCH / "show.s02/episode.mkv")]
    assert fallback_rows == 0


@pytest.mark.parametrize("stored_path", ["/watch/show/episode.mkv", "/data/watch/show/episode.mkv"])
def test_select_new_files_same_path_is_known_in_container_and_local_form(stored_path: str) -> None:
    new_files, fallback_rows = select_new_files(
        [_found("show/episode.mkv")], [(stored_path, "episode.mkv")], WATCH_ROOTS
    )

    assert new_files == []
    assert fallback_rows == 0


def test_select_new_files_row_outside_every_root_falls_back_to_the_name() -> None:
    found = [_found("show/episode.mkv"), _found("show/other.mkv")]
    known_rows = [("/old-mount/show/episode.mkv", "episode.mkv")]

    new_files, fallback_rows = select_new_files(found, known_rows, WATCH_ROOTS)

    assert new_files == [str(WATCH / "show/other.mkv")]
    assert fallback_rows == 1


def test_select_new_files_root_is_compared_by_segment() -> None:
    known_rows = [("/watch-old/show/episode.mkv", "episode.mkv")]

    new_files, fallback_rows = select_new_files([_found("show/episode.mkv")], known_rows, WATCH_ROOTS)

    assert new_files == []
    assert fallback_rows == 1


def test_select_new_files_nothing_known_returns_every_file() -> None:
    found = [_found("a.mkv"), _found("b.mkv")]

    assert select_new_files(found, [], WATCH_ROOTS) == ([str(WATCH / "a.mkv"), str(WATCH / "b.mkv")], 0)


def test_select_new_files_empty_inputs() -> None:
    assert select_new_files([], [], WATCH_ROOTS) == ([], 0)


def test_watch_folder_roots_holds_the_resolved_and_raw_folder_and_the_container_path(tmp_path: Path) -> None:
    assert watch_folder_roots(tmp_path) == [tmp_path.resolve(), tmp_path, Path("/watch")]


@pytest.fixture
def library_behind_a_symlink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(Path, "resolve", lambda self, strict=False: tmp_path / "real-disk" / self.name)
    return tmp_path / "movies"


def test_library_root_pairs_rebases_a_resolved_target_onto_the_configured_folder(
    tmp_path: Path, library_behind_a_symlink: Path
) -> None:
    stored = str(tmp_path / "real-disk" / "movies" / "Heat--1995")

    root_pairs = library_root_pairs(library_behind_a_symlink, tmp_path / "series")

    assert locate_in_library(stored, root_pairs) == library_behind_a_symlink / "Heat--1995"


def test_library_root_pairs_rebases_an_absolute_target_onto_the_configured_folder(
    tmp_path: Path, library_behind_a_symlink: Path
) -> None:
    stored = str(library_behind_a_symlink / "Heat--1995")

    root_pairs = library_root_pairs(library_behind_a_symlink, tmp_path / "series")

    assert locate_in_library(stored, root_pairs) == library_behind_a_symlink / "Heat--1995"


def test_library_root_pairs_rebases_the_absolute_target_of_a_relative_folder() -> None:
    movies = Path("relative-library/movies")
    stored = str(movies.absolute() / "Heat--1995")

    assert locate_in_library(stored, library_root_pairs(movies, Path("relative-library/series"))) == (
        movies / "Heat--1995"
    )


@pytest.mark.parametrize(
    ("stored_path", "expected"),
    [
        ("/movies/Heat--1995", "movies/Heat--1995"),
        ("/series/Slow-Horses/Season01", "series/Slow-Horses/Season01"),
        ("/series/Movies-That-Made-Us/Season01", "series/Movies-That-Made-Us/Season01"),
    ],
)
def test_locate_in_library_rebases_a_container_path_onto_its_configured_folder(
    tmp_path: Path, stored_path: str, expected: str
) -> None:
    root_pairs = library_root_pairs(tmp_path / "movies", tmp_path / "series")

    assert locate_in_library(stored_path, root_pairs) == tmp_path / expected


@pytest.mark.parametrize("relative", ["movies/Heat--1995", "series/Slow-Horses/Season01"])
def test_locate_in_library_keeps_a_path_already_under_the_configured_folder(tmp_path: Path, relative: str) -> None:
    root_pairs = library_root_pairs(tmp_path / "movies", tmp_path / "series")

    assert locate_in_library(str(tmp_path / relative), root_pairs) == tmp_path / relative


@pytest.mark.parametrize("stored_path", ["/elsewhere/Heat--1995", "/moviesfolder/Heat--1995"])
def test_locate_in_library_path_outside_every_root_returns_none(tmp_path: Path, stored_path: str) -> None:
    root_pairs = library_root_pairs(tmp_path / "movies", tmp_path / "series")

    assert locate_in_library(stored_path, root_pairs) is None


def test_parse_work_item_row_maps_every_column_including_the_content_hash() -> None:
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
        "abc123",
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
        "content_hash": "abc123",
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
