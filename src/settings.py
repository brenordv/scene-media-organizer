"""Service connection settings held in Infisical, and the startup step that reads every one of them once.

This module imports nothing that touches the database or the broker at import time, because it runs before either is
set up.
"""

from dataclasses import dataclass, field

from simple_log_factory_ext_otel import TracedLogger

from src.data.db import POSTGRES_CONNECTION_STRING_SECRET, build_conninfo
from src.secret_store import get_optional_secret, get_secret, get_secret_store
from src.utils import parse_base_url, to_int

MEDIA_IDENTIFIER_URL_SECRET = "MEDIA_ORGANIZER_MEDIA_IDENTIFIER_URL"
MQTT_HOST_SECRET = "MEDIA_ORGANIZER_MQTT_HOST"
MQTT_PORT_SECRET = "MEDIA_ORGANIZER_MQTT_PORT"
MQTT_USERNAME_SECRET = "MEDIA_ORGANIZER_MQTT_USERNAME"
MQTT_PASSWORD_SECRET = "MEDIA_ORGANIZER_MQTT_PASSWORD"
TELEGRAM_BOT_TOKEN_SECRET = "MEDIA_ORGANIZER_TELEGRAM_BOT_TOKEN"
TELEGRAM_CHAT_ID_SECRET = "MEDIA_ORGANIZER_TELEGRAM_CHAT_ID"

DEFAULT_MQTT_PORT = 1883
_MAX_PORT = 65535


@dataclass(frozen=True)
class MqttSettings:
    """How to reach the MQTT broker.

    Attributes:
        host: Broker host name or address.
        port: Broker TCP port.
        username: Login name, or None when the broker needs no authentication.
        password: Login password, or None.
    """

    host: str
    port: int
    username: str | None = field(default=None, repr=False)
    password: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class TelegramCredentials:
    """The bot that sends batch reports and the chat it sends them to."""

    bot_token: str = field(repr=False)
    chat_id: str = field(repr=False)


def media_identifier_base_url() -> str:
    """Return the media identifier's base URL (for example ``http://media-identifier:10147``).

    Returns:
        The base URL, without a trailing slash.

    Raises:
        SecretsError: If the secret is not set, or Infisical cannot be read.
        ValueError: If the value is not an http(s) URL with a host and no credentials, query or fragment.
    """
    return parse_base_url(get_secret(MEDIA_IDENTIFIER_URL_SECRET), MEDIA_IDENTIFIER_URL_SECRET)


def mqtt_settings() -> MqttSettings:
    """Return the broker settings; the port defaults to 1883.

    Returns:
        The settings.

    Raises:
        SecretsError: If the host is not set, or Infisical cannot be read.
        ValueError: If the port is not a number between 1 and 65535.
    """
    raw_port = get_optional_secret(MQTT_PORT_SECRET)
    port = to_int(raw_port, -1) if raw_port is not None else DEFAULT_MQTT_PORT
    if not 1 <= port <= _MAX_PORT:
        raise ValueError(f"Infisical secret '{MQTT_PORT_SECRET}' must be a port number between 1 and {_MAX_PORT}.")
    return MqttSettings(
        host=get_secret(MQTT_HOST_SECRET),
        port=port,
        username=get_optional_secret(MQTT_USERNAME_SECRET),
        password=get_optional_secret(MQTT_PASSWORD_SECRET),
    )


def telegram_credentials() -> TelegramCredentials | None:
    """Return the Telegram bot token and chat id, or None when either is not set.

    Raises:
        SecretsError: If Infisical cannot be read.
    """
    bot_token = get_optional_secret(TELEGRAM_BOT_TOKEN_SECRET)
    chat_id = get_optional_secret(TELEGRAM_CHAT_ID_SECRET)
    if bot_token is None or chat_id is None:
        return None
    return TelegramCredentials(bot_token=bot_token, chat_id=chat_id)


def load_settings(logger: TracedLogger) -> None:
    """Read and validate every setting held in Infisical, so nothing reaches Infisical after startup.

    Args:
        logger: Logger for progress lines; they name settings and the source, never values.

    Raises:
        SecretsError: If the bootstrap is incomplete, Infisical cannot be read, or a required setting is not set.
        ValueError: If a setting is set but invalid.
    """
    store = get_secret_store()
    if store.uses_plain_http:
        logger.warning("INFISICAL_HOST uses plain http: the machine identity's client secret travels unencrypted.")
    logger.info(f"Reading settings from {store.source}")

    build_conninfo()
    media_identifier_base_url()
    mqtt_settings()
    if telegram_credentials() is None:
        logger.warning(
            f"{TELEGRAM_BOT_TOKEN_SECRET} or {TELEGRAM_CHAT_ID_SECRET} is not set: Telegram notifications are disabled."
        )

    logger.info(
        f"Settings loaded: {POSTGRES_CONNECTION_STRING_SECRET}, {MEDIA_IDENTIFIER_URL_SECRET}, "
        f"{MQTT_HOST_SECRET} and the optional MQTT and Telegram settings."
    )
