from pathlib import Path
from queue import Queue

from src.data.activity_logger import ActivityTracker
from src.tasks.check_file_is_compressed import is_compressed_file
from src.tasks.check_if_should_copy_file import check_should_copy_file
from src.tasks.check_is_main_file_in_archive import is_main_archive_file
from src.data.work_queue_manager import WorkQueueManager

_q = Queue()
_work_manager = WorkQueueManager()
_activity_tracker = ActivityTracker("Queue Worker")


def add_to_queue(filename, is_directory):
    file_type = "Directory" if is_directory else "File"
    _activity_tracker.debug(f"[EVENT TRIGGERED] {file_type} created: {filename}")
    _q.put((filename, is_directory))


def queue_consumer():
    tag = "[QUEUE CONSUMER]"
    while True:
        filename, is_directory = _q.get()
        if is_directory:
            _activity_tracker.debug(f"{tag} Ignoring directory: {filename}")
            continue

        _activity_tracker.info(f"{tag} File created: {filename}")
        prepare_file_for_processing(filename)


def prepare_file_for_processing(filename):
    is_archive = is_compressed_file(filename)
    main_archive_file = is_main_archive_file(filename) if is_archive else False
    should_copy_file = check_should_copy_file(filename)
    status = "IGNORED" if (is_archive and not main_archive_file) or not should_copy_file else "PENDING"
    path = Path(filename)

    _activity_tracker.debug(f"[QUEUE CONSUMER] Adding file [{filename}] to queue with status [{status}]")
    new_queue_item_id = _work_manager.add_to_queue(
        full_path=filename,
        filename=path.name,
        parent=str(path.parent),
        target_path=None,
        status=status,
        is_archive=is_archive,
        is_main_archive_file=main_archive_file,
        media_info_cache_id=None
    )
    _activity_tracker.info(f"[QUEUE CONSUMER] Added file [{filename}] to queue with status [{status}], and ID [{new_queue_item_id}]")
