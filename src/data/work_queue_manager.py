import uuid
import psycopg2

from src.data.activity_logger import ActivityTracker
from src.data.base_repository import BaseRepository

_activity_tracker = ActivityTracker("Work Queue Manager")


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

                    conn.commit()
        except psycopg2.Error as e:
            error_message = f"Error creating the work queue table: {str(e)}"
            self._logger.error(error_message)
            raise RuntimeError(error_message) from e

    def add_to_queue(self, full_path, filename, parent, target_path, status, is_archive, is_main_archive_file, media_info_cache_id):
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
        except psycopg2.Error as e:
            error_message = f"Error adding {full_path} to the work queue: {str(e)}"
            self._logger.error(error_message)
            raise RuntimeError(error_message) from e

    def update(self, work_item):
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
        except psycopg2.Error as e:
            error_message = f"Error updating work item {work_item['id']}: {str(e)}"
            self._logger.error(error_message)
            raise RuntimeError(error_message) from e

    def get_next_batch(self, batch_id = None, force_new_batch = False):
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
                                                  modified_at = CURRENT_TIMESTAMP
                                              WHERE status = 'PENDING'
                                              RETURNING id, full_path, filename, parent, target_path, status, is_archive, is_main_archive_file, created_at, modified_at, media_info_cache_id"""

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

        except psycopg2.Error as e:
            error_message = f"Error getting next batch of work items: {str(e)}"
            self._logger.error(error_message)
            raise RuntimeError(error_message) from e

    def set_batch_as_done(self, batch_id):
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    self._logger.debug(f"Setting batch [{batch_id}] as done...")
                    update_query = """UPDATE batch_control SET in_progress = FALSE, modified_at = CURRENT_TIMESTAMP WHERE batch_id = %s"""
                    cursor.execute(update_query, (batch_id,))
                    conn.commit()

        except psycopg2.Error as e:
            error_message = f"Error setting batch [{batch_id}] as done: {str(e)}"
            self._logger.error(error_message)
            raise RuntimeError(error_message) from e

    def move_working_items_back_to_pending(self, batch_id):
        try:
            if batch_id is None:
                self._logger.warning("No batch id provided. Moving all working items back to pending...")

            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    self._logger.debug(f"[Batch ID: {batch_id}] Moving working items back to pending so the next back end can process them...")
                    # TODO: Add attempt control, otherwise we might end up retrying impossibly wrong items forever.

                    update_query = """UPDATE work_queue
                                      SET status      = 'PENDING',
                                          modified_at = CURRENT_TIMESTAMP
                                      WHERE status = 'WORKING'"""

                    if batch_id is not None:
                        update_query += """ AND id IN ( SELECT work_queue_id FROM batch_control WHERE batch_id = %s )"""
                        cursor.execute(update_query, (batch_id,))
                    else:
                        cursor.execute(update_query)

                    conn.commit()

        except psycopg2.Error as e:
            error_message = f"[Batch ID: {batch_id}] Error moving working items back to pending: {str(e)}"
            self._logger.error(error_message)
            raise RuntimeError(error_message) from e

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

        except psycopg2.Error as e:
            error_message = f"[Batch ID: {batch_id}] Error getting batch data: {str(e)}"
            self._logger.error(error_message)
            raise RuntimeError(error_message) from e

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

        except psycopg2.Error as e:
            error_message = f"[Batch ID: {batch_id}] Error updating batch verification: {str(e)}"
            self._logger.error(error_message)
            raise RuntimeError(error_message) from e

    def filter_only_existing_filenames(self, filenames):
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    self._logger.debug(f"Checking database for filenames: {filenames}")
                    select_query = """SELECT filename FROM work_queue WHERE filename = ANY(%s)"""
                    cursor.execute(select_query, (filenames,))
                    rows = cursor.fetchall()
                    return rows
        except psycopg2.Error as e:
            error_message = f"Error checking database for filenames: {str(e)}"
            self._logger.error(error_message)
            raise RuntimeError(error_message) from e

    @staticmethod
    def _parse_work_item_row_to_object(row):
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
            "media_info_cache_id": row[10]
        }
