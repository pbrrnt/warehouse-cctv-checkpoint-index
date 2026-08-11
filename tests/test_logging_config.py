"""
เทสต์ services/common/logging_config.py

★ setup_logging() แก้ state ของ root logger (global process-wide) ตรง ๆ —
ทุกเทสต์ต้อง snapshot + restore handlers เดิมกลับ ไม่งั้นจะไปกวน test
module อื่นที่ log ผ่าน root logger เหมือนกัน (worker.py, search.py ฯลฯ)
และต้องปิด FileHandler **ก่อน** ที่ tempfile.TemporaryDirectory จะลบโฟลเดอร์
ตอนออกจาก `with` — ไม่งั้นบน Windows shutil.rmtree จะพังเพราะไฟล์ยังถูก
process นี้ lock อยู่ (ต่างจาก POSIX ที่ unlink ไฟล์ที่เปิดอยู่ได้เฉย ๆ)

รันด้วย:  python -m unittest tests.test_logging_config -v
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.common.logging_config import setup_logging  # noqa: E402


class TestSetupLogging(unittest.TestCase):
    def setUp(self):
        self._root = logging.getLogger()
        self._original_handlers = list(self._root.handlers)
        self._original_level = self._root.level
        os.environ.pop("LOG_DIR", None)

    def tearDown(self):
        self._close_new_handlers()
        self._root.setLevel(self._original_level)
        os.environ.pop("LOG_DIR", None)

    def _close_new_handlers(self):
        for h in list(self._root.handlers):
            if h not in self._original_handlers:
                h.close()
                self._root.removeHandler(h)

    def test_without_log_dir_only_adds_console_handler(self):
        setup_logging("testsvc")
        new_handlers = [h for h in self._root.handlers if h not in self._original_handlers]
        self.assertEqual(len(new_handlers), 1)
        self.assertIsInstance(new_handlers[0], logging.StreamHandler)

    def test_with_log_dir_creates_file_and_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["LOG_DIR"] = tmp
            setup_logging("testsvc")
            logging.getLogger("testsvc.mod").warning("สวัสดี")

            log_path = Path(tmp) / "testsvc.log"
            self.assertTrue(log_path.exists())
            self.assertIn("สวัสดี", log_path.read_text(encoding="utf-8"))

            self._close_new_handlers()  # ★ ปิดก่อน temp dir จะถูกลบ (สำคัญบน Windows)

    def test_creates_log_dir_if_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            nested = Path(tmp) / "nested" / "logs"
            os.environ["LOG_DIR"] = str(nested)
            setup_logging("testsvc")
            self.assertTrue(nested.exists())
            self._close_new_handlers()

    def test_unwritable_path_does_not_raise(self):
        # LOG_DIR ชี้ไปที่ "โฟลเดอร์" ที่จริง ๆ เป็นไฟล์ธรรมดา — mkdir(parents=True) ต้องพัง
        # แต่ setup_logging ต้องจับไว้ ไม่ปล่อยให้ service เริ่มไม่ได้เพราะ logging
        with tempfile.TemporaryDirectory() as tmp:
            blocked = Path(tmp) / "not-a-dir"
            blocked.write_text("x", encoding="utf-8")
            os.environ["LOG_DIR"] = str(blocked)
            try:
                setup_logging("testsvc")
            except Exception as e:  # noqa: BLE001
                self.fail(f"setup_logging raised unexpectedly: {e}")
            self._close_new_handlers()


if __name__ == "__main__":
    unittest.main()
