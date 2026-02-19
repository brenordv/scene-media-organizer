import hashlib
import os
from pathlib import Path
from typing import Optional, Union

from simple_log_factory_ext_otel import otel_log_factory, TracedLogger

_all_loggers: dict[str, TracedLogger] = {}


def to_int(value: Optional[Union[str, int]], default: int) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def get_env(name: str) -> Optional[str]:
    value = os.environ.get(name)
    if value is not None:
        value = value.strip()
    return value or None


def to_bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open('rb') as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b''):
            hasher.update(chunk)
    return hasher.hexdigest()

def get_otel_log_handler(log_name: str, **kwargs) -> TracedLogger:
    cached = _all_loggers.get(log_name)
    if cached is not None:
        return cached

    otel_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")

    if not otel_endpoint:
        raise ValueError(
            "OTEL_EXPORTER_OTLP_ENDPOINT environment variable must be set."
        )

    service_name = "scene-media-organizer"

    traced = otel_log_factory(
        service_name=service_name,
        log_name=log_name,
        otel_exporter_endpoint=otel_endpoint,
        instrument_db={"psycopg2": {"enable_commenter": True}},
        **kwargs,
    )

    _all_loggers[log_name] = traced

    return traced


def flush_all_otel_loggers() -> None:
    """Flush every OtelLogHandler created via get_otel_log_handler().

    Must be called before blocking event loops on Windows to drain all
    BatchLogRecordProcessor queues and avoid a deadlock between
    the batch-export background threads and event loop init.
    """
    for traced in _all_loggers.values():
        for h in traced.logger.handlers:
            h.flush()
