"""
ตั้งค่า logging ให้เขียนทั้ง stdout (Docker เก็บผ่าน json-file driver — ดู
docker-compose.yml) และไฟล์ log ที่อยู่ทนแยกจากอายุ container (RotatingFileHandler
mount ผ่าน volume ${DATA_ROOT}/logs) — ★ ก่อนหน้านี้มีแค่ stdout อย่างเดียว
ถ้า `docker compose down` แล้ว `up` ใหม่ log เก่าหายหมดทันที (container
ใหม่ = json-file log ใหม่) ต่างจาก DB/media ที่ mount เป็น volume ถาวรอยู่แล้ว
— ไฟล์ในนี้แก้ช่องโหว่นั้น ให้เข้าไปดู log ย้อนหลังได้แม้ container เพิ่ง
สร้างใหม่ (เช่น หลัง redeploy หรือ crash-restart)

ใช้ตอน debug จริง: `tail -f ${DATA_ROOT}/logs/indexer.log` หรือ
`docker compose exec indexer tail -f /logs/indexer.log`
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"

# ★ ไฟล์ log ต่อ service เก็บ 5 ไฟล์ x 10MB = 50MB สูงสุด (มากกว่า Docker
# json-file ตั้งไว้ 10MB x 2-3 ไฟล์ในตอนนี้ เพราะนี่คือสำเนาที่ตั้งใจให้
# อยู่ทนกว่า ไว้สืบย้อนหลังได้นานกว่า)
MAX_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 5


def setup_logging(service_name: str, level: int = logging.INFO) -> None:
    """
    เรียกครั้งเดียวตอนเริ่ม process (ดู services/indexer/main.py,
    services/api/main.py) — LOG_DIR ไม่ตั้งไว้ (ไม่มี env var หรือ path
    เขียนไม่ได้) จะข้าม file handler เงียบ ๆ เหลือแค่ stdout ปกติ ไม่ทำให้
    service พังเพราะ logging ตั้งค่าไม่ได้
    """
    root = logging.getLogger()
    root.setLevel(level)

    formatter = logging.Formatter(LOG_FORMAT)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    log_dir = os.environ.get("LOG_DIR")
    if not log_dir:
        return

    try:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            Path(log_dir) / f"{service_name}.log",
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError:
        # ★ เขียนไฟล์ไม่ได้ (permission, disk เต็ม ฯลฯ) ไม่ควรทำให้ service
        # เริ่มไม่ได้เพราะเรื่อง logging — เหลือ stdout handler ไว้พอ
        root.warning("ตั้งค่า log file ที่ %s ไม่สำเร็จ — เหลือแค่ stdout", log_dir)
