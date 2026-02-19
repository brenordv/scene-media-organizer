import os

import requests
from opentelemetry import trace

from src.utils import get_otel_log_handler

_url = os.environ.get('API_URL')
_logger = get_otel_log_handler("Identify File", unique_handler_types=True)


@_logger.trace("identify_file")
def identify_file(full_path: str):
    span = trace.get_current_span()
    if span.is_recording():
        span.set_attributes({
            "file.path": full_path,
            "http.url": _url,
        })

    response = requests.get(_url, params={'it': full_path})

    if span.is_recording():
        span.set_attribute("http.status_code", response.status_code)

    if response.status_code == 204:
        _logger.debug(
            f"Success identify request, but no useful data returned for: {full_path}"
        )
        return None

    if response.ok:
        _logger.debug(f"Identify request successful for: {full_path}")
        return response.json()

    _logger.error(
        f"Identify request failed ({response.status_code}) for [{full_path}] "
        f"with error: {response.text}"
    )
    return None
