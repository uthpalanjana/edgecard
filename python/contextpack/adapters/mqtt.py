"""
adapters/mqtt.py — MQTTAdapter with persistent connection and in-memory store.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from ..card import Quality, Reading
from ..sources import ConnectionResult, DataSource

logger = logging.getLogger(__name__)


@dataclass
class MQTTTLSConfig:
    ca_cert_path: str
    client_cert_path: Optional[str] = None
    client_key_path: Optional[str] = None


@dataclass
class MQTTTopicMapping:
    topic: str
    field: str
    unit: Optional[str] = None
    json_path: Optional[str] = None  # dot-notation into the payload JSON


class MQTTAdapter(DataSource):
    """
    Maintains a persistent MQTT connection and stores latest value per topic.
    poll() reads from in-memory store — does NOT issue new network requests.
    """

    def __init__(
        self,
        name: str,
        broker_host: str,
        broker_port: int = 1883,
        topics: list[MQTTTopicMapping] | None = None,
        client_id: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        keepalive_seconds: int = 60,
        reconnect_delay_seconds: int = 5,
        tls: Optional[MQTTTLSConfig] = None,
        poll_interval_seconds: int = 30,
    ) -> None:
        super().__init__(name=name, poll_interval_seconds=poll_interval_seconds)
        self.broker_host = broker_host
        self.broker_port = broker_port
        self.topics = topics or []
        self.client_id = client_id
        self.username = username
        self.password = password
        self.keepalive_seconds = keepalive_seconds
        self.reconnect_delay_seconds = reconnect_delay_seconds
        self.tls = tls

        self._store: dict[str, dict] = {}  # topic -> {value, unit, timestamp}
        self._lock = threading.Lock()
        self._client = None
        self._running = False
        self._connect_thread: Optional[threading.Thread] = None

        self._topic_map: dict[str, MQTTTopicMapping] = {m.topic: m for m in self.topics}

    def _get_paho(self):
        try:
            import paho.mqtt.client as mqtt
            return mqtt
        except ImportError:
            return None

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info("MQTTAdapter '%s': connected to %s:%d", self.name, self.broker_host, self.broker_port)
            for mapping in self.topics:
                client.subscribe(mapping.topic, qos=1)
        else:
            logger.warning("MQTTAdapter '%s': connection failed, rc=%d", self.name, rc)

    def _on_message(self, client, userdata, msg):
        try:
            payload = msg.payload.decode("utf-8", errors="replace")
            mapping = self._topic_map.get(msg.topic)
            if mapping is None:
                return

            if mapping.json_path:
                try:
                    data = json.loads(payload)
                    parts = mapping.json_path.split(".")
                    for p in parts:
                        data = data[p]
                    value = data
                except Exception:
                    value = payload
            else:
                # Try JSON parse for single values
                try:
                    value = json.loads(payload)
                except json.JSONDecodeError:
                    value = payload

            with self._lock:
                self._store[msg.topic] = {
                    "value": value,
                    "unit": mapping.unit,
                    "timestamp": datetime.now(timezone.utc),
                    "field": mapping.field,
                }
        except Exception as exc:
            logger.error("MQTTAdapter '%s': on_message error: %s", self.name, exc)

    def _on_disconnect(self, client, userdata, rc):
        if rc != 0 and self._running:
            logger.warning(
                "MQTTAdapter '%s': unexpected disconnect (rc=%d), reconnecting in %ds",
                self.name, rc, self.reconnect_delay_seconds,
            )

    def start(self) -> None:
        """Start the persistent MQTT connection in a background thread."""
        mqtt = self._get_paho()
        if mqtt is None:
            logger.error("MQTTAdapter '%s': paho-mqtt not installed", self.name)
            return

        self._running = True
        self._connect_thread = threading.Thread(target=self._run_loop, daemon=True, name=f"mqtt-{self.name}")
        self._connect_thread.start()

    def _run_loop(self) -> None:
        mqtt = self._get_paho()
        if mqtt is None:
            return

        delay = self.reconnect_delay_seconds
        while self._running:
            try:
                client = mqtt.Client(client_id=self.client_id or "")
                if self.username:
                    client.username_pw_set(self.username, self.password)
                if self.tls:
                    client.tls_set(
                        ca_certs=self.tls.ca_cert_path,
                        certfile=self.tls.client_cert_path,
                        keyfile=self.tls.client_key_path,
                    )
                client.on_connect = self._on_connect
                client.on_message = self._on_message
                client.on_disconnect = self._on_disconnect
                client.connect(self.broker_host, self.broker_port, keepalive=self.keepalive_seconds)
                self._client = client
                client.loop_forever()
            except Exception as exc:
                logger.error("MQTTAdapter '%s': connection error: %s", self.name, exc)
            if self._running:
                time.sleep(delay)
                delay = min(delay * 2, 60)  # exponential backoff up to 60s

    def stop(self) -> None:
        """Stop the MQTT connection."""
        self._running = False
        if self._client:
            try:
                self._client.disconnect()
            except Exception:
                pass

    def poll(self) -> dict[str, Reading]:
        """Read from in-memory store. Returns stale for fields with no message received."""
        results: dict[str, Reading] = {}
        now = datetime.now(timezone.utc)
        with self._lock:
            store_copy = dict(self._store)

        for mapping in self.topics:
            stored = store_copy.get(mapping.topic)
            if stored is not None:
                results[mapping.field] = Reading(
                    value=stored["value"],
                    unit=stored.get("unit"),
                    quality=Quality.measured,
                    timestamp=stored["timestamp"],
                )
            else:
                results[mapping.field] = Reading(
                    value=0,
                    unit=mapping.unit,
                    quality=Quality.stale,
                    timestamp=now,
                )
        return results

    def test_connection(self) -> ConnectionResult:
        """Connect, subscribe to first topic, wait 5s, disconnect."""
        mqtt = self._get_paho()
        if mqtt is None:
            return ConnectionResult(success=False, message="paho-mqtt not installed")

        if not self.topics:
            return ConnectionResult(success=False, message="No topics configured")

        result = {"success": False, "message": "Timed out waiting for connection"}
        event = threading.Event()

        def on_connect(client, userdata, flags, rc):
            if rc == 0:
                result["success"] = True
                result["message"] = f"Connected to {self.broker_host}:{self.broker_port}"
            else:
                result["success"] = False
                result["message"] = f"Connection refused, rc={rc}"
            event.set()

        try:
            client = mqtt.Client(client_id=(self.client_id or "") + "-test")
            if self.username:
                client.username_pw_set(self.username, self.password)
            if self.tls:
                client.tls_set(
                    ca_certs=self.tls.ca_cert_path,
                    certfile=self.tls.client_cert_path,
                    keyfile=self.tls.client_key_path,
                )
            client.on_connect = on_connect
            client.connect_async(self.broker_host, self.broker_port, keepalive=30)
            client.loop_start()
            event.wait(timeout=5.0)
            client.loop_stop()
            client.disconnect()
        except Exception as exc:
            return ConnectionResult(success=False, message=str(exc))

        return ConnectionResult(success=result["success"], message=result["message"])
