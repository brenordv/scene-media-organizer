"""The heartbeat file: when the service last checked its dependencies and what it found.

The file is JSON (``time``, ``postgres``, ``mqtt``) under a fixed name in the system temp folder. The name and the
format are a contract with readers outside this package, so changing either needs the readers changed too.
"""

import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from simple_log_factory_ext_otel import TracedLogger

HEARTBEAT_FILE_NAME = "scene-media-organizer-heartbeat.json"
HEARTBEAT_INTERVAL_SECONDS = 60


@dataclass(frozen=True)
class HealthState:
    """What the last dependency check found.

    Attributes:
        postgres: The database answered a query.
        mqtt: The notification receiver holds a broker connection.
    """

    postgres: bool
    mqtt: bool


def heartbeat_path() -> Path:
    """Return where the heartbeat is written: the system temp folder (``/tmp`` in the container)."""
    return Path(tempfile.gettempdir()) / HEARTBEAT_FILE_NAME


def write_heartbeat(state: HealthState, path: Path, now: float) -> None:
    """Replace the heartbeat file atomically, so a reader never sees a partial file.

    Args:
        state: The dependency check's result.
        path: The heartbeat file.
        now: The check's time, as a Unix timestamp.

    Raises:
        OSError: If the file cannot be written.
    """
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps({"time": now, "postgres": state.postgres, "mqtt": state.mqtt}), encoding="utf-8")
    os.replace(temporary, path)


def beat(
    previous: HealthState | None,
    probe_database: Callable[[], bool],
    is_broker_connected: Callable[[], bool],
    path: Path,
    now: float,
    logger: TracedLogger,
) -> HealthState:
    """Check the dependencies, log what changed, and write the heartbeat.

    Writing is best-effort: when it fails, the heartbeat goes stale and the health check reports it, which is the
    signal that matters.

    Args:
        previous: The last check's result, or None for the first check.
        probe_database: Tells whether the database answers.
        is_broker_connected: Tells whether the broker connection is up.
        path: The heartbeat file.
        now: The check's time, as a Unix timestamp.
        logger: Logger for transitions and write failures.

    Returns:
        This check's result.
    """
    current = HealthState(postgres=probe_database(), mqtt=is_broker_connected())
    report_transitions(previous, current, logger)
    try:
        write_heartbeat(current, path, now)
    except OSError as e:
        logger.warning(f"Writing the heartbeat file failed ({type(e).__name__}); the health check will report it.")
    return current


def report_transitions(previous: HealthState | None, current: HealthState, logger: TracedLogger) -> None:
    """Log a dependency only when its state changes, so a steady state stays quiet.

    Args:
        previous: The last check's result, or None for the first check.
        current: This check's result.
        logger: Logger for the transition lines.
    """
    for name, was_ok, is_ok in (
        ("Postgres", previous.postgres if previous else True, current.postgres),
        ("MQTT broker", previous.mqtt if previous else True, current.mqtt),
    ):
        if was_ok and not is_ok:
            logger.warning(f"Health check: {name} is unreachable.")
        elif not was_ok and is_ok:
            logger.info(f"Health check: {name} is reachable again.")
