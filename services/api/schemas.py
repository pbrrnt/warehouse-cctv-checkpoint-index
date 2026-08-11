"""Pydantic response model — รูปร่าง JSON ที่ web UI จะได้รับ"""

from __future__ import annotations

from pydantic import BaseModel


class CameraInfo(BaseModel):
    id: str
    name: str
    nvr_channel: int | None


class PlateSearchResult(BaseModel):
    event_id: str
    plate_norm: str | None
    raw_text: str | None
    province: str | None
    ocr_conf: float | None
    match_score: float  # 0-1 ยิ่งสูงยิ่งตรงกับคำค้น (มาจาก weighted_edit_distance)
    camera: CameraInfo
    direction: str | None
    local_time: str  # ISO-8601 เวลาที่ระบบตรวจพบ (ชดเชย clock_offset แล้ว)
    nvr_playback_time: str  # ISO-8601 เวลาที่แนะนำให้พิมพ์ใน NVR (เผื่อย้อนหลัง)
    owner_label: str | None = None  # จาก plate_registry ถ้ามี — เช่น 'รถส่งของบริษัท ก'
    watch: bool = False  # ธงเฝ้าระวังจาก plate_registry


class RegistryEntry(BaseModel):
    plate_norm: str
    owner_label: str | None
    notes: str | None
    watch: bool


class RegistryUpsertRequest(BaseModel):
    owner_label: str | None = None
    notes: str | None = None
    watch: bool = False


class VehicleSearchResult(BaseModel):
    event_id: str
    vehicle_type: str | None
    color: str | None
    plate_norm: str | None  # NULL ได้ — ยังไม่มีการเชื่อมโยงข้าม event กับกล้อง LPR (ดู docstring search.py)
    camera: CameraInfo
    direction: str | None
    local_time: str
    nvr_playback_time: str


class NowEntry(BaseModel):
    plate_norm: str
    province: str | None
    camera: CameraInfo
    local_time: str
