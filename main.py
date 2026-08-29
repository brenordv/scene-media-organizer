import argparse
import os
import sys
import threading
import time
import traceback
from collections.abc import Callable

from src.config import add_config_arguments, apply_config, require_env


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the scene-media-organizer watchdog service.")
    add_config_arguments(parser)
    return parser.parse_args(argv)


_args = _parse_args(sys.argv[1:])
apply_config(_args)
require_env(
    "WATCH_FOLDER",
    "MOVIES_BASE_FOLDER",
    "SERIES_BASE_FOLDER",
    "POSTGRES_HOST",
    "API_URL",
    "MQTT_HOST",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
)

from src.data.db import init_pool, shutdown_pool  # noqa: E402

init_pool()

from watchdog.events import FileSystemEvent, FileSystemEventHandler  # noqa: E402

# Imports below this line intentionally run after configuration is applied,
# because these modules read the environment at import time.
from watchdog.observers import Observer  # noqa: E402

from src.batch_processor import batch_processor  # noqa: E402
from src.data.activity_logger import ActivityTracker  # noqa: E402
from src.data.work_queue_manager import WorkQueueManager  # noqa: E402
from src.notification_receiver import handle_notification_messages  # noqa: E402
from src.queue_worker import add_to_queue, queue_consumer  # noqa: E402
from src.utils import flush_all_otel_loggers, get_otel_log_handler  # noqa: E402

_work_queue_manager = WorkQueueManager()
_activity_logger = ActivityTracker("SMO-Watchdog")

# The supervisor logs through the OTEL TracedLogger (async export, never raises
# on collector failure), never through ActivityTracker: ActivityTracker writes
# each line to Postgres and raises when the DB is down, which is one of the very
# failures the supervisor exists to survive.
_supervisor_logger = get_otel_log_handler("Supervisor", unique_handler_types=True)


def _safe_log(message: str) -> None:
    try:
        _supervisor_logger.error(message)
    except Exception:
        print(message)


def _supervised(name: str, target: Callable[[], None]) -> None:
    """Run target forever; log and restart it with capped exponential backoff."""
    delay_seconds = 5
    while True:
        started = time.monotonic()
        crash_details = None
        try:
            target()
        except Exception:
            crash_details = traceback.format_exc()
        if time.monotonic() - started > 60:
            delay_seconds = 5  # the worker ran healthily for a while; reset the backoff
        if crash_details is None:
            _safe_log(f"[SUPERVISOR] {name} returned unexpectedly. Restarting in {delay_seconds}s.")
        else:
            _safe_log(f"[SUPERVISOR] {name} crashed. Restarting in {delay_seconds}s.\n{crash_details}")
        time.sleep(delay_seconds)
        delay_seconds = min(delay_seconds * 2, 300)


class MyHandler(FileSystemEventHandler):
    def on_created(self, event: FileSystemEvent) -> None:
        add_to_queue(str(event.src_path), event.is_directory)


def main() -> None:
    monitored_path = os.environ["WATCH_FOLDER"]
    event_handler = MyHandler()
    observer = Observer()
    observer.schedule(event_handler, monitored_path, recursive=True)

    _activity_logger.info(f"Watching folder: {monitored_path}")
    observer.start()

    threading.Thread(
        target=_supervised, args=("notification-receiver", handle_notification_messages), daemon=True
    ).start()
    threading.Thread(target=_supervised, args=("queue-consumer", queue_consumer), daemon=True).start()
    threading.Thread(target=_supervised, args=("batch-processor", batch_processor), daemon=True).start()

    try:
        while True:
            time.sleep(60)
            if not observer.is_alive():
                _safe_log("[SUPERVISOR] Watchdog observer died. Exiting so the container restarts.")
                sys.exit(1)
    except KeyboardInterrupt:
        observer.stop()

    observer.join()


if __name__ == "__main__":
    # Flush ALL OTEL log handlers before starting the main loop.
    # On Windows the BatchLogRecordProcessor's background HTTP export
    # can deadlock with event loop initialisation if both run
    # concurrently. Every TracedLogger has its own
    # BatchLogRecordProcessor; we must drain them all.
    print("Flushing buffered OTEL log records before starting.")
    flush_all_otel_loggers()

    print("Starting scene-media-organizer watchdog.")
    try:
        main()
    finally:
        shutdown_pool()
