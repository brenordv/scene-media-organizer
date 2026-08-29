import uuid

import psycopg
from opentelemetry import trace

from src.data.activity_logger import ActivityTracker
from src.data.base_repository import BaseRepository
from src.helpers import parse_work_item_row

_activity_tracker = ActivityTracker("Work Queue Manager")

_MAX_ATTEMPTS = 5


class WorkQueueManager(BaseRepository):
    def __init__(self):
        super().__init__("Work Queue Manager")
        self._logger = _activity_tracker

    def _ensure_table_exists(self):
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    self._logger.debug("Enabling uuid-ossp extension")
                    cursor.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";')

                    self._logger.debug("Creating work_queue table if it does not exist")
                    create_table_query = """
                                         CREATE TABLE IF NOT EXISTS work_queue (
                                             id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                                             full_path TEXT NOT NULL,
                                             filename TEXT NOT NULL,
                                             parent TEXT NOT NULL,
                                             target_path TEXT NULL,
                                             status TEXT NOT NULL,
                                             is_archive BOOLEAN NOT NULL DEFAULT FALSE,
                                             is_main_archive_file BOOLEAN NOT NULL DEFAULT FALSE,
                                             created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                                             modified_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                                             media_info_cache_id UUID NULL);"""
                    cursor.execute(create_table_query)

                    self._logger.debug("Creating batch_control table if it does not exist")
                    create_table_query = """
                                         CREATE TABLE IF NOT EXISTS batch_control (
                                             batch_id UUID NOT NULL,
                                             work_queue_id UUID NOT NULL,
                                             in_progress BOOLEAN NOT NULL DEFAULT FALSE,
                                             verified BOOLEAN NOT NULL DEFAULT FALSE,
                                             created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                                             modified_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                                         );"""
                    cursor.execute(create_table_query)

                    self._logger.debug("Ensuring work_queue.attempts column exists")
                    cursor.execute(
                        "ALTER TABLE work_queue ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0;"
                    )

                    conn.commit()
        except psycopg.Error as e:
            error_message = f"Error creating the work queue table: {str(e)}"
            self._logger.error(error_message)
            raise RuntimeError(error_message) from e

    @_activity_tracker.trace("WorkQueueManager.recover_stale_batches")
    def recover_stale_batches(self):
        """Called once at processor startup. Any in-progress batch at that moment
        is an orphan from a previous crash: release its items and close it."""
        span = trace.get_current_span()
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "UPDATE work_queue SET status = 'PENDING', modified_at = CURRENT_TIMESTAMP WHERE status = 'WORKING'"
                    )
                    recovered_items = cursor.rowcount

                    cursor.execute(
                        "UPDATE batch_control SET in_progress = FALSE, modified_at = CURRENT_TIMESTAMP WHERE in_progress = TRUE"
                    )
                    recovered_batches = cursor.rowcount

                    conn.commit()

            self._logger.info(
                f"Startup recovery: released {recovered_items} stale WORKING item(s) and "
                f"closed {recovered_batches} orphaned in-progress batch(es)."
            )
            if span.is_recording():
                span.set_attributes({
                    "recovery.items": recovered_items,
                    "recovery.batches": recovered_batches,
                })

        except psycopg.Error as e:
            error_message = f"Error recovering stale batches: {str(e)}"
            self._logger.error(error_message)
            raise RuntimeError(error_message) from e

    @_activity_tracker.trace("WorkQueueManager.add_to_queue")
    def add_to_queue(self, full_path, filename, parent, target_path, status, is_archive, is_main_archive_file, media_info_cache_id):
        span = trace.get_current_span()
        if span.is_recording():
            span.set_attributes({
                "db.table": "work_queue",
                "db.operation": "insert",
                "file.path": str(full_path),
                "file.name": str(filename),
                "queue.status": str(status),
            })

        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    insert_query = """INSERT INTO work_queue (full_path, filename, parent, target_path, status, is_archive, is_main_archive_file, media_info_cache_id)
                                      VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                                      returning id"""
                    cursor.execute(insert_query, (full_path, filename, parent, target_path, status, is_archive, is_main_archive_file, media_info_cache_id))
                    conn.commit()
                    row = cursor.fetchone()
                    if row is not None:
                        return row[0]

                    raise RuntimeError(f"Error adding {full_path} to the work queue: No row returned from the database")
        except psycopg.Error as e:
            error_message = f"Error adding {full_path} to the work queue: {str(e)}"
            self._logger.error(error_message)
            raise RuntimeError(error_message) from e

    @_activity_tracker.trace("WorkQueueManager.update")
    def update(self, work_item):
        span = trace.get_current_span()
        if span.is_recording():
            span.set_attributes({
                "db.table": "work_queue",
                "db.operation": "update",
                "work_item.id": str(work_item.get('id', '')),
                "work_item.status": str(work_item.get('status', '')),
            })

        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    work_item_id = work_item['id']
                    full_path = work_item.get('full_path')
                    target_path = work_item.get('target_path')
                    status = work_item.get('status')
                    self._logger.debug(f"Updating work item [{work_item_id}]. Full path: {full_path}, target path: {target_path}, status: {status}")

                    keys = [key for key in work_item.keys() if key not in ['id', 'created_at', 'modified_at']]
                    values = []
                    fields = []

                    for key in keys:
                        values.append(work_item[key])
                        fields.append(f"{key} = %s")

                    fields_str = ", ".join(fields)

                    update_query = f"UPDATE work_queue SET {fields_str} WHERE id = %s"
                    cursor.execute(update_query, values + [work_item_id])
                    conn.commit()
        except psycopg.Error as e:
            error_message = f"Error updating work item {work_item['id']}: {str(e)}"
            self._logger.error(error_message)
            raise RuntimeError(error_message) from e

    @_activity_tracker.trace("WorkQueueManager.get_next_batch")
    def get_next_batch(self, batch_id=None, force_new_batch=False):
        span = trace.get_current_span()
        if span.is_recording():
            span.set_attributes({
                "db.table": "work_queue",
                "db.operation": "select_and_update",
                "batch.id": str(batch_id or ""),
                "batch.force_new": force_new_batch,
            })

        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    select_query = "SELECT * FROM batch_control WHERE in_progress = TRUE"
                    cursor.execute(select_query)
                    row = cursor.fetchone()

                    if row is not None and not force_new_batch:
                        self._logger.debug(f"Batch [{batch_id}] is already in progress. Returning empty batch...")
                        return [], batch_id

                    update_and_select_query = """
                                              UPDATE work_queue
                                              SET status = 'WORKING',
                                                  attempts = attempts + 1,
                                                  modified_at = CURRENT_TIMESTAMP
                                              WHERE status = 'PENDING'
                                              RETURNING id, full_path, filename, parent, target_path, status, is_archive, is_main_archive_file, created_at, modified_at, media_info_cache_id, attempts"""

                    cursor.execute(update_and_select_query)
                    rows = cursor.fetchall()

                    if len(rows) == 0:
                        conn.commit()
                        return [], None

                    self._logger.debug(f"Found {len(rows)} work items to process. Creating a new batch...")
                    batch = [self._parse_work_item_row_to_object(row) for row in rows]

                    # At this point we can generate a new batch ID, because there's nothing in progress.
                    batch_id = str(uuid.uuid4())

                    for batch_item in batch:
                        insert_query = """INSERT INTO batch_control (batch_id, work_queue_id, in_progress) VALUES (%s, %s, true)"""
                        cursor.execute(insert_query, (batch_id, batch_item['id']))

                    conn.commit()
                return batch, batch_id

        except psycopg.Error as e:
            error_message = f"Error getting next batch of work items: {str(e)}"
            self._logger.error(error_message)
            raise RuntimeError(error_message) from e

    @_activity_tracker.trace("WorkQueueManager.set_batch_as_done")
    def set_batch_as_done(self, batch_id):
        span = trace.get_current_span()
        if span.is_recording():
            span.set_attributes({
                "db.table": "batch_control",
                "db.operation": "update",
                "batch.id": str(batch_id),
            })

        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    self._logger.debug(f"Setting batch [{batch_id}] as done...")
                    update_query = """UPDATE batch_control SET in_progress = FALSE, modified_at = CURRENT_TIMESTAMP WHERE batch_id = %s"""
                    cursor.execute(update_query, (batch_id,))
                    conn.commit()

        except psycopg.Error as e:
            error_message = f"Error setting batch [{batch_id}] as done: {str(e)}"
            self._logger.error(error_message)
            raise RuntimeError(error_message) from e

    @_activity_tracker.trace("WorkQueueManager.move_working_items_back_to_pending")
    def move_working_items_back_to_pending(self, batch_id):
        span = trace.get_current_span()
        if span.is_recording():
            span.set_attributes({
                "db.table": "work_queue",
                "db.operation": "update",
                "batch.id": str(batch_id or ""),
            })

        try:
            if batch_id is None:
                self._logger.warning("No batch id provided. Moving all working items back to pending...")

            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    self._logger.debug(f"[Batch ID: {batch_id}] Sweeping working items: capping exhausted ones, releasing the rest to pending...")

                    batch_filter = " AND id IN ( SELECT work_queue_id FROM batch_control WHERE batch_id = %s )"

                    fail_query = """UPDATE work_queue
                                    SET status = 'FAILED_MAX_ATTEMPTS',
                                        modified_at = CURRENT_TIMESTAMP
                                    WHERE status = 'WORKING' AND attempts >= %s"""
                    release_query = """UPDATE work_queue
                                       SET status = 'PENDING',
                                           modified_at = CURRENT_TIMESTAMP
                                       WHERE status = 'WORKING'"""

                    if batch_id is not None:
                        fail_query += batch_filter
                        release_query += batch_filter
                        fail_params = (_MAX_ATTEMPTS, batch_id)
                        release_params = (batch_id,)
                    else:
                        fail_params = (_MAX_ATTEMPTS,)
                        release_params = ()

                    fail_query += " RETURNING filename"

                    cursor.execute(fail_query, fail_params)
                    capped_rows = cursor.fetchall()

                    cursor.execute(release_query, release_params)

                    conn.commit()

            if capped_rows:
                capped_filenames = [row[0] for row in capped_rows]
                self._logger.warning(
                    f"[Batch ID: {batch_id}] {len(capped_filenames)} item(s) reached the attempt "
                    f"cap ({_MAX_ATTEMPTS}) and were marked FAILED_MAX_ATTEMPTS: {capped_filenames}"
                )
                if span.is_recording():
                    span.set_attribute("batch.capped_items", len(capped_filenames))

        except psycopg.Error as e:
            error_message = f"[Batch ID: {batch_id}] Error moving working items back to pending: {str(e)}"
            self._logger.error(error_message)
            raise RuntimeError(error_message) from e

    @_activity_tracker.trace("WorkQueueManager.get_batch_data")
    def get_batch_data(self, batch_id):
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    select_query = """SELECT * FROM work_queue
                                      WHERE id IN (SELECT work_queue_id FROM batch_control WHERE batch_id = %s)"""
                    cursor.execute(select_query, (batch_id,))
                    rows = cursor.fetchall()

                    if len(rows) == 0:
                        return []

                    batch = [self._parse_work_item_row_to_object(row) for row in rows]
                    return batch

        except psycopg.Error as e:
            error_message = f"[Batch ID: {batch_id}] Error getting batch data: {str(e)}"
            self._logger.error(error_message)
            raise RuntimeError(error_message) from e

    @_activity_tracker.trace("WorkQueueManager.update_batch_verification")
    def update_batch_verification(self, batch_id, verified):
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    self._logger.debug(f"[Batch ID: {batch_id}] Updating batch verification to {verified}...")
                    update_query = """UPDATE batch_control
                                      SET verified = %s,
                                          modified_at = CURRENT_TIMESTAMP
                                      WHERE batch_id = %s"""
                    cursor.execute(update_query, (verified, batch_id))
                    conn.commit()

        except psycopg.Error as e:
            error_message = f"[Batch ID: {batch_id}] Error updating batch verification: {str(e)}"
            self._logger.error(error_message)
            raise RuntimeError(error_message) from e

    @_activity_tracker.trace("WorkQueueManager.filter_only_existing_filenames")
    def filter_only_existing_filenames(self, filenames):
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    self._logger.debug(f"Checking database for filenames: {filenames}")
                    select_query = """SELECT distinct filename FROM work_queue WHERE filename = ANY(%s)"""
                    cursor.execute(select_query, (filenames,))
                    rows = cursor.fetchall()
                    return [row[0] for row in rows]
        except psycopg.Error as e:
            error_message = f"Error checking database for filenames: {str(e)}"
            self._logger.error(error_message)
            raise RuntimeError(error_message) from e

    @staticmethod
    def _parse_work_item_row_to_object(row):
        return parse_work_item_row(row)
