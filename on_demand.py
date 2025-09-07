from dotenv import load_dotenv
load_dotenv()

import os
import sys

from src.batch_processor import process_batch
from src.data.activity_logger import ActivityTracker
from src.data.notification_repository import NotificationRepository
from src.data.work_queue_manager import WorkQueueManager

_work_queue_manager = WorkQueueManager()
_watch_folder = os.environ.get('WATCH_FOLDER')
_movies_base_folder = os.environ.get('MOVIES_BASE_FOLDER')
_series_base_folder = os.environ.get('SERIES_BASE_FOLDER')
_notification_agent = NotificationRepository(client_id="smo-watchdog-notification-sender")


from pathlib import Path

old_path = Path("/old/root/folder/subdir/file.txt")
old_root = Path("/old/root/folder")
new_root = Path("/new/root/folder")

new_path = new_root / old_path.relative_to(old_root)


def on_demand_batch():
    activity_tracker = ActivityTracker("On Demand: Batch")

    activity_tracker.info("Moving files back to PENDING.")
    _work_queue_manager.move_working_items_back_to_pending(batch_id=None)

    activity_tracker.info("Forcing a new batch to be processed.")
    batch, batch_id = _work_queue_manager.get_next_batch(force_new_batch=True)

    activity_tracker.info(f"New batch created with id [{batch_id}] with {len(batch)} items.")
    if len(batch) == 0:
        activity_tracker.info("No items found in the queue. Nothing to do here.")
        return

    activity_tracker.info("Adapting paths for running locally...")
    for item in batch:
        try:
            activity_tracker.debug(f"Old full_path: {item['full_path']}")
            full_path = Path(_watch_folder) / Path(item['full_path']).relative_to(Path("/watch"))
            item['full_path'] = str(full_path.resolve())
            activity_tracker.debug(f"New full_path: {item['full_path']}")
            item["parent"] = str(full_path.parent)
        except ValueError:
            activity_tracker.debug(f"Seems like the file is already in the new location.")


        if item["target_path"] is None:
            activity_tracker.debug(f"No target path found for item [{item['id']}].")
            continue

        activity_tracker.debug(f"Old target_path: {item['target_path']}")

        try:
            if "movies" in item['target_path'].lower():
                target_path = Path(_movies_base_folder) / Path(item['target_path']).relative_to(Path("/movies"))
                item['target_path'] = str(target_path.resolve())
                activity_tracker.debug(f"New target_path: {item['target_path']}")
                continue

            target_path = Path(_series_base_folder) / Path(item['target_path']).relative_to(Path("/series"))
            item['target_path'] = str(target_path.resolve())
            activity_tracker.debug(f"New target_path: {item['target_path']}")
        except ValueError:
            activity_tracker.debug(f"Seems like the file is already in the new location.")

    activity_tracker.info(f"Processing batch with id [{batch_id}]...")

    process_batch(batch, batch_id)


def on_demand_process_missing():
    activity_tracker = ActivityTracker("On Demand: Missing")


def main():
    if len(sys.argv) < 2:
        print("Usage: python on_demand.py [batch|missing-add|missing-process]")
        print("  batch: creates a new batch, and process all pending/working files.")
        print("  missing-add: reads the IN folder and adds the missing files to the queue.")
        print("  missing-process: similar to missing-add, but processes all missing files.")

        return

    print("\nATTENTION: It is advisable to stop the container service before running this command!")

    command = sys.argv[1].strip()

    if command == "batch":
        on_demand_batch()
    else:
        on_demand_process_missing()


if __name__ == '__main__':
    main()
