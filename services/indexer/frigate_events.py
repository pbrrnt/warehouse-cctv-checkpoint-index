"""
แปลง MQTT payload ดิบจาก Frigate (topic frigate/events) เป็นข้อมูลที่ใช้ได้

★ หมายเหตุความน่าเชื่อถือ: เขียนตามสคีมาที่ยืนยันได้จากเอกสาร Frigate และ
GitHub discussions ของโปรเจกต์ (ดู docs/01-architecture.md, docs/08-...) แต่
"ยืนยันจากเอกสาร" ไม่เท่ากับ "ทดสอบกับ Frigate จริงแล้ว" — เครื่องพัฒนาไม่มี
Frigate รันอยู่ให้ทดสอบ ต้องเทียบ payload จริงตอนขึ้นระบบเฟส 2 จริง
(เก็บ payload ตัวอย่างจริงไว้เทียบ แล้วปรับ field ที่นี่ถ้าไม่ตรง)

ฟิลด์ที่ยืนยันแล้วจากเอกสาร: type, before/after, id, camera, label,
sub_label, score, start_time/end_time (unix timestamp วินาที), has_clip,
has_snapshot, zones, entered_zones, current_zones
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class ParsedEvent:
    frigate_id: str
    frigate_camera: str  # ชื่อกล้องตามที่ตั้งใน frigate/config.yml (ขีดล่าง)
    label: str
    sub_label: str | None
    score: float | None
    start_ts: datetime
    end_ts: datetime | None
    has_clip: bool
    entered_zones: list[str]
    raw: dict  # payload ดิบทั้งก้อน — ★ เก็บไว้เสมอ ตาม ADR ห้ามทิ้งข้อมูลดิบ


def frigate_camera_to_db_id(site_id: str, frigate_camera: str) -> str:
    """
    Frigate ไม่รับเครื่องหมาย '-' ในชื่อกล้อง เราจึงตั้งชื่อกล้องใน
    frigate/config.yml ด้วย '_' (เช่น WH01_GATE_LPR) แต่ cameras.id ในฐาน
    ข้อมูลใช้ '-' (เช่น WH01-GATE-LPR) ตามธรรมเนียมที่ตั้งไว้ใน
    docs/09-multi-site-runbook.md — ฟังก์ชันนี้แปลงกลับ

    ★ อาศัยว่าทุกไซต์ตั้งชื่อกล้องตามธรรมเนียมนี้เป๊ะ ๆ ถ้าไม่ตรง (เช่น
    เผลอตั้งชื่อกล้องปนขีดกลาง) ฟังก์ชันนี้จะแปลงผิด — ควรมี unit test/
    integration test เช็กว่าไซต์จริงตั้งชื่อถูกต้องตอนขึ้นระบบ
    """
    return frigate_camera.replace("_", "-")


def _parse_timestamp(value: float | int | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(float(value), tz=timezone.utc)


def _parse_sub_label(value) -> str | None:
    # Frigate บางเวอร์ชันส่ง sub_label เป็น string เฉย ๆ บางเวอร์ชันส่งเป็น
    # [name, score] — รองรับทั้งสองแบบไว้ก่อน ต้องยืนยันกับ Frigate จริงอีกที
    if value is None:
        return None
    if isinstance(value, (list, tuple)) and value:
        return str(value[0])
    return str(value)


def parse_frigate_event(raw_payload: bytes | str | dict) -> ParsedEvent | None:
    """
    รับ payload ดิบจาก MQTT topic frigate/events คืน ParsedEvent เฉพาะ
    เหตุการณ์ที่ "จบแล้ว" (type == "end") เท่านั้น — เหตุการณ์ระหว่างทาง
    (new/update) คืน None เพราะ start_time/end_time/entered_zones ยังไม่นิ่ง

    คืน None ด้วยถ้า payload ผิดรูปแบบ (กัน indexer ล้มเพราะข้อความ MQTT
    เสียหรือมาจากเวอร์ชัน Frigate ที่ schema เปลี่ยน)
    """
    try:
        payload = json.loads(raw_payload) if isinstance(raw_payload, (bytes, str)) else raw_payload

        # JSON ที่ decode ได้แต่ไม่ใช่ object เช่น "null" หรือ "[]" ต้องถือว่า
        # ใช้ไม่ได้เหมือนกัน ไม่ใช่แค่ decode error เท่านั้น
        if not isinstance(payload, dict):
            return None

        if payload.get("type") != "end":
            return None

        after = payload.get("after")
        if not after or not after.get("id"):
            return None

        return ParsedEvent(
            frigate_id=after["id"],
            frigate_camera=after["camera"],
            label=after["label"],
            sub_label=_parse_sub_label(after.get("sub_label")),
            score=after.get("score") or after.get("top_score"),
            start_ts=_parse_timestamp(after.get("start_time")),
            end_ts=_parse_timestamp(after.get("end_time")),
            has_clip=bool(after.get("has_clip", False)),
            entered_zones=list(after.get("entered_zones") or []),
            raw=payload,
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def compute_direction(entered_zones: list[str], outside_zone: str | None, inside_zone: str | None) -> str | None:
    """
    หาทิศทางเข้า/ออกจากลำดับที่ track ผ่านโซน — ดู
    docs/10-chokepoint-design.md เรื่องการวางกล้อง/โซน

    entered_zones เป็นลิสต์ตามลำดับที่ Frigate บันทึกว่า track เข้าโซนไหน
    ก่อนหลัง (ยืนยันจากเอกสาร Frigate ว่าสะสมตามลำดับเวลาเข้าโซนครั้งแรก)

        outside ก่อน inside  -> เข้า ('in')
        inside ก่อน outside  -> ออก ('out')
        เข้าโซนเดียวหรือไม่ครบ -> ไม่ทราบทิศทาง (None)

    outside_zone/inside_zone มาจาก cameras.zone_config ของกล้องนั้น ๆ
    (คีย์ 'outside_zone' / 'inside_zone') ไม่ฮาร์ดโค้ดชื่อโซน เพราะแต่ละ
    ไซต์อาจตั้งชื่อโซนไม่เหมือนกัน
    """
    if not outside_zone or not inside_zone:
        return None
    if outside_zone not in entered_zones or inside_zone not in entered_zones:
        return None

    if entered_zones.index(outside_zone) < entered_zones.index(inside_zone):
        return "in"
    return "out"
