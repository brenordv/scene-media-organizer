"""Queue files that are in the watch folder but not in the work queue, such as files that arrived while the service
was down."""

from pathlib import Path
from typing import NamedTuple

from opentelemetry import trace
from simple_log_factory_ext_otel import TracedLogger

from src.data.activity_logger import ActivityTracker
from src.data.work_queue_manager import WorkQueueManager
from src.helpers import select_new_files, watch_folder_roots
from src.queue_worker import prepare_file_for_processing

_work_queue_manager = WorkQueueManager()


class ScanCounts(NamedTuple):
    """The outcome of a watch folder scan.

    Attributes:
        found: Files on disk.
        known: Files the work queue already tracks.
        fallback_rows: Queue rows whose path fits no watch root, so they were matched by file name.
        new: Files added to the work queue.
        pending: New files queued as work to do.
        ignored: New files queued as ``IGNORED``.
    """

    found: int
    known: int
    fallback_rows: int
    new: int
    pending: int
    ignored: int


def run_catch_up_scan(watch_folder: Path, logger: TracedLogger) -> None:
    """Queue the files that arrived while the service was not watching, logging the outcome once.

    Best-effort: a failure is logged and the service goes on watching, because ``on_demand.py missing`` queues the
    same files on request.

    Args:
        watch_folder: The folder to walk.
        logger: An OTEL-only logger: the scan's likely failure is the database, which ActivityTracker writes to.
    """
    with logger.span("catch_up_scan"):
        try:
            counts = queue_untracked_files(watch_folder)
        except Exception:
            logger.exception("Startup catch-up scan failed; run `on_demand.py missing` to queue untracked files.")
            return
        log_scan_counts(counts, logger)


def queue_untracked_files(watch_folder: Path) -> ScanCounts:
    """Queue every file under ``watch_folder`` that the work queue does not track yet.

    Files are matched by their path inside the folder, so a file that shares its name with a tracked file elsewhere is
    new; each new file goes through the same checks as a file the watcher reports. A symlink counts as the file it
    points to, which is what the queue stores.

    Args:
        watch_folder: The folder to walk, recursively.

    Returns:
        The scan's counts.
    """
    span = trace.get_current_span()
    resolved_root = watch_folder.resolve()
    found = {
        _describe_found_file(path, watch_folder, resolved_root) for path in watch_folder.rglob("*") if path.is_file()
    }
    known_rows = _work_queue_manager.get_known_paths([name for _, name, _ in found])
    new_files, fallback_rows = select_new_files(found, known_rows, watch_folder_roots(watch_folder))

    statuses = [prepare_file_for_processing(new_file) for new_file in new_files]

    counts = ScanCounts(
        found=len(found),
        known=len(found) - len(new_files),
        fallback_rows=fallback_rows,
        new=len(new_files),
        pending=statuses.count("PENDING"),
        ignored=statuses.count("IGNORED"),
    )
    if span.is_recording():
        span.set_attributes({f"catch_up.{name}": value for name, value in counts._asdict().items()})
    return counts


def _describe_found_file(path: Path, watch_folder: Path, resolved_root: Path) -> tuple[str, str, Path]:
    """Describe a file on disk the way the work queue would store it.

    Args:
        path: The file, as the walk found it.
        watch_folder: The folder that was walked.
        resolved_root: ``watch_folder``, resolved.

    Returns:
        The resolved full path, the resolved file name, and the path relative to the watch folder. For a symlink that
        points outside the folder, the relative path is the link's own.
    """
    resolved = path.resolve()
    if resolved.is_relative_to(resolved_root):
        return str(resolved), resolved.name, resolved.relative_to(resolved_root)
    return str(resolved), resolved.name, path.relative_to(watch_folder)


def log_scan_counts(counts: ScanCounts, logger: TracedLogger | ActivityTracker) -> None:
    """Log what a scan found, with a warning when queue rows had to be matched by file name.

    Args:
        counts: The scan's counts.
        logger: Where to log.
    """
    logger.info(
        f"Watch folder scan: {counts.found} file(s) found, {counts.known} already known, {counts.new} new "
        f"({counts.pending} to process, {counts.ignored} ignored)."
    )
    if counts.fallback_rows:
        logger.warning(
            f"{counts.fallback_rows} work queue row(s) have a path outside the watch folder and were matched by file "
            "name instead. A different file with one of those names is not queued."
        )
