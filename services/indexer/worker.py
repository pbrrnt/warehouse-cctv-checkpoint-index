"""
ประมวลผลงานจาก Redis Stream ทีละชิ้น:
  1. เอา ParsedEvent + ข้อมูลกล้องมาสร้างแถว events
  2. ดึง snapshot จาก Frigate แล้วเก็บผ่าน ContentStore
  3. upsert แถว events ลง Postgres (idempotent ตาม frigate event id)
  4. ถ้า label เป็นยานพาหนะ (car/motorcycle/truck/bus) — แยกตาม role ของ
     กล้อง (cameras.role) ที่เกิด event:
       role='overview' -> services/common/analytics/vehicle.py หา
         type/color/embedding บันทึกลง detections + vehicles (_process_vehicle)
       role='lpr'       -> services/common/analytics/thai_ocr.py อ่านป้าย
         ตรงจากภาพครอป (ไม่มี plate_detect.py แยก — ดู _process_plate ว่าทำไม)
         บันทึกลง detections + plates (_process_plate)
     กล้อง role อื่น (face/context/None) ไม่วิเคราะห์อะไรเพิ่ม

แยกฟังก์ชัน "สร้างข้อมูลที่จะเขียน" ออกจาก "เขียนจริงลง DB" เพื่อทดสอบ
ส่วนแรกได้โดยไม่ต้องมี Postgres จริง — ดู tests/test_indexer.py
"""

from __future__ import annotations

import logging

import cv2
import numpy as np
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from services.common.analytics.plate_normalize import normalize_plate
from services.common.analytics.thai_ocr import read_plate_text
from services.common.analytics.vehicle import analyze_vehicle
from services.common.db.models import Camera, CameraRole, Detection, DetectionKind, Event, Plate, Vehicle
from services.common.storage import ContentStore
from services.indexer.frigate_client import FrigateClient, FrigateClientError
from services.indexer.frigate_events import ParsedEvent, compute_direction, frigate_camera_to_db_id

logger = logging.getLogger(__name__)

# ★ ต้องตรงกับ objects.track ใน frigate/config.yml (ไม่รวม 'person'/'bicycle'
# — bicycle ไม่ใช่เป้าหมายของ chokepoint LPR ตาม docs/10-chokepoint-design.md)
VEHICLE_LABELS = {"car", "motorcycle", "truck", "bus"}


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
        vehicle_analyzer=analyze_vehicle,
        plate_reader=read_plate_text,
    ):
        self.session_factory = session_factory
        self.frigate_client = frigate_client
        self.store = store
        self.site_id = site_id
        # ★ inject ได้เพื่อเทสต์โดยไม่ต้องโหลดโมเดล OpenVINO/PaddleOCR จริง
        # (ดู tests/test_indexer.py) — ค่า default คือฟังก์ชันจริงจาก
        # vehicle.py / thai_ocr.py
        self.vehicle_analyzer = vehicle_analyzer
        self.plate_reader = plate_reader

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

            if parsed.label in VEHICLE_LABELS:
                if camera.role == CameraRole.overview:
                    self._process_vehicle(session, parsed)
                elif camera.role == CameraRole.lpr:
                    self._process_plate(session, parsed)
                else:
                    # ★ กล้อง role อื่น (face/context) หรือยังไม่ได้ตั้ง role
                    # เห็นยานพาหนะได้ปกติ (เช่น กล้องหน้าประตูคน) แค่ไม่ใช่
                    # กล้องที่ออกแบบมาให้วิเคราะห์รถ — ไม่ใช่ error แค่ไม่ทำอะไร
                    logger.debug(
                        "กล้อง %s (role=%s) ไม่ใช่ overview/lpr — ข้ามการวิเคราะห์รถของ event %s",
                        camera_id, camera.role, parsed.frigate_id,
                    )

        logger.info("บันทึก event %s (%s, %s) สำเร็จ", parsed.frigate_id, camera_id, direction or "-")

    def _detection_exists(self, session: Session, event_id: str, kind: DetectionKind) -> bool:
        """
        เช็กก่อน insert เพราะ detections.id เป็น autoincrement ไม่มี event id
        ให้ ON CONFLICT ได้เหมือน events — Redis Stream ส่งข้อความเดิมซ้ำได้
        (at-least-once) ถ้าไม่เช็กจะได้แถวซ้ำทุกครั้งที่ redeliver
        """
        existing = session.execute(
            select(Detection.id).where(Detection.event_id == event_id, Detection.kind == kind)
        ).scalar_one_or_none()
        return existing is not None

    def _fetch_crop_image(self, parsed: ParsedEvent, purpose: str) -> np.ndarray | None:
        """ดึงภาพครอปเฉพาะกรอบวัตถุจาก Frigate (?crop=1) + decode เป็น numpy
        array BGR — ใช้ร่วมกันทั้ง _process_vehicle และ _process_plate"""
        try:
            crop_bytes = self.frigate_client.get_snapshot(parsed.frigate_id, crop=True)
        except FrigateClientError:
            # ★ เหมือน snapshot ทั้งเฟรม — ดึงไม่ได้ไม่ควรทำให้ event หายไป
            # ทั้งแถว บันทึก metadata (ทำไปแล้วก่อนเรียกฟังก์ชันนี้) ต่อโดยข้าม
            # การวิเคราะห์เท่านั้น
            logger.warning(
                "ดึงภาพครอปไม่สำเร็จสำหรับ event %s (%s) — ข้าม",
                parsed.frigate_id, purpose,
            )
            return None

        img = cv2.imdecode(np.frombuffer(crop_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            logger.warning("decode ภาพครอปไม่สำเร็จสำหรับ event %s (%s)", parsed.frigate_id, purpose)
            return None
        return img

    def _process_vehicle(self, session: Session, parsed: ParsedEvent) -> None:
        """เรียก vehicle.py วิเคราะห์รถ (กล้อง role='overview') แล้วเขียนแถว
        detections + vehicles"""
        if self._detection_exists(session, parsed.frigate_id, DetectionKind.vehicle):
            return

        img = self._fetch_crop_image(parsed, "vehicle")
        if img is None:
            return

        try:
            result = self.vehicle_analyzer(img)
        except Exception:
            # ★ โมเดล AI ล้มเหลว (ไฟล์โมเดลหาย, ภาพเสีย ฯลฯ) ไม่ควรทำให้
            # event ทั้งแถวหายไปด้วย — log ให้ครบเพื่อ debug ทีหลัง (ดู
            # docs/00-decisions.md บันทึกปัญหาจากหน้างาน)
            logger.exception("วิเคราะห์รถไม่สำเร็จสำหรับ event %s", parsed.frigate_id)
            return

        detection = Detection(
            event_id=parsed.frigate_id,
            kind=DetectionKind.vehicle,
            ts=parsed.end_ts or parsed.start_ts,
        )
        session.add(detection)
        session.flush()  # ★ ต้อง flush ก่อนถึงจะมี detection.id ไปใช้เป็น FK

        attrs = result.attrs
        session.add(
            Vehicle(
                detection_id=detection.id,
                vehicle_type=attrs.vehicle_type if attrs else None,
                color=attrs.color if attrs else None,
                embedding=result.embedding,
            )
        )
        session.commit()

    def _process_plate(self, session: Session, parsed: ParsedEvent) -> None:
        """
        อ่านป้ายทะเบียนตรงจากภาพครอปกล้อง LPR (role='lpr') แล้วเขียนแถว
        detections + plates

        ★ ตั้งใจไม่มี plate_detect.py (YOLO หาตำแหน่งป้าย) แยกต่างหาก — ตาม
        docs/00-decisions.md (พักการตัดสินใจไว้ก่อน) ทดลองใช้ text detector
        ที่มีอยู่แล้วใน thai_ocr.py (PP-OCRv5_mobile_det) ตรง ๆ โดยอาศัยว่า
        กล้อง role='lpr' ออกแบบ FOV แคบ + ติดต่ำระดับป้าย + ระยะควบคุมอยู่แล้ว
        ตาม docs/10-chokepoint-design.md (≤3.2 ม. ที่ 1080p) ทำให้ภาพครอป
        วัตถุมีป้ายเป็นข้อความเด่นในเฟรมโดยธรรมชาติ ไม่ต้องหาตำแหน่งป้ายเอง
        อีกชั้นก่อน OCR — ยังไม่เคยทดสอบกับภาพจริงจากกล้อง (ดู
        docs/11-testing.md) ถ้าเจอ false positive จากข้อความอื่นในเฟรมบ่อย
        ตอนขึ้นระบบจริง ค่อยกลับมาพิจารณา plate_detect.py แยกอีกที
        """
        if self._detection_exists(session, parsed.frigate_id, DetectionKind.plate):
            return

        img = self._fetch_crop_image(parsed, "plate")
        if img is None:
            return

        try:
            ocr_result = self.plate_reader(img)
        except Exception:
            logger.exception("อ่านป้ายทะเบียนไม่สำเร็จสำหรับ event %s", parsed.frigate_id)
            return

        if not ocr_result.lines:
            # ไม่เจอข้อความเลยในภาพ (มุม/แสงไม่ดี, ป้ายไม่อยู่ในเฟรม ฯลฯ) —
            # ปกติได้ ไม่ใช่ error แค่ไม่มีอะไรให้บันทึก
            return

        reading = normalize_plate(ocr_result.lines, char_confs=ocr_result.char_confs, ocr_conf=ocr_result.ocr_conf)

        detection = Detection(
            event_id=parsed.frigate_id,
            kind=DetectionKind.plate,
            ts=parsed.end_ts or parsed.start_ts,
        )
        session.add(detection)
        session.flush()

        session.add(
            Plate(
                detection_id=detection.id,
                raw_text=reading.raw_text,
                plate_norm=reading.plate_norm,
                province=reading.province,
                ocr_conf=reading.ocr_conf,
                char_confs=reading.char_confs,
                flags=reading.flags,
            )
        )
        session.commit()
