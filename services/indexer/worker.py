"""
ประมวลผลงานจาก Redis Stream ทีละชิ้น — เฟส 2 (ไม่มี AI):
  1. เอา ParsedEvent + ข้อมูลกล้องมาสร้างแถว events
  2. ดึง snapshot จาก Frigate แล้วเก็บผ่าน ContentStore
  3. upsert แถว events ลง Postgres (idempotent ตาม frigate event id)

แยกฟังก์ชัน "สร้างข้อมูลที่จะเขียน" ออกจาก "เขียนจริงลง DB" เพื่อทดสอบ
ส่วนแรกได้โดยไม่ต้องมี Postgres จริง — ดู tests/test_indexer_worker.py
"""

from __future__ import annotations

import logging

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from services.common.db.models import Camera, Event
from services.common.storage import ContentStore
from services.indexer.frigate_client import FrigateClient, FrigateClientError
from services.indexer.frigate_events import ParsedEvent, compute_direction, frigate_camera_to_db_id

logger = logging.getLogger(__name__)


def build_event_values(
    parsed: ParsedEvent,
    site_id: str,
    camera_id: str,
    direction: str | None,
    thumb_key: str | None,
) -> dict:
    """
    ประกอบ dict ของค่าที่จะ insert/update ในตาราง events — แยกออกมาเป็น
    pure function ตั้งใจ ทดสอบได้เต็มที่โดยไม่ต้องพึ่ง Postgres จริง
    """
    return {
        "id": parsed.frigate_id,
        "site_id": site_id,
        "camera_id": camera_id,
        "label": parsed.label,
        "sub_label": parsed.sub_label,
        "score": parsed.score,
        "start_ts": parsed.start_ts,
        "end_ts": parsed.end_ts,
        "direction": direction,
        "zones": parsed.entered_zones or None,
        "has_clip": parsed.has_clip,
        "thumb_key": thumb_key,
        "raw": parsed.raw,
    }


def snapshot_key(site_id: str, event_id: str) -> str:
    return f"{site_id}/events/{event_id}/snapshot.jpg"


def upsert_event(session: Session, values: dict) -> None:
    """
    INSERT ... ON CONFLICT (id) DO UPDATE — ★ ทำให้ idempotent ตามที่ระบุใน
    docs/04-data-model.md หัวข้อ 6: "ใช้ frigate event id เป็น PK ของ events
    ทำให้ idempotent — รับ event ซ้ำแล้วไม่เกิดแถวซ้ำ"

    รับ event ซ้ำเกิดขึ้นได้จริงเพราะ Redis Stream อาจส่งข้อความซ้ำเมื่อ
    worker ตายกลางทางก่อน XACK (at-least-once delivery)
    """
    stmt = pg_insert(Event).values(**values)
    update_cols = {col: stmt.excluded[col] for col in values if col != "id"}
    stmt = stmt.on_conflict_do_update(index_elements=["id"], set_=update_cols)
    session.execute(stmt)
    session.commit()


class EventWorker:
    def __init__(
        self,
        session_factory,
        frigate_client: FrigateClient,
        store: ContentStore,
        site_id: str,
    ):
        self.session_factory = session_factory
        self.frigate_client = frigate_client
        self.store = store
        self.site_id = site_id

    def process(self, parsed: ParsedEvent) -> None:
        camera_id = frigate_camera_to_db_id(self.site_id, parsed.frigate_camera)

        with self.session_factory() as session:
            camera = session.get(Camera, camera_id)
            if camera is None:
                # ★ ยังไม่รู้จักกล้องนี้ในตาราง cameras — น่าจะยังไม่ได้ onboard
                # ตาม docs/03-camera-onboarding.md ข้ามงานนี้ไปแทนที่จะพัง
                # ทั้ง worker (กล้องอื่นควรทำงานต่อได้ตามปกติ)
                logger.error(
                    "ไม่พบกล้อง %s ในตาราง cameras (event %s) — ข้ามการบันทึก "
                    "ตรวจว่า onboard กล้องนี้แล้วหรือยัง (03-camera-onboarding.md)",
                    camera_id, parsed.frigate_id,
                )
                return

            zone_config = camera.zone_config or {}
            direction = compute_direction(
                parsed.entered_zones,
                zone_config.get("outside_zone"),
                zone_config.get("inside_zone"),
            )

            thumb_key = None
            try:
                snapshot = self.frigate_client.get_snapshot(parsed.frigate_id)
                thumb_key = snapshot_key(self.site_id, parsed.frigate_id)
                self.store.save(thumb_key, snapshot)
            except FrigateClientError:
                # ★ ดึง snapshot ไม่ได้ (เช่น Frigate เพิ่งรีสตาร์ต, event ถูก
                # ลบไปแล้วตาม retention) ไม่ควรทำให้ metadata หายไปด้วย —
                # บันทึก events แถวนี้ต่อโดยไม่มี thumb_key แทนที่จะข้ามทั้งงาน
                logger.warning(
                    "ดึง snapshot ไม่สำเร็จสำหรับ event %s — บันทึก metadata "
                    "ต่อโดยไม่มี thumbnail", parsed.frigate_id,
                )

            values = build_event_values(parsed, self.site_id, camera_id, direction, thumb_key)
            upsert_event(session, values)

        logger.info("บันทึก event %s (%s, %s) สำเร็จ", parsed.frigate_id, camera_id, direction or "-")
