import os
import time
from pathlib import Path

from simple_log_factory.log_factory import log_factory

from src.tasks.check_for_file_stability import check_is_file_stable
from src.tasks.copy_file import copy_file
from src.tasks.decompress_file import decompress_file
from src.tasks.identify_file import identify_file
from src.tasks.sanitize_string_for_filename import sanitize_string_for_filename
from src.work_queue_manager import WorkQueueManager

_logger = log_factory("Batch Processor", unique_handler_types=True)
_work_queue_manager = WorkQueueManager()
_movies_base_folder = os.environ.get('MOVIES_BASE_FOLDER')
_series_base_folder = os.environ.get('SERIES_BASE_FOLDER')

if _series_base_folder is None or _movies_base_folder is None:
    _logger.error("No base folders defined. Exiting...")
    exit(1)


def batch_processor():
    current_batch_id = None
    while True:
        _logger.debug("Checking if we have a batch to work with...")
        batch, current_batch_id = _work_queue_manager.get_next_batch(batch_id=current_batch_id)

        if batch is not None and len(batch) > 0:
            _process_batch(batch, current_batch_id)

        else:
            _logger.debug("No batch to work with. Let's keep waiting...")

        time.sleep(10)


def _process_batch(batch, current_batch_id):
    _logger.debug(f"Processing batch of {len(batch)} items. Batch id: {current_batch_id}...")
    try_again = []

    # First try.
    for item in batch:
        try:
            file_to_retry = _process_batch_item(item)
            if file_to_retry is not None:
                try_again.append(file_to_retry)
                continue
        except Exception as e:
            _logger.error(f"Error processing item [{item['id']}]: {str(e)}")
            item['status'] = 'FAILED_PROCESSING'
            _work_queue_manager.update(item)

    if len(try_again) > 0:
        _logger.debug(f"Retrying to process {len(try_again)} items...")

    # Retry.
    for item in try_again:
        try:
            _process_batch_item(item)
        except Exception as e:
            _logger.error(f"Error retrying to process item [{item['id']}]: {str(e)}")
            item['status'] = 'FAILED_PROCESSING_RETRY'
            _work_queue_manager.update(item)

    _work_queue_manager.set_batch_as_done(current_batch_id)


def _process_batch_item(item):
    item_id = item['id']
    full_path = item['full_path']
    full_path_obj = Path(full_path)
    _logger.debug(f"Processing item {item_id}...")
    is_file_stable = check_is_file_stable(full_path)

    if not is_file_stable:
        _logger.warning(f"Item {item_id} is not stable. Will try again later.")
        return item

    media_info = identify_file(full_path)

    if media_info is None:
        item['status'] = 'FAILED_ID'
        _work_queue_manager.update(item)
        return None

    if item['is_archive']:
        decompress_result = decompress_file(full_path)
        if not decompress_result:
            return item

        item['status'] = 'DONE'
        _work_queue_manager.update(item)
        return None

    media_info_id = media_info.get('id')

    if media_info_id is None:
        _logger.error(f"Item {item_id} has no media info id. No way to proceed with it. Media Info cache id: {media_info_id}")
        item['status'] = 'FAILED_ID'
        _work_queue_manager.update(item)

    item['media_info_cache_id'] = media_info_id
    _work_queue_manager.update(item)

    media_type = media_info.get('media_type')

    if media_type is None or media_type not in ['movie', 'tv']:
        _logger.error(f"Item {item_id} has no media type. No way to proceed with it. Media Info cache id: {media_info_id}")
        item['status'] = 'FAILED_ID'
        _work_queue_manager.update(item)
        return None

    title = media_info.get('title')

    if title is None:
        _logger.error(f"Item {item_id} has no title. No way to proceed with it. Media Info cache id: {media_info_id}")
        item['status'] = 'FAILED_ID'
        _work_queue_manager.update(item)
        return None

    title_as_filename = sanitize_string_for_filename(title)

    if media_type == 'movie':
        movies_base_path = Path(_movies_base_folder)
        year = media_info.get('year')

        destination_path = movies_base_path.joinpath(f"{title_as_filename}--{year}") if year is not None else movies_base_path.joinpath(title_as_filename)

    else:
        series_base_path = Path(_series_base_folder)
        season_number = media_info.get('season')

        if season_number is None:
            _logger.error(f"Item {item_id} has no season number. No way to proceed with it. Media Info cache id: {media_info_id}")
            item['status'] = 'FAILED_ID'
            _work_queue_manager.update(item)
            return None

        destination_path = series_base_path.joinpath(title_as_filename).joinpath(f"Season{season_number:02d}")

    destination_path.mkdir(parents=True, exist_ok=True)

    item['target_path'] = str(destination_path.absolute())
    _work_queue_manager.update(item)

    copy_result = copy_file(full_path, destination_path)

    if copy_result:
        item['status'] = 'DONE'
        _work_queue_manager.update(item)
        _logger.info(f"All done with [{full_path_obj.name}]! \\o/")
        return None

    _logger.warning(f"Failed to copy file [{full_path_obj.name}]. Will try again later.")

    return None
