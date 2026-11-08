#!/usr/bin/env python3
"""Container health check: the watch folder exists and the service's heartbeat is fresh and healthy.

Standard library only. The image runs this file from outside the application folder, so it must not import ``src``
(which would also start telemetry on every check). The heartbeat's name and format are shared with
``src/heartbeat.py``.
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

HEARTBEAT_FILE_NAME = "scene-media-organizer-heartbeat.json"
MAX_HEARTBEAT_AGE_SECONDS = 180


def find_problems(watch_folder: str | None, heartbeat_file: Path, now: float) -> list[str]:
    """Return what is wrong, or an empty list when the service looks healthy.

    Args:
        watch_folder: The ``WATCH_FOLDER`` setting.
        heartbeat_file: The heartbeat the service writes.
        now: The current Unix time.

    Returns:
        One message per problem.
    """
    problems: list[str] = []
    if not watch_folder or not os.path.isdir(watch_folder):
        problems.append(f"WATCH_FOLDER missing or not a directory: {watch_folder!r}")

    try:
        heartbeat = json.loads(heartbeat_file.read_text(encoding="utf-8"))
    except OSError, ValueError:
        problems.append("No readable heartbeat from the service yet")
        return problems

    if not isinstance(heartbeat, dict) or not isinstance(heartbeat.get("time"), (int, float)):
        problems.append("Heartbeat file is malformed")
        return problems

    age = now - heartbeat["time"]
    if age > MAX_HEARTBEAT_AGE_SECONDS:
        problems.append(f"Heartbeat is {int(age)} s old")
    if heartbeat.get("postgres") is not True:
        problems.append("Postgres unreachable")
    if heartbeat.get("mqtt") is not True:
        problems.append("MQTT unreachable")
    return problems


def main() -> int:
    """Print the result and return the exit code: 0 when healthy, 1 otherwise."""
    problems = find_problems(os.getenv("WATCH_FOLDER"), Path(tempfile.gettempdir()) / HEARTBEAT_FILE_NAME, time.time())
    if problems:
        print("; ".join(problems))
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
