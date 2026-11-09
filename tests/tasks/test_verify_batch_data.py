import hashlib
from pathlib import Path
from typing import Any

import pytest

from src.tasks import verify_batch_data as verify_module
from src.tasks.verify_batch_data import verify_batch_data
from src.utils import _sha256

BATCH_ID = "batch-1"


def _done_item(source: Path, destination_dir: Path) -> dict[str, Any]:
    return {
        "id": "item-1",
        "status": "DONE",
        "full_path": str(source),
        "target_path": str(destination_dir),
        "filename": source.name,
    }


def _make_copy(tmp_path: Path, source_content: bytes, copy_content: bytes) -> tuple[Path, Path]:
    source = tmp_path / "watch" / "movie.mkv"
    source.parent.mkdir()
    source.write_bytes(source_content)
    destination_dir = tmp_path / "library"
    destination_dir.mkdir()
    (destination_dir / "movie.mkv").write_bytes(copy_content)
    return source, destination_dir


def test_verify_batch_data_identical_copy_passes(tmp_path: Path) -> None:
    source, destination_dir = _make_copy(tmp_path, b"video", b"video")

    verified, details = verify_batch_data(BATCH_ID, [_done_item(source, destination_dir)])

    assert verified is True
    assert details == {"movie.mkv": {"size": True, "hash": True}}


def test_verify_batch_data_size_mismatch_fails(tmp_path: Path) -> None:
    source, destination_dir = _make_copy(tmp_path, b"video", b"vid")

    verified, details = verify_batch_data(BATCH_ID, [_done_item(source, destination_dir)])

    assert verified is False
    assert details == {"movie.mkv": {"size": False, "hash": None}}


def test_verify_batch_data_content_mismatch_fails(tmp_path: Path) -> None:
    source, destination_dir = _make_copy(tmp_path, b"video", b"VIDEO")

    verified, details = verify_batch_data(BATCH_ID, [_done_item(source, destination_dir)])

    assert verified is False
    assert details == {"movie.mkv": {"size": True, "hash": False}}


def test_verify_batch_data_missing_destination_fails(tmp_path: Path) -> None:
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"video")

    verified, details = verify_batch_data(BATCH_ID, [_done_item(source, tmp_path / "library")])

    assert verified is False
    assert details == {}


def test_verify_batch_data_stored_hash_of_the_copied_content_passes_without_reading_the_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, destination_dir = _make_copy(tmp_path, b"video", b"video")
    read: list[Path] = []

    def recording_sha256(path: Path) -> str:
        read.append(path)
        return _sha256(path)

    monkeypatch.setattr(verify_module, "_sha256", recording_sha256)
    item = dict(_done_item(source, destination_dir), content_hash=hashlib.sha256(b"video").hexdigest())

    verified, details = verify_batch_data(BATCH_ID, [item])

    assert verified is True
    assert details == {"movie.mkv": {"size": True, "hash": True}}
    assert read == [destination_dir / "movie.mkv"]


def test_verify_batch_data_copy_differing_from_the_stored_hash_fails(tmp_path: Path) -> None:
    source, destination_dir = _make_copy(tmp_path, b"video", b"video")
    item = dict(_done_item(source, destination_dir), content_hash=hashlib.sha256(b"VIDEO").hexdigest())

    verified, details = verify_batch_data(BATCH_ID, [item])

    assert verified is False
    assert details == {"movie.mkv": {"size": True, "hash": False}}


def test_verify_batch_data_source_changed_in_size_after_hashing_fails(tmp_path: Path) -> None:
    source, destination_dir = _make_copy(tmp_path, b"changed since", b"video")
    item = dict(_done_item(source, destination_dir), content_hash=hashlib.sha256(b"video").hexdigest())

    verified, details = verify_batch_data(BATCH_ID, [item])

    assert verified is False
    assert details == {"movie.mkv": {"size": False, "hash": None}}


def test_verify_batch_data_null_hash_falls_back_to_reading_the_source(tmp_path: Path) -> None:
    source, destination_dir = _make_copy(tmp_path, b"video", b"video")
    item = dict(_done_item(source, destination_dir), content_hash=None)

    verified, details = verify_batch_data(BATCH_ID, [item])

    assert verified is True
    assert details == {"movie.mkv": {"size": True, "hash": True}}


def test_verify_batch_data_no_done_items_fails() -> None:
    verified, details = verify_batch_data(BATCH_ID, [{"id": "item-1", "status": "FAILED_ID", "target_path": None}])

    assert verified is False
    assert details == {}
