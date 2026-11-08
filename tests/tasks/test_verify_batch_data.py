from pathlib import Path
from typing import Any

from src.tasks.verify_batch_data import verify_batch_data

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


def test_verify_batch_data_no_done_items_fails() -> None:
    verified, details = verify_batch_data(BATCH_ID, [{"id": "item-1", "status": "FAILED_ID", "target_path": None}])

    assert verified is False
    assert details == {}
