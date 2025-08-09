from dotenv import load_dotenv
load_dotenv()

import threading
import os
import time
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from src.data.activity_logger import ActivityTracker
from src.batch_processor import batch_processor
from src.queue_worker import add_to_queue, queue_consumer
from src.data.work_queue_manager import WorkQueueManager
from src.notification_receiver import handle_notification_messages

_work_queue_manager = WorkQueueManager()
_activity_logger = ActivityTracker("SMO-Watchdog")

class MyHandler(FileSystemEventHandler):
    def on_created(self, event):
        add_to_queue(event.src_path, event.is_directory)


def main():
    monitored_path = os.environ.get('WATCH_FOLDER')
    event_handler = MyHandler()
    observer = Observer()
    observer.schedule(event_handler, monitored_path, recursive=True)

    _activity_logger.info(f"Watching folder: {monitored_path}")
    observer.start()

    threading.Thread(target=handle_notification_messages, daemon=True).start()
    threading.Thread(target=queue_consumer, daemon=True).start()
    threading.Thread(target=batch_processor, daemon=True).start()

    try:
        while True:
            # I could use this as a tick type of thing.
            time.sleep(60)
    except KeyboardInterrupt:
        observer.stop()

    observer.join()


if __name__ == '__main__':
    main()
