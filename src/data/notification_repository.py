import json
import os
from typing import Callable, Optional, Sequence, Tuple, Union
import paho.mqtt.client as mqtt
from raccoontools.shared.serializer import obj_dump_serializer

from src.data.activity_logger import ActivityTracker
from src.utils import to_int

PayloadType = Union[str, bytes, dict]


class NotificationRepository:
    """
    Lightweight MQTT repository to publish and consume messages.

    Public API:
    - post_message: publish a message to a topic (acts as "queue").
    - start_reading: connect and start receiving messages, invoking a user callback.
    """

    def __init__(
        self,
        broker_host: Optional[str] = None,
        broker_port: Optional[int] = None,
        base_topic: Optional[str] = None,
        client_id: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        tls_ca_cert: Optional[str] = None,
        keepalive_seconds: Optional[int] = None,
        log_level: Optional[str] = None,
    ) -> None:
        resolved_log_level = (log_level or os.getenv("MQTT_LOG_LEVEL") or "DEBUG").upper()
        self._logger = ActivityTracker("Notification Repository", resolved_log_level)

        host = broker_host or os.getenv("MQTT_HOST")
        if not host:
            raise ValueError("MQTT broker host not configured. Provide 'broker_host' or set environment variable 'MQTT_HOST'.")

        port = broker_port if broker_port is not None else to_int(os.getenv("MQTT_PORT"), 1883)
        topic = (base_topic or os.getenv("MQTT_BASE_TOPIC") or "notifications").strip()
        client_id_final = client_id or os.getenv("MQTT_CLIENT_ID")
        username_final = username or os.getenv("MQTT_USERNAME")
        password_final = password or os.getenv("MQTT_PASSWORD")
        tls_ca_cert_final = tls_ca_cert or os.getenv("MQTT_TLS_CA_CERT")
        keepalive_final = keepalive_seconds if keepalive_seconds is not None else to_int(os.getenv("MQTT_KEEPALIVE_SECONDS"), 60)

        self._broker_host = host
        self._broker_port = port
        self._base_topic = topic
        self._keepalive_seconds = keepalive_final

        self._client = mqtt.Client(client_id=client_id_final, protocol=mqtt.MQTTv311)
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)

        if username_final is not None or password_final is not None:
            self._client.username_pw_set(username_final, password_final)

        if tls_ca_cert_final:
            self._client.tls_set(ca_certs=tls_ca_cert_final)

        self._is_connected: bool = False
        self._subscriptions: Sequence[Tuple[str, int]] = []
        self._message_handler: Optional[Callable[[str, bytes], None]] = None

        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

    def post_message(
        self,
        message: PayloadType,
        topic: Optional[str] = None,
        qos: int = 1,
        retain: bool = False,
    ) -> None:
        target_topic = (topic or self._base_topic).strip()
        if not target_topic:
            raise ValueError("Topic must be provided either via constructor base_topic or the 'topic' argument")

        if isinstance(message, dict):
            payload: Union[str, bytes] = json.dumps(message, default=obj_dump_serializer)
        elif isinstance(message, (str, bytes)):
            payload = message
        else:
            raise TypeError("message must be str, bytes, or dict")

        if not self._is_connected:
            self._connect_if_needed()
            self._ensure_background_loop()

        result = self._client.publish(target_topic, payload=payload, qos=qos, retain=retain)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            self._logger.error(f"Failed to publish to '{target_topic}': rc={result.rc}")
        else:
            self._logger.debug(f"Published to '{target_topic}' (qos={qos}, retain={retain})")

    def start_reading(
        self,
        topics: Optional[Union[str, Sequence[str]]] = None,
        message_handler: Optional[Callable[[str, bytes], None]] = None,
        qos: int = 1,
        background: bool = True,
    ) -> None:
        """
        Connect to the broker and begin receiving messages.

        - topics: one or many topics to subscribe to. Defaults to the base topic
        - message_handler: optional callback invoked as callback(topic: str, payload: bytes)
        - qos: subscription QoS for all topics (0, 1, 2)
        - background: if True, starts a background network loop; otherwise blocks
        """
        subscribe_topics: Sequence[str]
        if topics is None:
            subscribe_topics = [self._base_topic]
        elif isinstance(topics, str):
            subscribe_topics = [topics]
        else:
            subscribe_topics = list(topics)

        self._subscriptions = [(t.strip(), qos) for t in subscribe_topics if t and t.strip()]
        if not self._subscriptions:
            raise ValueError("At least one valid topic must be provided to start_reading")

        self._message_handler = message_handler

        self._connect_if_needed()

        # Subscribe immediately if already connected; on_connect will re-subscribe after reconnects
        for (topic_name, topic_qos) in self._subscriptions:
            res = self._client.subscribe(topic=(topic_name, topic_qos))
            if isinstance(res, tuple):
                rc = res[0]
            else:
                rc = res.rc
            if rc != mqtt.MQTT_ERR_SUCCESS:
                self._logger.error(f"Failed to subscribe to '{topic_name}': rc={rc}")
            else:
                self._logger.debug(f"Subscribed to '{topic_name}' (qos={topic_qos})")

        if background:
            self._ensure_background_loop()
        else:
            # Ensure no background loop is running before blocking
            self._stop_background_loop_if_running()
            # This will block until disconnect
            self._client.loop_forever(retry_first_connection=True)

    def _connect_if_needed(self) -> None:
        if self._is_connected:
            return
        try:
            self._logger.debug(
                f"Connecting to MQTT broker {self._broker_host}:{self._broker_port} (keepalive={self._keepalive_seconds})"
            )
            self._client.connect(self._broker_host, self._broker_port, keepalive=self._keepalive_seconds)
        except Exception as exc:  # pragma: no cover - defensive logging
            self._logger.error(f"Error connecting to MQTT broker: {exc}")
            raise

    def _ensure_background_loop(self) -> None:
        if getattr(self, "_loop_running", False):
            return
        self._client.loop_start()
        self._loop_running = True

    def _stop_background_loop_if_running(self) -> None:
        if getattr(self, "_loop_running", False):
            self._client.loop_stop()
            self._loop_running = False

    # Paho v3.1.1 callback signatures
    def _on_connect(self, client: mqtt.Client, userdata, flags, rc: int) -> None:
        self._is_connected = (rc == 0)
        if rc == 0:
            self._logger.info("Connected to MQTT broker")
            # Ensure subscriptions are applied on (re)connect
            for (topic_name, topic_qos) in self._subscriptions:
                result = self._client.subscribe(topic=(topic_name, topic_qos))
                if isinstance(result, tuple):
                    sub_rc = result[0]
                else:
                    sub_rc = result.rc
                if sub_rc != mqtt.MQTT_ERR_SUCCESS:
                    self._logger.error(f"Re-subscribe failed for '{topic_name}': rc={sub_rc}")
        else:
            self._logger.error(f"Failed to connect to MQTT broker: rc={rc}")

    def _on_disconnect(self, client: mqtt.Client, userdata, rc: int) -> None:
        self._is_connected = False
        if rc != 0:
            self._logger.warning(f"Unexpected MQTT disconnection: rc={rc}")
        else:
            self._logger.info("Disconnected from MQTT broker")

    def _on_message(self, client: mqtt.Client, userdata, msg: mqtt.MQTTMessage) -> None:
        try:
            payload_bytes: bytes = msg.payload if isinstance(msg.payload, (bytes, bytearray)) else bytes(str(msg.payload), "utf-8")
            if self._message_handler:
                self._message_handler(msg.topic, payload_bytes)
            else:
                # Default behavior: log the message
                preview = payload_bytes[:256]
                self._logger.info(f"Message on '{msg.topic}': {preview}")
        except Exception as exc:  # pragma: no cover - defensive logging
            self._logger.error(f"Error handling incoming MQTT message: {exc}")


