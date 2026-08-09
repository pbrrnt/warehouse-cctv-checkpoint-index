"""
อ่านงานจาก Redis Stream ผ่าน consumer group แล้วส่งให้ EventWorker ประมวลผล

ack เฉพาะตอนสำเร็จ (หรือข้อความที่ตั้งใจข้าม เช่น type != "end") — ถ้า
ประมวลผลพัง ไม่ ack ปล่อยให้ค้างใน pending list ของ consumer group เพื่อ
ให้ลองใหม่ได้ทีหลัง (Redis Streams รองรับ at-least-once delivery แบบนี้อยู่แล้ว)
"""

from __future__ import annotations

import logging

import redis

from services.indexer.frigate_events import parse_frigate_event
from services.indexer.mqtt_bridge import STREAM_NAME
from services.indexer.worker import EventWorker

logger = logging.getLogger(__name__)

CONSUMER_GROUP = "indexer-workers"


def ensure_consumer_group(redis_client: redis.Redis, stream: str, group: str) -> None:
    try:
        redis_client.xgroup_create(stream, group, id="0", mkstream=True)
    except redis.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def process_one_message(
    redis_client: redis.Redis,
    stream: str,
    group: str,
    message_id: bytes,
    fields: dict,
    event_worker: EventWorker,
) -> None:
    """ประมวลผล 1 ข้อความ แยกออกมาให้ทดสอบได้อิสระจาก loop จริง"""
    raw_payload = fields.get(b"payload") or fields.get("payload")
    try:
        parsed = parse_frigate_event(raw_payload)
        if parsed is not None:
            event_worker.process(parsed)
        redis_client.xack(stream, group, message_id)
    except Exception:
        logger.exception(
            "ประมวลผลข้อความล้มเหลว (message_id=%s) — ไม่ ack จะลองใหม่ภายหลัง",
            message_id,
        )


def run_worker_loop(
    redis_client: redis.Redis,
    event_worker: EventWorker,
    consumer_name: str,
    stream: str = STREAM_NAME,
    group: str = CONSUMER_GROUP,
    stop_event=None,
    block_ms: int = 5000,
) -> None:
    ensure_consumer_group(redis_client, stream, group)
    logger.info("worker %s เริ่มทำงาน (stream=%s group=%s)", consumer_name, stream, group)

    while stop_event is None or not stop_event.is_set():
        response = redis_client.xreadgroup(
            group, consumer_name, {stream: ">"}, count=1, block=block_ms
        )
        if not response:
            continue

        for _stream_name, messages in response:
            for message_id, fields in messages:
                process_one_message(redis_client, stream, group, message_id, fields, event_worker)
