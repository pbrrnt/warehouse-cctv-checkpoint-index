#!/usr/bin/env python3
"""
สร้าง/อัปเดตแถว sites + cameras จากไฟล์ config — ใช้ตอนขึ้นระบบไซต์ใหม่จริง
(ดู docs/09-multi-site-runbook.md ขั้นที่ 5.3) หลังรัน migration แต่ก่อนสตาร์ต
`docker compose --profile app up -d`

★ จำเป็นมาก ห้ามข้าม — services/indexer/worker.py เช็ค session.get(Camera, ...)
ก่อนทำอะไรทั้งหมด ถ้าไม่มีแถว cameras ตรงกับชื่อกล้องใน Frigate เลย indexer
จะ log "ไม่พบกล้อง...ข้ามการบันทึก" แล้วไม่เขียนอะไรลง DB เลยเงียบ ๆ (ดูแล้ว
เหมือนระบบทำงานปกติ เพราะไม่ error แค่ไม่มีข้อมูลออกมา)

ใช้งาน:
  python scripts/seed_site.py --config site_config.json --database-url "$DATABASE_URL"

รูปแบบไฟล์ config ดูตัวอย่างที่ scripts/site_config.example.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from services.common.db.models import Camera, CameraRole, Site  # noqa: E402

VALID_ROLES = {r.value for r in CameraRole}
VALID_TIERS = {"A", "B", "C"}


def load_config(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def validate_config(config: dict) -> list[str]:
    """คืน list ของข้อผิดพลาด (ว่าง = ผ่าน) — เช็คก่อน insert ดีกว่าให้ DB
    error กลางทางแล้วไม่รู้ว่าเหลือกล้องไหนที่ยังไม่ได้ seed"""
    errors = []
    site = config.get("site")
    if not site or not site.get("id") or not site.get("name"):
        errors.append("ต้องมี site.id และ site.name")

    cameras = config.get("cameras") or []
    if not cameras:
        errors.append("ต้องมีกล้องอย่างน้อย 1 ตัวใน cameras")

    seen_ids = set()
    for i, cam in enumerate(cameras):
        prefix = f"cameras[{i}] ({cam.get('id', '?')})"
        if not cam.get("id"):
            errors.append(f"{prefix}: ต้องมี id")
        elif cam["id"] in seen_ids:
            errors.append(f"{prefix}: id ซ้ำในไฟล์ config เอง")
        else:
            seen_ids.add(cam["id"])
        if not cam.get("name"):
            errors.append(f"{prefix}: ต้องมี name")
        if cam.get("tier") not in VALID_TIERS:
            errors.append(f"{prefix}: tier ต้องเป็น A/B/C (ได้ {cam.get('tier')!r})")
        if cam.get("role") is not None and cam.get("role") not in VALID_ROLES:
            errors.append(f"{prefix}: role ต้องเป็นหนึ่งใน {sorted(VALID_ROLES)} หรือไม่ใส่เลย (ได้ {cam.get('role')!r})")

    return errors


def seed(session: Session, config: dict) -> tuple[Site, list[Camera]]:
    """upsert Site + Camera ทุกตัวในไฟล์ config — เรียกหลัง validate_config() ผ่านแล้วเท่านั้น"""
    site_cfg = config["site"]
    site = session.get(Site, site_cfg["id"])
    if site is None:
        site = Site(id=site_cfg["id"], name=site_cfg["name"])
        session.add(site)
    else:
        site.name = site_cfg["name"]
    if site_cfg.get("timezone"):
        site.timezone = site_cfg["timezone"]
    site.nvr_host = site_cfg.get("nvr_host")
    site.nvr_vendor = site_cfg.get("nvr_vendor")

    cameras: list[Camera] = []
    for cam_cfg in config["cameras"]:
        camera = session.get(Camera, cam_cfg["id"])
        if camera is None:
            camera = Camera(id=cam_cfg["id"], site_id=site_cfg["id"], name=cam_cfg["name"], tier=cam_cfg["tier"])
            session.add(camera)
        else:
            camera.site_id = site_cfg["id"]
            camera.name = cam_cfg["name"]
            camera.tier = cam_cfg["tier"]
        camera.role = CameraRole(cam_cfg["role"]) if cam_cfg.get("role") else None
        camera.location = cam_cfg.get("location")
        camera.vendor = cam_cfg.get("vendor")
        camera.nvr_channel = cam_cfg.get("nvr_channel")
        camera.zone_config = cam_cfg.get("zone_config")
        camera.clock_offset_ms = cam_cfg.get("clock_offset_ms", 0)
        camera.enabled = cam_cfg.get("enabled", True)
        cameras.append(camera)

    session.commit()
    return site, cameras


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path, help="path ไฟล์ JSON (ดู site_config.example.json)")
    parser.add_argument("--database-url", required=True, help="เช่น postgresql+psycopg://user:pass@localhost:5432/db")
    args = parser.parse_args()

    config = load_config(args.config)
    errors = validate_config(config)
    if errors:
        print("[FAIL] config ไม่ถูกต้อง:")
        for e in errors:
            print(f"  - {e}")
        return 1

    engine = create_engine(args.database_url, pool_pre_ping=True)
    session_factory = sessionmaker(bind=engine)
    with session_factory() as session:
        site, cameras = seed(session, config)

    print(f"OK — site {site.id} ({site.name}), กล้อง {len(cameras)} ตัว:")
    for cam in cameras:
        print(f"  - {cam.id} role={cam.role.value if cam.role else '-'} nvr_channel={cam.nvr_channel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
