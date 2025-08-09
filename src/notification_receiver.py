import json
import html
from typing import Any, Dict, List

from raccoontools.shared.serializer import obj_dump_deserializer

from src.data.activity_logger import ActivityTracker
from src.data.notification_repository import NotificationRepository
from src.tasks.send_telegram_message import send_telegram_message

_activity_logger = ActivityTracker("Notification Receiver")
_notification_agent = NotificationRepository(client_id="smo-watchdog-notification-receiver")


def _get_insights_from_payload(payload):
    # Considering the payload, what useful insights  can we extract in a heuristic way?
    if not isinstance(payload, dict):
        return {
            "batch_id": None,
            "total_items": 0,
            "status_counts": {},
            "failed_items": [],
            "archive_counts": {"archives": 0, "main_archive_files": 0},
            "with_target_path": 0,
        }

    items: List[Dict[str, Any]] = payload.get("items") or []
    batch_id = payload.get("batch_id")

    status_counts: Dict[str, int] = {}
    failed_items: List[Dict[str, Any]] = []
    archives_count = 0
    main_archive_files_count = 0
    with_target_path = 0

    for item in items:
        if not isinstance(item, dict):
            continue

        status = item.get("status") or "UNKNOWN"
        status_counts[status] = status_counts.get(status, 0) + 1

        if status.startswith("FAILED"):
            failed_items.append(
                {
                    "id": item.get("id"),
                    "filename": item.get("filename"),
                    "full_path": item.get("full_path"),
                    "status": status,
                }
            )

        if bool(item.get("is_archive")):
            archives_count += 1

        if bool(item.get("is_main_archive_file")):
            main_archive_files_count += 1

        if item.get("target_path"):
            with_target_path += 1

    insights: Dict[str, Any] = {
        "batch_id": batch_id,
        "total_items": len(items),
        "status_counts": status_counts,
        "failed_items": failed_items,
        "archive_counts": {
            "archives": archives_count,
            "main_archive_files": main_archive_files_count,
        },
        "with_target_path": with_target_path,
    }

    return insights

def _get_summary_from_payload(payload):
    # From all the items in this batch, what is the summary?
    if not isinstance(payload, dict):
        return {
            "batch_id": None,
            "total": 0,
            "done": 0,
            "failed": 0,
            "pending": 0,
            "working": 0,
            "failed_retry": 0,
        }

    items: List[Dict[str, Any]] = payload.get("items") or []

    def count_status(prefix: str) -> int:
        return sum(1 for it in items if isinstance(it, dict) and str(it.get("status", "")).startswith(prefix))

    def count_exact(value: str) -> int:
        return sum(1 for it in items if isinstance(it, dict) and it.get("status") == value)

    summary: Dict[str, Any] = {
        "batch_id": payload.get("batch_id"),
        "total": len(items),
        "done": count_exact("DONE"),
        "failed": count_status("FAILED"),
        "pending": count_exact("PENDING"),
        "working": count_exact("WORKING"),
        "failed_retry": count_exact("FAILED_PROCESSING_RETRY"),
    }

    return summary

def _compose_notification_message(insights, summary):
    # Compose a nice message to send to Telegram.
    batch_id = insights.get("batch_id") or summary.get("batch_id") or "?"

    total = summary.get("total", 0)
    done = summary.get("done", 0)
    failed = summary.get("failed", 0)
    pending = summary.get("pending", 0)
    working = summary.get("working", 0)
    failed_retry = summary.get("failed_retry", 0)

    status_counts: Dict[str, int] = insights.get("status_counts", {})
    archive_counts: Dict[str, int] = insights.get("archive_counts", {})
    with_target_path = insights.get("with_target_path", 0)

    def fmt_kv_lines(mapping: Dict[str, int]) -> str:
        if not mapping:
            return ""
        ordered = sorted(mapping.items(), key=lambda kv: kv[0])
        return "\n".join(f"• <code>{html.escape(str(k))}</code>: <b>{v}</b>" for k, v in ordered)

    header = (
        f"<b>🎬 Batch completed</b>"\
        f"\n<b>Batch ID</b>: <code>{html.escape(str(batch_id))}</code>"
    )

    high_level = (
        f"\n\n<b>Summary</b>"\
        f"\n• <b>Total</b>: {total}"\
        f"\n• <b>Done</b>: {done}"\
        f"\n• <b>Failed</b>: {failed}"\
        f"\n• <b>Pending</b>: {pending}"\
        f"\n• <b>Working</b>: {working}"
    )

    if failed_retry:
        high_level += f"\n• <b>Failed (Retry)</b>: {failed_retry}"

    details = ""
    sc_text = fmt_kv_lines(status_counts)
    if sc_text:
        details += f"\n\n<b>Status breakdown</b>\n{sc_text}"

    ac_text = fmt_kv_lines(archive_counts)
    if ac_text:
        details += f"\n\n<b>Archives</b>\n{ac_text}"

    details += f"\n\n<b>Items with destination set</b>: {with_target_path}"

    failed_items: List[Dict[str, Any]] = insights.get("failed_items", [])
    if failed_items:
        def shorten(path: str, limit: int = 96) -> str:
            if path is None:
                return ""
            text = str(path)
            return text if len(text) <= limit else f"…{text[-limit:]}"

        failed_lines = []
        for it in failed_items[:20]:
            fname = it.get("filename") or (it.get("full_path") and it.get("full_path").split("/")[-1]) or "?"
            status = it.get("status") or "FAILED"
            path_preview = shorten(it.get("full_path") or "")
            failed_lines.append(
                f"• <b>{html.escape(str(status))}</b> — <code>{html.escape(str(fname))}</code>\n    <code>{html.escape(path_preview)}</code>"
            )

        more_note = ""
        if len(failed_items) > 20:
            more_note = f"\n… and {len(failed_items) - 20} more"

        details += f"\n\n<b>Failures</b>\n" + "\n".join(failed_lines) + more_note

    message = header + high_level + details
    return message.strip()


def _split_messages_to_prevent_message_too_long_error(message):
    # Split the message into smaller chunks if it's too long to send.'
    if message is None:
        return []

    max_len = 3900  # Telegram text limit is ~4096; keep a margin for safety
    text = str(message)
    if len(text) <= max_len:
        return [text]

    chunks: List[str] = []

    def flush(buffer: List[str]):
        if not buffer:
            return
        combined = "\n".join(buffer)
        if len(combined) <= max_len:
            chunks.append(combined)
            buffer.clear()
            return
        # Fallback to hard chunking
        start = 0
        while start < len(combined):
            chunks.append(combined[start : start + max_len])
            start += max_len
        buffer.clear()

    # Prefer splitting by paragraphs, then lines, then hard chunks
    paragraphs = text.split("\n\n")
    buffer: List[str] = []
    for para in paragraphs:
        if len(para) <= max_len:
            trial = ("\n\n".join(buffer + [para])).strip("\n")
            if buffer and len(trial) <= max_len:
                buffer.append(para)
            else:
                flush(buffer)
                buffer.append(para)
        else:
            # Split by lines
            lines = para.split("\n")
            for line in lines:
                if len(line) <= max_len:
                    trial = ("\n".join(buffer + [line])).strip("\n")
                    if buffer and len(trial) <= max_len:
                        buffer.append(line)
                    else:
                        flush(buffer)
                        buffer.append(line)
                else:
                    # Hard chunk this long line
                    flush(buffer)
                    start = 0
                    while start < len(line):
                        chunks.append(line[start : start + max_len])
                        start += max_len

    flush(buffer)
    return chunks

def _handle_notification(topic, payload_bytes):
    preview = payload_bytes[:256]
    _activity_logger.debug(f"Message on '{topic}': {preview}")

    payload = json.loads(payload_bytes, default=obj_dump_deserializer)
    insights = _get_insights_from_payload(payload)
    summary = _get_summary_from_payload(payload)
    message = _compose_notification_message(insights, summary)
    messages = _split_messages_to_prevent_message_too_long_error(message)

    for msg in messages:
        send_telegram_message(msg)

def handle_notification_messages():
    _activity_logger.debug("Starting to listen for notification messages...")
    _notification_agent.start_reading(message_handler=_handle_notification, background=False)
