from datetime import datetime

from src.helpers import parse_work_item_row, partition_open_batches, select_new_files


def test_select_new_files_partial_overlap():
    files_found = [("/a/x.mkv", "x.mkv"), ("/a/y.mkv", "y.mkv")]
    assert select_new_files(files_found, ["x.mkv"]) == ["/a/y.mkv"]


def test_select_new_files_no_overlap():
    files_found = [("/a/x.mkv", "x.mkv"), ("/a/y.mkv", "y.mkv")]
    assert select_new_files(files_found, []) == ["/a/x.mkv", "/a/y.mkv"]


def test_select_new_files_all_existing():
    files_found = [("/a/x.mkv", "x.mkv"), ("/a/y.mkv", "y.mkv")]
    assert select_new_files(files_found, ["x.mkv", "y.mkv"]) == []


def test_select_new_files_empty_inputs():
    assert select_new_files([], []) == []
    assert select_new_files([], ["x.mkv"]) == []


def test_parse_work_item_row_maps_twelve_columns_including_attempts():
    row = (
        "id-1", "/a/x.mkv", "x.mkv", "/a", "/dest", "WORKING",
        True, False, "2026-01-01", "2026-01-02", "cache-1", 3,
    )
    assert parse_work_item_row(row) == {
        "id": "id-1",
        "full_path": "/a/x.mkv",
        "filename": "x.mkv",
        "parent": "/a",
        "target_path": "/dest",
        "status": "WORKING",
        "is_archive": True,
        "is_main_archive_file": False,
        "created_at": "2026-01-01",
        "modified_at": "2026-01-02",
        "media_info_cache_id": "cache-1",
        "attempts": 3,
    }


def test_partition_open_batches_empty_input():
    assert partition_open_batches([]) == (None, [])


def test_partition_open_batches_single_batch():
    row = ("batch-1", datetime(2026, 1, 1, 10, 0, 0), datetime(2026, 1, 1, 10, 5, 0), 3)
    resume_row, stale_batch_ids = partition_open_batches([row])
    assert resume_row == row
    assert stale_batch_ids == []


def test_partition_open_batches_most_recent_wins():
    older = ("batch-old", datetime(2026, 1, 1, 9, 0, 0), datetime(2026, 1, 1, 9, 30, 0), 2)
    newer = ("batch-new", datetime(2026, 1, 1, 12, 0, 0), datetime(2026, 1, 1, 12, 1, 0), 5)
    resume_row, stale_batch_ids = partition_open_batches([older, newer])
    assert resume_row == newer
    assert stale_batch_ids == ["batch-old"]


def test_partition_open_batches_tie_broken_by_batch_id():
    created = datetime(2026, 1, 1, 10, 0, 0)
    row_a = ("batch-aaa", created, created, 1)
    row_b = ("batch-bbb", created, created, 1)
    resume_row, stale_batch_ids = partition_open_batches([row_a, row_b])
    assert resume_row == row_b
    assert stale_batch_ids == ["batch-aaa"]
