"""Save the content hash of finished work items that have none, so a later copy of their files counts as a duplicate.

Each hash is saved as soon as it is known, so a run that stops keeps its progress.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opentelemetry import trace

from src.helpers import locate_in_library
from src.utils import _sha256, get_otel_log_handler

UNREADABLE_EXIT_CODE = 1
INTERRUPTED_EXIT_CODE = 130

RootPairs = Sequence[tuple[Path, Path]]
SaveItem = Callable[[dict[str, Any]], None]

_logger = get_otel_log_handler("Backfill", unique_handler_types=True)


@dataclass
class BackfillReport:
    """The outcome of a backfill run.

    Attributes:
        hashed: Items whose hash was saved.
        gone: Items with no file left to hash: every place checked is empty or holds nothing.
        unreadable: Items with no hash because a place could not be read, or its stored path fits no configured root.
    """

    hashed: int = 0
    gone: int = 0
    unreadable: int = 0


def _candidates(item: dict[str, Any], library_roots: RootPairs, watch_roots: RootPairs) -> list[Path | None]:
    """List the places that may hold an item's content, best first.

    The library copy comes first: an old ``full_path`` may no longer hold what was filed.

    Args:
        item: A work item with ``target_path``, ``filename`` and ``full_path``.
        library_roots: (stored root, configured base) pairs for ``target_path``.
        watch_roots: (stored root, configured base) pairs for ``full_path``.

    Returns:
        The paths to try; None stands for a stored path that fits no root.
    """
    candidates: list[Path | None] = []
    if item["target_path"] is not None:
        folder = locate_in_library(item["target_path"], library_roots)
        candidates.append(None if folder is None else folder / item["filename"])
    candidates.append(locate_in_library(item["full_path"], watch_roots))
    return candidates


def _hash_item(item: dict[str, Any], library_roots: RootPairs, watch_roots: RootPairs) -> tuple[str | None, bool]:
    """Hash the first place that holds the item's content.

    A zero-byte file has no content to compare, so it is skipped without being opened.

    Args:
        item: A work item with ``id``, ``target_path``, ``filename`` and ``full_path``.
        library_roots: (stored root, configured base) pairs for ``target_path``.
        watch_roots: (stored root, configured base) pairs for ``full_path``.

    Returns:
        The hash, or None when no place gave one, and whether a place on the way could not be checked.
    """
    could_not_check = False
    for candidate in _candidates(item, library_roots, watch_roots):
        if candidate is None:
            could_not_check = True
            continue
        try:
            if candidate.stat().st_size > 0:
                return _sha256(candidate), could_not_check
        except FileNotFoundError, NotADirectoryError:
            continue
        except OSError as e:
            _logger.warning(f"Could not read [{candidate}] for item [{item['id']}]: {e}")
            could_not_check = True
    return None, could_not_check


@_logger.trace("backfill_hashes")
def backfill_hashes(
    items: Sequence[dict[str, Any]], save: SaveItem, library_roots: RootPairs, watch_roots: RootPairs
) -> BackfillReport:
    """Hash each item's file and save the result, printing one line per item.

    Args:
        items: Work items without a hash.
        save: Writes the fields of a partial work item (``id`` and ``content_hash``).
        library_roots: (stored root, configured base) pairs for ``target_path``.
        watch_roots: (stored root, configured base) pairs for ``full_path``.

    Returns:
        The report.
    """
    span = trace.get_current_span()
    report = BackfillReport()

    for position, item in enumerate(items, start=1):
        content_hash, could_not_check = _hash_item(item, library_roots, watch_roots)
        if content_hash is not None:
            save({"id": item["id"], "content_hash": content_hash})
            report.hashed += 1
            outcome = "hashed"
        elif could_not_check:
            report.unreadable += 1
            outcome = "unreadable"
        else:
            report.gone += 1
            outcome = "gone"
        print(f"[{position}/{len(items)}] {item['filename']}: {outcome}")

    if span.is_recording():
        span.set_attributes(
            {
                "backfill.hashed": report.hashed,
                "backfill.gone": report.gone,
                "backfill.unreadable": report.unreadable,
            }
        )
    _logger.info(f"Backfill: {report.hashed} hashed, {report.gone} gone, {report.unreadable} unreadable")
    return report


def run_backfill(
    items: Sequence[dict[str, Any]], save: SaveItem, library_roots: RootPairs, watch_roots: RootPairs
) -> int:
    """Backfill the items, print the final counts, and return the command's exit code.

    Args:
        items: Work items without a hash.
        save: Writes the fields of a partial work item (``id`` and ``content_hash``).
        library_roots: (stored root, configured base) pairs for ``target_path``.
        watch_roots: (stored root, configured base) pairs for ``full_path``.

    Returns:
        0 when every item was hashed or is gone, ``UNREADABLE_EXIT_CODE`` when an item could not be read,
        ``INTERRUPTED_EXIT_CODE`` when the operator stopped the run.
    """
    try:
        report = backfill_hashes(items, save, library_roots, watch_roots)
    except KeyboardInterrupt:
        print("\nInterrupted. The hashes saved so far are kept; run the command again to continue.")
        return INTERRUPTED_EXIT_CODE
    print(f"Hashed: {report.hashed}\nGone: {report.gone}\nUnreadable: {report.unreadable}")
    return UNREADABLE_EXIT_CODE if report.unreadable else 0
