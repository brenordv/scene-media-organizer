import os
import time

from simple_log_factory.log_factory import log_factory

_logger = log_factory("Check File Stability", unique_handler_types=True)

def check_is_file_stable(filename):
    # by default, we wait 3 minutes for the file to become stable, checking every 6 seconds
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
        _logger.error(f"Error checking for file stability. Assuming it is not stable. File: {str(e)}")
        return False
