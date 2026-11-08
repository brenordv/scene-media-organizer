import hashlib
import os
from pathlib import Path
from typing import Any

import pytest

from src import backfill as backfill_module
from src.backfill import (
    INTERRUPTED_EXIT_CODE,
    UNREADABLE_EXIT_CODE,
    BackfillReport,
    backfill_hashes,
    run_backfill,
)
from src.utils import _sha256
from tests.fakes import RecordingSpan


def _hash_of(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class _Folders:
    def __init__(self, root: Path) -> None:
        self.library = root / "library"
        self.watch = root / "watch"
        self.library.mkdir()
        self.watch.mkdir()
        self.library_roots = [(Path("/movies"), self.library)]
        self.watch_roots = [(Path("/watch"), self.watch)]
        self.saved: list[dict[str, Any]] = []

    def library_file(self, name: str, content: bytes) -> Path:
        folder = self.library / "A--2001"
        folder.mkdir(exist_ok=True)
        path = folder / name
        path.write_bytes(content)
        return path

    def source_file(self, name: str, content: bytes) -> Path:
        path = self.watch / name
        path.write_bytes(content)
        return path

    def run(self, items: list[dict[str, Any]]) -> BackfillReport:
        return backfill_hashes(items, self.saved.append, self.library_roots, self.watch_roots)


@pytest.fixture
def folders(tmp_path: Path) -> _Folders:
    return _Folders(tmp_path)


def _item(name: str = "a.mkv", item_id: str = "item-1", target_path: str | None = "/movies/A--2001") -> dict[str, Any]:
    return {"id": item_id, "filename": name, "target_path": target_path, "full_path": f"/watch/{name}"}


def _deny_reading(monkeypatch: pytest.MonkeyPatch, denied: Path) -> None:
    def guarded_sha256(path: Path) -> str:
        if path == denied:
            raise PermissionError(13, "Permission denied", str(path))
        return _sha256(path)

    monkeypatch.setattr(backfill_module, "_sha256", guarded_sha256)


def _fail_stat(monkeypatch: pytest.MonkeyPatch, denied: Path, error: OSError) -> None:
    real_stat = Path.stat

    def guarded_stat(self: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if self == denied:
            raise error
        return real_stat(self, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", guarded_stat)


def test_backfill_hashes_library_copy_before_the_source(folders: _Folders) -> None:
    folders.library_file("a.mkv", b"filed content")
    folders.source_file("a.mkv", b"something newer")

    report = folders.run([_item()])

    assert report == BackfillReport(hashed=1)
    assert folders.saved == [{"id": "item-1", "content_hash": _hash_of(b"filed content")}]


def test_backfill_falls_back_to_the_source_when_the_library_copy_is_gone(folders: _Folders) -> None:
    folders.source_file("a.mkv", b"source content")

    report = folders.run([_item()])

    assert report == BackfillReport(hashed=1)
    assert folders.saved == [{"id": "item-1", "content_hash": _hash_of(b"source content")}]


def test_backfill_hashes_the_source_of_an_archive_with_no_target_path(folders: _Folders) -> None:
    folders.source_file("a.rar", b"archive content")

    report = folders.run([_item("a.rar", target_path=None)])

    assert report == BackfillReport(hashed=1)
    assert folders.saved == [{"id": "item-1", "content_hash": _hash_of(b"archive content")}]


def test_backfill_item_with_no_file_left_counts_as_gone_and_saves_nothing(folders: _Folders) -> None:
    report = folders.run([_item()])

    assert report == BackfillReport(gone=1)
    assert folders.saved == []


def test_backfill_empty_files_are_not_hashed_and_count_as_gone(
    folders: _Folders, monkeypatch: pytest.MonkeyPatch
) -> None:
    folders.library_file("a.mkv", b"")
    folders.source_file("a.mkv", b"")
    read: list[Path] = []
    monkeypatch.setattr(backfill_module, "_sha256", read.append)

    report = folders.run([_item()])

    assert report == BackfillReport(gone=1)
    assert read == []
    assert folders.saved == []


def test_backfill_unreadable_library_copy_falls_through_to_the_source(
    folders: _Folders, monkeypatch: pytest.MonkeyPatch
) -> None:
    _deny_reading(monkeypatch, folders.library_file("a.mkv", b"filed content"))
    folders.source_file("a.mkv", b"source content")

    report = folders.run([_item()])

    assert report == BackfillReport(hashed=1)
    assert folders.saved == [{"id": "item-1", "content_hash": _hash_of(b"source content")}]


def test_backfill_unreadable_file_counts_as_unreadable_and_continues_with_the_next_row(
    folders: _Folders, monkeypatch: pytest.MonkeyPatch
) -> None:
    _deny_reading(monkeypatch, folders.library_file("denied.mkv", b"denied content"))
    folders.library_file("b.mkv", b"readable content")

    report = folders.run([_item("denied.mkv", "item-1"), _item("b.mkv", "item-2")])

    assert report == BackfillReport(hashed=1, unreadable=1)
    assert folders.saved == [{"id": "item-2", "content_hash": _hash_of(b"readable content")}]


def test_backfill_library_folder_replaced_by_a_file_counts_as_gone(
    folders: _Folders, monkeypatch: pytest.MonkeyPatch
) -> None:
    library_file = folders.library / "A--2001" / "a.mkv"
    _fail_stat(monkeypatch, library_file, NotADirectoryError(20, "Not a directory", str(library_file)))

    assert folders.run([_item()]) == BackfillReport(gone=1)


def test_backfill_io_error_on_stat_counts_as_unreadable_and_continues_with_the_next_row(
    folders: _Folders, monkeypatch: pytest.MonkeyPatch
) -> None:
    library_file = folders.library_file("broken.mkv", b"content")
    _fail_stat(monkeypatch, library_file, OSError(5, "Input/output error", str(library_file)))
    folders.library_file("b.mkv", b"readable content")

    report = folders.run([_item("broken.mkv", "item-1"), _item("b.mkv", "item-2")])

    assert report == BackfillReport(hashed=1, unreadable=1)
    assert folders.saved == [{"id": "item-2", "content_hash": _hash_of(b"readable content")}]


def test_backfill_target_outside_every_root_still_hashes_the_source(folders: _Folders) -> None:
    folders.source_file("a.mkv", b"source content")

    report = folders.run([_item(target_path="/old-mount/A")])

    assert report == BackfillReport(hashed=1)
    assert folders.saved == [{"id": "item-1", "content_hash": _hash_of(b"source content")}]


def test_backfill_records_its_counts_on_the_span(folders: _Folders, recording_span: RecordingSpan) -> None:
    folders.library_file("a.mkv", b"filed content")

    folders.run([_item("a.mkv", "item-1"), _item("gone.mkv", "item-2")])

    assert recording_span.attributes == {"backfill.hashed": 1, "backfill.gone": 1, "backfill.unreadable": 0}


def test_backfill_stored_paths_outside_every_root_count_as_unreadable_not_gone(folders: _Folders) -> None:
    item = {"id": "item-1", "filename": "a.mkv", "target_path": "/old-mount/A", "full_path": "/old-watch/a.mkv"}

    assert folders.run([item]) == BackfillReport(unreadable=1)
    assert folders.saved == []


def test_run_backfill_exits_zero_and_prints_the_counts(folders: _Folders, capsys: pytest.CaptureFixture[str]) -> None:
    folders.library_file("a.mkv", b"filed content")

    exit_code = run_backfill(
        [_item("a.mkv", "item-1"), _item("gone.mkv", "item-2")],
        folders.saved.append,
        folders.library_roots,
        folders.watch_roots,
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "[1/2] a.mkv: hashed" in output
    assert "[2/2] gone.mkv: gone" in output
    assert "Hashed: 1\nGone: 1\nUnreadable: 0" in output


def test_run_backfill_unreadable_item_exits_non_zero(folders: _Folders, capsys: pytest.CaptureFixture[str]) -> None:
    item = {"id": "item-1", "filename": "a.mkv", "target_path": "/old-mount/A", "full_path": "/old-watch/a.mkv"}

    exit_code = run_backfill([item], folders.saved.append, folders.library_roots, folders.watch_roots)

    assert exit_code == UNREADABLE_EXIT_CODE
    assert "Unreadable: 1" in capsys.readouterr().out


def test_run_backfill_interrupted_keeps_what_was_saved_and_says_a_rerun_continues(
    folders: _Folders, capsys: pytest.CaptureFixture[str]
) -> None:
    folders.library_file("a.mkv", b"first")
    folders.library_file("b.mkv", b"second")
    saved: list[dict[str, Any]] = []

    def save_then_interrupt(item: dict[str, Any]) -> None:
        saved.append(item)
        raise KeyboardInterrupt

    exit_code = run_backfill(
        [_item("a.mkv", "item-1"), _item("b.mkv", "item-2")],
        save_then_interrupt,
        folders.library_roots,
        folders.watch_roots,
    )

    assert exit_code == INTERRUPTED_EXIT_CODE
    assert saved == [{"id": "item-1", "content_hash": _hash_of(b"first")}]
    assert "run the command again to continue" in capsys.readouterr().out
