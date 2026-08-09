"""
อ่านค่าตั้งค่าจาก environment variable — ตัวเดียวกับที่ docker-compose.yml
ส่งให้แต่ละ service (ดู .env.example) ใช้ร่วมกันทั้ง indexer และ api
"""

from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    pass


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"ไม่พบ environment variable {name} — ตรวจ .env หรือ docker-compose.yml")
    return value


@dataclass(frozen=True)
class Settings:
    site_id: str
    database_url: str
    redis_url: str
    mqtt_host: str
    mqtt_port: int
    frigate_url: str
    media_path: str
    indexer_workers: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            site_id=_require("SITE_ID"),
            database_url=_require("DATABASE_URL"),
            redis_url=os.environ.get("REDIS_URL", "redis://redis:6379/0"),
            mqtt_host=os.environ.get("MQTT_HOST", "mosquitto"),
            mqtt_port=int(os.environ.get("MQTT_PORT", "1883")),
            frigate_url=os.environ.get("FRIGATE_URL", "http://frigate:5000"),
            media_path=os.environ.get("MEDIA_PATH", "/media"),
            indexer_workers=int(os.environ.get("INDEXER_WORKERS", "2")),
        )
