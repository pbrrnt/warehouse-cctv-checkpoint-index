"""
เทสต์ scripts/link_vehicle_plates.py — เน้น match_vehicles_to_plates() (pure
function หัวใจของความถูกต้อง) ให้ครบทุกเคสกำกวม + apply_matches (mock session)

รันด้วย:  python -m unittest tests.test_link_vehicle_plates -v
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.link_vehicle_plates import (  # noqa: E402
    PlateRecord,
    VehicleRecord,
    apply_matches,
    match_vehicles_to_plates,
)

BASE = datetime(2026, 1, 15, 8, 0, 0, tzinfo=timezone.utc)


def _v(det_id, offset_sec=0.0, camera="WH01-GATE-OVERVIEW"):
    return VehicleRecord(vehicle_detection_id=det_id, camera_id=camera, ts=BASE + timedelta(seconds=offset_sec))


def _p(plate_id, offset_sec=0.0, camera="WH01-GATE-LPR"):
    return PlateRecord(plate_id=plate_id, camera_id=camera, ts=BASE + timedelta(seconds=offset_sec))


class TestMatchVehiclesToPlates(unittest.TestCase):
    def test_simple_one_to_one_match(self):
        vehicles = [_v(1, 0.0)]
        plates = [_p(100, 1.0)]  # ห่าง 1 วิ อยู่ในกรอบ 3 วิ
        result = match_vehicles_to_plates(vehicles, plates, window_seconds=3.0)
        self.assertEqual(result, {1: 100})

    def test_no_match_outside_window(self):
        vehicles = [_v(1, 0.0)]
        plates = [_p(100, 10.0)]  # ห่าง 10 วิ เกินกรอบ
        result = match_vehicles_to_plates(vehicles, plates, window_seconds=3.0)
        self.assertEqual(result, {})

    def test_vehicle_with_two_candidate_plates_is_ambiguous(self):
        # ★ รถ 1 คัน แต่มีป้าย 2 ใบในกรอบเวลา — กำกวม ไม่เดา
        vehicles = [_v(1, 0.0)]
        plates = [_p(100, 0.5), _p(101, 1.0)]
        result = match_vehicles_to_plates(vehicles, plates, window_seconds=3.0)
        self.assertEqual(result, {})

    def test_plate_with_two_candidate_vehicles_is_ambiguous(self):
        # ★ ป้าย 1 ใบ แต่มีรถ 2 คันในกรอบ (รถตามกันมาติด ๆ) — กำกวมฝั่งกลับ ไม่เดา
        vehicles = [_v(1, 0.0), _v(2, 1.0)]
        plates = [_p(100, 0.5)]
        result = match_vehicles_to_plates(vehicles, plates, window_seconds=3.0)
        self.assertEqual(result, {})

    def test_two_clearly_separated_pairs_both_match(self):
        # รถ+ป้าย 2 คู่ ห่างกันมากพอ (คนละช่วงเวลา) — จับคู่ได้ทั้งคู่
        vehicles = [_v(1, 0.0), _v(2, 60.0)]
        plates = [_p(100, 0.5), _p(101, 60.5)]
        result = match_vehicles_to_plates(vehicles, plates, window_seconds=3.0)
        self.assertEqual(result, {1: 100, 2: 101})

    def test_boundary_exactly_at_window_matches(self):
        vehicles = [_v(1, 0.0)]
        plates = [_p(100, 3.0)]  # พอดี 3.0 วิ — เกณฑ์คือ <= ต้องเข้า
        result = match_vehicles_to_plates(vehicles, plates, window_seconds=3.0)
        self.assertEqual(result, {1: 100})

    def test_negative_offset_within_window(self):
        # ป้ายมาก่อนรถ (lpr เห็นก่อน overview) — abs() ต้องรองรับทั้งสองทิศ
        vehicles = [_v(1, 2.0)]
        plates = [_p(100, 0.0)]
        result = match_vehicles_to_plates(vehicles, plates, window_seconds=3.0)
        self.assertEqual(result, {1: 100})

    def test_empty_inputs(self):
        self.assertEqual(match_vehicles_to_plates([], [], 3.0), {})
        self.assertEqual(match_vehicles_to_plates([_v(1)], [], 3.0), {})
        self.assertEqual(match_vehicles_to_plates([], [_p(100)], 3.0), {})

    def test_camera_pairs_filter_blocks_cross_gate(self):
        # รถประตู A (overview) กับป้ายประตู B (lpr) เวลาใกล้กัน แต่คนละจุดคอขวด
        vehicles = [_v(1, 0.0, camera="WH01-GATEA-OVERVIEW")]
        plates = [_p(100, 0.5, camera="WH01-GATEB-LPR")]
        pairs = {("WH01-GATEA-OVERVIEW", "WH01-GATEA-LPR")}  # จับคู่ได้เฉพาะ A↔A
        result = match_vehicles_to_plates(vehicles, plates, window_seconds=3.0, camera_pairs=pairs)
        self.assertEqual(result, {})  # ถูกบล็อกเพราะข้ามประตู

    def test_camera_pairs_allows_matched_gate(self):
        vehicles = [_v(1, 0.0, camera="WH01-GATEA-OVERVIEW")]
        plates = [_p(100, 0.5, camera="WH01-GATEA-LPR")]
        pairs = {("WH01-GATEA-OVERVIEW", "WH01-GATEA-LPR")}
        result = match_vehicles_to_plates(vehicles, plates, window_seconds=3.0, camera_pairs=pairs)
        self.assertEqual(result, {1: 100})


class _FakeVehicle:
    def __init__(self, detection_id):
        self.detection_id = detection_id
        self.plate_id = None


class _FakeSession:
    def __init__(self, vehicles_by_id=None):
        self._vehicles = vehicles_by_id or {}
        self.committed = 0

    def get(self, model, pk):
        return self._vehicles.get(pk)

    def commit(self):
        self.committed += 1


class TestApplyMatches(unittest.TestCase):
    def test_dry_run_does_not_write(self):
        session = _FakeSession({1: _FakeVehicle(1)})
        count = apply_matches(session, {1: 100}, dry_run=True)
        self.assertEqual(count, 1)  # นับว่าจะจับคู่
        self.assertIsNone(session._vehicles[1].plate_id)  # แต่ไม่เขียนจริง
        self.assertEqual(session.committed, 0)

    def test_execute_writes_plate_id(self):
        veh = _FakeVehicle(1)
        session = _FakeSession({1: veh})
        count = apply_matches(session, {1: 100}, dry_run=False)
        self.assertEqual(count, 1)
        self.assertEqual(veh.plate_id, 100)
        self.assertEqual(session.committed, 1)

    def test_missing_vehicle_skipped(self):
        # รถถูกลบไประหว่างทาง (retention) — ต้องข้าม ไม่ crash
        session = _FakeSession({})  # get() คืน None
        count = apply_matches(session, {1: 100}, dry_run=False)
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
