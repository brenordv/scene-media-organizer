from src.helpers import parse_work_item_row, select_new_files


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
