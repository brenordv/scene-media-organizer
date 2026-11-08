import hashlib
import html
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
from src.utils import _sha256
from tests.fakes import FakeDatabase, RecordingSpan

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


def test_permission_alert_during_a_copy_names_the_destination(
    recorder: _Recorder, media_file: Path, denied_copy: list[str], telegram_messages: list[str]
) -> None:
    _process_batch_item(_item(media_file))

    assert "Could not read or copy a file: permission problem" in telegram_messages[0]
    assert "<b>Destination</b>" in telegram_messages[0]


FILM = {"id": "cache-1", "media_type": "movie", "title": "A", "year": 2001}


@pytest.fixture
def hashed_paths(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    read: list[Path] = []

    def recording_sha256(path: Path) -> str:
        read.append(path)
        return _sha256(path)

    monkeypatch.setattr(processor_module, "_sha256", recording_sha256)
    return read


def _deny_hashing(monkeypatch: pytest.MonkeyPatch, denied: Path, error: OSError) -> None:
    def guarded_sha256(path: Path) -> str:
        if path == denied:
            raise error
        return _sha256(path)

    monkeypatch.setattr(processor_module, "_sha256", guarded_sha256)


def _deny_stat(monkeypatch: pytest.MonkeyPatch, denied: Path, error: OSError) -> None:
    real_stat = Path.stat

    def guarded_stat(self: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if self == denied:
            raise error
        return real_stat(self, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", guarded_stat)


def test_hash_is_saved_on_the_item_and_the_file_is_copied(
    recorder: _Recorder, media_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _identify_returns(monkeypatch, FILM)

    _process_batch_item(_item(media_file))

    assert recorder.updates[-1]["content_hash"] == hashlib.sha256(b"video").hexdigest()
    assert recorder.updates[-1]["status"] == "DONE"
    assert len(recorder.copies) == 1


def test_empty_file_is_not_hashed_and_skips_the_duplicate_check(
    recorder: _Recorder,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    hashed_paths: list[Path],
) -> None:
    _identify_returns(monkeypatch, FILM)
    empty = tmp_path / "empty.mkv"
    empty.write_bytes(b"")
    lookups: list[str] = []

    def recording_lookup(content_hash: str) -> list[dict[str, Any]]:
        lookups.append(content_hash)
        return []

    monkeypatch.setattr(processor_module._work_queue_manager, "find_done_items_by_hash", recording_lookup)

    _process_batch_item(_item(empty))

    assert hashed_paths == []
    assert lookups == []
    assert recorder.updates[-1]["content_hash"] is None
    assert recorder.updates[-1]["status"] == "DONE"


def test_requeued_item_that_became_empty_loses_its_old_hash(
    recorder: _Recorder, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _identify_returns(monkeypatch, FILM)
    empty = tmp_path / "empty.mkv"
    empty.write_bytes(b"")

    _process_batch_item(dict(_item(empty), content_hash="hash-of-the-old-content"))

    assert recorder.updates[-1]["content_hash"] is None


def test_identifier_unavailable_does_not_read_the_file(
    recorder: _Recorder, media_file: Path, monkeypatch: pytest.MonkeyPatch, hashed_paths: list[Path]
) -> None:
    _identify_raises(monkeypatch, IdentifierUnavailableError("Media identifier unavailable (HTTP 503)"))

    with pytest.raises(IdentifierUnavailableError):
        _process_batch_item(_item(media_file))

    assert hashed_paths == []


def test_unidentified_file_is_not_read(
    recorder: _Recorder, media_file: Path, monkeypatch: pytest.MonkeyPatch, hashed_paths: list[Path]
) -> None:
    _identify_returns(monkeypatch, None)

    _process_batch_item(_item(media_file))

    assert hashed_paths == []


def test_unreadable_source_is_terminal_with_one_alert_and_no_destination_line(
    recorder: _Recorder, media_file: Path, monkeypatch: pytest.MonkeyPatch, telegram_messages: list[str]
) -> None:
    _identify_returns(monkeypatch, FILM)
    _deny_hashing(monkeypatch, media_file, PermissionError(13, "Permission denied", str(media_file)))

    result = _process_batch_item(_item(media_file))

    assert result is None
    assert recorder.updates[-1]["status"] == "FAILED_PERMISSIONS"
    assert recorder.copies == []
    assert len(telegram_messages) == 1
    assert "Destination" not in telegram_messages[0]


def test_source_stat_denied_is_terminal_with_one_alert(
    recorder: _Recorder, media_file: Path, monkeypatch: pytest.MonkeyPatch, telegram_messages: list[str]
) -> None:
    _identify_returns(monkeypatch, FILM)
    _deny_stat(monkeypatch, media_file, PermissionError(13, "Permission denied", str(media_file)))

    result = _process_batch_item(_item(media_file))

    assert result is None
    assert recorder.updates[-1]["status"] == "FAILED_PERMISSIONS"
    assert len(telegram_messages) == 1


def test_unreadable_source_records_its_status_on_the_span(
    recorder: _Recorder,
    media_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    telegram_messages: list[str],
    recording_span: RecordingSpan,
) -> None:
    _identify_returns(monkeypatch, FILM)
    _deny_hashing(monkeypatch, media_file, PermissionError(13, "Permission denied", str(media_file)))

    _process_batch_item(_item(media_file))

    assert recording_span.attributes["work_item.status"] == "FAILED_PERMISSIONS"


def test_unreadable_archive_alerts_and_is_not_decompressed(
    recorder: _Recorder, media_file: Path, monkeypatch: pytest.MonkeyPatch, telegram_messages: list[str]
) -> None:
    _identify_returns(monkeypatch, FILM)
    _deny_hashing(monkeypatch, media_file, PermissionError(13, "Permission denied", str(media_file)))
    decompressed: list[str] = []
    monkeypatch.setattr(processor_module, "decompress_file", decompressed.append)

    assert _process_batch_item(_item(media_file, is_archive=True)) is None
    assert recorder.updates[-1]["status"] == "FAILED_PERMISSIONS"
    assert decompressed == []
    assert len(telegram_messages) == 1


def test_other_read_error_while_hashing_ends_as_failed_processing_without_an_alert(
    recorder: _Recorder,
    media_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    batch_side_effects: list[str],
    telegram_messages: list[str],
) -> None:
    _identify_returns(monkeypatch, FILM)
    _deny_hashing(monkeypatch, media_file, OSError(5, "Input/output error"))

    process_batch([_item(media_file)], "batch-1")

    assert recorder.updates[-1]["status"] == "FAILED_PROCESSING"
    assert telegram_messages == []


@pytest.fixture
def library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "library"
    folder.mkdir()
    monkeypatch.setattr(processor_module, "_movies_base_path", folder)
    return folder


def _filed_copy(library: Path, name: str, content: bytes = b"video") -> Path:
    path = library / "A--2001" / name
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(content)
    return path


def test_duplicate_with_its_library_copy_present_ends_without_copy_or_alert(
    recorder: _Recorder,
    media_file: Path,
    library: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_db: FakeDatabase,
    telegram_messages: list[str],
) -> None:
    _identify_returns(monkeypatch, FILM)
    _filed_copy(library, "earlier.mkv")
    fake_db.queue_result([("earlier-1", str(library / "A--2001"), "earlier.mkv")])

    result = _process_batch_item(_item(media_file))

    assert result is None
    assert recorder.updates[-1]["status"] == "DUPLICATE"
    assert recorder.updates[-1]["content_hash"] == hashlib.sha256(b"video").hexdigest()
    assert recorder.copies == []
    assert telegram_messages == []


def test_duplicate_log_names_every_match_and_the_library_check(
    recorder: _Recorder, media_file: Path, library: Path, monkeypatch: pytest.MonkeyPatch, fake_db: FakeDatabase
) -> None:
    _identify_returns(monkeypatch, FILM)
    _filed_copy(library, "earlier.mkv")
    fake_db.queue_result(
        [("earlier-1", str(library / "A--2001"), "earlier.mkv"), ("earlier-2", str(library / "B"), "other.mkv")]
    )
    lines: list[str] = []
    monkeypatch.setattr(processor_module._activity_tracker, "info", lines.append)

    _process_batch_item(_item(media_file))

    assert "earlier-1, earlier-2" in lines[-1]
    assert "library copy: present, gone" in lines[-1]


def test_duplicate_with_every_library_copy_gone_sends_one_alert(
    recorder: _Recorder,
    media_file: Path,
    library: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_db: FakeDatabase,
    telegram_messages: list[str],
) -> None:
    _identify_returns(monkeypatch, FILM)
    fake_db.queue_result(
        [("earlier-1", str(library / "A--2001"), "gone.mkv"), ("earlier-2", str(library / "B"), "gone-too.mkv")]
    )

    _process_batch_item(_item(media_file))

    assert recorder.updates[-1]["status"] == "DUPLICATE"
    assert recorder.copies == []
    assert len(telegram_messages) == 1
    assert "earlier-1" in telegram_messages[0]
    assert html.escape(str(library / "A--2001" / "gone.mkv")) in telegram_messages[0]
    assert "not copied again" in telegram_messages[0]


def test_duplicate_whose_folder_survives_but_file_is_gone_alerts(
    recorder: _Recorder,
    media_file: Path,
    library: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_db: FakeDatabase,
    telegram_messages: list[str],
) -> None:
    _identify_returns(monkeypatch, FILM)
    _filed_copy(library, "another-file.mkv")
    fake_db.queue_result([("earlier-1", str(library / "A--2001"), "gone.mkv")])

    _process_batch_item(_item(media_file))

    assert len(telegram_messages) == 1


def _match_rows(library: Path, kinds: list[str]) -> list[tuple[Any, ...]]:
    rows: dict[str, tuple[Any, ...]] = {
        "present": ("earlier-1", str(library / "A--2001"), "earlier.mkv"),
        "gone": ("earlier-2", str(library / "B"), "gone.mkv"),
        "unknown": ("earlier-3", "/old-mount/A--2001", "elsewhere.mkv"),
        "no_path": ("earlier-4", None, "release.rar"),
    }
    return [rows[kind] for kind in kinds]


@pytest.mark.parametrize(
    "kinds",
    [
        ["present", "gone"],
        ["gone", "present"],
        ["gone", "unknown"],
        ["unknown", "gone"],
        ["gone", "no_path"],
        ["no_path", "gone"],
    ],
)
def test_duplicate_with_any_copy_not_known_to_be_gone_sends_no_alert(
    recorder: _Recorder,
    media_file: Path,
    library: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_db: FakeDatabase,
    telegram_messages: list[str],
    kinds: list[str],
) -> None:
    _identify_returns(monkeypatch, FILM)
    _filed_copy(library, "earlier.mkv")
    fake_db.queue_result(_match_rows(library, kinds))
    lines: list[str] = []
    monkeypatch.setattr(processor_module._activity_tracker, "info", lines.append)

    _process_batch_item(_item(media_file))

    assert recorder.updates[-1]["status"] == "DUPLICATE"
    assert telegram_messages == []
    assert f"library copy: {', '.join(kinds)}." in lines[-1]


@pytest.mark.parametrize(
    "error",
    [PermissionError(13, "Permission denied"), OSError(5, "Input/output error")],
    ids=["permission", "io-error"],
)
def test_duplicate_library_copy_that_cannot_be_checked_reads_unknown_and_sends_no_alert(
    recorder: _Recorder,
    media_file: Path,
    library: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_db: FakeDatabase,
    telegram_messages: list[str],
    error: OSError,
) -> None:
    _identify_returns(monkeypatch, FILM)
    filed = _filed_copy(library, "earlier.mkv")
    _deny_stat(monkeypatch, filed, error)
    fake_db.queue_result([("earlier-1", str(library / "A--2001"), "earlier.mkv")])
    warnings: list[str] = []
    lines: list[str] = []
    monkeypatch.setattr(processor_module._activity_tracker, "warning", warnings.append)
    monkeypatch.setattr(processor_module._activity_tracker, "info", lines.append)

    _process_batch_item(_item(media_file))

    assert recorder.updates[-1]["status"] == "DUPLICATE"
    assert telegram_messages == []
    assert "Could not check" in warnings[0]
    assert "library copy: unknown." in lines[-1]


def test_duplicate_whose_library_folder_became_a_file_reads_gone_and_alerts(
    recorder: _Recorder,
    media_file: Path,
    library: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_db: FakeDatabase,
    telegram_messages: list[str],
) -> None:
    _identify_returns(monkeypatch, FILM)
    expected = library / "A--2001" / "earlier.mkv"
    _deny_stat(monkeypatch, expected, NotADirectoryError(20, "Not a directory", str(expected)))
    fake_db.queue_result([("earlier-1", str(library / "A--2001"), "earlier.mkv")])

    _process_batch_item(_item(media_file))

    assert len(telegram_messages) == 1


def test_duplicate_target_in_container_form_is_checked_under_the_configured_folder(
    recorder: _Recorder,
    media_file: Path,
    library: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_db: FakeDatabase,
    telegram_messages: list[str],
) -> None:
    _identify_returns(monkeypatch, FILM)
    _filed_copy(library, "earlier.mkv")
    fake_db.queue_result([("earlier-1", "/movies/A--2001", "earlier.mkv")])
    lines: list[str] = []
    monkeypatch.setattr(processor_module._activity_tracker, "info", lines.append)

    _process_batch_item(_item(media_file))

    assert telegram_messages == []
    assert "library copy: present." in lines[-1]


def test_duplicate_gone_alert_points_to_the_readme_section_that_has_the_force_copy_steps(
    recorder: _Recorder,
    media_file: Path,
    library: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_db: FakeDatabase,
    telegram_messages: list[str],
) -> None:
    _identify_returns(monkeypatch, FILM)
    fake_db.queue_result([("earlier-1", "/movies/A--2001", "gone.mkv")])

    _process_batch_item(_item(media_file))

    assert html.escape(str(library / "A--2001" / "gone.mkv")) in telegram_messages[0]
    assert "readme (Duplicates and conflicts)" in telegram_messages[0]


def test_duplicate_records_its_outcome_on_the_span(
    recorder: _Recorder,
    media_file: Path,
    library: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_db: FakeDatabase,
    recording_span: RecordingSpan,
) -> None:
    _identify_returns(monkeypatch, FILM)
    _filed_copy(library, "earlier.mkv")
    fake_db.queue_result(_match_rows(library, ["present", "gone"]))

    _process_batch_item(_item(media_file))

    assert recording_span.attributes["work_item.status"] == "DUPLICATE"
    assert recording_span.attributes["work_item.duplicate_of"] == "earlier-1"
    assert recording_span.attributes["work_item.library_copy"] == ["present", "gone"]
    assert recording_span.attributes["file.content_hash"] == hashlib.sha256(b"video").hexdigest()
    assert recording_span.attributes["file.size_bytes"] == 5


def test_duplicate_target_outside_every_library_folder_warns_and_sends_no_alert(
    recorder: _Recorder,
    media_file: Path,
    library: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_db: FakeDatabase,
    telegram_messages: list[str],
) -> None:
    _identify_returns(monkeypatch, FILM)
    fake_db.queue_result([("earlier-1", "/old-mount/A--2001", "earlier.mkv")])
    warnings: list[str] = []
    monkeypatch.setattr(processor_module._activity_tracker, "warning", warnings.append)

    _process_batch_item(_item(media_file))

    assert recorder.updates[-1]["status"] == "DUPLICATE"
    assert telegram_messages == []
    assert "outside the configured library folders" in warnings[0]


def test_duplicate_of_an_archive_with_no_target_path_sends_no_alert(
    recorder: _Recorder,
    media_file: Path,
    library: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_db: FakeDatabase,
    telegram_messages: list[str],
) -> None:
    _identify_returns(monkeypatch, FILM)
    decompressed: list[str] = []
    monkeypatch.setattr(processor_module, "decompress_file", decompressed.append)
    fake_db.queue_result([("earlier-1", None, "release.rar")])

    _process_batch_item(_item(media_file, is_archive=True))

    assert recorder.updates[-1]["status"] == "DUPLICATE"
    assert decompressed == []
    assert telegram_messages == []


def test_duplicate_alert_that_raises_keeps_the_status(
    recorder: _Recorder, media_file: Path, library: Path, monkeypatch: pytest.MonkeyPatch, fake_db: FakeDatabase
) -> None:
    _identify_returns(monkeypatch, FILM)
    fake_db.queue_result([("earlier-1", str(library / "A--2001"), "gone.mkv")])
    warnings: list[str] = []
    monkeypatch.setattr(processor_module._activity_tracker, "warning", warnings.append)

    def failing_send(message: str) -> bool:
        raise SecretsError("Infisical cannot be read")

    monkeypatch.setattr(processor_module, "send_telegram_message", failing_send)

    assert _process_batch_item(_item(media_file)) is None
    assert recorder.updates[-1]["status"] == "DUPLICATE"
    assert warnings == ["[I.ID: item-1] Alert not sent (SecretsError)."]


def test_duplicate_alert_escapes_values_and_accepts_uuid_ids(
    recorder: _Recorder,
    tmp_path: Path,
    library: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_db: FakeDatabase,
    telegram_messages: list[str],
) -> None:
    _identify_returns(monkeypatch, FILM)
    source = tmp_path / "Tom & Jerry.mkv"
    source.write_bytes(b"video")
    item_id, earlier_id = uuid4(), uuid4()
    fake_db.queue_result([(earlier_id, str(library / "A--2001"), "Tom & Jerry.mkv")])

    _process_batch_item(dict(_item(source), id=item_id))

    assert "Tom &amp; Jerry.mkv" in telegram_messages[0]
    assert str(item_id) in telegram_messages[0]
    assert str(earlier_id) in telegram_messages[0]


def test_requeued_done_item_is_processed_again_when_nothing_else_matches(
    recorder: _Recorder, media_file: Path, library: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _identify_returns(monkeypatch, FILM)

    _process_batch_item(dict(_item(media_file), content_hash=hashlib.sha256(b"video").hexdigest()))

    assert recorder.updates[-1]["status"] == "DONE"
    assert len(recorder.copies) == 1


def test_same_content_already_at_the_destination_finishes_without_copying(
    recorder: _Recorder, media_file: Path, library: Path, monkeypatch: pytest.MonkeyPatch, telegram_messages: list[str]
) -> None:
    _identify_returns(monkeypatch, FILM)
    _filed_copy(library, media_file.name)

    _process_batch_item(_item(media_file))

    assert recorder.updates[-1]["status"] == "DONE"
    assert recorder.updates[-1]["target_path"] == str((library / "A--2001").absolute())
    assert recorder.copies == []
    assert telegram_messages == []


@pytest.mark.parametrize("existing_content", [b"VIDEO", b"a different size"], ids=["same-size", "other-size"])
def test_different_file_at_the_destination_is_a_conflict_and_is_left_alone(
    recorder: _Recorder,
    media_file: Path,
    library: Path,
    monkeypatch: pytest.MonkeyPatch,
    telegram_messages: list[str],
    existing_content: bytes,
) -> None:
    _identify_returns(monkeypatch, FILM)
    existing = _filed_copy(library, media_file.name, existing_content)

    result = _process_batch_item(_item(media_file))

    assert result is None
    assert recorder.updates[-1]["status"] == "FAILED_CONFLICT"
    assert "target_path" not in recorder.updates[-1]
    assert recorder.copies == []
    assert existing.read_bytes() == existing_content
    assert len(telegram_messages) == 1


def test_conflict_records_its_status_on_the_span(
    recorder: _Recorder,
    media_file: Path,
    library: Path,
    monkeypatch: pytest.MonkeyPatch,
    recording_span: RecordingSpan,
) -> None:
    _identify_returns(monkeypatch, FILM)
    _filed_copy(library, media_file.name, b"other content")

    _process_batch_item(_item(media_file))

    assert recording_span.attributes["work_item.status"] == "FAILED_CONFLICT"


def test_different_size_conflict_does_not_read_the_library_file(
    recorder: _Recorder,
    media_file: Path,
    library: Path,
    monkeypatch: pytest.MonkeyPatch,
    telegram_messages: list[str],
    hashed_paths: list[Path],
) -> None:
    _identify_returns(monkeypatch, FILM)
    _filed_copy(library, media_file.name, b"a different size")

    _process_batch_item(_item(media_file))

    assert hashed_paths == [media_file]


def test_conflict_alert_names_both_paths_and_escapes_them(
    recorder: _Recorder, tmp_path: Path, library: Path, monkeypatch: pytest.MonkeyPatch, telegram_messages: list[str]
) -> None:
    _identify_returns(monkeypatch, FILM)
    source = tmp_path / "Tom & Jerry.mkv"
    source.write_bytes(b"video")
    existing = _filed_copy(library, source.name, b"other content")

    _process_batch_item(_item(source))

    assert html.escape(str(source)) in telegram_messages[0]
    assert html.escape(str(existing)) in telegram_messages[0]
    assert "Tom &amp; Jerry.mkv" in telegram_messages[0]


def test_conflict_alert_that_raises_keeps_the_status(
    recorder: _Recorder, media_file: Path, library: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _identify_returns(monkeypatch, FILM)
    _filed_copy(library, media_file.name, b"other content")

    def failing_send(message: str) -> bool:
        raise SecretsError("Infisical cannot be read")

    monkeypatch.setattr(processor_module, "send_telegram_message", failing_send)

    assert _process_batch_item(_item(media_file)) is None
    assert recorder.updates[-1]["status"] == "FAILED_CONFLICT"


def test_empty_source_on_a_non_empty_library_file_is_a_conflict(
    recorder: _Recorder, tmp_path: Path, library: Path, monkeypatch: pytest.MonkeyPatch, telegram_messages: list[str]
) -> None:
    _identify_returns(monkeypatch, FILM)
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"")
    existing = _filed_copy(library, source.name, b"good content")

    _process_batch_item(_item(source))

    assert recorder.updates[-1]["status"] == "FAILED_CONFLICT"
    assert existing.read_bytes() == b"good content"


def test_empty_source_on_an_empty_library_file_finishes_without_copying(
    recorder: _Recorder, tmp_path: Path, library: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _identify_returns(monkeypatch, FILM)
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"")
    _filed_copy(library, source.name, b"")

    _process_batch_item(_item(source))

    assert recorder.updates[-1]["status"] == "DONE"
    assert recorder.copies == []


def test_requeued_item_with_a_saved_target_conflicts_with_a_different_file(
    recorder: _Recorder, media_file: Path, library: Path, monkeypatch: pytest.MonkeyPatch, telegram_messages: list[str]
) -> None:
    _identify_returns(monkeypatch, FILM)
    _filed_copy(library, media_file.name, b"damaged")
    saved_target = str(library / "A--2001")

    _process_batch_item(dict(_item(media_file), target_path=saved_target))

    assert recorder.updates[-1]["status"] == "FAILED_CONFLICT"
    assert recorder.updates[-1]["target_path"] == saved_target
    assert len(telegram_messages) == 1


def test_free_destination_copies_and_saves_the_target(
    recorder: _Recorder, media_file: Path, library: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _identify_returns(monkeypatch, FILM)

    _process_batch_item(_item(media_file))

    assert recorder.copies == [(str(media_file), library / "A--2001")]
    assert recorder.updates[-1]["target_path"] == str((library / "A--2001").absolute())
    assert recorder.updates[-1]["status"] == "DONE"


def test_destination_stat_denied_ends_as_failed_permissions(
    recorder: _Recorder, media_file: Path, library: Path, monkeypatch: pytest.MonkeyPatch, telegram_messages: list[str]
) -> None:
    _identify_returns(monkeypatch, FILM)
    destination = library / "A--2001" / media_file.name
    _deny_stat(monkeypatch, destination, PermissionError(13, "Permission denied", str(destination)))

    _process_batch_item(_item(media_file))

    assert recorder.updates[-1]["status"] == "FAILED_PERMISSIONS"
    assert recorder.copies == []
    assert "<b>Destination</b>" in telegram_messages[0]


def test_destination_hash_denied_ends_as_failed_permissions(
    recorder: _Recorder, media_file: Path, library: Path, monkeypatch: pytest.MonkeyPatch, telegram_messages: list[str]
) -> None:
    _identify_returns(monkeypatch, FILM)
    existing = _filed_copy(library, media_file.name)
    _deny_hashing(monkeypatch, existing, PermissionError(13, "Permission denied", str(existing)))

    _process_batch_item(_item(media_file))

    assert recorder.updates[-1]["status"] == "FAILED_PERMISSIONS"
    assert recorder.copies == []
    assert len(telegram_messages) == 1


def test_other_error_on_the_destination_ends_as_failed_processing(
    recorder: _Recorder,
    media_file: Path,
    library: Path,
    monkeypatch: pytest.MonkeyPatch,
    batch_side_effects: list[str],
    telegram_messages: list[str],
) -> None:
    _identify_returns(monkeypatch, FILM)
    destination = library / "A--2001" / media_file.name
    _deny_stat(monkeypatch, destination, OSError(5, "Input/output error"))

    process_batch([_item(media_file)], "batch-1")

    assert recorder.updates[-1]["status"] == "FAILED_PROCESSING"
    assert recorder.copies == []
    assert telegram_messages == []


class _BatchReport:
    def __init__(self) -> None:
        self.batch_data: list[dict[str, Any]] = []
        self.closing_steps: list[str] = []
        self.verifications: list[bool] = []
        self.verified_items: list[list[dict[str, Any]]] = []
        self.messages: list[dict[str, Any]] = []


@pytest.fixture
def batch_report(monkeypatch: pytest.MonkeyPatch) -> _BatchReport:
    report = _BatchReport()
    manager = processor_module._work_queue_manager
    monkeypatch.setattr(
        manager, "move_working_items_back_to_pending", lambda batch_id: report.closing_steps.append("sweep")
    )
    monkeypatch.setattr(manager, "set_batch_as_done", lambda batch_id: report.closing_steps.append("done"))
    monkeypatch.setattr(manager, "get_batch_data", lambda batch_id: report.batch_data)
    monkeypatch.setattr(
        manager, "update_batch_verification", lambda batch_id, verified: report.verifications.append(verified)
    )

    def fake_verify(batch_id: str, items: list[dict[str, Any]]) -> tuple[bool, dict[str, Any]]:
        report.verified_items.append(items)
        return False, {}

    monkeypatch.setattr(processor_module, "verify_batch_data", fake_verify)
    monkeypatch.setattr(
        processor_module._notification_agent, "post_message", lambda message: report.messages.append(message)
    )
    return report


def test_process_batch_of_only_duplicates_closes_the_batch_sends_no_report_and_counts_as_verified(
    batch_report: _BatchReport,
) -> None:
    batch_report.batch_data = [{"id": "item-1", "status": "DUPLICATE"}, {"id": "item-2", "status": "DUPLICATE"}]

    process_batch([], "batch-1")

    assert batch_report.closing_steps == ["sweep", "done"]
    assert batch_report.messages == []
    assert batch_report.verified_items == []
    assert batch_report.verifications == [True]


def test_process_batch_mixed_with_duplicates_reports_only_the_others(batch_report: _BatchReport) -> None:
    done = {"id": "item-1", "status": "DONE"}
    conflict = {"id": "item-3", "status": "FAILED_CONFLICT"}
    batch_report.batch_data = [done, {"id": "item-2", "status": "DUPLICATE"}, conflict]

    process_batch([], "batch-1")

    assert batch_report.verified_items == [[done, conflict]]
    assert batch_report.messages[0]["items"] == [done, conflict]
    assert batch_report.messages[0]["verified"] is False
    assert batch_report.verifications == [False]


def test_process_batch_empty_resumed_batch_still_verifies_and_reports(batch_report: _BatchReport) -> None:
    process_batch([], "batch-1")

    assert batch_report.verified_items == [[]]
    assert batch_report.messages[0]["items"] == []
    assert batch_report.verifications == [False]


def test_process_batch_records_how_many_duplicates_it_held(
    batch_report: _BatchReport, recording_span: RecordingSpan
) -> None:
    batch_report.batch_data = [{"id": "item-1", "status": "DUPLICATE"}, {"id": "item-2", "status": "DONE"}]

    process_batch([], "batch-1")

    assert recording_span.attributes["batch.duplicates"] == 1
