"""Pure helpers with no configuration, service, or package dependencies.
Tests import this module directly; keep it stdlib-only and side-effect free."""


def select_new_files(files_found, existing_filenames):
    """files_found: iterable of (full_path, filename) tuples.
    existing_filenames: iterable of filename strings already in the queue.
    Returns the full paths whose filename is not yet known."""
    existing = set(existing_filenames)
    return [full_path for full_path, filename in files_found if filename not in existing]


def parse_work_item_row(row):
    return {
        "id": row[0],
        "full_path": row[1],
        "filename": row[2],
        "parent": row[3],
        "target_path": row[4],
        "status": row[5],
        "is_archive": row[6],
        "is_main_archive_file": row[7],
        "created_at": row[8],
        "modified_at": row[9],
        "media_info_cache_id": row[10],
        "attempts": row[11],
    }
