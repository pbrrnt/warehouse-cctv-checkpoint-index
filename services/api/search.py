"""
คิวรีค้นหา — ใช้ SQL pattern ที่ spec ไว้ใน docs/04-data-model.md หัวข้อ 4
ตรง ๆ (fuzzy plate search สองชั้น, ค้นรถแบบผสม, "ตอนนี้มีรถอะไรอยู่")

แยกฟังก์ชัน "สร้าง query" (build_*) ออกจาก "แปลงแถวผลลัพธ์เป็น response
schema" (to_*) เพื่อทดสอบทั้งคู่ได้โดยไม่ต้องมี Postgres จริง — build_*
ทดสอบได้ด้วยการ compile เป็น SQL text เทียบโครงสร้าง (ดู tests/test_api.py)
ส่วน to_*/rerank_* เป็น pure function ทดสอบตรง ๆ ได้เลย

★ ยังไม่เคยรันกับ Postgres จริงเลย (เครื่องพัฒนาไม่มี Docker) — โดยเฉพาะ
pg_trgm operator '%' และ DISTINCT ON ที่ SQLAlchemy สร้างให้ ต้องพิสูจน์ตอน
ขึ้นระบบจริง

★ vehicles.plate_id เซ็ตโดย scripts/link_vehicle_plates.py (pass แยกรันเป็น
ระยะ ดู ADR-024) ไม่ใช่ worker.py — VehicleSearchResult.plate_norm จะมีค่า
เฉพาะรถที่จับคู่กับป้ายได้แบบไม่กำกวม (1:1) ที่เหลือเป็น None (ปกติ ไม่ใช่บั๊ก)
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Select, func, select

from services.api.schemas import CameraInfo, NowEntry, PlateSearchResult, VehicleSearchResult
from services.api.timefmt import nvr_playback_time, to_local_iso
from services.common.analytics.plate_normalize import weighted_edit_distance
from services.common.db.models import Camera, Detection, Event, EventDirection, Plate, PlateRegistry, Vehicle

MAX_FUZZY_CANDIDATES = 200  # ชั้น 1 (trigram) ดึงมากได้เพราะเร็ว ยังไม่ใช่ผลสุดท้าย
MAX_RESULTS = 50  # ชั้น 2 (weighted edit distance) / ค้นรถ — จำนวนที่คืนให้ผู้ใช้จริง


def _similarity_from_distance(distance: float, a: str, b: str) -> float:
    """
    แปลง weighted edit distance (0 = เหมือนกันเป๊ะ, ยิ่งมากยิ่งต่าง) เป็น
    คะแนน 0-1 (ยิ่งสูงยิ่งตรง) — normalize ด้วยความยาวสตริงที่ยาวกว่า เพราะ
    cost ต่อการแก้ 1 ตัวอักษรอยู่ในสเกล ~0-1 อยู่แล้ว (ดู INSERT_DELETE_COST/
    CONFUSION_COST ใน plate_normalize.py)
    """
    longest = max(len(a), len(b), 1)
    return max(0.0, 1.0 - distance / longest)


def build_plate_fuzzy_query(site_id: str, q: str, from_ts: datetime, to_ts: datetime) -> Select:
    """
    ชั้น 1 — กรองเร็วด้วย pg_trgm ก่อน (ดู 04-data-model.md หัวข้อ 4.1)

    LEFT JOIN plate_registry ด้วย (ไม่ใช่ INNER — ทะเบียนส่วนใหญ่จะไม่ได้อยู่
    ในทะเบียนที่รู้จัก ต้องยังเจอผลค้นหาปกติแม้ไม่มีแถวใน registry)
    """
    similarity_expr = func.similarity(Plate.plate_norm, q)
    return (
        select(Plate, Event, Camera, PlateRegistry, similarity_expr.label("trgm_sim"))
        .join(Detection, Detection.id == Plate.detection_id)
        .join(Event, Event.id == Detection.event_id)
        .join(Camera, Camera.id == Event.camera_id)
        .outerjoin(
            PlateRegistry,
            (PlateRegistry.site_id == Event.site_id) & (PlateRegistry.plate_norm == Plate.plate_norm),
        )
        .where(
            Event.site_id == site_id,
            Plate.plate_norm.isnot(None),
            Plate.plate_norm.op("%")(q),
            Event.start_ts.between(from_ts, to_ts),
        )
        .order_by(similarity_expr.desc())
        .limit(MAX_FUZZY_CANDIDATES)
    )


def rerank_plate_candidates(rows, q: str, limit: int = MAX_RESULTS):
    """
    ชั้น 2 — จัดอันดับใหม่ด้วย weighted edit distance ที่รู้จักอักษรไทย
    สับสน (ดู 05-thai-lpr.md หัวข้อ 4) แม่นกว่า trigram similarity เฉย ๆ
    เพราะ trigram ไม่รู้ว่า 'ก'/'ถ' หน้าตาคล้ายกันตอน OCR อ่านผิด

    rows: iterable ของ (Plate, Event, Camera, PlateRegistry | None, trgm_sim)
    — ผลจาก build_plate_fuzzy_query คืนค่าเป็น list ของ
    (score, Plate, Event, Camera, PlateRegistry | None) เรียงคะแนนมากไปน้อย
    """
    scored = []
    for plate, event, camera, registry_entry, _trgm_sim in rows:
        distance = weighted_edit_distance(plate.plate_norm, q)
        score = _similarity_from_distance(distance, plate.plate_norm, q)
        scored.append((score, plate, event, camera, registry_entry))
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[:limit]


def build_vehicle_search_query(
    site_id: str,
    from_ts: datetime,
    to_ts: datetime,
    camera_id: str | None = None,
    color: str | None = None,
    vehicle_type: str | None = None,
    direction: str | None = None,
) -> Select:
    """ค้นรถแบบผสม (ดู 04-data-model.md หัวข้อ 4.4)"""
    stmt = (
        select(Vehicle, Event, Camera, Plate)
        .join(Detection, Detection.id == Vehicle.detection_id)
        .join(Event, Event.id == Detection.event_id)
        .join(Camera, Camera.id == Event.camera_id)
        .outerjoin(Plate, Plate.id == Vehicle.plate_id)
        .where(Event.site_id == site_id, Event.start_ts.between(from_ts, to_ts))
        .order_by(Event.start_ts.desc())
        .limit(MAX_RESULTS)
    )
    if camera_id:
        stmt = stmt.where(Event.camera_id == camera_id)
    if color:
        stmt = stmt.where(Vehicle.color == color)
    if vehicle_type:
        stmt = stmt.where(Vehicle.vehicle_type == vehicle_type)
    if direction:
        stmt = stmt.where(Event.direction == direction)
    return stmt


def build_now_query(site_id: str, since_ts: datetime) -> Select:
    """
    "ตอนนี้มีรถอะไรอยู่ในโกดัง" — ดู 04-data-model.md หัวข้อ 4.3 (DISTINCT ON
    ทะเบียน เอาแถวล่าสุดของแต่ละป้าย แล้วกรองว่าล่าสุดคือขาเข้า = ยังไม่ออก)

    since_ts คำนวณจาก caller (เช่น now - 24h) ตั้งใจไม่ผูก wall-clock ไว้ใน
    ฟังก์ชันนี้เพื่อให้เทสต์ได้ตรง ๆ ด้วยเวลาคงที่
    """
    latest_per_plate = (
        select(
            Plate.plate_norm.label("plate_norm"),
            Plate.province.label("province"),
            Event.direction.label("direction"),
            Event.start_ts.label("start_ts"),
            Event.camera_id.label("camera_id"),
        )
        .distinct(Plate.plate_norm)
        .join(Detection, Detection.id == Plate.detection_id)
        .join(Event, Event.id == Detection.event_id)
        .where(
            Event.site_id == site_id,
            Plate.plate_norm.isnot(None),
            Event.start_ts > since_ts,
        )
        .order_by(Plate.plate_norm, Event.start_ts.desc())
        .subquery()
    )
    return (
        select(latest_per_plate, Camera)
        .join(Camera, Camera.id == latest_per_plate.c.camera_id)
        .where(latest_per_plate.c.direction == EventDirection.in_.value)
        .order_by(latest_per_plate.c.start_ts.desc())
    )


def _camera_info(camera: Camera) -> CameraInfo:
    return CameraInfo(id=camera.id, name=camera.name, nvr_channel=camera.nvr_channel)


def to_plate_result(
    score: float,
    plate: Plate,
    event: Event,
    camera: Camera,
    tz_name: str,
    registry_entry: PlateRegistry | None = None,
) -> PlateSearchResult:
    return PlateSearchResult(
        event_id=event.id,
        plate_norm=plate.plate_norm,
        raw_text=plate.raw_text,
        province=plate.province,
        ocr_conf=plate.ocr_conf,
        match_score=round(score, 4),
        camera=_camera_info(camera),
        direction=event.direction.value if event.direction else None,
        local_time=to_local_iso(event.start_ts, camera.clock_offset_ms, tz_name),
        nvr_playback_time=nvr_playback_time(event.start_ts, camera.clock_offset_ms, tz_name),
        owner_label=registry_entry.owner_label if registry_entry else None,
        watch=registry_entry.watch if registry_entry else False,
    )


def to_vehicle_result(vehicle: Vehicle, event: Event, camera: Camera, plate: Plate | None, tz_name: str) -> VehicleSearchResult:
    return VehicleSearchResult(
        event_id=event.id,
        vehicle_type=vehicle.vehicle_type,
        color=vehicle.color,
        plate_norm=plate.plate_norm if plate else None,
        camera=_camera_info(camera),
        direction=event.direction.value if event.direction else None,
        local_time=to_local_iso(event.start_ts, camera.clock_offset_ms, tz_name),
        nvr_playback_time=nvr_playback_time(event.start_ts, camera.clock_offset_ms, tz_name),
    )


def to_now_entry(row, camera: Camera, tz_name: str) -> NowEntry:
    return NowEntry(
        plate_norm=row.plate_norm,
        province=row.province,
        camera=_camera_info(camera),
        local_time=to_local_iso(row.start_ts, camera.clock_offset_ms, tz_name),
    )
