"""
เทสต์ scripts/find_orphans.py — find_orphans() (pure set logic) และ
scan_disk_keys() (เดินไฟล์จริงใน temp dir เพื่อยืนยัน normalize เป็น '/' —
จุดที่ Windows/Linux ต่างกัน)

รันด้วย:  python -m unittest tests.test_find_orphans -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.find_orphans import find_orphans, format_report, scan_disk_keys  # noqa: E402


class TestFindOrphans(unittest.TestCase):
    def test_orphan_on_disk_not_in_db(self):
        disk = {"a.jpg", "b.jpg", "orphan.jpg"}
        db = {"a.jpg", "b.jpg"}
        orphans, missing = find_orphans(disk, db)
        self.assertEqual(orphans, {"orphan.jpg"})
        self.assertEqual(missing, set())

    def test_missing_in_db_not_on_disk(self):
        disk = {"a.jpg"}
        db = {"a.jpg", "gone.jpg"}
        orphans, missing = find_orphans(disk, db)
        self.assertEqual(orphans, set())
        self.assertEqual(missing, {"gone.jpg"})

    def test_both_directions_at_once(self):
        disk = {"a.jpg", "orphan.jpg"}
        db = {"a.jpg", "gone.jpg"}
        orphans, missing = find_orphans(disk, db)
        self.assertEqual(orphans, {"orphan.jpg"})
        self.assertEqual(missing, {"gone.jpg"})

    def test_perfect_match_no_issues(self):
        keys = {"a.jpg", "b.jpg"}
        orphans, missing = find_orphans(keys, set(keys))
        self.assertEqual(orphans, set())
        self.assertEqual(missing, set())

    def test_empty_inputs(self):
        self.assertEqual(find_orphans(set(), set()), (set(), set()))


class TestScanDiskKeys(unittest.TestCase):
    def test_returns_relative_posix_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # สร้างโครงสร้างซ้อนโฟลเดอร์ เลียนแบบ key จริง wh01/events/e1/snapshot.jpg
            nested = root / "wh01" / "events" / "e1"
            nested.mkdir(parents=True)
            (nested / "snapshot.jpg").write_bytes(b"x")
            (root / "top.jpg").write_bytes(b"y")

            keys = scan_disk_keys(root)

            # ★ ต้องเป็น forward slash เสมอ แม้รันบน Windows
            self.assertIn("wh01/events/e1/snapshot.jpg", keys)
            self.assertIn("top.jpg", keys)
            self.assertTrue(all("\\" not in k for k in keys), f"เจอ backslash ใน key: {keys}")

    def test_ignores_directories_only_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "emptydir").mkdir()
            (root / "file.jpg").write_bytes(b"x")
            keys = scan_disk_keys(root)
            self.assertEqual(keys, {"file.jpg"})

    def test_nonexistent_path_returns_empty(self):
        self.assertEqual(scan_disk_keys("/path/that/does/not/exist/xyz123"), set())

    def test_matches_db_keys_end_to_end(self):
        # ยืนยันว่า key ที่ scan ได้ match กับ key รูปแบบ DB จริง (ทั้ง pipeline)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "wh01" / "events" / "e1").mkdir(parents=True)
            (root / "wh01" / "events" / "e1" / "snapshot.jpg").write_bytes(b"x")
            (root / "wh01" / "orphan.jpg").write_bytes(b"z")

            disk = scan_disk_keys(root)
            db = {"wh01/events/e1/snapshot.jpg"}  # รูปแบบเดียวกับ snapshot_key() ใน worker.py
            orphans, missing = find_orphans(disk, db)
            self.assertEqual(orphans, {"wh01/orphan.jpg"})
            self.assertEqual(missing, set())


class TestFormatReport(unittest.TestCase):
    def test_reports_counts(self):
        text = format_report({"a.jpg"}, {"b.jpg"}, deleted=None)
        self.assertIn("ไฟล์กำพร้า", text)
        self.assertIn("ไฟล์หาย", text)

    def test_shows_deleted_count_when_deleting(self):
        text = format_report({"a.jpg"}, set(), deleted=1)
        self.assertIn("ลบไฟล์กำพร้าไปแล้ว: 1", text)

    def test_no_deleted_line_when_report_only(self):
        text = format_report({"a.jpg"}, set(), deleted=None)
        self.assertNotIn("ลบไฟล์กำพร้าไปแล้ว", text)


if __name__ == "__main__":
    unittest.main()
