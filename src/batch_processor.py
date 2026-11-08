import html
import os
import time
from pathlib import Path
from typing import Any

from opentelemetry import trace

from src.data.activity_logger import ActivityTracker
from src.data.notification_repository import NotificationRepository
from src.data.work_queue_manager import WorkQueueManager
from src.helpers import resolve_destination
from src.tasks.check_for_file_stability import check_is_file_stable
from src.tasks.copy_file import CopyPermissionError, copy_file
from src.tasks.decompress_file import decompress_file
from src.tasks.identify_file import IdentifierError, IdentifierUnavailableError, identify_file
from src.tasks.send_telegram_message import send_telegram_message
from src.tasks.verify_batch_data import verify_batch_data
from src.utils import release_idle_memory

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
    batch_verification, batch_verification_details = verify_batch_data(current_batch_id, batch_data)
    _work_queue_manager.update_batch_verification(current_batch_id, batch_verification)

    try:
        notification_message = {
            "batch_id": current_batch_id,
            "items": batch_data,
            "verified": batch_verification,
            "verification_details": batch_verification_details,
        }
        _notification_agent.post_message(message=notification_message)
    except Exception as e:
        _activity_tracker.error(f"{tag} Error sending batch completion notification: {str(e)}")


@_activity_tracker.trace("_process_batch_item")
def _process_batch_item(item: dict[str, Any]) -> dict[str, Any] | None:
    """Process one work item: wait for the file to settle, identify it, then decompress or copy it.

    Args:
        item: The work item; its status, attempts, target path and media id are updated in the queue.

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

    destination_path = destination.path

    item["target_path"] = str(destination_path.absolute())
    _work_queue_manager.update(item)

    try:
        copy_result = copy_file(full_path, destination_path)
    except CopyPermissionError as e:
        _report_permission_failure(item, destination_path, e)
        return None

    if copy_result:
        item["status"] = "DONE"
        _work_queue_manager.update(item)
        _activity_tracker.info(f"{tag} All done with [{full_path_obj.name}]! \\o/")
        return None

    _activity_tracker.warning(f"{tag} Failed to copy file [{full_path_obj.name}]. Will try again later.")

    return None


def _report_permission_failure(item: dict[str, Any], destination_path: Path, error: CopyPermissionError) -> None:
    """End a work item as ``FAILED_PERMISSIONS`` and send one Telegram alert about it.

    The status is terminal, so the item is not picked up again and the alert is not repeated.

    Args:
        item: The work item; its status is updated in the queue.
        destination_path: The folder the file was being copied to.
        error: The permission failure.
    """
    span = trace.get_current_span()
    item_id = str(item["id"])
    filename = Path(item["full_path"]).name

    _activity_tracker.error(f"[I.ID: {item_id}] Could not finish copying [{filename}]: {error}")
    item["status"] = "FAILED_PERMISSIONS"
    _work_queue_manager.update(item)

    if span.is_recording():
        span.set_attribute("work_item.status", "FAILED_PERMISSIONS")

    message = (
        "<b>⚠️ Could not finish copying a file: permission problem</b>"
        f"\n<b>File</b>: <code>{html.escape(filename)}</code>"
        f"\n<b>Item</b>: <code>{html.escape(item_id)}</code>"
        f"\n<b>Destination</b>: <code>{html.escape(str(destination_path))}</code>"
        f"\n<b>Error</b>: <code>{html.escape(str(error))}</code>"
        "\n\nThe item is now FAILED_PERMISSIONS. Fix the permissions, then re-queue it (readme, Item statuses)."
    )
    try:
        send_telegram_message(message)
    except Exception as e:
        # Best-effort: the status and the error line above already record the failure. Only the class name is logged,
        # because a sending error can quote the bot token.
        _activity_tracker.warning(f"[I.ID: {item_id}] Permission alert not sent ({type(e).__name__}).")
