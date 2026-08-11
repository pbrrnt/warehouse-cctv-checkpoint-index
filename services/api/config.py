"""อ่านค่าตั้งค่าจาก environment variable — ดู .env.example ส่วน 'Services ของเรา'"""

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
    site_name: str
    database_url: str
    api_secret_key: str
    timezone: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            site_id=_require("SITE_ID"),
            site_name=os.environ.get("SITE_NAME", ""),
            database_url=_require("DATABASE_URL"),
            api_secret_key=_require("API_SECRET_KEY"),
            timezone=os.environ.get("TZ", "Asia/Bangkok"),
        )
