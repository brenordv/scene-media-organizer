import html
import os
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from opentelemetry import trace

from src.data.activity_logger import ActivityTracker
from src.data.notification_repository import NotificationRepository
from src.data.work_queue_manager import WorkQueueManager
from src.helpers import library_root_pairs, locate_in_library, resolve_destination
from src.tasks.check_for_file_stability import check_is_file_stable
from src.tasks.copy_file import CopyPermissionError, copy_file
from src.tasks.decompress_file import decompress_file
from src.tasks.identify_file import IdentifierError, IdentifierUnavailableError, identify_file
from src.tasks.send_telegram_message import send_telegram_message
from src.tasks.verify_batch_data import verify_batch_data
from src.utils import _sha256, release_idle_memory

_work_queue_manager = WorkQueueManager()
_movies_base_folder = os.environ.get("MOVIES_BASE_FOLDER")
_series_base_folder = os.environ.get("SERIES_BASE_FOLDER")
_activity_tracker = ActivityTracker("Batch Processor")
_notification_agent = NotificationRepository(client_id="smo-watchdog-notification-sender")

if _series_base_folder is None or _movies_base_folder is None:
    raise RuntimeError("MOVIES_BASE_FOLDER and SERIES_BASE_FOLDER must be set")

_movies_base_path = Path(_movies_base_folder)
_series_base_path = Path(_series_base_folder)


def batch_processor() -> None:
    tag = "[BATCH PROCESSOR]"
    current_batch_id = None
    idle_cycles = 0
    _work_queue_manager.recover_stale_batches()
    while True:
        batch, current_batch_id = _work_queue_manager.get_next_batch(batch_id=current_batch_id)

        if batch is not None and len(batch) > 0:
            idle_cycles = 0
            _activity_tracker.info(f"{tag} NEW BATCH FOUND! Working on [{current_batch_id}]!")

            process_batch(batch, current_batch_id)

            _activity_tracker.info(f"{tag} BATCH PROCESSING DONE! Batch id: {current_batch_id}...")
            current_batch_id = None
            release_idle_memory()
        else:
            idle_cycles += 1
            if idle_cycles % 6 == 0:  # ~every 60s of idle polling
                release_idle_memory()

        time.sleep(10)


@_activity_tracker.trace("process_batch")
def process_batch(batch: list[dict[str, Any]], current_batch_id: Any) -> None:
    span = trace.get_current_span()
    if span.is_recording():
        span.set_attributes(
            {
                "batch.id": str(current_batch_id),
                "batch.items_count": len(batch),
            }
        )

    tag = f"[B.ID: {current_batch_id}]"
    try_again = []
    unavailable_count = 0

    # First try.
    for item in batch:
        try:
            file_to_retry = _process_batch_item(item)
            if file_to_retry is not None:
                try_again.append(file_to_retry)
                continue
        except IdentifierUnavailableError:
            # Not retried in this batch: the identifier call already retried with backoff. The item stays WORKING
            # with its attempt refunded, and the sweep below returns it to PENDING for a later batch.
            unavailable_count += 1
        except Exception as e:
            _activity_tracker.error(f"{tag} Error processing item [{item['id']}]: {str(e)}")
            item["status"] = "FAILED_PROCESSING"
            _work_queue_manager.update(item)

    if len(try_again) > 0:
        _activity_tracker.debug(f"{tag} Retrying to process {len(try_again)} items...")

    # Retry.
    for item in try_again:
        try:
            _process_batch_item(item)
        except IdentifierUnavailableError:
            unavailable_count += 1
        except Exception as e:
            _activity_tracker.error(f"{tag} Error retrying to process item [{item['id']}]: {str(e)}")
            item["status"] = "FAILED_PROCESSING_RETRY"
            _work_queue_manager.update(item)

    if unavailable_count:
        _activity_tracker.warning(
            f"{tag} Media identifier unavailable for {unavailable_count} identification attempt(s); "
            "the items stay queued and will be retried in a later batch."
        )
    if span.is_recording():
        span.set_attribute("batch.identifier_unavailable", unavailable_count)

    _activity_tracker.debug(
        f"{tag} In case any 'WORKING' items slipped through, we're going to move them "
        "back to pending so the next batch will take care of them."
    )
    _work_queue_manager.move_working_items_back_to_pending(current_batch_id)
    _work_queue_manager.set_batch_as_done(current_batch_id)

    batch_data = _work_queue_manager.get_batch_data(current_batch_id)
    reportable = [batch_item for batch_item in batch_data if batch_item["status"] != "DUPLICATE"]
    if span.is_recording():
        span.set_attribute("batch.duplicates", len(batch_data) - len(reportable))

    if batch_data and not reportable:
        # Nothing was copied, so there is nothing to verify, and verifying would report the batch as failed.
        _work_queue_manager.update_batch_verification(current_batch_id, True)
        _activity_tracker.info(f"{tag} Every item in this batch is a duplicate; no report to send.")
        return

    batch_verification, batch_verification_details = verify_batch_data(current_batch_id, reportable)
    _work_queue_manager.update_batch_verification(current_batch_id, batch_verification)

    try:
        notification_message = {
            "batch_id": current_batch_id,
            "items": reportable,
            "verified": batch_verification,
            "verification_details": batch_verification_details,
        }
        _notification_agent.post_message(message=notification_message)
    except Exception as e:
        _activity_tracker.error(f"{tag} Error sending batch completion notification: {str(e)}")


@_activity_tracker.trace("_process_batch_item")
def _process_batch_item(item: dict[str, Any]) -> dict[str, Any] | None:
    """Process one work item: wait for the file to settle, identify it, check its content, then decompress or copy it.

    Args:
        item: The work item; its status, attempts, target path, media id and content hash are updated in the queue.

    Returns:
        The item when it should be retried later in this batch, otherwise None.

    Raises:
        IdentifierUnavailableError: If the media identifier is unavailable; the item's attempt has been refunded and
            saved, and the caller decides when to retry.
    """
    span = trace.get_current_span()
    item_id = item["id"]
    full_path = item["full_path"]
    full_path_obj = Path(full_path)
    tag = f"[I.ID: {item_id}]"

    if span.is_recording():
        span.set_attributes(
            {
                "work_item.id": str(item_id),
                "file.path": full_path,
                "file.name": full_path_obj.name,
            }
        )

    if not full_path_obj.exists():
        _activity_tracker.warning(f"{tag} File no longer exists: {full_path}")
        item["status"] = "FAILED_MISSING"
        _work_queue_manager.update(item)
        return None

    _activity_tracker.debug(f"{tag} Processing item {full_path}. Checking if file is stable...")
    is_file_stable = check_is_file_stable(full_path)

    if not is_file_stable:
        _activity_tracker.warning(f"{tag} File is not stable. Will try again later. File: {full_path}")
        item["attempts"] = max(0, item["attempts"] - 1)
        _work_queue_manager.update(item)
        return item

    try:
        media_info = identify_file(full_path)
    except IdentifierUnavailableError as e:
        _activity_tracker.debug(f"{tag} {e}. The attempt is refunded; the item will be retried later.")
        item["attempts"] = max(0, item["attempts"] - 1)
        _work_queue_manager.update(item)
        raise
    except IdentifierError as e:
        _activity_tracker.warning(f"{tag} {e}. This counts as a failed attempt ({item['attempts']}).")
        return item

    if media_info is None:
        _activity_tracker.info(f"{tag} The media identifier found no movie or episode in [{full_path_obj.name}].")
        item["status"] = "FAILED_ID"
        _work_queue_manager.update(item)
        return None

    try:
        source_size = full_path_obj.stat().st_size
        # An empty file has no content to compare. Not opening it also avoids blocking on a FIFO, which reports size 0.
        item["content_hash"] = _hash_file(full_path_obj, source_size) if source_size > 0 else None
    except PermissionError as e:
        _report_permission_failure(item, None, e)
        return None

    if item["content_hash"] is not None:
        matches = _work_queue_manager.find_done_items_by_hash(item["content_hash"])
        if matches:
            _report_duplicate(item, matches)
            return None

    if item["is_archive"]:
        decompress_result = decompress_file(full_path)
        if not decompress_result:
            return item

        item["status"] = "DONE"
        _work_queue_manager.update(item)
        return None

    media_info_id = media_info["id"]
    item["media_info_cache_id"] = media_info_id
    _work_queue_manager.update(item)

    destination = resolve_destination(media_info, _movies_base_path, _series_base_path)
    if destination.path is None:
        _activity_tracker.error(
            f"{tag} Cannot place the file: {destination.reason}. Media Info cache id: {media_info_id}"
        )
        item["status"] = "FAILED_ID"
        _work_queue_manager.update(item)
        return None

    _copy_to_library(item, destination.path, source_size)
    return None


def _copy_to_library(item: dict[str, Any], destination_path: Path, source_size: int) -> None:
    """Copy an item's file into its library folder, unless a file already holds its place.

    A library file is never replaced. A file with the same content there finishes the item with nothing to copy; a
    different one ends it as ``FAILED_CONFLICT``.

    Args:
        item: The work item, already hashed; its status and target path are updated in the queue.
        destination_path: The library folder.
        source_size: The size of the item's file.
    """
    item_id = item["id"]
    full_path = item["full_path"]
    filename = Path(full_path).name
    library_file = destination_path / filename
    tag = f"[I.ID: {item_id}]"

    try:
        existing_copy = _compare_with_existing_copy(library_file, source_size, item["content_hash"])
    except PermissionError as e:
        _report_permission_failure(item, destination_path, e)
        return

    if existing_copy == "different":
        _report_conflict(item, library_file)
        return

    item["target_path"] = str(destination_path.absolute())

    if existing_copy == "same":
        item["status"] = "DONE"
        _work_queue_manager.update(item)
        _activity_tracker.info(f"{tag} [{filename}] is already in the library with the same content. Nothing to copy.")
        return

    _work_queue_manager.update(item)

    try:
        copy_result = copy_file(full_path, destination_path)
    except CopyPermissionError as e:
        _report_permission_failure(item, destination_path, e)
        return

    if copy_result:
        item["status"] = "DONE"
        _work_queue_manager.update(item)
        _activity_tracker.info(f"{tag} All done with [{filename}]! \\o/")
        return

    _activity_tracker.warning(f"{tag} Failed to copy file [{filename}]. Will try again later.")


def _compare_with_existing_copy(library_file: Path, source_size: int, content_hash: str | None) -> str:
    """Compare a source file with what is already at its place in the library.

    The library file is read only when the sizes match.

    Args:
        library_file: The file's path in the library.
        source_size: The size of the source.
        content_hash: The hash of the source; None when it is empty.

    Returns:
        ``free`` when nothing is there, ``same`` when both are empty or their hashes match, otherwise ``different``.
    """
    try:
        existing_size = library_file.stat().st_size
    except FileNotFoundError:
        return "free"

    if existing_size != source_size:
        return "different"
    if source_size == 0 or _sha256(library_file) == content_hash:
        return "same"
    return "different"


@_activity_tracker.trace("hash_file")
def _hash_file(path: Path, size_bytes: int) -> str:
    """Return the SHA-256 of a file's content.

    Args:
        path: The file.
        size_bytes: The file's size, recorded on the span next to the hash.

    Returns:
        The hash, as hexadecimal text.
    """
    content_hash = _sha256(path)
    span = trace.get_current_span()
    if span.is_recording():
        span.set_attributes({"file.size_bytes": size_bytes, "file.content_hash": content_hash})
    return content_hash


def _check_library_copy(match: dict[str, Any]) -> tuple[str, Path | None]:
    """Check whether a finished item's file is still in the library.

    The check is on the file and not its folder, so a surviving folder does not read as ``present``.

    Args:
        match: The finished item's ``id``, ``target_path`` and ``filename``.

    Returns:
        The state and the path checked. The state is ``present``, ``gone``, ``unknown`` (the file could not be checked,
        or its stored folder fits no configured library root) or ``no_path`` (the item has no library copy, as for an
        archive).
    """
    target_path = match["target_path"]
    if target_path is None:
        return "no_path", None

    tag = f"[I.ID: {match['id']}]"
    folder = locate_in_library(target_path, library_root_pairs(_movies_base_path, _series_base_path))
    if folder is None:
        _activity_tracker.warning(f"{tag} Target path [{target_path}] is outside the configured library folders.")
        return "unknown", None

    library_file = folder / match["filename"]
    try:
        library_file.stat()
    except FileNotFoundError, NotADirectoryError:
        return "gone", library_file
    except OSError as e:
        _activity_tracker.warning(f"{tag} Could not check [{library_file}]: {e}")
        return "unknown", library_file
    return "present", library_file


def _report_duplicate(item: dict[str, Any], matches: list[dict[str, Any]]) -> None:
    """End a work item as ``DUPLICATE``, sending one Telegram alert only when every earlier library copy is gone.

    The status is terminal, so the item is not picked up again and the alert is not repeated.

    Args:
        item: The work item; its status and content hash are updated in the queue.
        matches: The finished items with the same content, oldest first.
    """
    span = trace.get_current_span()
    item_id = str(item["id"])
    filename = Path(item["full_path"]).name
    tag = f"[I.ID: {item_id}]"
    first_match_id = str(matches[0]["id"])
    checks = [_check_library_copy(match) for match in matches]
    states = [state for state, _ in checks]

    item["status"] = "DUPLICATE"
    _work_queue_manager.update(item)

    if span.is_recording():
        span.set_attributes(
            {
                "work_item.status": "DUPLICATE",
                "work_item.duplicate_of": first_match_id,
                "work_item.library_copy": states,
            }
        )

    earlier_items = ", ".join(str(match["id"]) for match in matches)
    _activity_tracker.info(
        f"{tag} [{filename}] has the same content as earlier item(s) {earlier_items}; library copy: "
        f"{', '.join(states)}. Not processing it again."
    )

    if all(state == "gone" for state in states):
        _activity_tracker.error(f"{tag} The library copy of [{filename}] is gone and was not copied again.")
        _send_alert(
            item_id,
            "A duplicate arrived, and the library copy is gone",
            {"File": filename, "Item": item_id, "Earlier item": first_match_id, "Expected at": checks[0][1]},
            "The file was not copied again. To force a copy, see the readme (Duplicates and conflicts).",
        )


def _report_conflict(item: dict[str, Any], library_file: Path) -> None:
    """End a work item as ``FAILED_CONFLICT`` and send one Telegram alert about it.

    The status is terminal, so the item is not picked up again and the alert is not repeated.

    Args:
        item: The work item; its status is updated in the queue, and its target path is left as it was.
        library_file: The different file that holds the item's place in the library.
    """
    span = trace.get_current_span()
    item_id = str(item["id"])
    source = item["full_path"]

    _activity_tracker.error(f"[I.ID: {item_id}] A different file is at [{library_file}]; not copying [{source}].")
    item["status"] = "FAILED_CONFLICT"
    _work_queue_manager.update(item)

    if span.is_recording():
        span.set_attribute("work_item.status", "FAILED_CONFLICT")

    _send_alert(
        item_id,
        "A different file is already in the library",
        {"Source": source, "Library": library_file, "Item": item_id},
        "The item is now FAILED_CONFLICT and nothing was written. To replace the library file, delete or move it, then "
        "re-queue the item (readme, Item statuses).",
    )


def _report_permission_failure(item: dict[str, Any], destination_path: Path | None, error: Exception) -> None:
    """End a work item as ``FAILED_PERMISSIONS`` and send one Telegram alert about it.

    The status is terminal, so the item is not picked up again and the alert is not repeated.

    Args:
        item: The work item; its status is updated in the queue.
        destination_path: The folder the file was being copied to; None when it was not known yet.
        error: The permission failure.
    """
    span = trace.get_current_span()
    item_id = str(item["id"])
    filename = Path(item["full_path"]).name

    _activity_tracker.error(f"[I.ID: {item_id}] Could not read or copy [{filename}]: {error}")
    item["status"] = "FAILED_PERMISSIONS"
    _work_queue_manager.update(item)

    if span.is_recording():
        span.set_attribute("work_item.status", "FAILED_PERMISSIONS")

    fields: dict[str, object] = {"File": filename, "Item": item_id}
    if destination_path is not None:
        fields["Destination"] = destination_path
    fields["Error"] = error
    _send_alert(
        item_id,
        "Could not read or copy a file: permission problem",
        fields,
        "The item is now FAILED_PERMISSIONS. Fix the permissions, then re-queue it (readme, Item statuses).",
    )


def _send_alert(item_id: str, headline: str, fields: Mapping[str, object], closing: str) -> None:
    """Build and send one Telegram alert, best-effort.

    Args:
        item_id: The work item, named in the log line when the alert cannot be sent.
        headline: The first line, in plain text.
        fields: Label and value of each detail line. Values are shown as escaped text.
        closing: The last line, in plain text.
    """
    try:
        details = "\n".join(
            f"<b>{label}</b>: <code>{html.escape(str(value))}</code>" for label, value in fields.items()
        )
        send_telegram_message(f"<b>⚠️ {headline}</b>\n{details}\n\n{closing}")
    except Exception as e:
        # Best-effort: the item's status and log line already record what happened. Only the class name is logged,
        # because a sending error can quote the bot token.
        _activity_tracker.warning(f"[I.ID: {item_id}] Alert not sent ({type(e).__name__}).")
