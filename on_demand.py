from dotenv import load_dotenv
load_dotenv()

import os
import sys

from src.batch_processor import process_batch
from src.data.activity_logger import ActivityTracker
from src.data.notification_repository import NotificationRepository
from src.data.work_queue_manager import WorkQueueManager
from src.queue_worker import prepare_file_for_processing

_work_queue_manager = WorkQueueManager()
_watch_folder = os.environ.get('WATCH_FOLDER')
_movies_base_folder = os.environ.get('MOVIES_BASE_FOLDER')
_series_base_folder = os.environ.get('SERIES_BASE_FOLDER')
_notification_agent = NotificationRepository(client_id="smo-watchdog-notification-sender")
_activity_tracker = ActivityTracker("On Demand")

from pathlib import Path

old_path = Path("/old/root/folder/subdir/file.txt")
old_root = Path("/old/root/folder")
new_root = Path("/new/root/folder")

new_path = new_root / old_path.relative_to(old_root)


def on_demand_batch():
    tag = "[BATCH]"

    _activity_tracker.info(f"{tag} Moving files back to PENDING.")
    _work_queue_manager.move_working_items_back_to_pending(batch_id=None)

    _activity_tracker.info(f"{tag} Forcing a new batch to be processed.")
    batch, batch_id = _work_queue_manager.get_next_batch(force_new_batch=True)

    _activity_tracker.info(f"{tag} New batch created with id [{batch_id}] with {len(batch)} items.")
    if len(batch) == 0:
        _activity_tracker.info(f"{tag} No items found in the queue. Nothing to do here.")
        return

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

    diff = len(filenames) - len(existing_filenames)

    if diff <= 0:
        _activity_tracker.info(f"{tag} No new files found to add to the queue.")
        return

    _activity_tracker.info(f"{tag} Found {diff} new files to add to the queue.")

    new_files = [f[0] for f in files_found if f[1] not in existing_filenames]
    for new_file in new_files:
        _activity_tracker.debug(f"{tag} Adding new file to the queue: {new_file}")
        prepare_file_for_processing(new_file)

    on_demand_batch()


def print_usage():
    print("Usage: python on_demand.py [batch|missing]")
    print("  batch: creates a new batch, and process all pending/working files.")
    print("  missing: reads the IN folder and adds the missing files to the queue, and processes them.")


def main():
    if len(sys.argv) < 2:
        print_usage()
        return

    print("\nATTENTION: It is advisable to stop the container service before running this command!")
    print("Press ENTER to continue or CTRL+C to abort.")
    input()

    command = sys.argv[1].strip()

    if command == "batch":
        on_demand_batch()
    elif command == "missing":
        on_demand_process_missing_add()
    else:
        print("Invalid command.\n")
        print_usage()


if __name__ == '__main__':
    main()
