from collections.abc import Iterator
from typing import Any

import pytest
import requests

from src.secret_store import SecretsError
from src.settings import MEDIA_IDENTIFIER_URL_SECRET as URL_SECRET
from src.tasks.identify_file import IdentifierError, IdentifierUnavailableError, identify_file

BASE_URL = "http://media-identifier:10147"
FILE_PATH = "/watch/Slow.Horses.S06E06.1080p.mkv"
RECORD = {"id": "0f8c", "media_type": "tv", "title": "Slow Horses", "season": 6, "used_nimble": False}


class _Response:
    def __init__(self, status_code: int, body: Any = None) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> Any:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class _FakeIdentifier:
    def __init__(self, answers: list[_Response | Exception]) -> None:
        self._answers = answers
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append({"url": url, **kwargs})
        answer = self._answers[min(len(self.calls), len(self._answers)) - 1]
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture(autouse=True)
def _configured(monkeypatch: pytest.MonkeyPatch, secret_values: dict[str, str]) -> Iterator[None]:
    secret_values[URL_SECRET] = f"{BASE_URL}/"
    monkeypatch.setattr("raccoontools.decorators.retry.sleep", lambda seconds: None)
    yield


def _serve(monkeypatch: pytest.MonkeyPatch, *answers: _Response | Exception) -> _FakeIdentifier:
    fake = _FakeIdentifier(list(answers))
    monkeypatch.setattr("requests.get", fake.get)
    return fake


def test_identify_file_200_returns_the_record(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _serve(monkeypatch, _Response(200, RECORD))

    assert identify_file(FILE_PATH) == RECORD
    assert fake.calls[0]["url"] == f"{BASE_URL}/api/guess"
    assert fake.calls[0]["params"] == {"it": FILE_PATH}


@pytest.mark.parametrize("status_code", [204, 400, 404, 422])
def test_identify_file_final_empty_answer_returns_none_without_retry(
    monkeypatch: pytest.MonkeyPatch, status_code: int
) -> None:
    fake = _serve(monkeypatch, _Response(status_code))

    assert identify_file(FILE_PATH) is None
    assert len(fake.calls) == 1


def test_identify_file_503_then_200_retries_and_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _serve(monkeypatch, _Response(503), _Response(200, RECORD))

    assert identify_file(FILE_PATH) == RECORD
    assert len(fake.calls) == 2


@pytest.mark.parametrize("status_code", [502, 503, 504])
def test_identify_file_persistent_outage_raises_unavailable(monkeypatch: pytest.MonkeyPatch, status_code: int) -> None:
    fake = _serve(monkeypatch, _Response(status_code))

    with pytest.raises(IdentifierUnavailableError, match=str(status_code)):
        identify_file(FILE_PATH)
    assert len(fake.calls) == 3


def test_identify_file_500_is_not_retried_and_raises_identifier_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _serve(monkeypatch, _Response(500, {"detail": "Internal error while processing the request."}))

    with pytest.raises(IdentifierError) as raised:
        identify_file(FILE_PATH)
    assert not isinstance(raised.value, IdentifierUnavailableError)
    assert len(fake.calls) == 1


def test_identify_file_connection_error_raises_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _serve(monkeypatch, requests.ConnectionError(f"Max retries exceeded with url: {BASE_URL}/api/guess"))

    with pytest.raises(IdentifierUnavailableError) as raised:
        identify_file(FILE_PATH)
    assert "ConnectionError" in str(raised.value)
    assert BASE_URL not in str(raised.value)
    assert raised.value.__cause__ is None
    assert len(fake.calls) == 3


def test_identify_file_timeout_then_200_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, requests.Timeout(), _Response(200, RECORD))

    assert identify_file(FILE_PATH) == RECORD


@pytest.mark.parametrize(
    "body", [requests.JSONDecodeError("Expecting value", "", 0), ["not", "a", "record"], {"title": "No id"}]
)
def test_identify_file_200_without_a_record_raises_identifier_error(monkeypatch: pytest.MonkeyPatch, body: Any) -> None:
    _serve(monkeypatch, _Response(200, body))

    with pytest.raises(IdentifierError, match="without a media record"):
        identify_file(FILE_PATH)


@pytest.mark.parametrize("api_url", ["media-identifier:10147", "http://user:pass@media-identifier", "ftp://x"])
def test_identify_file_invalid_base_url_raises_value_error(
    monkeypatch: pytest.MonkeyPatch, secret_values: dict[str, str], api_url: str
) -> None:
    secret_values[URL_SECRET] = api_url
    _serve(monkeypatch, _Response(200, RECORD))

    with pytest.raises(ValueError, match=URL_SECRET):
        identify_file(FILE_PATH)


def test_identify_file_missing_base_url_raises_secrets_error(
    monkeypatch: pytest.MonkeyPatch, secret_values: dict[str, str]
) -> None:
    del secret_values[URL_SECRET]
    _serve(monkeypatch, _Response(200, RECORD))

    with pytest.raises(SecretsError, match=URL_SECRET):
        identify_file(FILE_PATH)
