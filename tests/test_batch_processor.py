import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from src import batch_processor as processor_module
from src.batch_processor import _process_batch_item, process_batch
from src.secret_store import SecretsError
from src.tasks.copy_file import CopyPermissionError
from src.tasks.identify_file import IdentifierError, IdentifierUnavailableError

MOVIES = Path(os.environ["MOVIES_BASE_FOLDER"])
SERIES = Path(os.environ["SERIES_BASE_FOLDER"])


class _Recorder:
    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []
        self.copies: list[tuple[str, Path]] = []


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    recorded = _Recorder()
    monkeypatch.setattr(
        processor_module._work_queue_manager, "update", lambda item: recorded.updates.append(dict(item))
    )
    monkeypatch.setattr(processor_module, "check_is_file_stable", lambda path: True)

    def fake_copy(source: str, destination: Path) -> bool:
        recorded.copies.append((source, destination))
        return True

    monkeypatch.setattr(processor_module, "copy_file", fake_copy)
    return recorded


@pytest.fixture
def media_file(tmp_path: Path) -> Path:
    path = tmp_path / "Movie.2020.1080p.mkv"
    path.write_bytes(b"video")
    return path


def _item(path: Path, is_archive: bool = False, attempts: int = 1) -> dict[str, Any]:
    return {"id": "item-1", "full_path": str(path), "is_archive": is_archive, "attempts": attempts, "status": "WORKING"}


def _identify_returns(monkeypatch: pytest.MonkeyPatch, media_info: dict[str, Any] | None) -> None:
    monkeypatch.setattr(processor_module, "identify_file", lambda path: media_info)


def test_missing_file_is_marked_failed_missing(recorder: _Recorder, tmp_path: Path) -> None:
    assert _process_batch_item(_item(tmp_path / "gone.mkv")) is None
    assert recorder.updates[-1]["status"] == "FAILED_MISSING"


def test_unstable_file_is_retried_with_attempt_refunded(
    recorder: _Recorder, media_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(processor_module, "check_is_file_stable", lambda path: False)

    result = _process_batch_item(_item(media_file, attempts=2))

    assert result is not None
    assert recorder.updates[-1]["attempts"] == 1


def test_unidentified_file_is_marked_failed_id(
    recorder: _Recorder, media_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _identify_returns(monkeypatch, None)

    assert _process_batch_item(_item(media_file)) is None
    assert recorder.updates[-1]["status"] == "FAILED_ID"
    assert recorder.copies == []


def _identify_raises(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    def failing_identify(path: str) -> dict[str, Any] | None:
        raise error

    monkeypatch.setattr(processor_module, "identify_file", failing_identify)


def test_identifier_unavailable_refunds_the_attempt_and_raises(
    recorder: _Recorder, media_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _identify_raises(monkeypatch, IdentifierUnavailableError("Media identifier unavailable (HTTP 503)"))

    with pytest.raises(IdentifierUnavailableError):
        _process_batch_item(_item(media_file, attempts=3))

    assert recorder.updates[-1]["attempts"] == 2
    assert recorder.updates[-1]["status"] == "WORKING"
    assert recorder.copies == []


def test_identifier_error_keeps_the_attempt_and_asks_for_a_retry(
    recorder: _Recorder, media_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _identify_raises(monkeypatch, IdentifierError("Media identifier failed (HTTP 500)"))

    result = _process_batch_item(_item(media_file, attempts=3))

    assert result is not None
    assert result["attempts"] == 3
    assert recorder.updates == []
    assert recorder.copies == []


@pytest.fixture
def batch_side_effects(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    manager = processor_module._work_queue_manager
    monkeypatch.setattr(manager, "move_working_items_back_to_pending", lambda batch_id: calls.append("sweep"))
    monkeypatch.setattr(manager, "set_batch_as_done", lambda batch_id: calls.append("done"))
    monkeypatch.setattr(manager, "get_batch_data", lambda batch_id: [])
    monkeypatch.setattr(manager, "update_batch_verification", lambda batch_id, verified: None)
    monkeypatch.setattr(processor_module, "verify_batch_data", lambda batch_id, data: (False, {}))
    monkeypatch.setattr(processor_module._notification_agent, "post_message", lambda message: calls.append("notify"))
    return calls


def test_process_batch_identifier_outage_logs_one_warning_and_leaves_items_for_the_sweep(
    recorder: _Recorder, media_file: Path, monkeypatch: pytest.MonkeyPatch, batch_side_effects: list[str]
) -> None:
    asked: list[str] = []

    def unavailable_identify(path: str) -> dict[str, Any] | None:
        asked.append(path)
        raise IdentifierUnavailableError("Media identifier unreachable (ConnectionError)")

    monkeypatch.setattr(processor_module, "identify_file", unavailable_identify)
    warnings: list[str] = []
    monkeypatch.setattr(processor_module._activity_tracker, "warning", warnings.append)
    second = dict(_item(media_file, attempts=3), id="item-2")

    process_batch([_item(media_file, attempts=3), second], "batch-1")

    assert len(asked) == 2
    assert len(warnings) == 1
    assert "2 identification attempt(s)" in warnings[0]
    assert [(update["id"], update["attempts"], update["status"]) for update in recorder.updates] == [
        ("item-1", 2, "WORKING"),
        ("item-2", 2, "WORKING"),
    ]
    assert batch_side_effects == ["sweep", "done", "notify"]


def test_process_batch_unexpected_error_marks_failed_processing(
    recorder: _Recorder, media_file: Path, monkeypatch: pytest.MonkeyPatch, batch_side_effects: list[str]
) -> None:
    _identify_raises(monkeypatch, RuntimeError("database went away"))

    process_batch([_item(media_file)], "batch-1")

    assert recorder.updates[-1]["status"] == "FAILED_PROCESSING"
    assert batch_side_effects == ["sweep", "done", "notify"]


def test_movie_is_copied_to_title_and_year_folder(
    recorder: _Recorder, media_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _identify_returns(monkeypatch, {"id": "cache-1", "media_type": "movie", "title": "The Matrix", "year": 1999})

    assert _process_batch_item(_item(media_file)) is None
    assert recorder.copies == [(str(media_file), MOVIES / "The-Matrix--1999")]
    assert recorder.updates[-1]["status"] == "DONE"
    assert recorder.updates[-1]["media_info_cache_id"] == "cache-1"


def test_movie_without_year_uses_title_only(
    recorder: _Recorder, media_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _identify_returns(monkeypatch, {"id": "cache-1", "media_type": "movie", "title": "The Matrix", "year": None})

    _process_batch_item(_item(media_file))

    assert recorder.copies[0][1] == MOVIES / "The-Matrix"


@pytest.mark.parametrize(("season", "folder"), [(2, "Season02"), (0, "Season00")])
def test_episode_is_copied_to_series_season_folder(
    recorder: _Recorder, media_file: Path, monkeypatch: pytest.MonkeyPatch, season: int, folder: str
) -> None:
    media_info = {"id": "cache-2", "media_type": "tv", "title": "Slow Horses", "season": season, "tmdb_id": 99}
    _identify_returns(monkeypatch, media_info)

    _process_batch_item(_item(media_file))

    assert recorder.copies[0][1] == SERIES / "Slow-Horses" / folder


@pytest.mark.parametrize(
    "media_info",
    [
        {"id": "cache-3", "media_type": "unknown", "title": "A"},
        {"id": "cache-3", "media_type": "movie", "title": None},
        {"id": "cache-3", "media_type": "movie", "title": "???"},
        {"id": "cache-3", "media_type": "tv", "title": "Show", "season": None},
    ],
)
def test_unusable_metadata_is_marked_failed_id_without_copying(
    recorder: _Recorder, media_file: Path, monkeypatch: pytest.MonkeyPatch, media_info: dict[str, Any]
) -> None:
    _identify_returns(monkeypatch, media_info)

    assert _process_batch_item(_item(media_file)) is None
    assert recorder.updates[-1]["status"] == "FAILED_ID"
    assert recorder.copies == []


def test_archive_is_decompressed_and_marked_done(
    recorder: _Recorder, media_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _identify_returns(monkeypatch, {"id": "cache-1", "media_type": "movie", "title": "A"})
    monkeypatch.setattr(processor_module, "decompress_file", lambda path: True)

    assert _process_batch_item(_item(media_file, is_archive=True)) is None
    assert recorder.updates[-1]["status"] == "DONE"


def test_archive_that_fails_to_decompress_is_retried(
    recorder: _Recorder, media_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _identify_returns(monkeypatch, {"id": "cache-1", "media_type": "movie", "title": "A"})
    monkeypatch.setattr(processor_module, "decompress_file", lambda path: False)

    assert _process_batch_item(_item(media_file, is_archive=True)) is not None


def test_failed_copy_leaves_item_unfinished(
    recorder: _Recorder, media_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _identify_returns(monkeypatch, {"id": "cache-1", "media_type": "movie", "title": "A", "year": 2001})
    monkeypatch.setattr(processor_module, "copy_file", lambda source, destination: False)

    _process_batch_item(_item(media_file))

    assert recorder.updates[-1]["status"] == "WORKING"


@pytest.fixture
def denied_copy(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    _identify_returns(monkeypatch, {"id": "cache-1", "media_type": "movie", "title": "A", "year": 2001})
    attempts: list[str] = []

    def fake_copy(source: str, destination: Path) -> bool:
        attempts.append(source)
        raise CopyPermissionError("Permission denied: <source>")

    monkeypatch.setattr(processor_module, "copy_file", fake_copy)
    return attempts


@pytest.fixture
def telegram_messages(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    messages: list[str] = []

    def fake_send(message: str) -> bool:
        messages.append(message)
        return True

    monkeypatch.setattr(processor_module, "send_telegram_message", fake_send)
    return messages


def test_copy_permission_failure_is_terminal_with_one_alert(
    recorder: _Recorder, media_file: Path, denied_copy: list[str], telegram_messages: list[str]
) -> None:
    result = _process_batch_item(_item(media_file))

    assert result is None
    assert recorder.updates[-1]["status"] == "FAILED_PERMISSIONS"
    assert len(telegram_messages) == 1


def test_copy_permission_failure_alerts_once_across_the_batch_and_its_retry_pass(
    recorder: _Recorder,
    media_file: Path,
    denied_copy: list[str],
    telegram_messages: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(processor_module._work_queue_manager, "move_working_items_back_to_pending", lambda batch: None)
    monkeypatch.setattr(processor_module._work_queue_manager, "set_batch_as_done", lambda batch: None)
    monkeypatch.setattr(processor_module._work_queue_manager, "get_batch_data", lambda batch: [])
    monkeypatch.setattr(processor_module._work_queue_manager, "update_batch_verification", lambda batch, ok: None)
    monkeypatch.setattr(processor_module._notification_agent, "post_message", lambda message: None)

    process_batch([_item(media_file)], "batch-1")

    assert len(denied_copy) == 1
    assert len(telegram_messages) == 1


def test_permission_alert_escapes_values_and_accepts_a_uuid_item_id(
    recorder: _Recorder, tmp_path: Path, denied_copy: list[str], telegram_messages: list[str]
) -> None:
    item_id = uuid4()
    source = tmp_path / "Tom & Jerry.mkv"
    source.write_bytes(b"video")

    _process_batch_item(dict(_item(source), id=item_id))

    assert "Tom &amp; Jerry.mkv" in telegram_messages[0]
    assert "Permission denied: &lt;source&gt;" in telegram_messages[0]
    assert str(item_id) in telegram_messages[0]


def test_permission_alert_rejected_by_telegram_keeps_the_status(
    recorder: _Recorder, media_file: Path, denied_copy: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(processor_module, "send_telegram_message", lambda message: False)

    assert _process_batch_item(_item(media_file)) is None
    assert recorder.updates[-1]["status"] == "FAILED_PERMISSIONS"


def test_permission_alert_that_raises_keeps_the_status(
    recorder: _Recorder, media_file: Path, denied_copy: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_send(message: str) -> bool:
        raise SecretsError("Infisical cannot be read")

    monkeypatch.setattr(processor_module, "send_telegram_message", failing_send)

    assert _process_batch_item(_item(media_file)) is None
    assert recorder.updates[-1]["status"] == "FAILED_PERMISSIONS"
