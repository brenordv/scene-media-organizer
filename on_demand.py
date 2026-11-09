import argparse
import os
import sys
from pathlib import Path

from src.config import add_config_arguments, apply_config, parse_command_line, require_env


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run scene-media-organizer maintenance commands.")
    parser.add_argument(
        "command",
        choices=["batch", "missing", "recheck", "backfill-hashes"],
        help="batch: process all pending items. "
        "missing: queue files present on disk but absent from the queue, then process. "
        "recheck: identify filed media again and report files that would now be filed elsewhere (read-only). "
        "backfill-hashes: save the content hash of finished items that have none, so their files count as duplicates.",
    )
    parser.add_argument(
        "-y", "--yes", action="store_true", help="Skip the confirmation prompt (for non-interactive runs)."
    )
    add_config_arguments(parser)
    return parse_command_line(parser, argv)


_args = _parse_args(sys.argv[1:])
apply_config(_args)
require_env("WATCH_FOLDER", "MOVIES_BASE_FOLDER", "SERIES_BASE_FOLDER", "OTEL_EXPORTER_OTLP_ENDPOINT")

# Startup logs through the OTEL-only TracedLogger: ActivityTracker writes to a
# database that is not reachable, or not migrated, yet.
from src.startup import prepare_services
from src.utils import flush_all_otel_loggers, get_otel_log_handler

prepare_services(get_otel_log_handler("On Demand Startup", unique_handler_types=True))

from src.data.db import init_pool, shutdown_pool

init_pool()

# Imports below this line intentionally run after the pool exists, because
# these modules build repositories at import time.
from src.backfill import run_backfill
from src.batch_processor import process_batch
from src.catch_up import log_scan_counts, queue_untracked_files
from src.data.activity_logger import ActivityTracker
from src.data.work_queue_manager import WorkQueueManager
from src.helpers import library_root_pairs, watch_folder_roots
from src.recheck import run_recheck
from src.tasks.identify_file import identify_file

_work_queue_manager = WorkQueueManager()
_watch_folder = os.environ["WATCH_FOLDER"]
_movies_base_folder = os.environ["MOVIES_BASE_FOLDER"]
_series_base_folder = os.environ["SERIES_BASE_FOLDER"]
_activity_tracker = ActivityTracker("On Demand")

_run_started = False


def on_demand_batch() -> None:
    global _run_started
    _run_started = True

    tag = "[BATCH]"

    resume_info = _work_queue_manager.find_resumable_batch()
    if resume_info is not None:
        _activity_tracker.info(
            f"{tag} Resuming interrupted batch [{resume_info['batch_id']}] "
            f"({resume_info['item_count']} item(s), last activity {resume_info['last_modified']})."
        )
        _work_queue_manager.release_interrupted_items()
        reuse_batch_id = resume_info["batch_id"]
    else:
        _activity_tracker.info(f"{tag} Moving files back to PENDING.")
        _work_queue_manager.move_working_items_back_to_pending(batch_id=None)
        reuse_batch_id = None

    batch, batch_id = _work_queue_manager.get_next_batch(force_new_batch=True, reuse_batch_id=reuse_batch_id)

    if reuse_batch_id is None:
        _activity_tracker.info(f"{tag} New batch created with id [{batch_id}] with {len(batch)} items.")
    else:
        _activity_tracker.info(f"{tag} Resumed batch [{batch_id}]: picked up {len(batch)} pending item(s).")

    if len(batch) == 0:
        if reuse_batch_id is None:
            _activity_tracker.info(f"{tag} No items found in the queue. Nothing to do here.")
            return
        _activity_tracker.info(f"{tag} Resumed batch [{batch_id}] has no pending items left; finalizing and reporting.")

    _activity_tracker.info(f"{tag} Adapting paths for running locally...")
    for item in batch:
        try:
            _activity_tracker.debug(f" {tag} Old full_path: {item['full_path']}")
            full_path = Path(_watch_folder) / Path(item["full_path"]).relative_to(Path("/watch"))
            item["full_path"] = str(full_path.resolve())
            _activity_tracker.debug(f" {tag} New full_path: {item['full_path']}")
            item["parent"] = str(full_path.parent)
        except ValueError:
            _activity_tracker.debug(f" {tag} Seems like the file is already in the new location.")

        if item["target_path"] is None:
            _activity_tracker.debug(f" {tag} No target path found for item [{item['id']}].")
            continue

        _activity_tracker.debug(f" {tag} Old target_path: {item['target_path']}")

        try:
            if "movies" in item["target_path"].lower():
                target_path = Path(_movies_base_folder) / Path(item["target_path"]).relative_to(Path("/movies"))
                item["target_path"] = str(target_path.resolve())
                _activity_tracker.debug(f" {tag} New target_path: {item['target_path']}")
                continue

            target_path = Path(_series_base_folder) / Path(item["target_path"]).relative_to(Path("/series"))
            item["target_path"] = str(target_path.resolve())
            _activity_tracker.debug(f" {tag} New target_path: {item['target_path']}")
        except ValueError:
            _activity_tracker.debug(f" {tag} Seems like the file is already in the new location.")

    _activity_tracker.info(f"{tag} Processing batch with id [{batch_id}]...")

    process_batch(batch, batch_id)


def on_demand_process_missing_add() -> None:
    tag = "[MISSING]"

    _activity_tracker.info(f"{tag} Reading watch folder recursively...")
    counts = queue_untracked_files(Path(_watch_folder))
    log_scan_counts(counts, _activity_tracker)
    if counts.pending == 0:
        _activity_tracker.info(f"{tag} No new files to process.")
        return

    on_demand_batch()


def on_demand_recheck() -> int:
    """Print which filed media the identifier would now file elsewhere. Returns the exit code."""
    print("Re-identifying filed media; nothing is moved or changed.")
    return run_recheck(
        _work_queue_manager.get_filed_items(),
        identify_file,
        Path(_movies_base_folder),
        Path(_series_base_folder),
    )


def on_demand_backfill() -> int:
    """Save the content hash of finished items that have none. Returns the exit code."""
    watch_folder = Path(_watch_folder)
    return run_backfill(
        _work_queue_manager.get_done_items_without_hash(),
        _work_queue_manager.update,
        library_root_pairs(Path(_movies_base_folder), Path(_series_base_folder)),
        [(root, watch_folder) for root in watch_folder_roots(watch_folder)],
    )


def main() -> int:
    command = _args.command
    if command == "recheck":
        return on_demand_recheck()
    if command == "backfill-hashes":
        return on_demand_backfill()

    if not _args.yes:
        print("\nATTENTION: It is advisable to stop the container service before running this command!")
        print("Press ENTER to continue or CTRL+C to abort.")
        input()

    if command == "batch":
        on_demand_batch()
    elif command == "missing":
        on_demand_process_missing_add()
    return 0


if __name__ == "__main__":
    print("Flushing buffered OTEL log records before starting.")
    flush_all_otel_loggers()

    exit_code = 0
    try:
        exit_code = main()
    except KeyboardInterrupt:
        if not _run_started:
            print("\nAborted before any work started. Nothing to clean up.")
            sys.exit(130)
        print("\nInterrupted. Releasing in-flight items so the next run can resume...")
        try:
            _activity_tracker.warning("[INTERRUPT] Run interrupted by operator; releasing items for resume.")
            _work_queue_manager.release_interrupted_items()
            print("Cleanup done. Run the same command again to resume this batch.")
        except Exception as exc:
            print(f"Cleanup failed ({type(exc).__name__}); the next run's startup recovery will handle it.")
        flush_all_otel_loggers()
        sys.exit(130)
    finally:
        shutdown_pool()
    sys.exit(exit_code)
