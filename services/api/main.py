"""
Entry point ของ search API — อ่าน env, ต่อ Postgres, รัน uvicorn

★ ยังไม่เคยรันจริง (ไม่มี Docker/Postgres บนเครื่องพัฒนา) — ตรวจสอบได้แค่
ระดับ import/wiring ไม่ใช่พฤติกรรมจริงตอนรัน ต้องทดสอบตอน
`docker compose --profile app up -d` บนเครื่องเป้าหมายจริง (ดู docs/11-testing.md)
"""

from __future__ import annotations

import logging

import uvicorn
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from services.api.app import create_app
from services.api.config import Settings
from services.common.logging_config import setup_logging

setup_logging("api")
logger = logging.getLogger(__name__)


def main() -> None:
    settings = Settings.from_env()
    logger.info("search API เริ่มทำงาน site_id=%s", settings.site_id)

    engine = create_engine(settings.database_url, pool_pre_ping=True)
    session_factory = sessionmaker(bind=engine)

    app = create_app(settings, session_factory)
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
