"""
Alembic environment — อ่าน DATABASE_URL จาก environment variable เสมอ
(ตัวเดียวกับที่ docker-compose.yml ส่งให้ service อื่น) ไม่ฮาร์ดโค้ดรหัสผ่าน
ไว้ในไฟล์ config ที่อาจหลุดเข้า git
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# ให้ import services.common.db.models ได้ตอนรันจากโฟลเดอร์ db/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from services.common.db.models import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "ไม่พบ DATABASE_URL — ตั้งค่าตัวแปรสภาพแวดล้อมก่อนรัน alembic เช่น\n"
            '  export DATABASE_URL="postgresql+psycopg://user:pass@localhost:5432/cctv_index"'
        )
    return url


def run_migrations_offline() -> None:
    """generate SQL script โดยไม่ต้องต่อฐานข้อมูลจริง (alembic upgrade --sql)"""
    context.configure(
        url=get_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """รัน migration จริงกับฐานข้อมูลที่ต่ออยู่"""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_database_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
