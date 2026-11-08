from pathlib import Path
from typing import Any

import pytest

from src import queue_worker as worker_module
from src.queue_worker import prepare_file_for_processing


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def fake_add_to_queue(**kwargs: Any) -> str:
        rows.append(kwargs)
        return "new-id"

    monkeypatch.setattr(worker_module._work_manager, "add_to_queue", fake_add_to_queue)
    return rows


@pytest.mark.parametrize(
    ("name", "status", "is_archive", "is_main"),
    [
        ("Movie.2020.mkv", "PENDING", False, False),
        ("Movie.2020.Sample.mkv", "IGNORED", False, False),
        ("release.rar", "PENDING", True, True),
        ("release.r00", "IGNORED", True, False),
        ("install.sh", "IGNORED", False, False),
    ],
)
def test_prepare_file_for_processing_sets_status_from_file_kind(
    tmp_path: Path, queued: list[dict[str, Any]], name: str, status: str, is_archive: bool, is_main: bool
) -> None:
    path = tmp_path / name
    path.write_bytes(b"content")

    prepare_file_for_processing(str(path))

    assert queued[0]["status"] == status
    assert queued[0]["is_archive"] is is_archive
    assert queued[0]["is_main_archive_file"] is is_main
    assert queued[0]["filename"] == name
    assert queued[0]["parent"] == str(tmp_path)
