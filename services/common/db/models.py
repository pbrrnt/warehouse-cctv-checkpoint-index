"""
SQLAlchemy ORM models — ตรงตาม docs/04-data-model.md ทุกตาราง

แหล่งความจริงของ schema คือเอกสารนั้น ไฟล์นี้แค่แปลเป็นโค้ด — ถ้าจะแก้
โครงสร้างตาราง แก้เอกสารก่อนแล้วค่อยตามมาแก้ที่นี่ + เขียน migration ใหม่

หมายเหตุสภาพแวดล้อมตอนเขียนไฟล์นี้: เครื่องพัฒนาไม่มี Docker/Postgres ให้ทดสอบ
จริง จึงตรวจสอบได้แค่ (1) โมเดล import และ configure สำเร็จ ไม่มี FK/ความสัมพันธ์
ผิด (2) DDL ที่ compile ออกมาตรงกับ SQL ในเอกสาร — ยังไม่เคยรัน migration จริง
กับ Postgres ต้องทำตอนมีเครื่องเป้าหมาย (ดู db/migrations/README.md)
"""

from __future__ import annotations

import enum
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SqlEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ============================================================
#  ENUM — หัวข้อ 2.2, 2.3, 2.4
# ============================================================

class CameraRole(str, enum.Enum):
    lpr = "lpr"
    overview = "overview"
    face = "face"
    context = "context"


class EventDirection(str, enum.Enum):
    in_ = "in"
    out = "out"


class DetectionKind(str, enum.Enum):
    face = "face"
    vehicle = "vehicle"
    plate = "plate"


# ============================================================
#  2.1 sites — หัวข้อ 2.1
#
#  ★ มีตารางนี้ตั้งแต่วันแรกแม้จะมีไซต์เดียว — เพิ่มทีหลังต้องแก้ทุกคิวรี
# ============================================================

class Site(Base):
    __tablename__ = "sites"

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # 'WH01'
    name: Mapped[str] = mapped_column(Text, nullable=False)
    timezone: Mapped[str] = mapped_column(Text, nullable=False, server_default="Asia/Bangkok")
    nvr_host: Mapped[str | None] = mapped_column(Text)
    nvr_vendor: Mapped[str | None] = mapped_column(Text)  # 'dahua' | 'hikvision' | 'onvif'
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    cameras: Mapped[list["Camera"]] = relationship(back_populates="site")
    events: Mapped[list["Event"]] = relationship(back_populates="site")
    persons: Mapped[list["Person"]] = relationship(back_populates="site")


# ============================================================
#  2.2 cameras — หัวข้อ 2.2
# ============================================================

class Camera(Base):
    __tablename__ = "cameras"

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # 'WH01-GATE-LPR'
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    tier: Mapped[str] = mapped_column(String(1), nullable=False)  # 'A' | 'B' | 'C'
    role: Mapped[CameraRole | None] = mapped_column(SqlEnum(CameraRole, name="camera_role"))
    location: Mapped[str | None] = mapped_column(Text)
    vendor: Mapped[str | None] = mapped_column(Text)
    nvr_channel: Mapped[int | None] = mapped_column(Integer)  # ★ ช่องใน NVR — ไม่รู้ = ค้นแล้วเปิดต่อไม่ได้
    rtsp_profile: Mapped[dict | None] = mapped_column(JSONB)  # ข้อมูลจาก probe_cameras.py
    clock_offset_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")  # ★ ชดเชยเวลากล้องที่เพี้ยน
    zone_config: Mapped[dict | None] = mapped_column(JSONB)  # โซนตรวจทิศทางเข้า/ออก
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    __table_args__ = (
        CheckConstraint("tier IN ('A', 'B', 'C')", name="ck_cameras_tier"),
    )

    site: Mapped[Site] = relationship(back_populates="cameras")
    events: Mapped[list["Event"]] = relationship(back_populates="camera")


# ============================================================
#  2.3 events — หัวข้อ 2.3 (1 tracked object = 1 แถว)
# ============================================================

class Event(Base):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # ใช้ frigate event id ตรง ๆ — idempotent
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), nullable=False)
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id"), nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)  # 'person' | 'car' | 'motorcycle'
    sub_label: Mapped[str | None] = mapped_column(Text)
    score: Mapped[float | None] = mapped_column(Float)
    start_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)  # ★ UTC เสมอ
    end_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    direction: Mapped[EventDirection | None] = mapped_column(SqlEnum(EventDirection, name="event_direction"))
    zones: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    has_clip: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    thumb_key: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict | None] = mapped_column(JSONB)  # payload ดิบจาก Frigate — ★ ห้ามทิ้ง
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    site: Mapped[Site] = relationship(back_populates="events")
    camera: Mapped[Camera] = relationship(back_populates="events")
    detections: Mapped[list["Detection"]] = relationship(back_populates="event", cascade="all, delete-orphan")


# ★ ประกาศ index ไว้นอก class เพราะ 3 ตัวหลังต้องใช้ .desc() ซึ่งต้องมี
# column object ของจริงก่อน (ยังไม่มีตอน class body กำลังรัน) — ดูหัวข้อ 3
# ใน 04-data-model.md สำหรับเหตุผลของแต่ละ index
Event.__table_args__ = (
    Index("idx_events_start_brin", Event.start_ts, postgresql_using="brin"),
    Index("idx_events_camera_ts", Event.camera_id, Event.start_ts.desc()),
    Index("idx_events_label_ts", Event.label, Event.start_ts.desc()),
    Index("idx_events_site_ts", Event.site_id, Event.start_ts.desc()),
)


# ============================================================
#  2.4 detections — หัวข้อ 2.4
# ============================================================

class Detection(Base):
    __tablename__ = "detections"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[DetectionKind] = mapped_column(SqlEnum(DetectionKind, name="detection_kind"), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    bbox: Mapped[list[int] | None] = mapped_column(ARRAY(Integer))  # [x1, y1, x2, y2]
    crop_key: Mapped[str | None] = mapped_column(Text)  # path ใน object store
    quality: Mapped[float | None] = mapped_column(Float)  # คะแนนคุณภาพภาพ (blur, ขนาด, มุม)

    __table_args__ = (
        Index("idx_detections_event", "event_id", "kind"),
    )

    event: Mapped[Event] = relationship(back_populates="detections")
    face_embedding: Mapped["FaceEmbedding | None"] = relationship(back_populates="detection", cascade="all, delete-orphan")
    vehicle: Mapped["Vehicle | None"] = relationship(back_populates="detection", cascade="all, delete-orphan")
    plate: Mapped["Plate | None"] = relationship(back_populates="detection", cascade="all, delete-orphan")


# ============================================================
#  2.5 ใบหน้า — หัวข้อ 2.5
# ============================================================

class Person(Base):
    __tablename__ = "persons"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text)  # NULL = บุคคลนิรนามจาก clustering
    notes: Mapped[str | None] = mapped_column(Text)
    consent_basis: Mapped[str | None] = mapped_column(Text)  # ★ ฐานทางกฎหมายตาม PDPA — ไม่ใช่ฟิลด์ประดับ
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    site: Mapped[Site] = relationship(back_populates="persons")
    face_embeddings: Mapped[list["FaceEmbedding"]] = relationship(back_populates="person")


class FaceEmbedding(Base):
    __tablename__ = "face_embeddings"

    detection_id: Mapped[int] = mapped_column(
        ForeignKey("detections.id", ondelete="CASCADE"), primary_key=True
    )
    embedding: Mapped[list[float]] = mapped_column(Vector(512), nullable=False)  # ArcFace
    person_id: Mapped[int | None] = mapped_column(ForeignKey("persons.id", ondelete="SET NULL"))

    __table_args__ = (
        Index(
            "idx_face_emb_hnsw", "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_with={"m": 16, "ef_construction": 64},
        ),
    )

    detection: Mapped[Detection] = relationship(back_populates="face_embedding")
    person: Mapped[Person | None] = relationship(back_populates="face_embeddings")


# ============================================================
#  2.6 ยานพาหนะ — หัวข้อ 2.6
# ============================================================

class Vehicle(Base):
    __tablename__ = "vehicles"

    detection_id: Mapped[int] = mapped_column(
        ForeignKey("detections.id", ondelete="CASCADE"), primary_key=True
    )
    vehicle_type: Mapped[str | None] = mapped_column(Text)  # 'car' | 'van' | 'truck' | 'bus'
    color: Mapped[str | None] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(256))  # vehicle-reid-0001
    plate_id: Mapped[int | None] = mapped_column(ForeignKey("plates.id", ondelete="SET NULL"))

    __table_args__ = (
        Index(
            "idx_vehicle_emb_hnsw", "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_with={"m": 16, "ef_construction": 64},
        ),
    )

    detection: Mapped[Detection] = relationship(back_populates="vehicle")
    plate: Mapped["Plate | None"] = relationship(foreign_keys=[plate_id])


# ============================================================
#  2.7 ป้ายทะเบียน — หัวข้อ 2.7
# ============================================================

class Plate(Base):
    __tablename__ = "plates"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    detection_id: Mapped[int] = mapped_column(
        ForeignKey("detections.id", ondelete="CASCADE"), nullable=False
    )
    raw_text: Mapped[str | None] = mapped_column(Text)  # ★ ข้อความดิบจาก OCR — ห้ามทิ้ง
    plate_norm: Mapped[str | None] = mapped_column(Text)  # '1กก1234' ไม่มีช่องว่าง
    province: Mapped[str | None] = mapped_column(Text)
    plate_color: Mapped[str | None] = mapped_column(Text)  # จากกล้อง overview
    ocr_conf: Mapped[float | None] = mapped_column(Float)
    char_confs: Mapped[dict | None] = mapped_column(JSONB)  # ความมั่นใจรายตัวอักษร
    flags: Mapped[list[str] | None] = mapped_column(ARRAY(Text))  # ['template_mismatch', ...]

    __table_args__ = (
        # ★ ค้นทะเบียนแบบ fuzzy — สำคัญที่สุด ต้องเปิด extension pg_trgm ก่อน (ดู migration)
        Index(
            "idx_plates_norm_trgm", "plate_norm",
            postgresql_using="gin",
            postgresql_ops={"plate_norm": "gin_trgm_ops"},
        ),
    )

    detection: Mapped[Detection] = relationship(back_populates="plate")


class PlateRegistry(Base):
    """watchlist ทะเบียน"""

    __tablename__ = "plate_registry"

    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), primary_key=True)
    plate_norm: Mapped[str] = mapped_column(Text, primary_key=True)
    owner_label: Mapped[str | None] = mapped_column(Text)  # 'รถส่งของบริษัท ก'
    notes: Mapped[str | None] = mapped_column(Text)
    watch: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")


# ============================================================
#  2.8 PDPA — หัวข้อ 2.8
# ============================================================

class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)  # 'search_face' | 'view_person' | ...
    target_type: Mapped[str | None] = mapped_column(Text)
    target_id: Mapped[str | None] = mapped_column(Text)
    query: Mapped[dict | None] = mapped_column(JSONB)  # เงื่อนไขที่ค้น
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    ip: Mapped[str | None] = mapped_column(INET)


class RetentionPolicy(Base):
    __tablename__ = "retention_policies"

    # 'clips' | 'snapshots' | 'crops' | 'face_embeddings' | 'events'
    scope: Mapped[str] = mapped_column(Text, primary_key=True)
    days: Mapped[int] = mapped_column(Integer, nullable=False)


__all__ = [
    "Base",
    "CameraRole",
    "EventDirection",
    "DetectionKind",
    "Site",
    "Camera",
    "Event",
    "Detection",
    "Person",
    "FaceEmbedding",
    "Vehicle",
    "Plate",
    "PlateRegistry",
    "AuditLog",
    "RetentionPolicy",
]
