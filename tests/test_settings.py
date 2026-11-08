import pytest

from src import secret_store
from src.secret_store import SecretsError
from src.settings import (
    MEDIA_IDENTIFIER_URL_SECRET,
    MQTT_HOST_SECRET,
    MQTT_PORT_SECRET,
    TELEGRAM_BOT_TOKEN_SECRET,
    TELEGRAM_CHAT_ID_SECRET,
    MqttSettings,
    TelegramCredentials,
    load_settings,
    media_identifier_base_url,
    mqtt_settings,
    telegram_credentials,
)
from tests.fakes import RecordingLogger, as_traced


def test_media_identifier_base_url_drops_the_trailing_slash(secret_values: dict[str, str]) -> None:
    secret_values[MEDIA_IDENTIFIER_URL_SECRET] = "http://media-identifier.lan:10147/"

    assert media_identifier_base_url() == "http://media-identifier.lan:10147"


def test_mqtt_settings_default_port_and_no_login() -> None:
    assert mqtt_settings() == MqttSettings(host="mqtt.test", port=1883)


@pytest.mark.parametrize("port", ["0", "65536", "mqtt"])
def test_mqtt_settings_invalid_port_raises_value_error(secret_values: dict[str, str], port: str) -> None:
    secret_values[MQTT_PORT_SECRET] = port

    with pytest.raises(ValueError, match=MQTT_PORT_SECRET):
        mqtt_settings()


def test_telegram_credentials_need_both_values(secret_values: dict[str, str]) -> None:
    secret_values[TELEGRAM_BOT_TOKEN_SECRET] = "123:token"

    assert telegram_credentials() is None


def test_telegram_credentials_repr_hides_the_token(secret_values: dict[str, str]) -> None:
    secret_values[TELEGRAM_BOT_TOKEN_SECRET] = "123:token"
    secret_values[TELEGRAM_CHAT_ID_SECRET] = "42"

    credentials = telegram_credentials()

    assert credentials == TelegramCredentials(bot_token="123:token", chat_id="42")
    assert "123:token" not in repr(credentials)


def test_load_settings_reads_everything_and_warns_when_telegram_is_off() -> None:
    logger = RecordingLogger()

    load_settings(as_traced(logger))

    assert any("Telegram notifications are disabled" in text for level, text in logger.lines if level == "warning")
    assert any("Settings loaded" in text for level, text in logger.lines if level == "info")


def test_load_settings_with_telegram_set_has_no_warning(secret_values: dict[str, str]) -> None:
    secret_values[TELEGRAM_BOT_TOKEN_SECRET] = "123:token"
    secret_values[TELEGRAM_CHAT_ID_SECRET] = "42"
    logger = RecordingLogger()

    load_settings(as_traced(logger))

    assert [level for level, _ in logger.lines] == ["info", "info"]


def test_load_settings_warns_about_a_plain_http_infisical_host(
    monkeypatch: pytest.MonkeyPatch, secret_values: dict[str, str]
) -> None:
    store = secret_store.SecretStore(secret_values.get, "test secrets", uses_plain_http=True)
    monkeypatch.setattr(secret_store, "_default_store", store)
    logger = RecordingLogger()

    load_settings(as_traced(logger))

    assert "plain http" in logger.lines[0][1]


@pytest.mark.parametrize("secret", [MQTT_HOST_SECRET, MEDIA_IDENTIFIER_URL_SECRET])
def test_load_settings_missing_required_setting_raises(secret_values: dict[str, str], secret: str) -> None:
    del secret_values[secret]

    with pytest.raises(SecretsError, match=secret):
        load_settings(as_traced(RecordingLogger()))


def test_load_settings_invalid_identifier_url_raises(secret_values: dict[str, str]) -> None:
    secret_values[MEDIA_IDENTIFIER_URL_SECRET] = "media-identifier:10147"

    with pytest.raises(ValueError, match=MEDIA_IDENTIFIER_URL_SECRET):
        load_settings(as_traced(RecordingLogger()))
