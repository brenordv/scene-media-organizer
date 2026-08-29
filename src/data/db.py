"""Application-level pool lifecycle. Entry points call init_pool() after
configuration is applied and before any repository module is imported."""

import os

from psycopg.conninfo import make_conninfo
from raccoontools.decorators.retry import retry
from raccoontools_db import PoolConfig, close_pool, create_pool, get_pool

from src.utils import get_otel_log_handler

# Creating this logger registers the psycopg OTEL instrumentation. It must happen
# before the pool opens its first connection; connections opened before
# instrumentation never produce query spans until the pool recycles them.
_logger = get_otel_log_handler("Database Pool", unique_handler_types=True)


def _build_conninfo() -> str:
    # `or`-defaults on purpose: the Dockerfile declares several variables as
    # empty strings, and an empty value must fall back like a missing one.
    params = {
        "host": os.environ.get("POSTGRES_HOST") or "localhost",
        "port": os.environ.get("POSTGRES_PORT") or "5432",
        "user": os.environ.get("POSTGRES_USER") or "postgres",
        "dbname": os.environ.get("POSTGRES_DB") or "smo_watchdog",
    }
    password = os.environ.get("POSTGRES_PASSWORD")
    if password:
        params["password"] = password
    return make_conninfo("", **params)


@retry(retries=5, delay=2, delay_is_exponential=True)
def init_pool() -> None:
    """Create the global pool and prove connectivity with one probe query.

    The probe matters: the pool opens lazily in the background, so creation
    alone does not confirm the database is reachable. On probe failure the
    pool is closed so the next retry starts clean.
    """
    try:
        create_pool(PoolConfig(conn_info=_build_conninfo()))
    except RuntimeError:
        # Pool object already exists from a previous attempt; probe it below.
        pass

    try:
        with get_pool().connection() as conn:
            conn.execute("SELECT 1")
        _logger.info("Database pool ready.")
    except Exception:
        close_pool()
        raise


def shutdown_pool() -> None:
    close_pool()
