import os
from typing import Any

import requests
from opentelemetry import trace
from raccoontools.decorators.retry import retry, retry_request

from src.utils import get_otel_log_handler

_url = os.environ.get("API_URL")
_logger = get_otel_log_handler("Identify File", unique_handler_types=True)

_RETRYABLE_STATUSES = [500, 502, 503, 504]
_REQUEST_TIMEOUT = (5, 30)  # connect, read (seconds)


@retry(
    retries=3,
    delay=2,
    delay_is_exponential=True,
    only_exceptions_of_type=[requests.exceptions.ConnectionError, requests.exceptions.Timeout],
)
@retry_request(retries=3, delay=2, delay_is_exponential=True, retry_only_on_status_codes=_RETRYABLE_STATUSES)
def _request_identify(url: str, full_path: str) -> requests.Response:
    return requests.get(url, params={"it": full_path}, timeout=_REQUEST_TIMEOUT)


@_logger.trace("identify_file")
def identify_file(full_path: str) -> Any:
    span = trace.get_current_span()
    if span.is_recording():
        span.set_attributes(
            {
                "file.path": full_path,
                "http.url": _url or "",
            }
        )

    response = _request_identify(_url, full_path)

    if span.is_recording():
        span.set_attribute("http.status_code", response.status_code)

    if response.status_code in _RETRYABLE_STATUSES:
        raise requests.exceptions.RetryError(f"Identify service still returning {response.status_code} after retries")

    if response.status_code == 204:
        _logger.debug(f"Success identify request, but no useful data returned for: {full_path}")
        return None

    if response.ok:
        _logger.debug(f"Identify request successful for: {full_path}")
        return response.json()

    _logger.error(f"Identify request failed ({response.status_code}) for [{full_path}] " f"with error: {response.text}")
    return None
