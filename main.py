import argparse
import os
import sys
import threading
import time
import traceback
from collections.abc import Callable
from pathlib import Path

from src.config import USAGE_ERROR_EXIT_CODE, add_config_arguments, apply_config, parse_command_line, require_env


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the scene-media-organizer watchdog service.")
    add_config_arguments(parser)
    return parse_command_line(parser, argv)


_args = _parse_args(sys.argv[1:])
apply_config(_args)
require_env("WATCH_FOLDER", "MOVIES_BASE_FOLDER", "SERIES_BASE_FOLDER", "OTEL_EXPORTER_OTLP_ENDPOINT")

# Startup logs through the OTEL-only TracedLogger: ActivityTracker writes to a
# database that is not reachable yet.
from src.secret_store import SecretsError
from src.settings import load_settings
from src.utils import flush_all_otel_loggers, get_otel_log_handler

_supervisor_logger = get_otel_log_handler("Supervisor", unique_handler_types=True)

try:
    load_settings(_supervisor_logger)
except (SecretsError, ValueError) as settings_error:
    print(f"Settings error: {settings_error}", file=sys.stderr)
    flush_all_otel_loggers()
    sys.exit(USAGE_ERROR_EXIT_CODE)

from src.data.db import init_pool, probe_database, shutdown_pool

init_pool()

# Imports below this line intentionally run after the pool exists, because
# these modules build repositories at import time.
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from src import notification_receiver
from src.batch_processor import batch_processor
from src.catch_up import run_catch_up_scan
from src.data.activity_logger import ActivityTracker
from src.heartbeat import HEARTBEAT_INTERVAL_SECONDS, HealthState, beat, heartbeat_path
from src.queue_worker import add_to_queue, queue_consumer

_activity_logger = ActivityTracker("SMO-Watchdog")
_catch_up_started = False


def _safe_log(message: str) -> None:
    # The supervisor logs through the OTEL TracedLogger (async export, never raises
    # on collector failure), never through ActivityTracker: ActivityTracker writes
    # each line to Postgres and raises when the DB is down, which is one of the very
    # failures the supervisor exists to survive.
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


def _consume_queue() -> None:
    """Queue the files that arrived while the service was down (first start only), then consume watcher events.

    Events that arrive during the scan wait in the in-memory queue.
    """
    global _catch_up_started
    if not _catch_up_started:
        _catch_up_started = True
        run_catch_up_scan(Path(os.environ["WATCH_FOLDER"]), _supervisor_logger)
    queue_consumer()


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
        target=_supervised,
        args=("notification-receiver", notification_receiver.handle_notification_messages),
        daemon=True,
    ).start()
    threading.Thread(target=_supervised, args=("queue-consumer", _consume_queue), daemon=True).start()
    threading.Thread(target=_supervised, args=("batch-processor", batch_processor), daemon=True).start()

    # The first check runs one interval after start, once the receiver has had time to connect; the container's
    # health check start period covers the wait.
    health: HealthState | None = None
    try:
        while True:
            time.sleep(HEARTBEAT_INTERVAL_SECONDS)
            if not observer.is_alive():
                _safe_log("[SUPERVISOR] Watchdog observer died. Exiting so the container restarts.")
                sys.exit(1)
            health = beat(
                health,
                probe_database,
                notification_receiver.is_connected,
                heartbeat_path(),
                time.time(),
                _supervisor_logger,
            )
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
