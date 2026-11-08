import json
from typing import Any

import paho.mqtt.client as mqtt
import pytest
from paho.mqtt.enums import CallbackAPIVersion, MQTTErrorCode
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.reasoncodes import ReasonCode

from src.data.notification_repository import NotificationRepository
from src.secret_store import SecretsError
from src.settings import MQTT_HOST_SECRET, MQTT_PASSWORD_SECRET, MQTT_PORT_SECRET, MQTT_USERNAME_SECRET


class _PublishResult:
    def __init__(self, rc: MQTTErrorCode) -> None:
        self.rc = rc


@pytest.fixture
def repository(monkeypatch: pytest.MonkeyPatch) -> NotificationRepository:
    repo = NotificationRepository(broker_host="127.0.0.1", broker_port=1883, base_topic="notifications")
    monkeypatch.setattr(repo._client, "connect", lambda *args, **kwargs: mqtt.MQTT_ERR_SUCCESS)
    monkeypatch.setattr(repo._client, "loop_start", lambda: mqtt.MQTT_ERR_SUCCESS)
    return repo


@pytest.fixture
def log_lines(monkeypatch: pytest.MonkeyPatch, repository: NotificationRepository) -> list[tuple[str, str]]:
    lines: list[tuple[str, str]] = []
    for level in ("debug", "info", "warning", "error"):
        monkeypatch.setattr(repository._logger, level, lambda text, level=level: lines.append((level, text)))
    return lines


def _connack(value: int) -> ReasonCode:
    return ReasonCode(PacketTypes.CONNACK, identifier=value)


def test_client_uses_callback_api_version_2(repository: NotificationRepository) -> None:
    assert repository._client.callback_api_version == CallbackAPIVersion.VERSION2


def test_broker_settings_come_from_the_secret_store(secret_values: dict[str, str]) -> None:
    secret_values[MQTT_PORT_SECRET] = "8883"
    secret_values[MQTT_USERNAME_SECRET] = "organizer"
    secret_values[MQTT_PASSWORD_SECRET] = "broker-password"

    repo = NotificationRepository()

    assert (repo._broker_host, repo._broker_port) == ("mqtt.test", 8883)
    assert repo._client._username == b"organizer"


def test_missing_broker_host_raises(secret_values: dict[str, str]) -> None:
    del secret_values[MQTT_HOST_SECRET]

    with pytest.raises(SecretsError, match=MQTT_HOST_SECRET):
        NotificationRepository()


def test_on_connect_success_resubscribes(repository: NotificationRepository, monkeypatch: pytest.MonkeyPatch) -> None:
    subscribed: list[Any] = []

    def fake_subscribe(topic: Any) -> tuple[MQTTErrorCode, int]:
        subscribed.append(topic)
        return MQTTErrorCode.MQTT_ERR_SUCCESS, 1

    monkeypatch.setattr(repository._client, "subscribe", fake_subscribe)
    repository._subscriptions = [("notifications", 1)]

    repository._on_connect(repository._client, None, mqtt.ConnectFlags(False), _connack(0), None)

    assert repository._is_connected is True
    assert subscribed == [("notifications", 1)]


def test_on_connect_refused_logs_the_reason(
    repository: NotificationRepository, log_lines: list[tuple[str, str]]
) -> None:
    repository._on_connect(repository._client, None, mqtt.ConnectFlags(False), _connack(135), None)

    assert repository._is_connected is False
    assert log_lines[-1][0] == "error"
    assert "Not authorized" in log_lines[-1][1]


def test_on_disconnect_unexpected_logs_warning(
    repository: NotificationRepository, log_lines: list[tuple[str, str]]
) -> None:
    repository._is_connected = True
    reason = ReasonCode(PacketTypes.DISCONNECT, identifier=128)

    repository._on_disconnect(repository._client, None, mqtt.DisconnectFlags(False), reason, None)

    assert repository._is_connected is False
    assert log_lines[-1][0] == "warning"


def test_on_disconnect_clean_logs_info(repository: NotificationRepository, log_lines: list[tuple[str, str]]) -> None:
    reason = ReasonCode(PacketTypes.DISCONNECT, identifier=0)

    repository._on_disconnect(repository._client, None, mqtt.DisconnectFlags(False), reason, None)

    assert log_lines[-1] == ("info", "Disconnected from MQTT broker")


def test_post_message_serializes_dict_payload(
    repository: NotificationRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    published: list[dict[str, Any]] = []

    def fake_publish(topic: str, **kwargs: Any) -> _PublishResult:
        published.append({"topic": topic, **kwargs})
        return _PublishResult(mqtt.MQTT_ERR_SUCCESS)

    monkeypatch.setattr(repository._client, "publish", fake_publish)

    repository.post_message({"batch_id": "b-1", "items": []})

    assert published[0]["topic"] == "notifications"
    assert json.loads(published[0]["payload"]) == {"batch_id": "b-1", "items": []}
    assert published[0]["qos"] == 1


def test_post_message_before_connack_is_queued_not_failed(
    repository: NotificationRepository, monkeypatch: pytest.MonkeyPatch, log_lines: list[tuple[str, str]]
) -> None:
    monkeypatch.setattr(repository._client, "publish", lambda topic, **kwargs: _PublishResult(mqtt.MQTT_ERR_NO_CONN))

    repository.post_message("report")

    assert all(level != "error" for level, _ in log_lines)
    assert any("Queued" in text for _, text in log_lines)


def test_post_message_rejected_logs_error(
    repository: NotificationRepository, monkeypatch: pytest.MonkeyPatch, log_lines: list[tuple[str, str]]
) -> None:
    monkeypatch.setattr(repository._client, "publish", lambda topic, **kwargs: _PublishResult(mqtt.MQTT_ERR_QUEUE_SIZE))

    repository.post_message("report")

    assert log_lines[-1][0] == "error"


def test_post_message_unsupported_type_raises(repository: NotificationRepository) -> None:
    with pytest.raises(TypeError):
        repository.post_message(42)  # type: ignore[arg-type]


def test_on_message_passes_bytes_to_handler(repository: NotificationRepository) -> None:
    received: list[tuple[str, bytes]] = []
    repository._message_handler = lambda topic, payload: received.append((topic, payload))
    message = mqtt.MQTTMessage(topic=b"notifications")
    message.payload = bytearray(b'{"batch_id": "b-1"}')

    repository._on_message(repository._client, None, message)

    assert received == [("notifications", b'{"batch_id": "b-1"}')]
    assert isinstance(received[0][1], bytes)


@pytest.mark.parametrize("client_connected", [True, False])
def test_is_connected_reflects_the_client(
    repository: NotificationRepository, monkeypatch: pytest.MonkeyPatch, client_connected: bool
) -> None:
    monkeypatch.setattr(repository._client, "is_connected", lambda: client_connected)

    assert repository.is_connected is client_connected
