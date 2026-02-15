import os
from contextlib import contextmanager
from psycopg2.pool import SimpleConnectionPool

from src.utils import get_otel_log_handler

_db_pool = SimpleConnectionPool(
    minconn = 1,
    maxconn = 20,
    host = os.environ.get('POSTGRES_HOST', 'localhost'),
    port = os.environ.get('POSTGRES_PORT', '5432'),
    user = os.environ.get('POSTGRES_USER', 'postgres'),
    password=os.environ.get('POSTGRES_PASSWORD', 'postgres'),
    dbname = 'smo_watchdog'
)

class BaseRepository:
    def __init__(self, log_name: str, log_level: str = "DEBUG"):
        self._logger = get_otel_log_handler(log_name, unique_handler_types=True, log_level=log_level)
        self._ensure_table_exists()

    @contextmanager
    def _get_connection(self):
        conn = _db_pool.getconn()
        try:
            yield conn
        finally:
            _db_pool.putconn(conn)

    def _ensure_table_exists(self):
        pass
