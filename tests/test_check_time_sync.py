"""
เทสต์ scripts/check_time_sync.py — เฉพาะส่วนที่ทดสอบได้โดยไม่ต้องมีอุปกรณ์
ONVIF จริง (parse XML, คำนวณ offset, จัดรูปแบบรายงาน, อ่าน device list จาก
config) ★ query_onvif_time() (ยิง HTTP จริง) ไม่มีเทสต์เพราะไม่มีอุปกรณ์ให้
ทดสอบ — ใช้ `python scripts/check_time_sync.py --single <ip>` ทดสอบเองตอน
มีอุปกรณ์จริงแทน

รันด้วย:  python -m unittest tests.test_check_time_sync -v
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.check_time_sync import (  # noqa: E402
    TimeSyncResult,
    compute_offset,
    format_report,
    load_devices,
    parse_onvif_time_response,
)


def _onvif_response_xml(year, month, day, hour, minute, second, envelope_prefix="SOAP-ENV") -> bytes:
    # ★ จงใจใช้ prefix คนละตัวกับที่สคริปต์คาดหวัง (soap) เพื่อยืนยันว่า parse
    # ทำงานตาม namespace URI ไม่ใช่ prefix string (อุปกรณ์แต่ละยี่ห้อตั้ง prefix ไม่เหมือนกัน)
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<{envelope_prefix}:Envelope xmlns:{envelope_prefix}="http://www.w3.org/2003/05/soap-envelope">
  <{envelope_prefix}:Body>
    <tds:GetSystemDateAndTimeResponse xmlns:tds="http://www.onvif.org/ver10/device/wsdl">
      <tds:SystemDateAndTime>
        <tt:UTCDateTime xmlns:tt="http://www.onvif.org/ver10/schema">
          <tt:Time>
            <tt:Hour>{hour}</tt:Hour>
            <tt:Minute>{minute}</tt:Minute>
            <tt:Second>{second}</tt:Second>
          </tt:Time>
          <tt:Date>
            <tt:Year>{year}</tt:Year>
            <tt:Month>{month}</tt:Month>
            <tt:Day>{day}</tt:Day>
          </tt:Date>
        </tt:UTCDateTime>
      </tds:SystemDateAndTime>
    </tds:GetSystemDateAndTimeResponse>
  </{envelope_prefix}:Body>
</{envelope_prefix}:Envelope>"""
    return xml.encode("utf-8")


class TestParseOnvifTimeResponse(unittest.TestCase):
    def test_parses_valid_response(self):
        xml = _onvif_response_xml(2026, 1, 15, 14, 32, 7)
        result = parse_onvif_time_response(xml)
        self.assertEqual(result, datetime(2026, 1, 15, 14, 32, 7, tzinfo=timezone.utc))

    def test_works_regardless_of_envelope_prefix(self):
        # ยืนยันว่า match ด้วย namespace URI ไม่ใช่ prefix (เจอ prefix ต่างกันจริงระหว่างยี่ห้อกล้อง)
        xml = _onvif_response_xml(2026, 6, 1, 0, 0, 0, envelope_prefix="soap")
        result = parse_onvif_time_response(xml)
        self.assertEqual(result, datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc))

    def test_missing_utc_datetime_raises_value_error(self):
        xml = b"""<?xml version="1.0"?><soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope">
        <soap:Body><foo/></soap:Body></soap:Envelope>"""
        with self.assertRaises(ValueError):
            parse_onvif_time_response(xml)

    def test_garbage_xml_raises(self):
        with self.assertRaises(Exception):
            parse_onvif_time_response(b"not xml at all")


class TestComputeOffset(unittest.TestCase):
    def test_zero_offset_when_perfectly_synced(self):
        sent_wall = 1_800_000_000.0  # ค่าคงที่ตามอำเภอใจ แค่ต้องคำนวณกลับมาตรงกัน
        round_trip = 0.2
        device_utc = datetime.fromtimestamp(sent_wall + round_trip / 2, tz=timezone.utc)
        self.assertAlmostEqual(compute_offset(device_utc, sent_wall, round_trip), 0.0, places=3)

    def test_positive_offset_when_device_ahead(self):
        sent_wall = 1_800_000_000.0
        round_trip = 0.0
        device_utc = datetime.fromtimestamp(sent_wall + 5.0, tz=timezone.utc)  # เร็วกว่า 5 วิ
        self.assertAlmostEqual(compute_offset(device_utc, sent_wall, round_trip), 5.0, places=3)

    def test_negative_offset_when_device_behind(self):
        sent_wall = 1_800_000_000.0
        round_trip = 0.0
        device_utc = datetime.fromtimestamp(sent_wall - 3.0, tz=timezone.utc)  # ช้ากว่า 3 วิ
        self.assertAlmostEqual(compute_offset(device_utc, sent_wall, round_trip), -3.0, places=3)


class TestFormatReport(unittest.TestCase):
    def test_all_ok_no_failures_in_summary(self):
        results = [
            TimeSyncResult("NVR", "192.168.1.10", True, 0.3, None),
            TimeSyncResult("WH01-GATE-LPR", "192.168.1.51", True, -0.5, None),
        ]
        report = format_report(results, threshold=2.0)
        self.assertIn("ผ่านทุกอุปกรณ์", report)
        self.assertNotIn("เกินเกณฑ์", report)

    def test_device_over_threshold_flagged(self):
        results = [TimeSyncResult("WH01-DOOR-FACE", "192.168.1.53", True, 14.2, None)]
        report = format_report(results, threshold=2.0)
        self.assertIn("เกินเกณฑ์", report)
        self.assertIn("1 อุปกรณ์ไม่ผ่านเกณฑ์", report)

    def test_error_device_counts_as_failure(self):
        results = [TimeSyncResult("WH01-GATE-LPR", "192.168.1.51", False, None, "Connection refused")]
        report = format_report(results, threshold=2.0)
        self.assertIn("ERROR", report)
        self.assertIn("Connection refused", report)
        self.assertIn("1 อุปกรณ์ไม่ผ่านเกณฑ์", report)

    def test_boundary_exactly_at_threshold_passes(self):
        # ★ เกณฑ์คือ "เกิน" ไม่ใช่ "เกินหรือเท่ากับ" — พอดี 2.0 วิต้องผ่าน
        results = [TimeSyncResult("cam", "1.2.3.4", True, 2.0, None)]
        report = format_report(results, threshold=2.0)
        self.assertIn("ผ่านทุกอุปกรณ์", report)


class TestLoadDevices(unittest.TestCase):
    def test_includes_nvr_and_cameras_with_ip(self):
        config = {
            "site": {"nvr_host": "192.168.1.10"},
            "cameras": [
                {"id": "WH01-GATE-LPR", "ip": "192.168.1.51"},
                {"id": "WH01-GATE-OVERVIEW", "ip": "192.168.1.52"},
            ],
        }
        devices = load_devices(config)
        self.assertEqual(len(devices), 3)
        self.assertTrue(any("192.168.1.10" in d[0] for d in devices))
        self.assertIn(("WH01-GATE-LPR", "192.168.1.51"), devices)

    def test_camera_without_ip_skipped_not_crashed(self):
        config = {
            "site": {"nvr_host": "192.168.1.10"},
            "cameras": [{"id": "WH01-NO-IP"}, {"id": "WH01-GATE-LPR", "ip": "192.168.1.51"}],
        }
        devices = load_devices(config)
        device_ids = [d[0] for d in devices]
        self.assertNotIn("WH01-NO-IP", device_ids)
        self.assertIn("WH01-GATE-LPR", device_ids)

    def test_no_nvr_host_skips_nvr_entry(self):
        config = {"site": {}, "cameras": [{"id": "WH01-GATE-LPR", "ip": "192.168.1.51"}]}
        devices = load_devices(config)
        self.assertEqual(len(devices), 1)

    def test_example_config_has_ip_on_every_camera(self):
        # ★ กันเคส site_config.example.json แก้แล้วลืมใส่ ip ให้กล้องตัวใหม่
        import json

        path = Path(__file__).resolve().parent.parent / "scripts" / "site_config.example.json"
        with open(path, encoding="utf-8") as f:
            config = json.load(f)
        devices = load_devices(config)
        # nvr_host 1 + กล้อง 3 ตัวในไฟล์ตัวอย่าง = 4 อุปกรณ์ ไม่มีตัวไหนถูกข้าม
        self.assertEqual(len(devices), 4)


if __name__ == "__main__":
    unittest.main()
