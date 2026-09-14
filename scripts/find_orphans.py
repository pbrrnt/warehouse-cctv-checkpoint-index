#!/usr/bin/env python3
"""
ตรวจไฟล์กำพร้าในดิสก์ — เทียบไฟล์จริงใน object store กับ key ที่ DB อ้างถึง
ดู docs/07-operations.md หัวข้อ 4.2 รันรายสัปดาห์ (cron)

รายงาน 2 ทิศทาง:
  1. ไฟล์กำพร้า (orphan) — มีไฟล์บนดิสก์แต่ไม่มีแถว DB ไหนอ้างถึง (เกิดจาก
     process ตายกลางคันหลังเขียนไฟล์แต่ก่อน commit แถว, หรือ retention ลบแถว
     แต่ลบไฟล์ไม่สำเร็จ) — กินพื้นที่เปล่า ลบได้ปลอดภัยหลังคนยืนยัน
  2. ไฟล์หาย (missing) — DB มี key แต่ไม่มีไฟล์บนดิสก์ (ร้ายแรงกว่า — ผลค้นหา
     จะชี้ไปหาภาพที่เปิดไม่ได้) รายงานให้รู้ แต่ลบอะไรไม่ได้ (ไฟล์หายไปแล้ว)

★ ค่าเริ่มต้น = รายงานอย่างเดียว ไม่ลบ (docs หัวข้อ 4.2: "ยังไม่ลบอัตโนมัติ
ให้คนตัดสินใจก่อน") ต้อง --delete-orphans ถึงจะลบไฟล์กำพร้าจริง — และลบเฉพาะ
ไฟล์กำพร้า (ทิศ 1) ไม่ยุ่งกับไฟล์หาย

★ ยังไม่เคยรันกับ Postgres/ดิสก์จริงพร้อมข้อมูลจริง (ไม่มี Docker บนเครื่อง
พัฒนา) — ดู docs/11-testing.md

ใช้งาน:
  python scripts/find_orphans.py --database-url "$DATABASE_URL" --media-path /media
  python scripts/find_orphans.py --database-url "$DATABASE_URL" --media-path /media --delete-orphans
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DEFAULT_MIN_AGE_HOURS = 1.0


def find_orphans(disk_keys: set[str], db_keys: set[str]) -> tuple[set[str], set[str]]:
    """
    pure function — คืน (orphan_files, missing_files)
      orphan_files = อยู่บนดิสก์ แต่ไม่มีใน DB (ลบได้)
      missing_files = อยู่ใน DB แต่ไม่มีบนดิสก์ (ไฟล์หาย)
    """
    orphan_files = disk_keys - db_keys
    missing_files = db_keys - disk_keys
    return orphan_files, missing_files


def partition_deletable_orphans(
    orphan_files: set[str],
    media_path: str | Path,
    min_age_seconds: float,
    now: float | None = None,
) -> tuple[set[str], set[str]]:
    """
    แยก orphan ออกเป็น (deletable, too_recent) ตามอายุไฟล์ (mtime)

    ★ กัน TOCTOU: indexer เขียนไฟล์ก่อน commit แถว event เสมอ (ดู worker.py)
    ถ้า find_orphans สแกนดิสก์เจอไฟล์สดที่ยัง commit แถวไม่ทัน จะเห็นเป็น orphan
    ทั้งที่เป็นไฟล์ถูกต้อง — ห้ามลบ ไฟล์ที่เพิ่งแก้ไขภายใน min_age_seconds จึง
    ถือว่า "ใหม่เกินตัดสิน" ไม่ลบ (แต่ยังรายงานได้) stat ไม่ได้ = เล่นให้ปลอดภัย
    ไม่ลบ
    """
    now = time.time() if now is None else now
    root = Path(media_path)
    deletable: set[str] = set()
    too_recent: set[str] = set()
    for key in orphan_files:
        try:
            age = now - (root / key).stat().st_mtime
        except OSError:
            too_recent.add(key)  # stat ไม่ได้ (ไฟล์เพิ่งหาย/สิทธิ์) — ไม่เสี่ยงลบ
            continue
        (deletable if age >= min_age_seconds else too_recent).add(key)
    return deletable, too_recent


def scan_disk_keys(media_path: str | Path) -> set[str]:
    """
    เดินไฟล์ทั้งหมดใต้ media_path คืนเป็นเซ็ตของ key (path สัมพัทธ์ใช้ '/' เสมอ)

    ★ ใช้ as_posix() บังคับ forward slash — key ใน DB เก็บด้วย '/' (เช่น
    'wh01/events/e1/snapshot.jpg') แต่บน Windows การเดินไฟล์ได้ '\\' ถ้าไม่
    normalize จะไม่มีทาง match กันเลย (เจอ pattern บั๊ก Windows-vs-Linux แบบ
    นี้มาแล้วในโปรเจกต์ — ดู ADR-023 opencv-headless) เป้าหมายจริงเป็น Linux
    แต่ต้อง normalize ให้ทำงานถูกทั้งสอง OS
    """
    root = Path(media_path)
    if not root.exists():
        return set()
    keys: set[str] = set()
    for path in root.rglob("*"):
        if path.is_file():
            keys.add(path.relative_to(root).as_posix())
    return keys


def fetch_db_keys(session) -> set[str]:
    """รวม key ทั้งหมดที่ DB อ้างถึง — crop_key (detections) + thumb_key (events)"""
    from sqlalchemy import select

    from services.common.db.models import Detection, Event

    keys: set[str] = set()
    for (crop_key,) in session.execute(select(Detection.crop_key).where(Detection.crop_key.isnot(None))):
        keys.add(crop_key)
    for (thumb_key,) in session.execute(select(Event.thumb_key).where(Event.thumb_key.isnot(None))):
        keys.add(thumb_key)
    return keys


def format_report(orphan_files: set[str], missing_files: set[str], deleted: int | None) -> str:
    lines = [
        f"ไฟล์กำพร้า (มีบนดิสก์ ไม่มีใน DB): {len(orphan_files)}",
        f"ไฟล์หาย (มีใน DB ไม่มีบนดิสก์): {len(missing_files)}",
    ]
    if orphan_files:
        lines.append("  ตัวอย่างไฟล์กำพร้า (สูงสุด 10):")
        lines.extend(f"    - {k}" for k in sorted(orphan_files)[:10])
    if missing_files:
        lines.append("  ★ ไฟล์หาย (สูงสุด 10) — ผลค้นหาที่ชี้ไปหาไฟล์เหล่านี้จะเปิดภาพไม่ได้:")
        lines.extend(f"    - {k}" for k in sorted(missing_files)[:10])
    if deleted is not None:
        lines.append(f"ลบไฟล์กำพร้าไปแล้ว: {deleted}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--media-path", required=True)
    parser.add_argument(
        "--delete-orphans",
        action="store_true",
        help="ลบไฟล์กำพร้าจริง (ค่าเริ่มต้น = รายงานอย่างเดียว ไม่ลบ)",
    )
    parser.add_argument(
        "--min-age-hours",
        type=float,
        default=DEFAULT_MIN_AGE_HOURS,
        help="ไม่ลบไฟล์กำพร้าที่เพิ่งแก้ไขภายในกี่ชั่วโมง (กันลบไฟล์สดที่ยัง commit แถวไม่ทัน)",
    )
    args = parser.parse_args()

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from services.common.storage import LocalFileStore

    engine = create_engine(args.database_url, pool_pre_ping=True)
    session_factory = sessionmaker(bind=engine)
    store = LocalFileStore(args.media_path)

    disk_keys = scan_disk_keys(args.media_path)
    with session_factory() as session:
        db_keys = fetch_db_keys(session)

    orphan_files, missing_files = find_orphans(disk_keys, db_keys)

    deleted = None
    skipped_recent = 0
    if args.delete_orphans:
        deleted = 0
        deletable, too_recent = partition_deletable_orphans(
            orphan_files, args.media_path, args.min_age_hours * 3600
        )
        skipped_recent = len(too_recent)
        for key in deletable:
            try:
                store.delete(key)
                deleted += 1
            except Exception as e:  # noqa: BLE001 — รายงานแล้วไปต่อ ไม่ให้ไฟล์เดียวล้มทั้งงาน
                print(f"[WARN] ลบ {key!r} ไม่สำเร็จ: {e}", file=sys.stderr)

    print(format_report(orphan_files, missing_files, deleted))
    if skipped_recent:
        print(f"ข้ามไฟล์กำพร้าที่ใหม่เกิน {args.min_age_hours} ชม. (ไม่ลบ กัน TOCTOU): {skipped_recent}")
    # exit code != 0 ถ้ามีไฟล์หาย (ปัญหาที่ต้องสนใจ) — ไฟล์กำพร้าเฉย ๆ ไม่ถือว่า fail
    return 1 if missing_files else 0


if __name__ == "__main__":
    raise SystemExit(main())
