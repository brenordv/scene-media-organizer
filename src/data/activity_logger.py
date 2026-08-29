from collections.abc import Callable
from typing import Any, TypeVar, cast

import psycopg
from opentelemetry import trace

from src.data.base_repository import BaseRepository

_log_levels = {"NOTSET": 0, "DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}

F = TypeVar("F", bound=Callable[..., Any])


class ActivityTracker(BaseRepository):
    def __init__(self, log_name: str | None = None, log_level: str = "DEBUG") -> None:
        super().__init__("Activity Tracker" if log_name is None else log_name, log_level)
        self._log_control = {
            "NOTSET": False,
            "DEBUG": False,
            "INFO": False,
            "WARNING": False,
            "ERROR": False,
            "CRITICAL": False,
        }
        self._set_log_level(log_level)

    def _set_log_level(self, log_level: str) -> None:
        target_level = log_level.upper().strip()
        level = 999

        for log_level, value in _log_levels.items():
            if log_level == target_level:
                level = value
                self._log_control[log_level] = True
                continue

            self._log_control[log_level] = value >= level

    def trace(self, span_name: str) -> Callable[[F], F]:
        """Delegate tracing to the underlying TracedLogger."""
        return cast(Callable[[F], F], self._logger.trace(span_name))

    def _ensure_table_exists(self) -> None:
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
        except psycopg.Error as e:
            error_message = f"Error creating the work queue table: {str(e)}"
            self._logger.error(error_message)
            raise RuntimeError(error_message) from e

    def log_activity(self, activity: str, log_level: str) -> None:

        if not self._log_control[log_level]:
            return

        tracer = trace.get_tracer(__name__)
        with tracer.start_as_current_span("ActivityTracker.log_activity") as span:
            if span.is_recording():
                span.set_attributes(
                    {
                        "db.table": "activity_tracker",
                        "db.operation": "insert",
                        "log.level": log_level,
                    }
                )

            try:
                with self._get_connection() as conn:
                    with conn.cursor() as cursor:
                        insert_query = "INSERT INTO activity_tracker (activity) VALUES (%s)"
                        cursor.execute(insert_query, (activity,))
                        conn.commit()

            except psycopg.Error as e:
                error_message = f"Error logging activity: {str(e)}"
                self._logger.error(error_message)
                raise RuntimeError(error_message) from e

    def debug(self, activity: str) -> None:
        self._logger.debug(activity)
        self.log_activity(activity, "DEBUG")

    def info(self, activity: str) -> None:
        self._logger.info(activity)
        self.log_activity(activity, "INFO")

    def warning(self, activity: str) -> None:
        self._logger.warning(activity)
        self.log_activity(activity, "WARNING")

    def error(self, activity: str) -> None:
        self._logger.error(activity)
        self.log_activity(activity, "ERROR")

    def critical(self, activity: str) -> None:
        self._logger.critical(activity)
        self.log_activity(activity, "CRITICAL")
