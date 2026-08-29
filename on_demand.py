import argparse
import os
import sys
from pathlib import Path

from src.config import add_config_arguments, apply_config, require_env


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Run scene-media-organizer maintenance commands.")
    parser.add_argument("command", choices=["batch", "missing"],
                        help="batch: process all pending items. "
                             "missing: queue files present on disk but absent from the queue, then process.")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="Skip the confirmation prompt (for non-interactive runs).")
    add_config_arguments(parser)
    return parser.parse_args(argv)


_args = _parse_args(sys.argv[1:])
apply_config(_args)
require_env(
    "WATCH_FOLDER", "MOVIES_BASE_FOLDER", "SERIES_BASE_FOLDER",
    "POSTGRES_HOST", "API_URL", "MQTT_HOST", "OTEL_EXPORTER_OTLP_ENDPOINT",
)

from src.data.db import init_pool, shutdown_pool  # noqa: E402

init_pool()

# Imports below this line intentionally run after configuration is applied,
# because these modules read the environment at import time.
from src.batch_processor import process_batch  # noqa: E402
from src.data.activity_logger import ActivityTracker  # noqa: E402
from src.data.notification_repository import NotificationRepository  # noqa: E402
from src.data.work_queue_manager import WorkQueueManager  # noqa: E402
from src.helpers import select_new_files  # noqa: E402
from src.queue_worker import prepare_file_for_processing  # noqa: E402
from src.utils import flush_all_otel_loggers  # noqa: E402

_work_queue_manager = WorkQueueManager()
_watch_folder = os.environ.get('WATCH_FOLDER')
_movies_base_folder = os.environ.get('MOVIES_BASE_FOLDER')
_series_base_folder = os.environ.get('SERIES_BASE_FOLDER')
_notification_agent = NotificationRepository(client_id="smo-watchdog-notification-sender")
_activity_tracker = ActivityTracker("On Demand")

_run_started = False


def on_demand_batch():
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

    batch, batch_id = _work_queue_manager.get_next_batch(
        force_new_batch=True, reuse_batch_id=reuse_batch_id
    )

    if reuse_batch_id is None:
        _activity_tracker.info(f"{tag} New batch created with id [{batch_id}] with {len(batch)} items.")
    else:
        _activity_tracker.info(f"{tag} Resumed batch [{batch_id}]: picked up {len(batch)} pending item(s).")

    if len(batch) == 0:
        if reuse_batch_id is None:
            _activity_tracker.info(f"{tag} No items found in the queue. Nothing to do here.")
            return
        _activity_tracker.info(
            f"{tag} Resumed batch [{batch_id}] has no pending items left; finalizing and reporting."
        )

    _activity_tracker.info(f"{tag} Adapting paths for running locally...")
    for item in batch:
        try:
            _activity_tracker.debug(f" {tag} Old full_path: {item['full_path']}")
            full_path = Path(_watch_folder) / Path(item['full_path']).relative_to(Path("/watch"))
            item['full_path'] = str(full_path.resolve())
            _activity_tracker.debug(f" {tag} New full_path: {item['full_path']}")
            item["parent"] = str(full_path.parent)
        except ValueError:
            _activity_tracker.debug(f" {tag} Seems like the file is already in the new location.")


        if item["target_path"] is None:
            _activity_tracker.debug(f" {tag} No target path found for item [{item['id']}].")
            continue

        _activity_tracker.debug(f" {tag} Old target_path: {item['target_path']}")

        try:
            if "movies" in item['target_path'].lower():
                target_path = Path(_movies_base_folder) / Path(item['target_path']).relative_to(Path("/movies"))
                item['target_path'] = str(target_path.resolve())
                _activity_tracker.debug(f" {tag} New target_path: {item['target_path']}")
                continue

            target_path = Path(_series_base_folder) / Path(item['target_path']).relative_to(Path("/series"))
            item['target_path'] = str(target_path.resolve())
            _activity_tracker.debug(f" {tag} New target_path: {item['target_path']}")
        except ValueError:
            _activity_tracker.debug(f" {tag} Seems like the file is already in the new location.")

    _activity_tracker.info(f"{tag} Processing batch with id [{batch_id}]...")

    process_batch(batch, batch_id)


def on_demand_process_missing_add():
    tag = "[MISSING]"
    watch_folder = Path(_watch_folder)

    _activity_tracker.info(f"{tag} Reading watch folder recursively...")
    files_found = set()
    for item in watch_folder.rglob("*"):
        if item.is_dir():
            _activity_tracker.debug(f" {tag} Ignoring directory: {item}")
            continue

        _activity_tracker.debug(f"{tag} Found file: {item}")
        files_found.add((str(item.resolve()), item.name))

    _activity_tracker.info(f"{tag} Found {len(files_found)} files in the watch folder.")
    filenames = [f[1] for f in files_found]
    existing_filenames = _work_queue_manager.filter_only_existing_filenames(filenames)

    _activity_tracker.info(f"{tag} Found {len(existing_filenames)} existing files in the queue.")

    new_files = select_new_files(files_found, existing_filenames)
    if not new_files:
        _activity_tracker.info(f"{tag} No new files found to add to the queue.")
        return

    _activity_tracker.info(f"{tag} Found {len(new_files)} new files to add to the queue.")

    for new_file in new_files:
        _activity_tracker.debug(f"{tag} Adding new file to the queue: {new_file}")
        prepare_file_for_processing(new_file)

    on_demand_batch()


def main():
    if not _args.yes:
        print("\nATTENTION: It is advisable to stop the container service before running this command!")
        print("Press ENTER to continue or CTRL+C to abort.")
        input()

    command = _args.command

    if command == "batch":
        on_demand_batch()
    elif command == "missing":
        on_demand_process_missing_add()


if __name__ == '__main__':
    print("Flushing buffered OTEL log records before starting.")
    flush_all_otel_loggers()

    try:
        main()
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
