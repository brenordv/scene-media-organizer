from typing import Any

import pytest
import requests

from src.settings import TELEGRAM_BOT_TOKEN_SECRET, TELEGRAM_CHAT_ID_SECRET
from src.tasks import send_telegram_message as telegram_module
from src.tasks.send_telegram_message import send_telegram_message

BOT_TOKEN = "123456:secret-bot-token"
CHAT_ID = "42"


class _Response:
    def __init__(self, status_code: int, body: Any) -> None:
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._body = body

    def json(self) -> Any:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


@pytest.fixture
def telegram_configured(secret_values: dict[str, str]) -> None:
    secret_values[TELEGRAM_BOT_TOKEN_SECRET] = f"  {BOT_TOKEN}\n"
    secret_values[TELEGRAM_CHAT_ID_SECRET] = CHAT_ID


@pytest.fixture
def activity_lines(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    lines: list[str] = []
    tracker = telegram_module._activity_logger
    for level in ("debug", "info", "warning", "error"):
        monkeypatch.setattr(tracker, level, lines.append)
    return lines


@pytest.mark.usefixtures("telegram_configured")
def test_send_telegram_message_accepted_returns_true(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_post(url: str, **kwargs: Any) -> _Response:
        calls.append({"url": url, **kwargs})
        return _Response(200, {"ok": True})

    monkeypatch.setattr("requests.post", fake_post)

    assert send_telegram_message("<b>done</b>") is True
    assert calls[0]["url"] == f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    assert calls[0]["json"]["chat_id"] == CHAT_ID
    assert calls[0]["json"]["parse_mode"] == "HTML"


@pytest.mark.usefixtures("telegram_configured")
def test_send_telegram_message_connection_error_never_logs_the_token(
    monkeypatch: pytest.MonkeyPatch, activity_lines: list[str]
) -> None:
    def failing_post(url: str, **kwargs: Any) -> _Response:
        raise requests.ConnectionError(f"Max retries exceeded with url: /bot{BOT_TOKEN}/sendMessage")

    monkeypatch.setattr("requests.post", failing_post)

    assert send_telegram_message("report") is False
    assert any("ConnectionError" in line for line in activity_lines)
    assert all(BOT_TOKEN not in line for line in activity_lines)


@pytest.mark.usefixtures("telegram_configured")
def test_send_telegram_message_rejected_logs_description_only(
    monkeypatch: pytest.MonkeyPatch, activity_lines: list[str]
) -> None:
    body = {"ok": False, "description": "Bad Request: chat not found"}
    monkeypatch.setattr("requests.post", lambda url, **kwargs: _Response(400, body))

    assert send_telegram_message("report") is False
    assert any("HTTP 400" in line and "chat not found" in line for line in activity_lines)


@pytest.mark.usefixtures("telegram_configured")
def test_send_telegram_message_ok_false_body_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("requests.post", lambda url, **kwargs: _Response(200, {"ok": False}))

    assert send_telegram_message("report") is False


@pytest.mark.usefixtures("telegram_configured")
def test_send_telegram_message_invalid_json_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    error = requests.JSONDecodeError("Expecting value", "", 0)
    monkeypatch.setattr("requests.post", lambda url, **kwargs: _Response(200, error))

    assert send_telegram_message("report") is False


def test_send_telegram_message_missing_configuration_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr("requests.post", lambda url, **kwargs: calls.append(url))

    assert send_telegram_message("report") is False
    assert calls == []
