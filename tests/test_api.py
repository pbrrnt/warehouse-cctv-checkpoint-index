"""
เทสต์ search API — services/api/*.py

แบ่งเป็น 3 กลุ่ม:
  1. pure function ล้วน (timefmt, rerank, auth logic) — เทสต์ตรง ๆ ไม่ต้องมี
     Postgres หรือแม้แต่ FastAPI request จริง
  2. query builder (search.py build_*) — compile เป็น Postgres SQL text แล้ว
     เช็คโครงสร้าง (join ถูกตาราง, operator ถูก, filter ถูก) ไม่ได้รันจริง
     กับ Postgres (เครื่องพัฒนาไม่มี Docker) เพราะงั้น**ไม่ได้ยืนยันว่าคิวรี
     ทำงานถูกจริง** โดยเฉพาะ pg_trgm operator '%' และ DISTINCT ON
  3. FastAPI route ผ่าน TestClient — mock session ผ่าน
     app.dependency_overrides[get_db] ไม่ต้องมี Postgres จริงเหมือนกัน

รันด้วย:  python -m unittest tests.test_api -v
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.dialects import postgresql  # noqa: E402

from services.api.app import create_app, get_db  # noqa: E402
from services.api.auth import require_api_key  # noqa: E402
from services.api.config import Settings  # noqa: E402
from services.api.search import (  # noqa: E402
    _similarity_from_distance,
    build_now_query,
    build_plate_fuzzy_query,
    build_vehicle_search_query,
    rerank_plate_candidates,
    to_now_entry,
    to_plate_result,
    to_vehicle_result,
)
from services.api.timefmt import nvr_playback_time, to_local_iso  # noqa: E402
from services.common.db.models import Camera, Event, EventDirection, Plate, PlateRegistry, Vehicle  # noqa: E402

TS = datetime(2026, 1, 15, 7, 32, 7, tzinfo=timezone.utc)  # 14:32:07 Asia/Bangkok


def _settings(**overrides) -> Settings:
    base = dict(
        site_id="WH01",
        site_name="โกดังทดสอบ",
        database_url="postgresql+psycopg://x:x@localhost/x",
        api_secret_key="test-secret-key",
        timezone="Asia/Bangkok",
    )
    base.update(overrides)
    return Settings(**base)


class TestSimilarityFromDistance(unittest.TestCase):
    def test_identical_strings_score_one(self):
        self.assertEqual(_similarity_from_distance(0.0, "1กก1234", "1กก1234"), 1.0)

    def test_completely_different_scores_near_zero(self):
        score = _similarity_from_distance(7.0, "1234567", "abcdefg")
        self.assertAlmostEqual(score, 0.0)

    def test_never_goes_negative(self):
        # ระยะห่างมากกว่าความยาวสตริงได้ (insert เยอะ) — ต้อง clamp ที่ 0
        score = _similarity_from_distance(100.0, "1กก1234", "1กก1234")
        self.assertEqual(score, 0.0)


class TestTimefmt(unittest.TestCase):
    def test_converts_to_bangkok_time(self):
        local = to_local_iso(TS, clock_offset_ms=0, tz_name="Asia/Bangkok")
        self.assertTrue(local.startswith("2026-01-15T14:32:07"))

    def test_applies_clock_offset(self):
        # กล้องเดินช้า 5 วินาที (offset ลบ) -> ต้องเลื่อนเวลาที่แสดงให้ตรง
        local = to_local_iso(TS, clock_offset_ms=-5000, tz_name="Asia/Bangkok")
        self.assertTrue(local.startswith("2026-01-15T14:32:02"))

    def test_naive_datetime_rejected(self):
        with self.assertRaises(ValueError):
            to_local_iso(datetime(2026, 1, 15), clock_offset_ms=0, tz_name="Asia/Bangkok")

    def test_nvr_playback_time_looks_back(self):
        # ดู docs/08-nvr-integration.md หัวข้อ 6.1 — เผื่อย้อนหลัง
        local = to_local_iso(TS, 0, "Asia/Bangkok")
        playback = nvr_playback_time(TS, 0, "Asia/Bangkok")
        self.assertLess(playback, local)  # ISO string เทียบ lexicographic ได้เพราะรูปแบบเดียวกัน


class TestRequireApiKey(unittest.TestCase):
    class _FakeAppState:
        def __init__(self, api_secret_key):
            self.settings = _settings(api_secret_key=api_secret_key)

    class _FakeApp:
        def __init__(self, api_secret_key):
            self.state = TestRequireApiKey._FakeAppState(api_secret_key)

    class _FakeRequest:
        def __init__(self, api_secret_key):
            self.app = TestRequireApiKey._FakeApp(api_secret_key)

    def test_correct_key_passes(self):
        request = self._FakeRequest("right-key")
        require_api_key(request, x_api_key="right-key")  # ไม่ raise ถือว่าผ่าน

    def test_missing_key_rejected(self):
        from fastapi import HTTPException
        request = self._FakeRequest("right-key")
        with self.assertRaises(HTTPException) as ctx:
            require_api_key(request, x_api_key=None)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_wrong_key_rejected(self):
        from fastapi import HTTPException
        request = self._FakeRequest("right-key")
        with self.assertRaises(HTTPException):
            require_api_key(request, x_api_key="wrong-key")


class TestQueryBuildersCompile(unittest.TestCase):
    """compile เป็น Postgres SQL text แล้วเช็คโครงสร้าง — ไม่ได้รันจริง"""

    def _sql(self, stmt) -> str:
        return str(stmt.compile(dialect=postgresql.dialect()))

    def test_plate_fuzzy_uses_trgm_operator_and_site_filter(self):
        sql = self._sql(build_plate_fuzzy_query("WH01", "1กก1234", TS, TS + timedelta(days=1)))
        self.assertIn("plates.plate_norm %%", sql)  # pg_trgm '%' operator (escaped %% ใน printed SQL)
        self.assertIn("events.site_id =", sql)
        self.assertIn("ORDER BY similarity", sql)
        self.assertIn("LIMIT", sql)

    def test_vehicle_search_joins_plate_as_outer(self):
        sql = self._sql(build_vehicle_search_query("WH01", TS, TS + timedelta(days=1), color="white"))
        self.assertIn("LEFT OUTER JOIN plates", sql)  # plate อาจไม่มี (ยังไม่เชื่อมโยง) ต้องไม่หายทั้งแถว
        self.assertIn("vehicles.color =", sql)

    def test_vehicle_search_without_filters_has_no_extra_where(self):
        # vehicles.color/vehicle_type ยังโผล่ใน SELECT list เสมอ (เป็นคอลัมน์
        # ของ entity) เช็คเฉพาะว่าไม่มีการเทียบค่า (= ) ใน WHERE ถ้าไม่ได้กรอง
        sql = self._sql(build_vehicle_search_query("WH01", TS, TS + timedelta(days=1)))
        self.assertNotIn("vehicles.color =", sql)
        self.assertNotIn("vehicles.vehicle_type =", sql)

    def test_now_query_uses_distinct_on_and_direction_filter(self):
        sql = self._sql(build_now_query("WH01", TS))
        self.assertIn("DISTINCT ON (plates.plate_norm)", sql)
        self.assertIn("anon_1.direction =", sql)


class TestRerankPlateCandidates(unittest.TestCase):
    def _row(self, plate_norm, camera_id="WH01-GATE-LPR", registry_entry=None):
        plate = Plate(id=1, detection_id=1, plate_norm=plate_norm, raw_text=plate_norm, province="กรุงเทพมหานคร", ocr_conf=0.9)
        event = Event(id="evt-1", site_id="WH01", camera_id=camera_id, label="car", start_ts=TS, direction=EventDirection.in_)
        camera = Camera(id=camera_id, site_id="WH01", name="ประตูใหญ่", tier="A", nvr_channel=3, clock_offset_ms=0)
        return (plate, event, camera, registry_entry, 0.5)  # trgm_sim ไม่ได้ใช้ตอน rerank

    def test_exact_match_ranks_first(self):
        rows = [self._row("1กข1234"), self._row("1กก1234")]
        ranked = rerank_plate_candidates(rows, "1กก1234")
        self.assertEqual(ranked[0][1].plate_norm, "1กก1234")
        self.assertEqual(ranked[0][0], 1.0)

    def test_limit_truncates_results(self):
        rows = [self._row(f"1กก{i:04d}") for i in range(10)]
        ranked = rerank_plate_candidates(rows, "1กก1234", limit=3)
        self.assertEqual(len(ranked), 3)

    def test_registry_entry_passed_through(self):
        registry_entry = PlateRegistry(site_id="WH01", plate_norm="1กก1234", owner_label="รถส่งของบริษัท ก", watch=False)
        rows = [self._row("1กก1234", registry_entry=registry_entry)]
        ranked = rerank_plate_candidates(rows, "1กก1234")
        self.assertIs(ranked[0][4], registry_entry)


class TestResultFormatters(unittest.TestCase):
    def test_to_plate_result(self):
        plate = Plate(id=1, detection_id=1, plate_norm="1กก1234", raw_text="1กก 1234", province="กรุงเทพมหานคร", ocr_conf=0.95)
        event = Event(id="evt-1", site_id="WH01", camera_id="WH01-GATE-LPR", label="car", start_ts=TS, direction=EventDirection.in_)
        camera = Camera(id="WH01-GATE-LPR", site_id="WH01", name="ประตูใหญ่ - อ่านป้าย", tier="A", nvr_channel=3, clock_offset_ms=0)

        result = to_plate_result(0.87, plate, event, camera, "Asia/Bangkok")
        self.assertEqual(result.plate_norm, "1กก1234")
        self.assertEqual(result.match_score, 0.87)
        self.assertEqual(result.camera.nvr_channel, 3)
        self.assertEqual(result.direction, "in")
        self.assertTrue(result.local_time.startswith("2026-01-15T14:32:07"))
        self.assertIsNone(result.owner_label)  # ไม่ได้ส่ง registry_entry มา
        self.assertFalse(result.watch)

    def test_to_plate_result_with_registry_entry(self):
        plate = Plate(id=1, detection_id=1, plate_norm="1กก1234", raw_text="1กก 1234", province="กรุงเทพมหานคร", ocr_conf=0.95)
        event = Event(id="evt-1", site_id="WH01", camera_id="WH01-GATE-LPR", label="car", start_ts=TS, direction=EventDirection.in_)
        camera = Camera(id="WH01-GATE-LPR", site_id="WH01", name="ประตูใหญ่ - อ่านป้าย", tier="A", nvr_channel=3, clock_offset_ms=0)
        registry_entry = PlateRegistry(site_id="WH01", plate_norm="1กก1234", owner_label="รถส่งของบริษัท ก", watch=True)

        result = to_plate_result(0.87, plate, event, camera, "Asia/Bangkok", registry_entry)
        self.assertEqual(result.owner_label, "รถส่งของบริษัท ก")
        self.assertTrue(result.watch)

    def test_to_vehicle_result_handles_null_plate(self):
        vehicle = Vehicle(detection_id=1, vehicle_type="car", color="white")
        event = Event(id="evt-2", site_id="WH01", camera_id="WH01-GATE-OVERVIEW", label="car", start_ts=TS, direction=None)
        camera = Camera(id="WH01-GATE-OVERVIEW", site_id="WH01", name="ประตูใหญ่ - ภาพรวม", tier="A", nvr_channel=4, clock_offset_ms=0)

        result = to_vehicle_result(vehicle, event, camera, None, "Asia/Bangkok")
        self.assertIsNone(result.plate_norm)
        self.assertIsNone(result.direction)

    def test_to_now_entry(self):
        class _Row:
            plate_norm = "1กก1234"
            province = "กรุงเทพมหานคร"
            start_ts = TS

        camera = Camera(id="WH01-GATE-LPR", site_id="WH01", name="ประตูใหญ่", tier="A", nvr_channel=3, clock_offset_ms=0)
        entry = to_now_entry(_Row(), camera, "Asia/Bangkok")
        self.assertEqual(entry.plate_norm, "1กก1234")
        self.assertEqual(entry.camera.name, "ประตูใหญ่")


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def scalars(self):
        return self._rows  # เพียงพอสำหรับ list(...) ใน registry.list_registry


class _FakeSession:
    def __init__(self, rows=()):
        self._rows = rows

    def execute(self, stmt):
        return _FakeResult(self._rows)

    def close(self):
        pass


class TestAppRoutes(unittest.TestCase):
    def _client(self, rows=()):
        settings = _settings()
        app = create_app(settings, session_factory=lambda: None)
        app.dependency_overrides[get_db] = lambda: _FakeSession(rows)
        return TestClient(app)

    def test_health_no_auth_needed(self):
        client = self._client()
        resp = client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["site_id"], "WH01")

    def test_cameras_requires_auth(self):
        client = self._client()
        resp = client.get("/cameras")
        self.assertEqual(resp.status_code, 401)

    def test_cameras_returns_list(self):
        cam = Camera(id="WH01-GATE-LPR", site_id="WH01", name="ประตูใหญ่", tier="A", nvr_channel=3, clock_offset_ms=0, enabled=True)
        client = self._client(rows=[cam])
        resp = client.get("/cameras", headers={"X-API-Key": "test-secret-key"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["id"], "WH01-GATE-LPR")
        self.assertEqual(body[0]["nvr_channel"], 3)

    def test_search_plates_without_key_rejected(self):
        client = self._client()
        resp = client.get("/search/plates", params={"q": "1กก1234"})
        self.assertEqual(resp.status_code, 401)

    def test_search_plates_with_key_returns_results(self):
        plate = Plate(id=1, detection_id=1, plate_norm="1กก1234", raw_text="1กก 1234", province="กรุงเทพมหานคร", ocr_conf=0.95)
        event = Event(id="evt-1", site_id="WH01", camera_id="WH01-GATE-LPR", label="car", start_ts=TS, direction=EventDirection.in_)
        camera = Camera(id="WH01-GATE-LPR", site_id="WH01", name="ประตูใหญ่", tier="A", nvr_channel=3, clock_offset_ms=0)

        client = self._client(rows=[(plate, event, camera, None, 0.9)])
        resp = client.get("/search/plates", params={"q": "1กก1234"}, headers={"X-API-Key": "test-secret-key"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["plate_norm"], "1กก1234")
        self.assertEqual(body[0]["camera"]["nvr_channel"], 3)
        self.assertIsNone(body[0]["owner_label"])

    def test_now_endpoint_requires_auth(self):
        client = self._client()
        resp = client.get("/now")
        self.assertEqual(resp.status_code, 401)

    def test_registry_list_without_key_rejected(self):
        client = self._client()
        resp = client.get("/registry")
        self.assertEqual(resp.status_code, 401)

    def test_registry_list_with_key_returns_entries(self):
        entry = PlateRegistry(site_id="WH01", plate_norm="1กก1234", owner_label="รถส่งของบริษัท ก", notes=None, watch=False)
        client = self._client(rows=[entry])
        resp = client.get("/registry", headers={"X-API-Key": "test-secret-key"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["owner_label"], "รถส่งของบริษัท ก")


class TestRegistryCrudRealSqlite(unittest.TestCase):
    """
    ทดสอบ services/api/registry.py กับ SQLite จริง (in-memory) ไม่ mock —
    ตาราง plate_registry ใช้ type ธรรมดาล้วน (ไม่มี JSONB/pgvector เหมือน
    ตารางอื่น) เลยรันบน SQLite ได้ตรง ๆ ยืนยันว่า session.get() composite
    primary key (site_id, plate_norm), add/commit/refresh/delete ทำงานจริง
    ไม่ใช่แค่ผ่าน mock (ต่างจากที่เหลือของ services/api/ ที่ยังไม่เคยรันกับ
    DB จริงเลยตามที่บันทึกไว้ใน docs/11-testing.md)
    """

    def setUp(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from services.common.db.models import Base, PlateRegistry, Site

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine, tables=[Site.__table__, PlateRegistry.__table__])
        Session = sessionmaker(bind=engine)
        self.session = Session()
        self.session.add(Site(id="WH01", name="โกดังทดสอบ"))
        self.session.commit()

    def test_upsert_creates_new_entry(self):
        from services.api.registry import list_registry, upsert_registry_entry

        upsert_registry_entry(self.session, "WH01", "1กก1234", "รถส่งของบริษัท ก", None, False)
        entries = list_registry(self.session, "WH01")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].owner_label, "รถส่งของบริษัท ก")

    def test_upsert_updates_existing_entry(self):
        from services.api.registry import list_registry, upsert_registry_entry

        upsert_registry_entry(self.session, "WH01", "1กก1234", "ชื่อเดิม", None, False)
        upsert_registry_entry(self.session, "WH01", "1กก1234", "ชื่อใหม่", "หมายเหตุ", True)
        entries = list_registry(self.session, "WH01")
        self.assertEqual(len(entries), 1)  # ยังไม่ซ้ำแถว
        self.assertEqual(entries[0].owner_label, "ชื่อใหม่")
        self.assertTrue(entries[0].watch)

    def test_delete_existing_returns_true(self):
        from services.api.registry import delete_registry_entry, list_registry, upsert_registry_entry

        upsert_registry_entry(self.session, "WH01", "1กก1234", "x", None, False)
        self.assertTrue(delete_registry_entry(self.session, "WH01", "1กก1234"))
        self.assertEqual(list_registry(self.session, "WH01"), [])

    def test_delete_missing_returns_false(self):
        from services.api.registry import delete_registry_entry

        self.assertFalse(delete_registry_entry(self.session, "WH01", "ไม่มีทะเบียนนี้"))

    def test_normalize_registry_plate_converts_thai_digits_and_spaces(self):
        from services.api.registry import normalize_registry_plate

        self.assertEqual(normalize_registry_plate("1กก 1234"), "1กก1234")
        self.assertEqual(normalize_registry_plate("๑กก๑๒๓๔"), "1กก1234")

    def test_normalize_registry_plate_empty_returns_none(self):
        from services.api.registry import normalize_registry_plate

        self.assertIsNone(normalize_registry_plate("   "))


if __name__ == "__main__":
    unittest.main()
