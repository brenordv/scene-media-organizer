"""Re-identify filed media and report the files the media identifier would now file elsewhere.

Media filed from media identifier 1.x answers can be wrong (TV filed as a movie, wrong episode data). The report only
reads: nothing is moved and the work queue is not changed.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from opentelemetry import trace

from src.helpers import resolve_destination
from src.tasks.identify_file import IdentifierError, IdentifierUnavailableError
from src.utils import get_otel_log_handler

SEASON_FOLDER_PREFIX = "Season"

Identify = Callable[[str], dict[str, Any] | None]

_logger = get_otel_log_handler("Recheck", unique_handler_types=True)


@dataclass(frozen=True)
class RecheckChange:
    """A filed file the identifier would now place differently.

    Attributes:
        filename: The file's name.
        filed_as: Where it was filed: ``Title--Year`` for a movie, ``Title/SeasonNN`` for an episode.
        now: Where it would be filed today, or why it could not be placed.
    """

    filename: str
    filed_as: str
    now: str


@dataclass
class RecheckReport:
    """The outcome of a recheck run.

    Attributes:
        unchanged: How many files the identifier would still file in the same place.
        changed: Files it would file elsewhere.
        not_identified: Names of files it no longer recognizes as a movie or episode (HTTP 204).
        errors: Names of files whose identification failed.
        stopped_early: The identifier became unavailable, so the remaining files were not checked.
    """

    unchanged: int = 0
    changed: list[RecheckChange] = field(default_factory=list)
    not_identified: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    stopped_early: bool = False


def library_location(folder: str | Path) -> str:
    """Return the part of a destination folder that identifies the media.

    Args:
        folder: A destination folder, as recorded in the queue (container or local, POSIX or Windows style).

    Returns:
        ``Title--Year`` (or ``Title``) for a movie folder, ``Title/SeasonNN`` for an episode folder.
    """
    text = str(folder)
    path = PureWindowsPath(text) if "\\" in text else PurePosixPath(text)
    parts = path.parts
    if len(parts) >= 2 and parts[-1].startswith(SEASON_FOLDER_PREFIX):
        return f"{parts[-2]}/{parts[-1]}"
    return parts[-1] if parts else ""


@_logger.trace("recheck")
def recheck_filed_items(
    items: Iterable[dict[str, Any]], identify: Identify, movies_base: Path, series_base: Path
) -> RecheckReport:
    """Identify each filed item again and compare where it would be filed with where it was filed.

    Args:
        items: Work items with ``filename`` and ``target_path``.
        identify: Identifies a file name, as ``identify_file`` does.
        movies_base: The movie library root.
        series_base: The series library root.

    Returns:
        The report; when the identifier becomes unavailable, it covers the items checked so far.
    """
    span = trace.get_current_span()
    report = RecheckReport()

    for item in items:
        filename = item["filename"]
        filed_as = library_location(item["target_path"])
        try:
            media_info = identify(filename)
        except IdentifierUnavailableError:
            report.stopped_early = True
            break
        except IdentifierError:
            report.errors.append(filename)
            continue

        if media_info is None:
            report.not_identified.append(filename)
            continue

        destination = resolve_destination(media_info, movies_base, series_base)
        now = (
            library_location(destination.path) if destination.path is not None else f"unplaceable: {destination.reason}"
        )
        if now == filed_as:
            report.unchanged += 1
        else:
            report.changed.append(RecheckChange(filename, filed_as, now))

    if span.is_recording():
        span.set_attributes(
            {
                "recheck.unchanged": report.unchanged,
                "recheck.changed": len(report.changed),
                "recheck.not_identified": len(report.not_identified),
                "recheck.errors": len(report.errors),
                "recheck.stopped_early": report.stopped_early,
            }
        )
    _logger.info(
        f"Recheck: {report.unchanged} unchanged, {len(report.changed)} changed, "
        f"{len(report.not_identified)} not identified, {len(report.errors)} failed"
        + (" (stopped early: the media identifier is unavailable)" if report.stopped_early else "")
    )
    return report


def format_report(report: RecheckReport) -> str:
    """Render the report for the console."""
    lines = [
        f"Unchanged: {report.unchanged}",
        f"Changed: {len(report.changed)}",
        f"Not identified any more: {len(report.not_identified)}",
        f"Identification failed: {len(report.errors)}",
    ]
    if report.stopped_early:
        lines.append("Stopped early: the media identifier is unavailable; run the command again later.")
    for change in report.changed:
        lines.append(f"  {change.filename}: {change.filed_as} -> {change.now}")
    for filename in report.not_identified:
        lines.append(f"  {filename}: no longer identified as a movie or episode")
    for filename in report.errors:
        lines.append(f"  {filename}: identification failed")
    return "\n".join(lines)
