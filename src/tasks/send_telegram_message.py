import requests
from src.data.activity_logger import ActivityTracker
from src.utils import get_env, to_bool_env

_activity_logger = ActivityTracker("Send Telegram Message")


def send_telegram_message(message: str) -> bool:
    token = get_env("TELEGRAM_BOT_TOKEN")
    chat_id = get_env("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        _activity_logger.error(
            "Missing Telegram configuration. Ensure TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set."
        )
        return False

    api_base = get_env("TELEGRAM_API_BASE") or "https://api.telegram.org"
    url = f"{api_base}/bot{token}/sendMessage"

    parse_mode = get_env("TELEGRAM_PARSE_MODE") or "HTML"

    payload: dict = {
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": to_bool_env("TELEGRAM_DISABLE_WEB_PREVIEW", True),
        "disable_notification": to_bool_env("TELEGRAM_DISABLE_NOTIFICATION", False),
        "parse_mode": parse_mode
    }

    try:
        _activity_logger.debug(f"Sending Telegram message to chat_id={chat_id}")
        response = requests.post(url, json=payload, timeout=10)

        if not response.ok:
            _activity_logger.error(
                f"Telegram API HTTP error {response.status_code}: {response.text}"
            )
            return False

        data = response.json()
        if not isinstance(data, dict) or not data.get("ok", False):
            _activity_logger.error(f"Telegram API returned error: {data}")
            return False

        _activity_logger.debug("Telegram message sent successfully")
        return True
    except Exception as exc:
        _activity_logger.error(f"Error sending Telegram message: {exc}")
        return False


