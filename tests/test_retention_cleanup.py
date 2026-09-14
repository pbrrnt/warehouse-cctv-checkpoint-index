"""
เทสต์ scripts/retention_cleanup.py — เน้น 2 พฤติกรรมสำคัญที่สุด:
  1. ลบไฟล์ (ContentStore) ก่อนลบแถว DB เสมอ
  2. ถ้าลบไฟล์ไม่สำเร็จ ต้อง**ข้าม**แถวนั้น ไม่ลบ DB row ทิ้งจนไฟล์ค้าง (orphan)
     — ตรงข้อบังคับ PDPA ที่ว่า "ต้องลบไฟล์จริง ไม่ใช่แค่แถว DB"
  3. dry-run ต้องไม่แตะ DB เลย (ไม่ commit/delete) แค่รายงาน

ใช้ fake session + fake store — Event/Detection มีคอลัมน์ JSONB (raw, query,
zones ฯลฯ) รันบน SQLite จริงไม่ได้ (ต่าง plate_registry) เลย mock ทั้ง query
path เหมือน tests/test_indexer.py

รันด้วย:  python -m unittest tests.test_retention_cleanup -v
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.retention_cleanup import (  # noqa: E402
    CleanupReport,
    cleanup_expired_audit_logs,
    cleanup_expired_events,
    cleanup_expired_faces,
    format_report,
)
from services.common.db.models import Detection, DetectionKind, Event  # noqa: E402

CUTOFF = datetime(2026, 1, 1, tzinfo=timezone.utc)


class _FakeStore:
    def __init__(self, fail_keys=()):
        self.deleted = []
        self._fail_keys = set(fail_keys)

    def delete(self, key):
        if key in self._fail_keys:
            raise OSError(f"จำลองลบไฟล์ {key} ไม่สำเร็จ")
        self.deleted.append(key)


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return list(self._rows)

    def scalar_one(self):
        # ใช้กับ count query — ส่งค่าตัวแรกเป็นผล COUNT(*)
        return self._rows[0] if self._rows else 0


class _FakeSession:
    """
    คืนผล execute() ตามลำดับที่กำหนดไว้ล่วงหน้า (queue) — retention script
    เรียก execute หลายครั้งต่อเฟส (ดึง detections/events แล้วดึง crop_key)
    """

    def __init__(self, execute_returns):
        self._returns = list(execute_returns)
        self.deleted = []
        self.committed = 0
        self.added = []

    def execute(self, stmt):
        return _FakeScalars(self._returns.pop(0))

    def delete(self, obj):
        self.deleted.append(obj)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.committed += 1


def _event(event_id="e1", thumb_key="wh01/events/e1/snapshot.jpg"):
    return Event(id=event_id, site_id="WH01", camera_id="WH01-GATE-LPR", label="car", start_ts=CUTOFF, thumb_key=thumb_key)


def _face_detection(det_id=1, crop_key="wh01/faces/1.jpg"):
    d = Detection(event_id="e1", kind=DetectionKind.face, ts=CUTOFF, crop_key=crop_key)
    d.id = det_id
    return d


class TestCleanupExpiredFaces(unittest.TestCase):
    def test_deletes_file_then_row(self):
        det = _face_detection()
        session = _FakeSession(execute_returns=[[det]])
        store = _FakeStore()
        report = CleanupReport()

        cleanup_expired_faces(session, store, CUTOFF, dry_run=False, report=report)

        self.assertEqual(store.deleted, ["wh01/faces/1.jpg"])  # ไฟล์ถูกลบ
        self.assertIn(det, session.deleted)  # แถวถูกลบ
        self.assertEqual(report.face_detections_deleted, 1)
        self.assertEqual(report.face_files_deleted, 1)

    def test_file_delete_failure_still_deletes_row(self):
        # ★ PDPA: ลบ crop ไม่ได้ ก็ยังต้องลบแถว (FaceEmbedding เป็นชีวมิติ ห้าม
        # เก็บเกินกำหนดเพราะไฟล์เดียวลบไม่ได้) ไฟล์ค้างให้ find_orphans เก็บ
        det = _face_detection(crop_key="wh01/faces/broken.jpg")
        session = _FakeSession(execute_returns=[[det]])
        store = _FakeStore(fail_keys=["wh01/faces/broken.jpg"])
        report = CleanupReport()

        cleanup_expired_faces(session, store, CUTOFF, dry_run=False, report=report)

        self.assertIn(det, session.deleted)  # ★ ลบแถวถึงแม้ไฟล์ลบไม่ได้
        self.assertEqual(report.face_detections_deleted, 1)
        self.assertEqual(report.face_files_deleted, 0)  # ไฟล์ลบไม่สำเร็จ ไม่นับ
        self.assertEqual(len(report.errors), 1)  # แต่ log error ไว้

    def test_dry_run_does_not_delete_db(self):
        det = _face_detection()
        session = _FakeSession(execute_returns=[[det]])
        store = _FakeStore()
        report = CleanupReport()

        cleanup_expired_faces(session, store, CUTOFF, dry_run=True, report=report)

        self.assertEqual(session.deleted, [])  # dry-run ไม่แตะ DB
        self.assertEqual(session.committed, 0)
        self.assertEqual(report.face_detections_deleted, 1)  # แต่ยังนับว่าจะลบ
        # ★ dry-run ต้องไม่ลบไฟล์จริงด้วย — เครื่องมือลบข้อมูลถาวรตาม PDPA
        # preview ต้องปลอดภัย 100% ไม่แตะอะไรเลย
        self.assertEqual(store.deleted, [])

    def test_detection_without_crop_key_still_counts(self):
        det = _face_detection(crop_key=None)
        session = _FakeSession(execute_returns=[[det]])
        store = _FakeStore()
        report = CleanupReport()

        cleanup_expired_faces(session, store, CUTOFF, dry_run=False, report=report)
        self.assertEqual(report.face_detections_deleted, 1)
        self.assertEqual(report.face_files_deleted, 0)  # ไม่มีไฟล์ให้ลบ
        self.assertIn(det, session.deleted)


class TestCleanupExpiredEvents(unittest.TestCase):
    def test_deletes_event_and_its_detection_files(self):
        event = _event()
        # execute ครั้งที่ 1 = select events, ครั้งที่ 2 = crop_key ของ detections ใน event นั้น
        session = _FakeSession(execute_returns=[[event], ["wh01/crops/v1.jpg"]])
        store = _FakeStore()
        report = CleanupReport()

        cleanup_expired_events(session, store, CUTOFF, dry_run=False, report=report)

        self.assertIn("wh01/events/e1/snapshot.jpg", store.deleted)  # thumb
        self.assertIn("wh01/crops/v1.jpg", store.deleted)  # crop ของ detection
        self.assertIn(event, session.deleted)
        self.assertEqual(report.events_deleted, 1)
        self.assertEqual(report.event_files_deleted, 2)

    def test_partial_file_failure_still_deletes_event(self):
        # ★ PDPA: ลบ crop นึงไม่ได้ ก็ยังต้องลบแถว event (ไม่งั้น metadata ค้าง
        # เกิน retention + ไฟล์ที่ลบสำเร็จไปแล้วจะทำให้ event ชี้ไฟล์หาย) ไฟล์
        # ที่ค้างกลายเป็น orphan ให้ find_orphans เก็บ — thumb (สำเร็จ) นับ 1
        event = _event()
        session = _FakeSession(execute_returns=[[event], ["wh01/crops/broken.jpg"]])
        store = _FakeStore(fail_keys=["wh01/crops/broken.jpg"])
        report = CleanupReport()

        cleanup_expired_events(session, store, CUTOFF, dry_run=False, report=report)

        self.assertIn(event, session.deleted)  # ★ event ถูกลบถึงแม้ crop ลบไม่ได้
        self.assertEqual(report.events_deleted, 1)
        self.assertEqual(report.event_files_deleted, 1)  # thumb ลบสำเร็จ, crop ไม่นับ
        self.assertEqual(len(report.errors), 1)


class TestCleanupExpiredAuditLogs(unittest.TestCase):
    def test_counts_and_deletes(self):
        # execute ครั้งแรก = COUNT(*) (scalar_one คืน 3), ครั้งสอง = delete statement
        session = _FakeSession(execute_returns=[[3], []])
        report = CleanupReport()

        cleanup_expired_audit_logs(session, CUTOFF, dry_run=False, report=report)

        self.assertEqual(report.audit_logs_deleted, 3)
        self.assertGreaterEqual(session.committed, 1)

    def test_dry_run_counts_but_does_not_delete(self):
        session = _FakeSession(execute_returns=[[2]])  # COUNT(*) = 2
        report = CleanupReport()

        cleanup_expired_audit_logs(session, CUTOFF, dry_run=True, report=report)

        self.assertEqual(report.audit_logs_deleted, 2)
        self.assertEqual(session.committed, 0)


class TestFormatReport(unittest.TestCase):
    def test_dry_run_label(self):
        report = CleanupReport(face_detections_deleted=5, events_deleted=10)
        text = format_report(report, dry_run=True)
        self.assertIn("DRY-RUN", text)
        self.assertIn("5", text)
        self.assertIn("10", text)

    def test_errors_shown(self):
        report = CleanupReport(errors=["ลบไฟล์ x ไม่สำเร็จ"])
        text = format_report(report, dry_run=False)
        self.assertIn("error 1", text)
        self.assertIn("ลบจริงแล้ว", text)


if __name__ == "__main__":
    unittest.main()
