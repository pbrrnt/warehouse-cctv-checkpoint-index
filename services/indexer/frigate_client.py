"""ไคลเอนต์ HTTP บาง ๆ คุยกับ Frigate API — ใช้แค่สิ่งที่ indexer ต้องการ"""

from __future__ import annotations

import requests


class FrigateClientError(RuntimeError):
    pass


class FrigateClient:
    def __init__(self, base_url: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def get_snapshot(self, event_id: str) -> bytes:
        """GET /api/events/<id>/snapshot.jpg — ดู docs/01-architecture.md หัวข้อ 6.1"""
        url = f"{self.base_url}/api/events/{event_id}/snapshot.jpg"
        try:
            resp = requests.get(url, timeout=self.timeout)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise FrigateClientError(f"ดึง snapshot ของ event {event_id} ไม่สำเร็จ: {exc}") from exc
        return resp.content
