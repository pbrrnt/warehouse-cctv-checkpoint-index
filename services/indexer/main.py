"""
Entry point ของ indexer — เฟส 2: MQTT → Redis Stream → Postgres (ยังไม่มี AI)

รันเป็น 1 process เดียว มี 2 ส่วนทำงานพร้อมกัน:
  1. MqttBridge   — thread หลัก รับจาก MQTT ส่งเข้า Redis Stream
  2. worker thread × INDEXER_WORKERS — อ่านจาก Redis Stream เขียนลง Postgres

★ ยังไม่เคยรันจริง (ไม่มี Docker/Postgres/MQTT broker บนเครื่องพัฒนา) —
ตรวจสอบได้แค่ระดับ import/wiring ไม่ใช่พฤติกรรมจริงตอนรัน ต้องทดสอบตอน
`docker compose --profile app up -d` บนเครื่องเป้าหมายจริง
"""

from __future__ import annotations

import logging
import threading

import redis
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from services.common.config import Settings
from services.common.storage import LocalFileStore
from services.indexer.frigate_client import FrigateClient
from services.indexer.mqtt_bridge import MqttBridge
from services.indexer.stream_consumer import run_worker_loop
from services.indexer.worker import EventWorker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    settings = Settings.from_env()
    logger.info("indexer เริ่มทำงาน site_id=%s workers=%s", settings.site_id, settings.indexer_workers)

    engine = create_engine(settings.database_url, pool_pre_ping=True)
    session_factory = sessionmaker(bind=engine)

    redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=False)
    frigate_client = FrigateClient(settings.frigate_url)
    store = LocalFileStore(settings.media_path)

    event_worker = EventWorker(session_factory, frigate_client, store, settings.site_id)

    stop_event = threading.Event()
    worker_threads = [
        threading.Thread(
            target=run_worker_loop,
            args=(redis_client, event_worker, f"worker-{i}"),
            kwargs={"stop_event": stop_event},
            daemon=True,
            name=f"indexer-worker-{i}",
        )
        for i in range(settings.indexer_workers)
    ]
    for t in worker_threads:
        t.start()

    bridge = MqttBridge(settings.mqtt_host, settings.mqtt_port, redis_client)
    try:
        bridge.run_forever()  # บล็อกอยู่ตรงนี้จนกว่าจะ Ctrl+C หรือ container หยุด
    except KeyboardInterrupt:
        logger.info("ได้รับสัญญาณหยุด กำลังปิด indexer")
    finally:
        stop_event.set()
        bridge.stop()
        for t in worker_threads:
            t.join(timeout=5)


if __name__ == "__main__":
    main()
