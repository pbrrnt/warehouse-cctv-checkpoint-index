"""
เทสต์ scripts/seed_site.py

validate_config() เป็น pure function ทดสอบตรง ๆ ได้เต็มที่ — seed() ต้อง
เขียนลง DB จริง (session.get/add/commit) ใช้ fake session แทน (Camera มี
คอลัมน์ JSONB (zone_config/rtsp_profile) รันบน SQLite จริงไม่ได้ ต่างจาก
plate_registry ใน tests/test_api.py ที่ไม่มี JSONB เลยใช้ SQLite จริงได้)

รันด้วย:  python -m unittest tests.test_seed_site -v
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.seed_site import load_config, seed, validate_config  # noqa: E402
from services.common.db.models import Camera, CameraRole, Site  # noqa: E402


def _valid_config(**camera_overrides) -> dict:
    camera = {
        "id": "WH01-GATE-LPR",
        "name": "ประตูใหญ่ - อ่านป้าย",
        "tier": "A",
        "role": "lpr",
        "nvr_channel": 3,
    }
    camera.update(camera_overrides)
    return {
        "site": {"id": "WH01", "name": "โกดังตัวอย่าง", "nvr_host": "192.168.1.10", "nvr_vendor": "dahua"},
        "cameras": [camera],
    }


class TestValidateConfig(unittest.TestCase):
    def test_valid_config_has_no_errors(self):
        self.assertEqual(validate_config(_valid_config()), [])

    def test_missing_site_id(self):
        config = _valid_config()
        config["site"]["id"] = ""
        errors = validate_config(config)
        self.assertTrue(any("site.id" in e for e in errors))

    def test_no_cameras(self):
        config = _valid_config()
        config["cameras"] = []
        errors = validate_config(config)
        self.assertTrue(any("อย่างน้อย 1 ตัว" in e for e in errors))

    def test_duplicate_camera_id(self):
        config = _valid_config()
        config["cameras"].append(dict(config["cameras"][0]))
        errors = validate_config(config)
        self.assertTrue(any("ซ้ำ" in e for e in errors))

    def test_invalid_tier(self):
        config = _valid_config(tier="Z")
        errors = validate_config(config)
        self.assertTrue(any("tier" in e for e in errors))

    def test_invalid_role(self):
        config = _valid_config(role="not-a-real-role")
        errors = validate_config(config)
        self.assertTrue(any("role" in e for e in errors))

    def test_role_is_optional(self):
        config = _valid_config()
        del config["cameras"][0]["role"]
        self.assertEqual(validate_config(config), [])

    def test_missing_camera_name(self):
        config = _valid_config()
        del config["cameras"][0]["name"]
        errors = validate_config(config)
        self.assertTrue(any("name" in e for e in errors))


class _FakeSession:
    def __init__(self, existing=None):
        self._existing = existing or {}  # {(model, pk): obj}
        self.added = []
        self.committed = False

    def get(self, model, pk):
        return self._existing.get((model, pk))

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.committed = True


class TestSeed(unittest.TestCase):
    def test_creates_new_site_and_camera(self):
        session = _FakeSession()
        config = _valid_config()

        site, cameras = seed(session, config)

        self.assertEqual(site.id, "WH01")
        self.assertEqual(site.name, "โกดังตัวอย่าง")
        self.assertIn(site, session.added)
        self.assertEqual(len(cameras), 1)
        self.assertEqual(cameras[0].id, "WH01-GATE-LPR")
        self.assertEqual(cameras[0].role, CameraRole.lpr)
        self.assertEqual(cameras[0].nvr_channel, 3)
        self.assertIn(cameras[0], session.added)
        self.assertTrue(session.committed)

    def test_updates_existing_site_and_camera_without_duplicating(self):
        existing_site = Site(id="WH01", name="ชื่อเดิม")
        existing_camera = Camera(id="WH01-GATE-LPR", site_id="WH01", name="ชื่อเดิม", tier="B")
        session = _FakeSession(existing={(Site, "WH01"): existing_site, (Camera, "WH01-GATE-LPR"): existing_camera})
        config = _valid_config(name="ชื่อใหม่", tier="A", nvr_channel=5)

        site, cameras = seed(session, config)

        self.assertIs(site, existing_site)
        self.assertEqual(site.name, "โกดังตัวอย่าง")  # site.name มาจาก config['site']['name'] เสมอ
        self.assertIs(cameras[0], existing_camera)
        self.assertEqual(cameras[0].name, "ชื่อใหม่")
        self.assertEqual(cameras[0].tier, "A")
        self.assertEqual(cameras[0].nvr_channel, 5)
        self.assertEqual(session.added, [])  # ไม่ควร add ซ้ำของที่มีอยู่แล้ว

    def test_camera_without_role_stays_none(self):
        session = _FakeSession()
        config = _valid_config()
        del config["cameras"][0]["role"]

        _site, cameras = seed(session, config)
        self.assertIsNone(cameras[0].role)

    def test_zone_config_passed_through(self):
        session = _FakeSession()
        config = _valid_config(zone_config={"outside_zone": "a", "inside_zone": "b"})

        _site, cameras = seed(session, config)
        self.assertEqual(cameras[0].zone_config, {"outside_zone": "a", "inside_zone": "b"})


class TestLoadConfig(unittest.TestCase):
    def test_example_config_file_is_valid(self):
        # ★ ยืนยันว่าไฟล์ตัวอย่างที่แจกจริงใช้ได้ ไม่ใช่แค่ syntax JSON ถูก
        # (กันเคส seed_site.py แก้ field ใหม่แล้วลืมอัปเดตไฟล์ตัวอย่าง)
        path = Path(__file__).resolve().parent.parent / "scripts" / "site_config.example.json"
        config = load_config(path)
        errors = validate_config(config)
        self.assertEqual(errors, [], f"site_config.example.json ไม่ผ่าน validate: {errors}")
        self.assertEqual(len(config["cameras"]), 3)


if __name__ == "__main__":
    unittest.main()
