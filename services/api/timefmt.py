"""
แปลงเวลาที่เก็บเป็น UTC ในฐานข้อมูลให้เป็นเวลาที่พนักงานเอาไปเปิดต่อใน NVR
ได้ตรง ๆ — ดู docs/08-nvr-integration.md หัวข้อ 4 (เวลาในหน้าจอผู้ใช้ต้อง
เป็นเวลาท้องถิ่นระดับวินาที + ชดเชย clock_offset_ms ของกล้องตัวนั้น)

★ คืนค่าเป็น ISO-8601 ธรรมดา (ปี ค.ศ.) ไม่ใช่ปี พ.ศ. แบบที่ตัวอย่างในเอกสาร
แสดง — การแปลงเป็นรูปแบบที่พนักงานอ่านง่าย (เช่น พ.ศ., รูปแบบวันที่ไทย)
เป็นหน้าที่ของ web UI ตอนแสดงผล ไม่ใช่ API เพราะ API ควรคืนข้อมูลที่แปลง
กลับได้แน่นอน (ISO-8601) ให้ UI เลือก format เองอีกที
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# ★ ดู docs/08-nvr-integration.md หัวข้อ 6.1 — เผื่อเวลาย้อนหลังตอนไปเปิด NVR
# เพราะระบบบันทึกตอนตรวจพบวัตถุ ซึ่งรถอาจเข้าเฟรมมาก่อนหน้านั้นเล็กน้อย
RECOMMENDED_LOOKBACK_SECONDS = 8


def to_local_iso(ts: datetime, clock_offset_ms: int, tz_name: str) -> str:
    """ts ต้องเป็น timezone-aware (UTC) — เก็บแบบนี้เสมอตาม ADR ในระบบนี้"""
    if ts.tzinfo is None:
        raise ValueError("ts ต้องเป็น timezone-aware datetime (UTC)")
    adjusted = ts + timedelta(milliseconds=clock_offset_ms)
    return adjusted.astimezone(ZoneInfo(tz_name)).isoformat()


def nvr_playback_time(ts: datetime, clock_offset_ms: int, tz_name: str) -> str:
    """เวลาที่แนะนำให้พิมพ์ในช่อง Playback ของ NVR (เผื่อย้อนหลังไว้แล้ว)"""
    adjusted = ts - timedelta(seconds=RECOMMENDED_LOOKBACK_SECONDS)
    return to_local_iso(adjusted, clock_offset_ms, tz_name)
