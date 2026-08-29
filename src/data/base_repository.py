from contextlib import contextmanager

from opentelemetry import trace
from raccoontools_db import get_pool

from src.utils import get_otel_log_handler


class BaseRepository:
    def __init__(self, log_name: str, log_level: str = "DEBUG"):
        self._logger = get_otel_log_handler(
            log_name, unique_handler_types=True, log_level=log_level
        )
        self._ensure_table_exists()

    @contextmanager
    def _get_connection(self):
        tracer = trace.get_tracer(__name__)
        with tracer.start_as_current_span("BaseRepository._get_connection"):
            with get_pool().connection(timeout=10) as conn:
                yield conn

    def _ensure_table_exists(self):
        pass
