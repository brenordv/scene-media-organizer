from dotenv import load_dotenv
load_dotenv()

import os
import sys

from src.batch_processor import process_batch
from src.data.activity_logger import ActivityTracker
from src.data.notification_repository import NotificationRepository
from src.data.work_queue_manager import WorkQueueManager

_work_queue_manager = WorkQueueManager()
_movies_base_folder = os.environ.get('MOVIES_BASE_FOLDER')
_series_base_folder = os.environ.get('SERIES_BASE_FOLDER')
_notification_agent = NotificationRepository(client_id="smo-watchdog-notification-sender")


def on_demand_batch():
    activity_tracker = ActivityTracker("On Demand: Batch")

    activity_tracker.info("Forcing a new batch to be processed.")
    batch, batch_id = _work_queue_manager.get_next_batch(force_new_batch=True)

    activity_tracker.info(f"New batch created with id [{batch_id}] with {len(batch)} items.")
    if len(batch) == 0:
        activity_tracker.info("No items found in the queue. Nothing to do here.")
        return

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
