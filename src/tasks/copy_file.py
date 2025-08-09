import os
import time
from pathlib import Path
import shutil

from simple_log_factory.log_factory import log_factory

_logger = log_factory("Copy File", unique_handler_types=True)


def copy_file(src_file, dst_path_str):
    max_retries = 5
    base_delay_seconds = 3

    src_path = Path(src_file)
    dst_path = Path(dst_path_str)

    if not src_path.exists():
        _logger.warning(f"Source file {src_file} does not exist. Will not copy.")
        return False

    if not dst_path.exists():
        dst_path.mkdir(parents=True, exist_ok=True)

    dst_file = dst_path.joinpath(src_path.name)

    for i in range(max_retries):
        try:
            _logger.debug(f"Copying file [{src_path.name}] to [{dst_path}]")
            st = os.stat(src_file, follow_symlinks=True)
            shutil.copy2(src_file, dst_file)
            os.chown(dst_file, st.st_uid, st.st_gid)  # requires root
            return True
        except Exception as e:
            _logger.warning(f"Error copying file [{src_file}] to [{dst_path}]: {str(e)}")
            delay_seconds = base_delay_seconds * (i + 1)
            _logger.debug(f"Retrying in {delay_seconds} seconds...")

            time.sleep(delay_seconds)

    return False
