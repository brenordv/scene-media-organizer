from typing import Any

import requests
from opentelemetry import trace

from src.data.activity_logger import ActivityTracker
from src.settings import telegram_credentials
from src.utils import get_env, get_telegram_api_base, to_bool_env

_REQUEST_TIMEOUT_SECONDS = 10

_activity_logger = ActivityTracker("Send Telegram Message")


def _error_description(response: requests.Response) -> str:
    try:
        data = response.json()
    except requests.JSONDecodeError:
        return "no description"
    if isinstance(data, dict) and isinstance(data.get("description"), str):
        return str(data["description"])
    return "no description"


@_activity_logger.trace("send_telegram_message")
def send_telegram_message(message: str) -> bool:
    """Send one message through the Telegram Bot API.

    Sending is best-effort: the batch report is a notification, not the record of the work, so every failure is
    logged and reported as False. Log lines never carry the request URL, which holds the bot token.

    Args:
        message: The text to send, formatted for ``TELEGRAM_PARSE_MODE`` (HTML by default).

    Returns:
        True when Telegram accepted the message, False when the configuration is missing or sending failed.
    """
    span = trace.get_current_span()
    credentials = telegram_credentials()

    if credentials is None:
        # Reported once at startup; every report would repeat it.
        _activity_logger.debug("Telegram is not configured; the report is not sent.")
        return False

    url = f"{get_telegram_api_base()}/bot{credentials.bot_token}/sendMessage"
    payload: dict[str, Any] = {
        "chat_id": credentials.chat_id,
        "text": message,
        "disable_web_page_preview": to_bool_env("TELEGRAM_DISABLE_WEB_PREVIEW", True),
        "disable_notification": to_bool_env("TELEGRAM_DISABLE_NOTIFICATION", False),
        "parse_mode": get_env("TELEGRAM_PARSE_MODE") or "HTML",
    }

    _activity_logger.debug("Sending Telegram message")
    try:
        response = requests.post(url, json=payload, timeout=_REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        # The exception text quotes the URL, and with it the bot token: only the class name is logged.
        _activity_logger.error(f"Sending the Telegram message failed ({type(exc).__name__}).")
        if span.is_recording():
            span.set_attribute("telegram.ok", False)
        return False

    if span.is_recording():
        span.set_attribute("http.status_code", response.status_code)

    try:
        data = response.json() if response.ok else None
    except requests.JSONDecodeError:
        data = None

    sent = isinstance(data, dict) and data.get("ok") is True
    if span.is_recording():
        span.set_attribute("telegram.ok", sent)

    if not sent:
        _activity_logger.error(
            f"Telegram rejected the message (HTTP {response.status_code}): {_error_description(response)}"
        )
        return False

    _activity_logger.debug("Telegram message sent successfully")
    return True
