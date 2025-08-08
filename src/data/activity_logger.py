import psycopg2

from src.data.base_repository import BaseRepository

_log_levels = {
    "NOTSET": 0,
    "DEBUG": 10,
    "INFO": 20,
    "WARNING": 30,
    "ERROR": 40,
    "CRITICAL": 50
}

class ActivityTracker(BaseRepository):
    def __init__(self, log_name = None, log_level="DEBUG"):
        super().__init__("Activity Tracker" if log_name is None else log_name, log_level)
        self._log_control = {
            "NOTSET": False,
            "DEBUG": False,
            "INFO": False,
            "WARNING": False,
            "ERROR": False,
            "CRITICAL": False
        }
        self._set_log_level(log_level)

    def _set_log_level(self, log_level):
        target_level = log_level.upper().strip()
        level = 999

        for log_level, value in _log_levels.items():
            if log_level == target_level:
                level = value
                self._log_control[log_level] = True
                continue

            self._log_control[log_level] = value >= level

    def _ensure_table_exists(self):
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    self._logger.debug("Enabling uuid-ossp extension")
                    cursor.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";')

                    self._logger.debug("Creating activity_tracker table if it does not exist")
                    create_table_query = """
                                         CREATE TABLE IF NOT EXISTS activity_tracker
                                         (
                                             id         UUID PRIMARY KEY   DEFAULT uuid_generate_v4(),
                                             created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                                             activity   TEXT      NOT NULL
                                         );"""
                    cursor.execute(create_table_query)
                    conn.commit()
        except psycopg2.Error as e:
            error_message = f"Error creating the work queue table: {str(e)}"
            self._logger.error(error_message)
            raise RuntimeError(error_message) from e

    def log_activity(self, activity, log_level):

        if not self._log_control[log_level]:
            return

        try:
            with self._get_connection() as conn:
                with conn.cursor() as cursor:
                    insert_query = "INSERT INTO activity_tracker (activity) VALUES (%s)"
                    cursor.execute(insert_query, (activity,))
                    conn.commit()

        except psycopg2.Error as e:
            error_message = f"Error logging activity: {str(e)}"
            self._logger.error(error_message)
            raise RuntimeError(error_message) from e

    def debug(self, activity):
        self._logger.debug(activity)
        self.log_activity(activity, "DEBUG")

    def info(self, activity):
        self._logger.info(activity)
        self.log_activity(activity, "INFO")

    def warning(self, activity):
        self._logger.warning(activity)
        self.log_activity(activity, "WARNING")

    def error(self, activity):
        self._logger.error(activity)
        self.log_activity(activity, "ERROR")

    def critical(self, activity):
        self._logger.critical(activity)
        self.log_activity(activity, "CRITICAL")
