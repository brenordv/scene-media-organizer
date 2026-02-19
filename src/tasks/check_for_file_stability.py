import os
import time

from opentelemetry import trace

from src.utils import get_otel_log_handler

_logger = get_otel_log_handler("Check File Stability", unique_handler_types=True)


@_logger.trace("check_is_file_stable")
def check_is_file_stable(filename):
    span = trace.get_current_span()
    if span.is_recording():
        span.set_attribute("file.path", str(filename))

    # By default, wait 3 minutes for the file to become stable, checking every 6 seconds
    max_time_to_wait_in_seconds = 3 * 60
    max_stable_checks = 30
    delay_between_checks = max_time_to_wait_in_seconds / max_stable_checks
    stable_checks_required = 3

    try:
        if not os.path.exists(filename):
            return False

        previous_size = os.path.getsize(filename)
        stable_checks = 0

        for _ in range(max_stable_checks):
            time.sleep(delay_between_checks)

            if not os.path.exists(filename):
                return False

            current_size = os.path.getsize(filename)

            if current_size == previous_size:
                stable_checks += 1
            else:
                stable_checks = 0
                previous_size = current_size

            if stable_checks == stable_checks_required:
                return True

        return stable_checks == stable_checks_required

    except (OSError, IOError) as e:
        _logger.error(
            f"Error checking for file stability. "
            f"Assuming it is not stable. File: {str(e)}"
        )
        return False
