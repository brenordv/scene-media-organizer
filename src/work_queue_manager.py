import os
from contextlib import contextmanager
import psycopg2
from psycopg2.pool import SimpleConnectionPool
from simple_log_factory.log_factory import log_factory


_db_pool = SimpleConnectionPool(
    minconn = 1,
    maxconn = 10,
    host = os.environ.get('POSTGRES_HOST', 'localhost'),
    port = os.environ.get('POSTGRES_PORT', '5432'),
    user = os.environ.get('POSTGRES_USER', 'postgres'),
    password=os.environ.get('POSTGRES_PASSWORD', 'postgres'),
    dbname = 'smo_watchdog'
)

class WorkQueueManager:
    def __init__(self):
        self._conn_pool = _db_pool
        self._logger = log_factory("Work Queue Manager", unique_handler_types=True)
        self._ensure_table_exists()

    @contextmanager
    def _get_connection(self):
        conn = self._conn_pool.getconn()
        try:
            yield conn
        finally:
            self._conn_pool.putconn(conn)

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
                    conn.commit()
        except psycopg2.Error as e:
            error_message = f"Error creating the work queue table: {str(e)}"
            self._logger.error(error_message)
            raise RuntimeError(error_message) from e

    def add_to_queue(self, full_path, filename, parent, target_path, status, is_archive, is_main_archive_file, media_info_cache_id):
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    self._logger.debug(f"Adding {full_path} to the work queue")
                    insert_query = """INSERT INTO work_queue (full_path, filename, parent, target_path, status, is_archive, is_main_archive_file, media_info_cache_id) 
                                      VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                                      returning id"""
                    cursor.execute(insert_query, (full_path, filename, parent, target_path, status, is_archive, is_main_archive_file, media_info_cache_id))
                    conn.commit()
                    row = cursor.fetchone()
                    if row is None:
                        raise RuntimeError(
                            f"Error adding {full_path} to the work queue: No row returned from the database")

                    return row[0]
        except psycopg2.Error as e:
            error_message = f"Error adding {full_path} to the work queue: {str(e)}"
            self._logger.error(error_message)
            raise RuntimeError(error_message) from e

    def update(self, work_item):
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    work_item_id = work_item['id']
                    self._logger.debug(f"Updating work item {work_item_id}")

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

    def get_next_batch(self):
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    self._logger.debug("Getting next batch of work items with status PENDING")

                    update_and_select_query = """
                                              UPDATE work_queue
                                              SET status = 'WORKING',
                                                  modified_at = CURRENT_TIMESTAMP
                                              WHERE status = 'PENDING'
                                              RETURNING id, full_path, filename, parent, target_path, status, is_archive, is_main_archive_file, created_at, modified_at, media_info_cache_id
                                              """

                    cursor.execute(update_and_select_query)
                    rows = cursor.fetchall()
                    conn.commit()

                self._logger.debug(f"Found {len(rows)} work items to process")
                return [self._parse_work_item_row_to_object(row) for row in rows]

        except psycopg2.Error as e:
            error_message = f"Error getting next batch of work items: {str(e)}"
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
