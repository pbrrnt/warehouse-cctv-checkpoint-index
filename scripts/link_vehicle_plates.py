#!/usr/bin/env python3
"""
เชื่อม vehicles.plate_id — จับคู่ "รถ" (จากกล้อง overview: vehicle_type/color/
embedding) กับ "ป้าย" (จากกล้อง lpr) ที่เป็นรถคันเดียวกันผ่านจุดคอขวดเดียวกัน
เพื่อให้ผลค้นหารถ (/search/vehicles) โชว์ทะเบียนได้ด้วย ดู ADR-024 ใน
docs/00-decisions.md สำหรับเหตุผลการออกแบบ

★ ปัญหา: กล้อง overview กับ lpr เห็นรถคันเดียวกันคนละ Frigate event (คนละ
แถว events) เวลาใกล้กันแต่ไม่ตรงเป๊ะ และลำดับมาไม่แน่นอน จึงทำเป็น pass
แยกที่รันเป็นระยะ (cron) ไม่ใช่ inline ตอน worker ประมวลผล (ตอนนั้นอีก event
อาจยังมาไม่ถึง)

★★ กฎจับคู่แบบระวังไว้ก่อน (conservative 1:1) — จับคู่เฉพาะเมื่อ**ไม่กำกวม
ทั้งสองฝั่ง**: ในกรอบเวลา window รถคันนั้นมีป้ายเข้าเกณฑ์ใบเดียว **และ** ป้าย
ใบนั้นมีรถเข้าเกณฑ์คันเดียว ถ้ามีรถ/ป้ายหลายตัวในกรอบเวลาเดียวกัน (รถตามกัน
มาติด ๆ) = กำกวม ไม่เดา ปล่อย plate_id เป็น NULL ต่อไป เพราะ "ค้นเจอทะเบียน
ผิดคัน" แย่กว่า "ไม่มีทะเบียน" สำหรับระบบช่วยค้นหา

★ ยังไม่เคยรันกับ Postgres จริง (ไม่มี Docker บนเครื่องพัฒนา) และ**ยังไม่เคย
ทดสอบว่า window 3 วินาทีเหมาะกับพฤติกรรมรถจริงหน้างานไหม** — ต้องปรับจากข้อมูล
จริงหลังขึ้นระบบ (ดู ADR-024)

ใช้งาน:
  python scripts/link_vehicle_plates.py --database-url "$DATABASE_URL"
      (ค่าเริ่มต้น = dry-run แค่รายงานว่าจะจับคู่กี่คู่ ไม่เขียนจริง)
  python scripts/link_vehicle_plates.py --database-url "$DATABASE_URL" --execute
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DEFAULT_WINDOW_SECONDS = 3.0
DEFAULT_LOOKBACK_HOURS = 24


@dataclass
class VehicleRecord:
    vehicle_detection_id: int  # PK ของตาราง vehicles (= detection_id)
    camera_id: str
    ts: datetime


@dataclass
class PlateRecord:
    plate_id: int
    camera_id: str
    ts: datetime


def match_vehicles_to_plates(
    vehicles: list[VehicleRecord],
    plates: list[PlateRecord],
    window_seconds: float = DEFAULT_WINDOW_SECONDS,
    camera_pairs: set[tuple[str, str]] | None = None,
) -> dict[int, int]:
    """
    คืน {vehicle_detection_id: plate_id} เฉพาะคู่ที่มั่นใจ (1:1 ไม่กำกวม)

    camera_pairs: เซ็ตของ (overview_camera_id, lpr_camera_id) ที่ถือว่าเป็นจุด
    คอขวดเดียวกัน — ถ้า None = จับคู่ข้ามกล้องได้หมดในไซต์ (ใช้ได้กับไซต์
    ประตูเดียวที่มี overview+lpr อย่างละตัว เช่นไซต์นำร่อง WH01) ★ ไซต์หลาย
    ประตูต้องส่ง camera_pairs มาด้วย ไม่งั้นรถประตู A อาจจับคู่ป้ายประตู B ที่
    บังเอิญเวลาใกล้กัน
    """
    def compatible(v: VehicleRecord, p: PlateRecord) -> bool:
        if abs((v.ts - p.ts).total_seconds()) > window_seconds:
            return False
        if camera_pairs is not None and (v.camera_id, p.camera_id) not in camera_pairs:
            return False
        return True

    result: dict[int, int] = {}
    for v in vehicles:
        candidate_plates = [p for p in plates if compatible(v, p)]
        if len(candidate_plates) != 1:
            continue  # 0 = ไม่มีป้าย, >1 = กำกวม ไม่เดา
        plate = candidate_plates[0]
        # ★ เช็คฝั่งกลับด้วย — ป้ายใบนี้ต้องมีรถเข้าเกณฑ์คันเดียวเท่านั้น
        peer_vehicles = [vv for vv in vehicles if compatible(vv, plate)]
        if len(peer_vehicles) != 1:
            continue
        result[v.vehicle_detection_id] = plate.plate_id
    return result


def build_records(session, site_id: str, since: datetime):
    """ดึง VehicleRecord (ที่ยัง plate_id NULL, จากกล้อง overview) และ
    PlateRecord (plate_norm ไม่ NULL, จากกล้อง lpr) ในช่วง lookback"""
    from sqlalchemy import select

    from services.common.db.models import (
        Camera,
        CameraRole,
        Detection,
        Event,
        Plate,
        Vehicle,
    )

    vehicle_stmt = (
        select(Vehicle.detection_id, Event.camera_id, Event.start_ts)
        .join(Detection, Detection.id == Vehicle.detection_id)
        .join(Event, Event.id == Detection.event_id)
        .join(Camera, Camera.id == Event.camera_id)
        .where(
            Event.site_id == site_id,
            Vehicle.plate_id.is_(None),
            Camera.role == CameraRole.overview,
            Event.start_ts >= since,
        )
    )
    vehicles = [
        VehicleRecord(vehicle_detection_id=row[0], camera_id=row[1], ts=row[2])
        for row in session.execute(vehicle_stmt).all()
    ]

    plate_stmt = (
        select(Plate.id, Event.camera_id, Event.start_ts)
        .join(Detection, Detection.id == Plate.detection_id)
        .join(Event, Event.id == Detection.event_id)
        .join(Camera, Camera.id == Event.camera_id)
        .where(
            Event.site_id == site_id,
            Plate.plate_norm.isnot(None),
            Camera.role == CameraRole.lpr,
            Event.start_ts >= since,
        )
    )
    plates = [
        PlateRecord(plate_id=row[0], camera_id=row[1], ts=row[2])
        for row in session.execute(plate_stmt).all()
    ]
    return vehicles, plates


def apply_matches(session, matches: dict[int, int], dry_run: bool) -> int:
    """เขียน vehicles.plate_id ตาม matches — คืนจำนวนแถวที่อัปเดต"""
    from services.common.db.models import Vehicle

    if dry_run:
        return len(matches)

    updated = 0
    for vehicle_detection_id, plate_id in matches.items():
        vehicle = session.get(Vehicle, vehicle_detection_id)
        if vehicle is None:
            continue  # ถูกลบไปแล้วระหว่างทาง (retention) — ข้าม
        vehicle.plate_id = plate_id
        updated += 1
    session.commit()
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--site-id", help="ถ้าไม่ใส่ อ่านจาก env SITE_ID")
    parser.add_argument("--execute", action="store_true", help="เขียนจริง — ไม่ใส่ = dry-run")
    parser.add_argument("--window-seconds", type=float, default=DEFAULT_WINDOW_SECONDS)
    parser.add_argument("--lookback-hours", type=float, default=DEFAULT_LOOKBACK_HOURS)
    args = parser.parse_args()

    import os

    site_id = args.site_id or os.environ.get("SITE_ID")
    if not site_id:
        parser.error("ต้องระบุ --site-id หรือตั้ง env SITE_ID")

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(args.database_url, pool_pre_ping=True)
    session_factory = sessionmaker(bind=engine)

    since = datetime.now(timezone.utc) - timedelta(hours=args.lookback_hours)
    dry_run = not args.execute

    with session_factory() as session:
        vehicles, plates = build_records(session, site_id, since)
        matches = match_vehicles_to_plates(vehicles, plates, args.window_seconds)
        applied = apply_matches(session, matches, dry_run)

    mode = "DRY-RUN (ยังไม่เขียน)" if dry_run else "เขียนจริงแล้ว"
    print(f"โหมด: {mode}")
    print(f"  รถ (overview, ยังไม่มีทะเบียน): {len(vehicles)} คัน")
    print(f"  ป้าย (lpr): {len(plates)} ใบ")
    print(f"  จับคู่ได้ (1:1 ไม่กำกวม): {applied} คู่")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
