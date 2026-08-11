"""
CRUD ทะเบียนที่รู้จัก (plate_registry) — ให้พนักงานติดป้ายชื่อรถที่มาบ่อย
(เช่น 'รถส่งของบริษัท ก', 'รถลูกค้ามารับของ') หรือตั้งธงเฝ้าระวัง ดู
docs/04-data-model.md หัวข้อ 2.7 (ตาราง plate_registry มีอยู่แล้ว แค่ยังไม่มี
API/UI มาก่อนหน้านี้)

★ ไม่ใช่ข้อมูลชีวมิติ (แค่ทะเบียน+ชื่อบริษัท/หมายเหตุ) ไม่ต้องเข้มงวดตาม
PDPA เท่าใบหน้า จึงใช้ auth เดียวกับ endpoint ค้นหา (shared API key) ไปก่อน
ตาม ADR-022 — ไปจำกัดสิทธิ์จริง (เช่น เฉพาะ role manager ขึ้นไปแก้ไขได้)
ตอนทำ RBAC เต็มรูปแบบ
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from services.common.analytics.plate_normalize import normalize_plate
from services.common.db.models import PlateRegistry


def normalize_registry_plate(raw: str) -> str | None:
    """ทำความสะอาดทะเบียนที่พนักงานพิมพ์เอง (แปลงเลขไทย, ตัดช่องว่าง) ด้วย
    ตัวทำความสะอาดเดียวกับที่ OCR ใช้ — เพื่อให้ตรงกับ plates.plate_norm พอดี
    ตอน join คืน None ถ้าพิมพ์ว่างเปล่าจริง ๆ"""
    reading = normalize_plate([raw])
    return reading.plate_norm


def list_registry(session: Session, site_id: str) -> list[PlateRegistry]:
    stmt = select(PlateRegistry).where(PlateRegistry.site_id == site_id).order_by(PlateRegistry.plate_norm)
    return list(session.execute(stmt).scalars())


def upsert_registry_entry(
    session: Session,
    site_id: str,
    plate_norm: str,
    owner_label: str | None,
    notes: str | None,
    watch: bool,
) -> PlateRegistry:
    """สร้างใหม่หรือแก้ของเดิมถ้ามีทะเบียนนี้อยู่แล้ว (upsert ตาม primary key
    (site_id, plate_norm))"""
    entry = session.get(PlateRegistry, (site_id, plate_norm))
    if entry is None:
        entry = PlateRegistry(site_id=site_id, plate_norm=plate_norm)
        session.add(entry)
    entry.owner_label = owner_label
    entry.notes = notes
    entry.watch = watch
    session.commit()
    session.refresh(entry)
    return entry


def delete_registry_entry(session: Session, site_id: str, plate_norm: str) -> bool:
    entry = session.get(PlateRegistry, (site_id, plate_norm))
    if entry is None:
        return False
    session.delete(entry)
    session.commit()
    return True
