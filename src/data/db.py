"""Application-level pool lifecycle. Entry points call init_pool() after the settings are loaded and before any
repository module is imported."""

import psycopg
from psycopg.conninfo import conninfo_to_dict
from raccoontools.decorators.retry import retry
from raccoontools_db import PoolConfig, close_pool, create_pool, get_pool

from src.secret_store import get_secret
from src.utils import get_otel_log_handler

POSTGRES_CONNECTION_STRING_SECRET = "MEDIA_ORGANIZER_POSTGRES_CONNECTION_STRING"
PROBE_TIMEOUT_SECONDS = 5

# Creating this logger registers the psycopg OTEL instrumentation. It must happen
# before the pool opens its first connection; connections opened before
# instrumentation never produce query spans until the pool recycles them.
_logger = get_otel_log_handler("Database Pool", unique_handler_types=True)


def build_conninfo() -> str:
    """Return the libpq connection string held in the ``MEDIA_ORGANIZER_POSTGRES_CONNECTION_STRING`` secret.

    The string is a URI (``postgresql://user:password@host:5432/dbname``) or ``key=value`` pairs, and must name the
    database.

    Returns:
        The connection string.

    Raises:
        SecretsError: If the secret is not set, or Infisical cannot be read.
        ValueError: If the secret is not a valid connection string with a database name.
    """
    conninfo = get_secret(POSTGRES_CONNECTION_STRING_SECRET)
    try:
        params = conninfo_to_dict(conninfo)
    except psycopg.ProgrammingError:
        params = None

    # libpq's parse errors quote the malformed part of the string, and a URI password holding an unencoded @ or /
    # still parses but spills into other parameters (host, port, database name, query options), which connect errors
    # quote; either can be the password. A spilled password always leaves the @ that should have ended it outside the
    # user name and password. The error is raised outside the except block so none of libpq's text travels with it.
    if (
        params is None
        or not params.get("dbname")
        or any("@" in str(value) for key, value in params.items() if key not in ("user", "password"))
    ):
        raise ValueError(
            f"Infisical secret '{POSTGRES_CONNECTION_STRING_SECRET}' is not a valid PostgreSQL connection string with "
            "a database name; percent-encode special characters in a URI user name and password, or use quoted "
            "key=value pairs."
        )
    return conninfo


@retry(retries=5, delay=2, delay_is_exponential=True)
def init_pool() -> None:
    """Create the global pool and prove connectivity with one probe query.

    The probe matters: the pool opens lazily in the background, so creation
    alone does not confirm the database is reachable. On probe failure the
    pool is closed so the next retry starts clean.
    """
    try:
        create_pool(PoolConfig(conn_info=build_conninfo()))
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


def probe_database() -> bool:
    """Tell whether the pool can run a query right now.

    Best-effort health reporting: a failure only means "not healthy", so it is returned rather than raised.

    Returns:
        True when ``SELECT 1`` succeeded within the timeout.
    """
    try:
        with get_pool().connection(timeout=PROBE_TIMEOUT_SECONDS) as conn:
            conn.execute("SELECT 1")
    except Exception:
        return False
    return True


def shutdown_pool() -> None:
    close_pool()
