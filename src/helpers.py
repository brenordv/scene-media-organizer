"""Pure helpers with no configuration, service, or package dependencies.
Tests import this module directly; keep it stdlib-only and side-effect free."""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.tasks.sanitize_string_for_filename import sanitize_string_for_filename

MEDIA_TYPES = ("movie", "tv")

CONTAINER_WATCH_ROOT = Path("/watch")
CONTAINER_MOVIES_ROOT = Path("/movies")
CONTAINER_SERIES_ROOT = Path("/series")


@dataclass(frozen=True)
class Destination:
    """Where a media file belongs in the library, or why that cannot be decided.

    Attributes:
        path: The destination folder, or None when the record cannot place the file.
        reason: Why the record cannot place the file; None when ``path`` is set.
    """

    path: Path | None
    reason: str | None = None


def resolve_destination(media_info: Mapping[str, Any], movies_base: Path, series_base: Path) -> Destination:
    """Decide the library folder for an identified file.

    Movies go to ``<movies_base>/<Title>--<Year>`` (``<Title>`` alone when the year is unknown); TV episodes go to
    ``<series_base>/<Title>/Season<NN>``, with season 0 for specials.

    Args:
        media_info: The media identifier's record (``media_type``, ``title``, ``year``, ``season``).
        movies_base: The movie library root.
        series_base: The series library root.

    Returns:
        The destination, or a reason when the type is not movie/tv, the title is missing or has no character usable
        in a folder name, or a TV record has no season.
    """
    media_type = media_info.get("media_type")
    if media_type not in MEDIA_TYPES:
        return Destination(None, f"media type is {media_type!r}, not movie or tv")

    title = media_info.get("title")
    if not title:
        return Destination(None, "the record has no title")

    folder_name = sanitize_string_for_filename(str(title))
    if not folder_name:
        return Destination(None, f"title {title!r} has no character usable in a folder name")

    if media_type == "movie":
        year = media_info.get("year")
        return Destination(movies_base / (f"{folder_name}--{year}" if year is not None else folder_name))

    season = media_info.get("season")
    if season is None:
        return Destination(None, "the TV record has no season number")
    return Destination(series_base / folder_name / f"Season{int(season):02d}")


def watch_folder_roots(watch_folder: Path) -> list[Path]:
    """Return the roots a stored ``full_path`` can start with: the folder, resolved and raw, and its container path.

    Args:
        watch_folder: The configured watch folder.

    Returns:
        The roots, in the order to try them.
    """
    return [watch_folder.resolve(), watch_folder, CONTAINER_WATCH_ROOT]


def library_root_pairs(movies_base: Path, series_base: Path) -> list[tuple[Path, Path]]:
    """Pair each root a stored ``target_path`` can start with and the library folder configured for it.

    Args:
        movies_base: The configured movie library root.
        series_base: The configured series library root.

    Returns:
        (stored root, configured base) pairs: each base absolute, resolved, and as its container path.
    """
    return [
        (movies_base.absolute(), movies_base),
        (movies_base.resolve(), movies_base),
        (CONTAINER_MOVIES_ROOT, movies_base),
        (series_base.absolute(), series_base),
        (series_base.resolve(), series_base),
        (CONTAINER_SERIES_ROOT, series_base),
    ]


def locate_in_library(stored_path: str, root_pairs: Iterable[tuple[Path, Path]]) -> Path | None:
    """Re-base a path stored in the work queue onto the folder configured for its root.

    Roots are compared by path segment, so ``/series/Movies-That-Made-Us`` fits ``/series`` and never ``/movies``.

    Args:
        stored_path: The path as saved, in container or local form.
        root_pairs: (stored root, configured base) pairs, tried in order.

    Returns:
        The path under the configured base, or None when it fits no stored root.
    """
    path = Path(stored_path)
    for stored_root, configured_base in root_pairs:
        if path.is_relative_to(stored_root):
            return configured_base / path.relative_to(stored_root)
    return None


def select_new_files(
    found: Iterable[tuple[str, str, Path]],
    known_rows: Iterable[tuple[str, str]],
    watch_roots: Sequence[Path],
) -> tuple[list[str], int]:
    """Pick the files in the watch folder that the work queue does not track yet.

    Files are compared by their path relative to the watch folder, because a stored ``full_path`` can be in container
    or local form. A known row that fits no root (the folder was mounted elsewhere, say) falls back to matching by file
    name, which keeps a moved folder from being queued all over again.

    Args:
        found: (full_path, filename, path relative to the watch folder) of each file on disk.
        known_rows: (full_path, filename) of the queue rows that could match.
        watch_roots: The roots a stored ``full_path`` can start with.

    Returns:
        The full paths of the new files, and how many known rows fell back to matching by name.
    """
    known_paths: set[Path] = set()
    fallback_names: set[str] = set()
    fallback_rows = 0
    for full_path, filename in known_rows:
        path = Path(full_path)
        root = next((root for root in watch_roots if path.is_relative_to(root)), None)
        if root is None:
            fallback_names.add(filename)
            fallback_rows += 1
        else:
            known_paths.add(path.relative_to(root))

    new_files = [
        full_path
        for full_path, filename, relative_path in found
        if relative_path not in known_paths and filename not in fallback_names
    ]
    return new_files, fallback_rows


WORK_ITEM_COLUMNS = (
    "id",
    "full_path",
    "filename",
    "parent",
    "target_path",
    "status",
    "is_archive",
    "is_main_archive_file",
    "created_at",
    "modified_at",
    "media_info_cache_id",
    "attempts",
    "content_hash",
)
"""The ``work_queue`` columns every work-item query selects, in this order."""


def parse_work_item_row(row: Sequence[Any]) -> dict[str, Any]:
    """Map a row selected with ``WORK_ITEM_COLUMNS`` to a work item keyed by column name."""
    return dict(zip(WORK_ITEM_COLUMNS, row, strict=True))


def partition_open_batches(
    rows: Iterable[tuple[Any, ...]],
) -> tuple[tuple[Any, ...] | None, list[Any]]:
    """Split the open batches into the one to resume and the stale rest.

    Args:
        rows: Tuples of (batch_id, created_at, last_modified, item_count)
            for batches currently in progress.

    Returns:
        A pair (resume_row, stale_batch_ids): resume_row is the most recently
        created batch, ties broken by str(batch_id) so the choice is
        deterministic, or None when rows is empty; stale_batch_ids holds every
        other batch_id, to be closed without resuming.
    """
    ordered = sorted(rows, key=lambda row: (row[1], str(row[0])), reverse=True)
    if not ordered:
        return None, []
    return ordered[0], [row[0] for row in ordered[1:]]
