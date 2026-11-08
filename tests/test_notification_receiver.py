import json
from typing import Any

import pytest

from src import notification_receiver as receiver_module
from src.notification_receiver import (
    _compose_notification_message,
    _get_insights_from_payload,
    _get_summary_from_payload,
    _handle_notification,
    _split_messages_to_prevent_message_too_long_error,
)

MAX_MESSAGE_LENGTH = 3900


def _payload() -> dict[str, Any]:
    return {
        "batch_id": "batch-1",
        "verified": True,
        "verification_details": {"a.mkv": {"size": True, "hash": True}},
        "items": [
            {"id": "1", "status": "DONE", "full_path": "/watch/a.mkv", "filename": "a.mkv", "target_path": "/m/A"},
            {"id": "2", "status": "FAILED_ID", "full_path": "/watch/<b>.mkv", "filename": "<b>.mkv"},
            {
                "id": "3",
                "status": "DONE",
                "full_path": "/watch/c.rar",
                "is_archive": True,
                "is_main_archive_file": True,
            },
        ],
    }


def test_get_insights_from_payload_counts_statuses_and_failures() -> None:
    insights = _get_insights_from_payload(_payload())

    assert insights["total_items"] == 3
    assert insights["status_counts"] == {"DONE": 2, "FAILED_ID": 1}
    assert [item["id"] for item in insights["failed_items"]] == ["2"]
    assert insights["archive_counts"] == {"archives": 1, "main_archive_files": 1}
    assert insights["with_target_path"] == 1


def test_get_summary_from_payload_groups_failures() -> None:
    summary = _get_summary_from_payload(_payload())

    assert (summary["total"], summary["done"], summary["failed"], summary["pending"]) == (3, 2, 1, 0)


@pytest.mark.parametrize("payload", [None, "text", 7])
def test_payload_that_is_not_an_object_gives_empty_insights_and_summary(payload: Any) -> None:
    assert _get_insights_from_payload(payload)["total_items"] == 0
    assert _get_summary_from_payload(payload)["total"] == 0


def test_compose_notification_message_escapes_file_names() -> None:
    payload = _payload()
    message = _compose_notification_message(
        _get_insights_from_payload(payload), _get_summary_from_payload(payload), True, payload["verification_details"]
    )

    assert "VERIFIED" in message
    assert "&lt;b&gt;.mkv" in message
    assert "<b>.mkv" not in message


def test_split_messages_short_message_is_one_chunk() -> None:
    assert _split_messages_to_prevent_message_too_long_error("hello") == ["hello"]


def test_split_messages_none_is_no_chunk() -> None:
    assert _split_messages_to_prevent_message_too_long_error(None) == []


def test_split_messages_long_message_respects_the_limit() -> None:
    paragraphs = "\n\n".join("x" * 1000 for _ in range(10))
    single_line = "y" * 9000

    for message in (paragraphs, single_line):
        chunks = _split_messages_to_prevent_message_too_long_error(message)
        assert len(chunks) > 1
        assert all(len(chunk) <= MAX_MESSAGE_LENGTH for chunk in chunks)
        assert "".join(chunks).replace("\n", "") == message.replace("\n", "")


def test_is_connected_reports_the_receiver_connection() -> None:
    assert receiver_module.is_connected() is False


def test_handle_notification_sends_the_composed_report(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[str] = []

    def fake_send(message: str) -> bool:
        sent.append(message)
        return True

    monkeypatch.setattr(receiver_module, "send_telegram_message", fake_send)

    _handle_notification("notifications", json.dumps(_payload()).encode())

    assert len(sent) == 1
    assert "batch-1" in sent[0]
