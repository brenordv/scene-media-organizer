"""Client for the media identifier API (2.0): ``GET {base}/api/guess?it=<path>``."""

import os
from typing import Any

import requests
from opentelemetry import trace
from raccoontools.decorators.retry import retry, retry_request

from src.utils import get_otel_log_handler, parse_base_url

IDENTIFIER_URL_SETTING = "API_URL"
GUESS_PATH = "/api/guess"
# Answers that will not change on a retry: 204 means not media or unidentified, 4xx means a bad request.
FINAL_EMPTY_STATUSES = frozenset({204, 400, 404, 422})
# Answers that mean a service the identifier needs is down for now.
UNAVAILABLE_STATUSES = [502, 503, 504]

_REQUEST_TIMEOUT = (5, 30)  # connect, read (seconds)
_NETWORK_RETRIES = 3
_STATUS_RETRIES = 3
_RETRY_DELAY_SECONDS = 2

_logger = get_otel_log_handler("Identify File", unique_handler_types=True)


class IdentifierError(Exception):
    """The identifier answered in a way that is not a usable result and not a known transient failure.

    The message carries the HTTP status code at most, never the response body or the URL.
    """


class IdentifierUnavailableError(IdentifierError):
    """The identifier, or a service it depends on, could not be reached; the same request can succeed later."""


def get_identifier_base_url() -> str:
    """Return the media identifier base URL from ``API_URL``.

    Returns:
        The base URL, without a trailing slash.

    Raises:
        ValueError: If ``API_URL`` is unset, or not an http(s) URL with a host and no credentials, query or fragment.
    """
    return parse_base_url(os.environ.get(IDENTIFIER_URL_SETTING, ""), IDENTIFIER_URL_SETTING)


# Network errors re-run the whole status-retry loop, so one call makes at most
# _NETWORK_RETRIES x _STATUS_RETRIES requests and sleeps up to about 24 s in total.
@retry(
    retries=_NETWORK_RETRIES,
    delay=_RETRY_DELAY_SECONDS,
    delay_is_exponential=True,
    only_exceptions_of_type=[requests.exceptions.ConnectionError, requests.exceptions.Timeout],
)
@retry_request(
    retries=_STATUS_RETRIES,
    delay=_RETRY_DELAY_SECONDS,
    delay_is_exponential=True,
    retry_only_on_status_codes=UNAVAILABLE_STATUSES,
)
def _request_identify(url: str, full_path: str) -> requests.Response:
    return requests.get(url, params={"it": full_path}, timeout=_REQUEST_TIMEOUT)


def _record(span: trace.Span, outcome: str, status_code: int | None = None) -> None:
    if span.is_recording():
        span.set_attribute("identify.outcome", outcome)
        if status_code is not None:
            span.set_attribute("http.status_code", status_code)


@_logger.trace("identify_file")
def identify_file(full_path: str) -> dict[str, Any] | None:
    """Ask the media identifier what a file is.

    Transient answers (502, 503, 504) and network errors are retried with backoff before giving up.

    Args:
        full_path: The file's path; the identifier also retries with the bare file name on its own.

    Returns:
        The media record for a 200 answer, or None when the answer is final and empty (204: not media or
        unidentified; 400, 404, 422: a request the identifier will never accept).

    Raises:
        IdentifierUnavailableError: If the identifier stayed unreachable or kept answering 502/503/504.
        IdentifierError: On a 500, any other unexpected status, or a 200 whose body is not a record with an ``id``.
    """
    span = trace.get_current_span()
    if span.is_recording():
        span.set_attribute("file.path", full_path)

    try:
        response = _request_identify(f"{get_identifier_base_url()}{GUESS_PATH}", full_path)
    except requests.RequestException as e:
        _record(span, "unavailable")
        raise IdentifierUnavailableError(f"Media identifier unreachable ({type(e).__name__})") from None

    status_code = response.status_code
    if status_code in FINAL_EMPTY_STATUSES:
        _record(span, "final_none", status_code)
        return None
    if status_code in UNAVAILABLE_STATUSES:
        _record(span, "unavailable", status_code)
        raise IdentifierUnavailableError(f"Media identifier unavailable (HTTP {status_code})")
    if status_code != 200:
        _record(span, "error", status_code)
        raise IdentifierError(f"Media identifier failed (HTTP {status_code})")

    try:
        body = response.json()
    except requests.JSONDecodeError:
        body = None

    if not isinstance(body, dict) or body.get("id") is None:
        _record(span, "error", status_code)
        raise IdentifierError("Media identifier answered 200 without a media record")

    _record(span, "identified", status_code)
    return body
