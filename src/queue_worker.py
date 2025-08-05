from pathlib import Path
from queue import Queue

from simple_log_factory.log_factory import log_factory

from src.tasks.check_file_is_compressed import is_compressed_file
from src.tasks.check_if_should_copy_file import check_should_copy_file
from src.tasks.check_is_main_file_in_archive import is_main_archive_file
from src.work_queue_manager import WorkQueueManager

_q = Queue()
_logger = log_factory("Queue Worker", unique_handler_types=True)
_work_manager = WorkQueueManager()


def add_to_queue(filename, is_directory):
    _q.put((filename, is_directory))


def queue_consumer():
    while True:
        filename, is_directory = _q.get()
        if is_directory:
            _logger.debug(f"Ignoring directory: {filename}")
        else:
            _logger.info(f"File created: {filename}")
            _prepare_file_for_processing(filename)


def _prepare_file_for_processing(filename):
    is_archive = is_compressed_file(filename)
    main_archive_file = is_main_archive_file(filename) if is_archive else False
    should_copy_file = check_should_copy_file(filename)
    status = "IGNORED" if (is_archive and not main_archive_file) or not should_copy_file else "PENDING"
    path = Path(filename)

    _work_manager.add_to_queue(
        full_path=filename,
        filename=path.name,
        parent=str(path.parent),
        target_path=None,
        status=status,
        is_archive=is_archive,
        is_main_archive_file=main_archive_file,
        media_info_cache_id=None
    )
