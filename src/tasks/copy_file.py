import os
import subprocess
import time
from pathlib import Path
import shutil

from src.utils import to_bool_env, get_otel_log_handler

_logger = get_otel_log_handler("Copy File", unique_handler_types=True)


def copy_file(src_file, dst_path_str):
    max_retries = 5
    base_delay_seconds = 3
    change_ownership = to_bool_env("WATCHDOG_CHANGE_DEST_OWNERSHIP_ON_COPY", False)
    copy_using_rsync = to_bool_env("WATCHDOG_COPY_USING_RSYNC", False)
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

            _copy_file(src_file, dst_file, copy_using_rsync)

            if change_ownership:
                # Will fail if the script is not run as root.
                _change_destination_ownership(dst_file, src_file)

            return True
        except Exception as e:
            _logger.warning(f"Error copying file [{src_file}] to [{dst_path}]: {str(e)}")
            delay_seconds = base_delay_seconds * (i + 1)
            _logger.debug(f"Retrying in {delay_seconds} seconds...")

            time.sleep(delay_seconds)

    return False


def _copy_file(src_file, dst_path_str, copy_using_rsync):
    if copy_using_rsync:
        cmd = [
            "rsync",
            "-a", "--info=progress2", "--human-readable",
            "--partial", "--append-verify", "--stats",
            "--xattrs", "--acls",
            src_file, dst_path_str
        ]
        # Stream output so you can mirror to your logger
        with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as p:
            for line in p.stdout:
                _logger.debug(line.rstrip())  # or _logger.debug(line.rstrip())

            exit_code = p.wait()
            success = exit_code == 0

            _logger.debug(f"rsync exit code: {exit_code} / Sucess: {success}")
            if not success:
                raise Exception("Failed to copy file using rsync")

    else:
        shutil.copy(src_file, dst_path_str)



def _change_destination_ownership(dst_file, src_file):
    """
    This changes the ownership of the destination file to match the source file.
    Required only in two situations:
    1. There's a process that modifies the destination file
    2. This script is run as the root
    :param dst_file: destination file
    :param src_file: source file
    :return: None.
    """
    st = os.stat(src_file, follow_symlinks=True)
    os.chown(dst_file, st.st_uid, st.st_gid)  # requires root
