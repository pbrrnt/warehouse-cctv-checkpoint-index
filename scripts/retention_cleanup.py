#!/usr/bin/env python3
"""
ลบข้อมูลที่เกิน retention จริง — ทั้งแถวใน DB และไฟล์ crop/snapshot ในดิสก์
ตาม docs/06-pdpa-compliance.md หัวข้อ 6 ("งานลบอัตโนมัติต้องรันทุกวัน และ
ต้องลบไฟล์จริงใน object store ด้วย ไม่ใช่แค่ลบแถวใน DB")

แบ่ง 3 เฟส (ใบหน้าต้องลบ**ก่อน**เหตุการณ์ทั่วไป เพราะ retention สั้นกว่า):
  1. Detection(kind='face') + FaceEmbedding ที่เกิน RETENTION_FACE_EMBEDDINGS_DAYS
  2. Event ทั้งแถว (cascade ลบ Detection/Vehicle/Plate/FaceEmbedding ที่เหลือให้เอง
     ผ่าน ondelete=CASCADE ที่ตั้งไว้ใน services/common/db/models.py) ที่เกิน
     RETENTION_EVENTS_DAYS
  3. AuditLog ที่เกิน RETENTION_AUDIT_LOG_DAYS (เก็บแยก 3 ปี ไม่ผูกกับ event)

ทุกเฟสลบไฟล์ (thumb_key/crop_key) ผ่าน ContentStore **ก่อน** ลบแถว DB เสมอ —
ถ้าลบไฟล์ไม่สำเร็จ ข้ามแถวนั้นไปก่อน (ไม่ลบ DB row แบบ orphan-ไฟล์-เหลือ)
บันทึกจำนวนที่ลบลง audit_log ทุกครั้งที่รันจริง (ไม่ใช่ dry-run) ตามข้อกำหนด

★ ยังไม่เคยรันกับ Postgres จริง (ไม่มี Docker บนเครื่องพัฒนา) — ดู
docs/11-testing.md

ใช้งาน:
  python scripts/retention_cleanup.py --database-url "$DATABASE_URL" --media-path /media
      (ค่าเริ่มต้น = dry-run เสมอ แค่รายงานว่าจะลบอะไร ไม่ลบจริง)
  python scripts/retention_cleanup.py --database-url "$DATABASE_URL" --media-path /media --execute
      (ลบจริง — ตั้งใน cron รายวันตาม docs/07-operations.md)
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DEFAULT_RETENTION_EVENTS_DAYS = 365
DEFAULT_RETENTION_FACE_EMBEDDINGS_DAYS = 180
DEFAULT_RETENTION_AUDIT_LOG_DAYS = 1095  # 3 ปี


@dataclass
class CleanupReport:
    face_detections_deleted: int = 0
    face_files_deleted: int = 0
    events_deleted: int = 0
    event_files_deleted: int = 0
    audit_logs_deleted: int = 0
    errors: list[str] = field(default_factory=list)

    def total_rows(self) -> int:
        return self.face_detections_deleted + self.events_deleted + self.audit_logs_deleted


def _delete_file_if_present(store, key: str | None, report_errors: list[str], dry_run: bool) -> bool:
    """ลบไฟล์ผ่าน ContentStore ถ้ามี key — คืน True ถ้าลบสำเร็จหรือไม่มี key ให้ลบ
    (ไม่มี key ไม่ใช่ error) คืน False เฉพาะตอนลบไฟล์ที่มี key แล้วพังจริง

    dry_run=True: ไม่ลบไฟล์จริง แค่คืน True เหมือนลบสำเร็จ — เพื่อให้ทั้ง
    เฟสนับจำนวนที่ "จะลบ" ได้ถูกต้องโดยไม่แตะอะไรเลย (dry-run ต้องปลอดภัย
    100% เพราะนี่คือเครื่องมือลบข้อมูลถาวรตาม PDPA เผลอลบตอน preview ไม่ได้)"""
    if not key:
        return True
    if dry_run:
        return True
    try:
        store.delete(key)
        return True
    except Exception as e:  # noqa: BLE001 — ต้องไม่ทำให้ทั้งงานพังเพราะไฟล์เดียว
        report_errors.append(f"ลบไฟล์ {key!r} ไม่สำเร็จ: {e}")
        return False


def cleanup_expired_faces(session, store, cutoff: datetime, dry_run: bool, report: CleanupReport) -> None:
    """เฟส 1 — Detection(kind='face') ที่ event เกิน cutoff (ลบก่อนเพราะ retention สั้นกว่า)"""
    from sqlalchemy import select

    from services.common.db.models import Detection, DetectionKind, Event, FaceEmbedding

    stmt = (
        select(Detection)
        .join(Event, Event.id == Detection.event_id)
        .where(Detection.kind == DetectionKind.face, Event.start_ts < cutoff)
    )
    detections = list(session.execute(stmt).scalars())

    for detection in detections:
        if not _delete_file_if_present(store, detection.crop_key, report.errors, dry_run):
            continue
        if detection.crop_key:
            report.face_files_deleted += 1
        if not dry_run:
            # FaceEmbedding มี ondelete=CASCADE บน detection_id อยู่แล้ว แต่ query ตรง ๆ
            # ก็ได้เพื่อความชัดเจนว่าตั้งใจลบคู่กัน
            session.query(FaceEmbedding).filter(FaceEmbedding.detection_id == detection.id).delete()
            session.delete(detection)
        report.face_detections_deleted += 1

    if not dry_run:
        session.commit()


def cleanup_expired_events(session, store, cutoff: datetime, dry_run: bool, report: CleanupReport) -> None:
    """เฟส 2 — Event ทั้งแถว (cascade ลบ Detection/Vehicle/Plate/FaceEmbedding ที่เหลือเอง)"""
    from sqlalchemy import select

    from services.common.db.models import Detection, Event

    stmt = select(Event).where(Event.start_ts < cutoff)
    events = list(session.execute(stmt).scalars())

    for event in events:
        keys = [event.thumb_key]
        detection_stmt = select(Detection.crop_key).where(Detection.event_id == event.id)
        keys.extend(session.execute(detection_stmt).scalars())

        all_files_ok = True
        for key in keys:
            if not _delete_file_if_present(store, key, report.errors, dry_run):
                all_files_ok = False
            elif key:
                report.event_files_deleted += 1

        if not all_files_ok:
            continue  # ★ ไฟล์บางไฟล์ลบไม่ได้ — ข้าม event นี้ไปก่อน กันแถว DB หายแต่ไฟล์ค้าง

        if not dry_run:
            session.delete(event)  # cascade ลบ detections/vehicles/plates/face_embeddings ที่เหลือ
        report.events_deleted += 1

    if not dry_run:
        session.commit()


def cleanup_expired_audit_logs(session, cutoff: datetime, dry_run: bool, report: CleanupReport) -> None:
    """เฟส 3 — audit_log เก็บ 3 ปี ไม่มีไฟล์ผูกด้วย"""
    from sqlalchemy import delete, select

    from services.common.db.models import AuditLog

    count = len(list(session.execute(select(AuditLog.id).where(AuditLog.ts < cutoff)).scalars()))
    if not dry_run and count:
        session.execute(delete(AuditLog).where(AuditLog.ts < cutoff))
        session.commit()
    report.audit_logs_deleted = count


def write_summary_to_audit_log(session, report: CleanupReport) -> None:
    from services.common.db.models import AuditLog

    session.add(
        AuditLog(
            actor="retention_cleanup.py",
            action="retention_delete",
            target_type="batch",
            query={
                "face_detections_deleted": report.face_detections_deleted,
                "events_deleted": report.events_deleted,
                "audit_logs_deleted": report.audit_logs_deleted,
                "errors": report.errors,
            },
        )
    )
    session.commit()


def format_report(report: CleanupReport, dry_run: bool) -> str:
    mode = "DRY-RUN (ยังไม่ลบจริง)" if dry_run else "ลบจริงแล้ว"
    lines = [
        f"โหมด: {mode}",
        f"  face detections: {report.face_detections_deleted} แถว, ไฟล์ {report.face_files_deleted}",
        f"  events (+ลูกทั้งหมด cascade): {report.events_deleted} แถว, ไฟล์ {report.event_files_deleted}",
        f"  audit_log: {report.audit_logs_deleted} แถว",
    ]
    if report.errors:
        lines.append(f"  ★ error {len(report.errors)} รายการ:")
        lines.extend(f"    - {e}" for e in report.errors)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--media-path", required=True, help="root ของ ContentStore เดียวกับที่ indexer ใช้ (MEDIA_PATH)")
    parser.add_argument("--execute", action="store_true", help="ลบจริง — ไม่ใส่ = dry-run เสมอ")
    parser.add_argument("--retention-events-days", type=int, default=DEFAULT_RETENTION_EVENTS_DAYS)
    parser.add_argument("--retention-face-days", type=int, default=DEFAULT_RETENTION_FACE_EMBEDDINGS_DAYS)
    parser.add_argument("--retention-audit-log-days", type=int, default=DEFAULT_RETENTION_AUDIT_LOG_DAYS)
    args = parser.parse_args()

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from services.common.storage import LocalFileStore

    engine = create_engine(args.database_url, pool_pre_ping=True)
    session_factory = sessionmaker(bind=engine)
    store = LocalFileStore(args.media_path)

    now = datetime.now(timezone.utc)
    face_cutoff = now - timedelta(days=args.retention_face_days)
    events_cutoff = now - timedelta(days=args.retention_events_days)
    audit_cutoff = now - timedelta(days=args.retention_audit_log_days)

    dry_run = not args.execute
    report = CleanupReport()

    with session_factory() as session:
        cleanup_expired_faces(session, store, face_cutoff, dry_run, report)
        cleanup_expired_events(session, store, events_cutoff, dry_run, report)
        cleanup_expired_audit_logs(session, audit_cutoff, dry_run, report)
        if not dry_run and report.total_rows():
            write_summary_to_audit_log(session, report)

    print(format_report(report, dry_run))
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
