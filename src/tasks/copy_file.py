import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from opentelemetry import trace

from src.utils import child_process_env, get_otel_log_handler, to_bool_env

PARTIAL_COPY_PREFIX = ".smo-partial-"

_logger = get_otel_log_handler("Copy File", unique_handler_types=True)


class CopyPermissionError(Exception):
    """A copy failed because the process lacks permission for the source, the destination or the ownership change."""


@_logger.trace("copy_file")
def copy_file(src_file: str, dst_dir: str | Path) -> bool:
    """Copy a file into a directory, retrying up to five times with a growing delay.

    The copy is all-or-nothing: a file with the final name is always a finished copy, and a failed attempt leaves
    whatever was there untouched.

    Args:
        src_file: The file to copy.
        dst_dir: The destination directory; created when missing.

    Returns:
        True when the copy (and the optional ownership change) succeeded, False when the source is missing or every
        attempt failed.

    Raises:
        CopyPermissionError: On the first permission failure. It is not retried, because waiting does not change
            permissions.
    """
    span = trace.get_current_span()
    src_path = Path(src_file)
    dst_path = Path(dst_dir)

    if span.is_recording():
        span.set_attributes(
            {
                "file.source_path": str(src_file),
                "file.destination_path": str(dst_dir),
                "file.name": src_path.name,
            }
        )

    max_retries = 5
    base_delay_seconds = 3
    change_ownership = to_bool_env("WATCHDOG_CHANGE_DEST_OWNERSHIP_ON_COPY", False)
    copy_using_rsync = to_bool_env("WATCHDOG_COPY_USING_RSYNC", False)

    if not src_path.exists():
        _logger.warning(f"Source file {src_file} does not exist. Will not copy.")
        return False

    dst_file = dst_path.joinpath(src_path.name)

    for i in range(max_retries):
        try:
            _logger.debug(f"Copying file [{src_path.name}] to [{dst_path}]")

            dst_path.mkdir(parents=True, exist_ok=True)
            _copy_file(src_file, dst_file, copy_using_rsync)

            if change_ownership:
                # Will fail if the script is not run as root.
                _change_destination_ownership(dst_file, src_file)

            return True
        except PermissionError as e:
            raise CopyPermissionError(str(e)) from e
        except Exception as e:
            _logger.warning(f"Error copying file [{src_file}] to [{dst_path}]: {str(e)}")
            delay_seconds = base_delay_seconds * (i + 1)
            _logger.debug(f"Retrying in {delay_seconds} seconds...")

            time.sleep(delay_seconds)

    return False


@_logger.trace("_copy_file")
def _copy_file(src_file: str, dst_file: Path, copy_using_rsync: bool) -> None:
    span = trace.get_current_span()
    copy_method = "rsync" if copy_using_rsync else "shutil"

    if span.is_recording():
        span.set_attributes({"copy.method": copy_method})

    if copy_using_rsync:
        cmd = [
            "rsync",
            "-a",
            "--info=progress2",
            "--human-readable",
            "--stats",
            "--xattrs",
            "--acls",
            src_file,
            str(dst_file),
        ]
        with subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=child_process_env()
        ) as p:
            assert p.stdout is not None  # stdout=PIPE guarantees a stream
            permission_denied_line = None
            for line in p.stdout:
                _logger.debug(line.rstrip())
                if "Permission denied" in line:
                    permission_denied_line = line.strip()

            exit_code = p.wait()
            success = exit_code == 0

            _logger.debug(f"rsync exit code: {exit_code} / Success: {success}")
            if not success:
                if permission_denied_line is not None:
                    raise PermissionError(permission_denied_line)
                raise Exception("Failed to copy file using rsync")

    else:
        _copy_through_temporary_file(src_file, dst_file)


def _copy_through_temporary_file(src_file: str, dst_file: Path) -> None:
    """Copy a file onto ``dst_file`` in one step, by writing a temporary file beside it and renaming that into place.

    The temporary file has a short, exclusive name, so a symlink placed there beforehand is not followed and a long
    file name still fits. Both names are in one folder, and so on one filesystem, which makes the rename atomic.

    Args:
        src_file: The file to copy.
        dst_file: The final path, in a folder that exists.

    Raises:
        OSError: If a step fails; ``dst_file`` is left as it was.
    """
    descriptor, temporary_name = tempfile.mkstemp(dir=dst_file.parent, prefix=PARTIAL_COPY_PREFIX)
    os.close(descriptor)
    temporary_file = Path(temporary_name)
    try:
        shutil.copy(src_file, temporary_file)
        os.replace(temporary_file, dst_file)
    finally:
        try:
            temporary_file.unlink(missing_ok=True)
        except OSError as e:
            # Best-effort: nothing reuses an orphan, and raising here would replace the copy's own outcome.
            _logger.warning(f"Could not remove the temporary file [{temporary_file}]: {e}")


@_logger.trace("_change_destination_ownership")
def _change_destination_ownership(dst_file: Path, src_file: str) -> None:
    """Change the ownership of the destination file to match the source file.

    Required only when:
    1. There's a process that modifies the destination file
    2. This script is run as root
    """
    st = os.stat(src_file, follow_symlinks=True)
    os.chown(dst_file, st.st_uid, st.st_gid)  # requires root
