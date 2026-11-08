"""Pure helpers with no configuration, service, or package dependencies.
Tests import this module directly; keep it stdlib-only and side-effect free."""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.tasks.sanitize_string_for_filename import sanitize_string_for_filename

MEDIA_TYPES = ("movie", "tv")


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


def select_new_files(files_found: Iterable[tuple[str, str]], existing_filenames: Iterable[str]) -> list[str]:
    """files_found: iterable of (full_path, filename) tuples.
    existing_filenames: iterable of filename strings already in the queue.
    Returns the full paths whose filename is not yet known."""
    existing = set(existing_filenames)
    return [full_path for full_path, filename in files_found if filename not in existing]


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
