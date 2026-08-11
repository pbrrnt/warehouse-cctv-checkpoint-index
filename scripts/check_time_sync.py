#!/usr/bin/env python3
"""
ตรวจสุขภาพเวลา — เทียบเวลาของ NVR + กล้องทุกตัวกับเวลาเครื่องนี้ (แหล่งอ้างอิง)
ผ่าน ONVIF `GetSystemDateAndTime` (เป็นหนึ่งในไม่กี่คำสั่ง ONVIF ที่**ไม่ต้อง
authenticate** ตามสเปก — ออกแบบมาให้ sync เวลาได้ก่อนจะ auth ด้วยซ้ำ) ดู
docs/08-nvr-integration.md หัวข้อ 7 สำหรับสเปกเต็ม

★ ยังไม่เคยทดสอบกับอุปกรณ์ ONVIF จริงเลย (ไม่มีกล้อง/NVR ให้ทดสอบตอนเขียน)
โครงสร้าง SOAP envelope/namespace เป็นไปตามสเปก ONVIF มาตรฐาน (ver10/device/wsdl)
แต่กล้อง/NVR บางยี่ห้อ/รุ่นอาจตอบไม่ตรงสเปกเป๊ะ — ใช้ --single <ip> ทดสอบทีละ
ตัวแยกได้ตอนมีอุปกรณ์จริงแล้ว ก่อนรันเต็มรูปแบบกับทุกอุปกรณ์

รายชื่ออุปกรณ์ (NVR + กล้อง) อ่านจากไฟล์ config เดียวกับ seed_site.py (ต้องมี
ฟิลด์ "ip" เพิ่มต่อกล้อง — ดู scripts/site_config.example.json) ไม่ได้อ่าน
จากตาราง cameras ใน DB เพราะ cameras.rtsp_profile ยังไม่มีอะไรเขียนเข้าจริง
(probe_cameras.py ยังไม่ต่อเข้า DB)

ใช้งาน:
  python scripts/check_time_sync.py --config site_config.json [--database-url "$DATABASE_URL"]
  python scripts/check_time_sync.py --single 192.168.1.51          # เทสต์อุปกรณ์เดียว ไม่ต้องมี config/DB
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

DEFAULT_THRESHOLD_SECONDS = 2.0
ONVIF_TIMEOUT_SECONDS = 5.0

_GET_TIME_ENVELOPE = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope" '
    'xmlns:tds="http://www.onvif.org/ver10/device/wsdl">'
    "<soap:Body><tds:GetSystemDateAndTime/></soap:Body>"
    "</soap:Envelope>"
)

_NS = {
    "soap": "http://www.w3.org/2003/05/soap-envelope",
    "tt": "http://www.onvif.org/ver10/schema",
}


@dataclass
class TimeSyncResult:
    device_id: str
    ip: str
    ok: bool
    offset_seconds: float | None
    error: str | None


def parse_onvif_time_response(xml_bytes: bytes) -> datetime:
    """
    parse SOAP response ของ GetSystemDateAndTime คืน UTC datetime ของอุปกรณ์
    แยกออกมาจากส่วนที่ยิง HTTP เพื่อทดสอบด้วย response ตัวอย่างได้โดยไม่ต้อง
    มีอุปกรณ์จริง
    """
    root = ET.fromstring(xml_bytes)
    utc_time = root.find(".//tt:UTCDateTime", _NS)
    if utc_time is None:
        raise ValueError("ไม่พบ UTCDateTime ใน response — อุปกรณ์นี้อาจไม่รองรับ ONVIF หรือ path ผิด")

    date_el = utc_time.find("tt:Date", _NS)
    time_el = utc_time.find("tt:Time", _NS)
    if date_el is None or time_el is None:
        raise ValueError("response มี UTCDateTime แต่ไม่มี Date/Time ครบ — รูปแบบไม่ตรงสเปก ONVIF")

    year = int(date_el.find("tt:Year", _NS).text)
    month = int(date_el.find("tt:Month", _NS).text)
    day = int(date_el.find("tt:Day", _NS).text)
    hour = int(time_el.find("tt:Hour", _NS).text)
    minute = int(time_el.find("tt:Minute", _NS).text)
    second = int(time_el.find("tt:Second", _NS).text)

    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


def query_onvif_time(ip: str, port: int = 80, timeout: float = ONVIF_TIMEOUT_SECONDS) -> tuple[datetime, float]:
    """ยิง ONVIF GetSystemDateAndTime จริง — คืน (เวลา UTC ของอุปกรณ์, round-trip วินาที)"""
    url = f"http://{ip}:{port}/onvif/device_service"
    sent_at = time.monotonic()
    resp = requests.post(
        url,
        data=_GET_TIME_ENVELOPE.encode("utf-8"),
        headers={"Content-Type": "application/soap+xml; charset=utf-8"},
        timeout=timeout,
    )
    round_trip = time.monotonic() - sent_at
    resp.raise_for_status()
    return parse_onvif_time_response(resp.content), round_trip


def compute_offset(device_utc: datetime, request_sent_wall: float, round_trip: float) -> float:
    """
    หักลบ network round-trip ออกก่อนเทียบ (ดู docs หัวข้อ 7.1 ข้อ 4) — สมมติว่า
    เวลาที่อุปกรณ์ตอบตรงกับ "กึ่งกลาง" ของ round-trip (network ไป/กลับสมมาตร
    โดยประมาณ ใช้ได้ดีพอสำหรับ LAN ภายในไซต์เดียวกัน)
    """
    local_utc_at_response = datetime.fromtimestamp(request_sent_wall + round_trip / 2, tz=timezone.utc)
    return (device_utc - local_utc_at_response).total_seconds()


def check_device(device_id: str, ip: str) -> TimeSyncResult:
    try:
        sent_wall = time.time()
        device_utc, round_trip = query_onvif_time(ip)
        offset = compute_offset(device_utc, sent_wall, round_trip)
        return TimeSyncResult(device_id=device_id, ip=ip, ok=True, offset_seconds=offset, error=None)
    except Exception as e:  # noqa: BLE001 — ต้องรายงาน error ของทุกอุปกรณ์ ไม่ใช่ให้ทั้งสคริปต์ล้มเพราะตัวเดียว
        return TimeSyncResult(device_id=device_id, ip=ip, ok=False, offset_seconds=None, error=str(e))


def format_report(results: list[TimeSyncResult], threshold: float) -> str:
    lines = [f"{'อุปกรณ์':<28}{'ส่วนต่าง':>12}   สถานะ", "─" * 55]
    fail_count = 0
    for r in results:
        if not r.ok:
            lines.append(f"{r.device_id:<28}{'—':>12}   ★ ERROR: {r.error}")
            fail_count += 1
            continue
        exceeded = abs(r.offset_seconds) > threshold
        sign = "+" if r.offset_seconds >= 0 else ""
        status = "★ เกินเกณฑ์" if exceeded else "OK"
        if exceeded:
            fail_count += 1
        lines.append(f"{r.device_id:<28}{sign}{r.offset_seconds:.1f} วิ{'':>7}   {status}")
    lines.append("─" * 55)
    lines.append(f"สรุป: {fail_count} อุปกรณ์ไม่ผ่านเกณฑ์" if fail_count else "สรุป: ผ่านทุกอุปกรณ์")
    return "\n".join(lines)


def load_devices(config: dict) -> list[tuple[str, str]]:
    """คืน list ของ (device_id, ip) จาก config — NVR (ถ้ามี nvr_host) + กล้อง
    ทุกตัวที่มีฟิลด์ ip (กล้องที่ไม่มี ip จะข้ามไปพร้อม warning ไม่ทำให้สคริปต์พัง)"""
    devices: list[tuple[str, str]] = []
    site = config.get("site", {})
    if site.get("nvr_host"):
        devices.append((f"NVR ({site['nvr_host']})", site["nvr_host"]))
    for cam in config.get("cameras", []):
        if cam.get("ip"):
            devices.append((cam["id"], cam["ip"]))
        else:
            print(f"[WARN] ข้าม {cam.get('id', '?')} — ไม่มีฟิลด์ ip ใน config", file=sys.stderr)
    return devices


def write_audit_log(database_url: str, results: list[TimeSyncResult]) -> None:
    """เขียนผลลง audit_log เพื่อดูแนวโน้มย้อนหลัง (ดู docs หัวข้อ 7.1 ข้อ 6) —
    เขียนไม่ได้ก็แค่เตือน ไม่ทำให้รายงาน/exit code ของการรันครั้งนี้เสียไปด้วย"""
    try:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from services.common.db.models import AuditLog

        engine = create_engine(database_url, pool_pre_ping=True)
        session_factory = sessionmaker(bind=engine)
        with session_factory() as session:
            for r in results:
                session.add(
                    AuditLog(
                        actor="check_time_sync.py",
                        action="time_sync_check",
                        target_type="device",
                        target_id=r.device_id,
                        query={"offset_seconds": r.offset_seconds, "ok": r.ok, "error": r.error},
                        ip=r.ip,
                    )
                )
            session.commit()
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] เขียน audit_log ไม่สำเร็จ: {e} (รายงานด้านบนยังใช้ได้ปกติ)", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, help="path ไฟล์ site config (ดู site_config.example.json)")
    parser.add_argument("--database-url", help="ถ้าใส่ จะเขียนผลลง audit_log ด้วย")
    parser.add_argument("--single", metavar="IP", help="เทสต์อุปกรณ์เดียวตรง ๆ ไม่ต้องมี --config/--database-url")
    parser.add_argument("--threshold-seconds", type=float, default=DEFAULT_THRESHOLD_SECONDS)
    args = parser.parse_args()

    if args.single:
        results = [check_device(args.single, args.single)]
    elif args.config:
        with open(args.config, encoding="utf-8") as f:
            config = json.load(f)
        devices = load_devices(config)
        if not devices:
            print("[FAIL] ไม่มีอุปกรณ์ให้ตรวจเลย (ไม่มี nvr_host และไม่มีกล้องที่มี ip ใน config)")
            return 1
        results = [check_device(device_id, ip) for device_id, ip in devices]
    else:
        parser.error("ต้องระบุ --config หรือ --single อย่างใดอย่างหนึ่ง")
        return 2  # ไม่ถึงตรงนี้จริง (parser.error exit เอง) แต่กันไว้เผื่อ argparse เปลี่ยนพฤติกรรม

    print(format_report(results, args.threshold_seconds))

    if args.database_url:
        write_audit_log(args.database_url, results)

    any_failed = any((not r.ok) or (abs(r.offset_seconds) > args.threshold_seconds) for r in results)
    return 1 if any_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
