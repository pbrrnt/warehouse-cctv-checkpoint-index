"""ไคลเอนต์ HTTP บาง ๆ คุยกับ Frigate API — ใช้แค่สิ่งที่ indexer ต้องการ"""

from __future__ import annotations

import requests


class FrigateClientError(RuntimeError):
    pass


class FrigateClient:
    def __init__(self, base_url: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def get_snapshot(self, event_id: str, crop: bool = False) -> bytes:
        """
        GET /api/events/<id>/snapshot.jpg — ดู docs/01-architecture.md หัวข้อ 6.1

        crop=True: ขอภาพครอปเฉพาะกรอบวัตถุ (query param ?crop=1 ของ Frigate)
        ใช้ตอนป้อนเข้าโมเดล AI (services/common/analytics/vehicle.py) ที่ต้อง
        การภาพครอปแน่น ๆ ไม่ใช่ทั้งเฟรม — ต่างจาก thumb_key (เก็บทั้งเฟรมไว้
        ให้พนักงานดูบริบทตอนค้นหา)
        """
        url = f"{self.base_url}/api/events/{event_id}/snapshot.jpg"
        params = {"crop": 1} if crop else None
        try:
            resp = requests.get(url, params=params, timeout=self.timeout)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise FrigateClientError(f"ดึง snapshot ของ event {event_id} ไม่สำเร็จ: {exc}") from exc
        return resp.content
