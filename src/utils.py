import ctypes
import gc
import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from simple_log_factory_ext_otel import TracedLogger, otel_log_factory

SERVICE_NAME = "media-organizer"
TELEGRAM_DEFAULT_API_BASE = "https://api.telegram.org"
CHILD_PROCESS_ENV_VARIABLES = ("PATH", "LANG", "LC_ALL", "HOME")

_all_loggers: dict[str, TracedLogger] = {}
_log = logging.getLogger(__name__)

# Resolve malloc_trim once at import time.
# Available on glibc (Debian/python:*-slim); silently absent elsewhere.
_malloc_trim: Any = None
try:
    _libc = ctypes.CDLL("libc.so.6")
    _malloc_trim = _libc.malloc_trim
    _malloc_trim.argtypes = [ctypes.c_int]
    _malloc_trim.restype = ctypes.c_int
except OSError:
    _malloc_trim = None
    _log.info("malloc_trim unavailable (non-glibc platform); idle memory release will use gc.collect() only.")


def to_int(value: str | int | None, default: int) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except TypeError, ValueError:
        return default


def get_env(name: str) -> str | None:
    value = os.environ.get(name)
    if value is not None:
        value = value.strip()
    return value or None


def to_bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def child_process_env() -> dict[str, str]:
    """Return the environment handed to external tools (rsync, 7zz).

    Only the variables a tool needs to run are passed on, so credentials held in this process's environment (the
    secret-manager bootstrap among them) never reach a child process.

    Returns:
        The variables in ``CHILD_PROCESS_ENV_VARIABLES`` that are set.
    """
    return {name: os.environ[name] for name in CHILD_PROCESS_ENV_VARIABLES if name in os.environ}


def get_telegram_api_base() -> str:
    """Return the Telegram Bot API base URL: ``TELEGRAM_API_BASE``, or the public API when unset.

    The bot token travels in the request path, so the URL must use https.

    Returns:
        The base URL without a trailing slash.

    Raises:
        ValueError: If ``TELEGRAM_API_BASE`` is not an https URL with a host, or carries credentials, a query or a
            fragment.
    """
    api_base = (get_env("TELEGRAM_API_BASE") or TELEGRAM_DEFAULT_API_BASE).rstrip("/")
    parts = urlsplit(api_base)
    if parts.scheme != "https" or not parts.hostname or "@" in parts.netloc or parts.query or parts.fragment:
        raise ValueError("TELEGRAM_API_BASE must be an https URL with a host and no credentials, query or fragment.")
    return api_base


def telegram_excluded_urls(api_base: str) -> str:
    """Return the requests-instrumentation exclusion pattern for the Telegram Bot API.

    The instrumentation records each request URL on its span, and Telegram URLs carry the bot token in the path.

    Args:
        api_base: The Telegram Bot API base URL.

    Returns:
        A regular expression matching every URL under ``api_base``'s scheme and host.
    """
    parts = urlsplit(api_base)
    return f"^{re.escape(parts.scheme)}://{re.escape(parts.netloc)}"


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def instrumentation_options() -> dict[str, Any]:
    """Return the database and HTTP auto-instrumentation options every logger is created with.

    psycopg spans carry SQL commenter context; requests spans skip the Telegram Bot API, whose URLs hold the bot token.

    Returns:
        The ``instrument_db`` and ``instrument_requests`` keyword arguments for ``otel_log_factory``.

    Raises:
        ValueError: If ``TELEGRAM_API_BASE`` is invalid.
    """
    return {
        "instrument_db": {"psycopg": {"enable_commenter": True}},
        "instrument_requests": {"excluded_urls": telegram_excluded_urls(get_telegram_api_base())},
    }


def get_otel_log_handler(log_name: str, **kwargs: Any) -> TracedLogger:
    """Create (or reuse) a logger that exports logs and traces to the OTLP endpoint.

    The instrumentation applies process-wide and only on the first call, so every call passes the same options.

    Args:
        log_name: Name of the logger; one logger is kept per name.
        **kwargs: Extra arguments for ``otel_log_factory``.

    Returns:
        The traced logger.

    Raises:
        ValueError: If ``OTEL_EXPORTER_OTLP_ENDPOINT`` is not set or ``TELEGRAM_API_BASE`` is invalid.
    """
    cached = _all_loggers.get(log_name)
    if cached is not None:
        return cached

    otel_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")

    if not otel_endpoint:
        raise ValueError("OTEL_EXPORTER_OTLP_ENDPOINT environment variable must be set.")

    traced = otel_log_factory(
        service_name=SERVICE_NAME,
        log_name=log_name,
        otel_exporter_endpoint=otel_endpoint,
        **instrumentation_options(),
        **kwargs,
    )

    _all_loggers[log_name] = traced

    return traced


def release_idle_memory() -> None:
    """Force garbage collection and return freed heap pages to the OS.

    Intended to be called during idle periods (no batches to process)
    to reduce the resident memory footprint of the process.
    """
    try:
        collected = gc.collect()
        if collected > 0:
            _log.debug("gc.collect() reclaimed %d objects.", collected)

        if _malloc_trim is not None:
            result = _malloc_trim(0)
            if result == 0:
                _log.debug("malloc_trim(0) returned 0 — no memory could be released to the OS.")
    except Exception as e:
        _log.exception(f"Unexpected error during idle memory release. Error: {e}")


def flush_all_otel_loggers() -> None:
    """Flush every OtelLogHandler created via get_otel_log_handler().

    Must be called before blocking event loops on Windows to drain all
    BatchLogRecordProcessor queues and avoid a deadlock between
    the batch-export background threads and event loop init.
    """
    for traced in _all_loggers.values():
        for h in traced.logger.handlers:
            h.flush()
