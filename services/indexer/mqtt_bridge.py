"""
รับข้อความจาก MQTT topic frigate/events แล้วส่งต่อเข้า Redis Stream

ทำไมต้องมีชั้นกันชนนี้: ถ้า worker ยังไม่ว่างหรือ Postgres ช้า ข้อความยังไม่
หาย เพราะ Redis Stream เก็บคิวค้างไว้ให้ (ต่างจากการประมวลผลตรงในตัว MQTT
callback ที่ถ้าช้าจะบล็อกการรับข้อความถัดไป และถ้า process ตาย ข้อความที่
กำลังทำอยู่จะหายไปเลยไม่มีทางกู้)
"""

from __future__ import annotations

import logging

import paho.mqtt.client as mqtt
import redis

logger = logging.getLogger(__name__)

STREAM_NAME = "frigate-events"
MQTT_TOPIC = "frigate/events"


class MqttBridge:
    def __init__(
        self,
        mqtt_host: str,
        mqtt_port: int,
        redis_client: redis.Redis,
        stream_name: str = STREAM_NAME,
        topic: str = MQTT_TOPIC,
    ):
        self.mqtt_host = mqtt_host
        self.mqtt_port = mqtt_port
        self.redis = redis_client
        self.stream_name = stream_name
        self.topic = topic

        self._client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            logger.info("ต่อ MQTT สำเร็จ (%s:%s) — subscribe %s", self.mqtt_host, self.mqtt_port, self.topic)
            client.subscribe(self.topic)
        else:
            logger.error("ต่อ MQTT ไม่สำเร็จ: %s", reason_code)

    def _on_message(self, client, userdata, msg):
        self.handle_payload(msg.payload)

    def handle_payload(self, raw_payload: bytes) -> None:
        """
        แยกออกมาจาก MQTT callback โดยตั้งใจ เพื่อทดสอบ logic การส่งเข้า
        Redis Stream ได้โดยไม่ต้องมี MQTT broker จริง (ดู
        tests/test_mqtt_bridge.py)
        """
        try:
            self.redis.xadd(self.stream_name, {"payload": raw_payload})
        except redis.RedisError:
            logger.exception("ส่งข้อความเข้า Redis Stream ไม่สำเร็จ — ข้อความนี้จะหายไป (ยังไม่ retry)")

    def run_forever(self) -> None:
        self._client.connect(self.mqtt_host, self.mqtt_port)
        self._client.loop_forever()

    def stop(self) -> None:
        self._client.disconnect()
