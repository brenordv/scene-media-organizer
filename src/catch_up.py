"""Queue files that are in the watch folder but not in the work queue, such as files that arrived while the service
was down."""

from pathlib import Path

from opentelemetry import trace
from simple_log_factory_ext_otel import TracedLogger

from src.data.work_queue_manager import WorkQueueManager
from src.helpers import select_new_files
from src.queue_worker import prepare_file_for_processing

_work_queue_manager = WorkQueueManager()


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
            queued = queue_untracked_files(watch_folder)
        except Exception:
            logger.exception("Startup catch-up scan failed; run `on_demand.py missing` to queue untracked files.")
            return
        logger.info(f"Startup catch-up scan queued {queued} file(s) that were not in the work queue.")


def queue_untracked_files(watch_folder: Path) -> int:
    """Queue every file under ``watch_folder`` whose name the work queue has never seen.

    Files are matched by name, as the queue tracks them; each new file goes through the same checks as a file the
    watcher reports.

    Args:
        watch_folder: The folder to walk, recursively.

    Returns:
        How many files were queued.
    """
    span = trace.get_current_span()
    files_found = {(str(path.resolve()), path.name) for path in watch_folder.rglob("*") if path.is_file()}
    existing = _work_queue_manager.filter_only_existing_filenames([name for _, name in files_found])
    new_files = select_new_files(files_found, existing)

    for new_file in new_files:
        prepare_file_for_processing(new_file)

    if span.is_recording():
        span.set_attributes({"catch_up.files_found": len(files_found), "catch_up.files_queued": len(new_files)})
    return len(new_files)
