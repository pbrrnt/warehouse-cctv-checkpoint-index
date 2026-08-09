"""initial schema

สร้างตารางทั้งหมดตาม docs/04-data-model.md — ทุกตาราง, ENUM, extension,
และ index (รวม HNSW/GIN trgm/BRIN) ตรงตามที่เอกสารระบุ

Revision ID: 0001
Revises:
Create Date: 2026-08-09
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------
    #  1. ส่วนขยาย — docs/04-data-model.md หัวข้อ 1
    # ------------------------------------------------------------
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gin")

    # ------------------------------------------------------------
    #  ENUM — ต้องสร้างก่อนตารางที่ใช้ แล้วปิด auto-create ตอน
    #  create_table (create_type=False) ไม่งั้น SQLAlchemy จะพยายาม
    #  CREATE TYPE ซ้ำแล้ว error
    # ------------------------------------------------------------
    camera_role = postgresql.ENUM("lpr", "overview", "face", "context", name="camera_role")
    event_direction = postgresql.ENUM("in", "out", name="event_direction")
    detection_kind = postgresql.ENUM("face", "vehicle", "plate", name="detection_kind")
    bind = op.get_bind()
    camera_role.create(bind, checkfirst=True)
    event_direction.create(bind, checkfirst=True)
    detection_kind.create(bind, checkfirst=True)

    # ------------------------------------------------------------
    #  2.1 sites
    # ------------------------------------------------------------
    op.create_table(
        "sites",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("timezone", sa.Text(), nullable=False, server_default="Asia/Bangkok"),
        sa.Column("nvr_host", sa.Text()),
        sa.Column("nvr_vendor", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # ------------------------------------------------------------
    #  2.2 cameras
    # ------------------------------------------------------------
    op.create_table(
        "cameras",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("site_id", sa.Text(), sa.ForeignKey("sites.id"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("tier", sa.String(1), nullable=False),
        sa.Column("role", postgresql.ENUM(name="camera_role", create_type=False)),
        sa.Column("location", sa.Text()),
        sa.Column("vendor", sa.Text()),
        sa.Column("nvr_channel", sa.Integer()),  # ★ ช่องใน NVR — ไม่รู้ = ค้นแล้วเปิดต่อไม่ได้
        sa.Column("rtsp_profile", postgresql.JSONB()),
        sa.Column("clock_offset_ms", sa.Integer(), nullable=False, server_default="0"),  # ★ ชดเชยเวลากล้องที่เพี้ยน
        sa.Column("zone_config", postgresql.JSONB()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.CheckConstraint("tier IN ('A', 'B', 'C')", name="ck_cameras_tier"),
    )

    # ------------------------------------------------------------
    #  2.3 events — id ใช้ frigate event id ตรง ๆ (idempotent)
    # ------------------------------------------------------------
    op.create_table(
        "events",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("site_id", sa.Text(), sa.ForeignKey("sites.id"), nullable=False),
        sa.Column("camera_id", sa.Text(), sa.ForeignKey("cameras.id"), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("sub_label", sa.Text()),
        sa.Column("score", sa.Float()),
        sa.Column("start_ts", sa.DateTime(timezone=True), nullable=False),  # ★ UTC เสมอ
        sa.Column("end_ts", sa.DateTime(timezone=True)),
        sa.Column("direction", postgresql.ENUM(name="event_direction", create_type=False)),  # ★ เข้า/ออก
        sa.Column("zones", postgresql.ARRAY(sa.Text())),
        sa.Column("has_clip", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("thumb_key", sa.Text()),
        sa.Column("raw", postgresql.JSONB()),  # ★ payload ดิบจาก Frigate — ห้ามทิ้ง
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # ------------------------------------------------------------
    #  2.4 detections
    # ------------------------------------------------------------
    op.create_table(
        "detections",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("event_id", sa.Text(), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", postgresql.ENUM(name="detection_kind", create_type=False), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bbox", postgresql.ARRAY(sa.Integer())),  # [x1, y1, x2, y2]
        sa.Column("crop_key", sa.Text()),
        sa.Column("quality", sa.Float()),
    )

    # ------------------------------------------------------------
    #  2.5 persons + face_embeddings
    # ------------------------------------------------------------
    op.create_table(
        "persons",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("site_id", sa.Text(), sa.ForeignKey("sites.id"), nullable=False),
        sa.Column("display_name", sa.Text()),  # NULL = บุคคลนิรนามจาก clustering
        sa.Column("notes", sa.Text()),
        sa.Column("consent_basis", sa.Text()),  # ★ ฐานทางกฎหมายตาม PDPA
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # ------------------------------------------------------------
    #  2.7 plates — สร้างก่อน vehicles เพราะ vehicles.plate_id อ้างถึง
    # ------------------------------------------------------------
    op.create_table(
        "plates",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("detection_id", sa.BigInteger(), sa.ForeignKey("detections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("raw_text", sa.Text()),  # ★ ข้อความดิบจาก OCR — ห้ามทิ้ง
        sa.Column("plate_norm", sa.Text()),  # '1กก1234' ไม่มีช่องว่าง
        sa.Column("province", sa.Text()),
        sa.Column("plate_color", sa.Text()),  # จากกล้อง overview
        sa.Column("ocr_conf", sa.Float()),
        sa.Column("char_confs", postgresql.JSONB()),  # ความมั่นใจรายตัวอักษร
        sa.Column("flags", postgresql.ARRAY(sa.Text())),  # ['template_mismatch', ...]
    )

    op.create_table(
        "face_embeddings",
        sa.Column("detection_id", sa.BigInteger(), sa.ForeignKey("detections.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("embedding", Vector(512), nullable=False),  # ArcFace
        sa.Column("person_id", sa.BigInteger(), sa.ForeignKey("persons.id", ondelete="SET NULL")),
    )

    # ------------------------------------------------------------
    #  2.6 vehicles
    # ------------------------------------------------------------
    op.create_table(
        "vehicles",
        sa.Column("detection_id", sa.BigInteger(), sa.ForeignKey("detections.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("vehicle_type", sa.Text()),  # 'car' | 'van' | 'truck' | 'bus'
        sa.Column("color", sa.Text()),
        sa.Column("embedding", Vector(256)),  # vehicle-reid-0001
        sa.Column("plate_id", sa.BigInteger(), sa.ForeignKey("plates.id", ondelete="SET NULL")),
    )

    # ------------------------------------------------------------
    #  2.7 plate_registry (watchlist)
    # ------------------------------------------------------------
    op.create_table(
        "plate_registry",
        sa.Column("site_id", sa.Text(), sa.ForeignKey("sites.id"), primary_key=True),
        sa.Column("plate_norm", sa.Text(), primary_key=True),
        sa.Column("owner_label", sa.Text()),  # 'รถส่งของบริษัท ก'
        sa.Column("notes", sa.Text()),
        sa.Column("watch", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    # ------------------------------------------------------------
    #  2.8 PDPA
    # ------------------------------------------------------------
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),  # 'search_face' | 'view_person' | ...
        sa.Column("target_type", sa.Text()),
        sa.Column("target_id", sa.Text()),
        sa.Column("query", postgresql.JSONB()),  # เงื่อนไขที่ค้น
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("ip", postgresql.INET()),
    )

    op.create_table(
        "retention_policies",
        # 'clips' | 'snapshots' | 'crops' | 'face_embeddings' | 'events'
        sa.Column("scope", sa.Text(), primary_key=True),
        sa.Column("days", sa.Integer(), nullable=False),
    )

    # ------------------------------------------------------------
    #  3. Index — docs/04-data-model.md หัวข้อ 3
    #
    #  ใช้ op.execute() สำหรับ index ที่ผูกกับ Postgres extension
    #  (HNSW ของ pgvector, GIN+gin_trgm_ops ของ pg_trgm) เพื่อควบคุม SQL
    #  ที่ส่งไปแบบตรงตัวเป๊ะ ไม่ต้องพึ่งการแปลงผ่าน dialect kwargs
    # ------------------------------------------------------------
    op.execute(
        "CREATE INDEX idx_face_emb_hnsw ON face_embeddings "
        "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )
    op.execute(
        "CREATE INDEX idx_vehicle_emb_hnsw ON vehicles "
        "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )
    # ★ ค้นทะเบียนแบบ fuzzy — สำคัญที่สุด
    op.execute(
        "CREATE INDEX idx_plates_norm_trgm ON plates USING gin (plate_norm gin_trgm_ops)"
    )
    # ตารางเรียงตามเวลา BRIN กินพื้นที่น้อยกว่า btree หลายสิบเท่า
    op.execute("CREATE INDEX idx_events_start_brin ON events USING brin (start_ts)")

    # index แบบ btree ปกติ ใช้ op.create_index ได้ตรง ๆ
    op.create_index("idx_events_camera_ts", "events", ["camera_id", sa.text("start_ts DESC")])
    op.create_index("idx_events_label_ts", "events", ["label", sa.text("start_ts DESC")])
    op.create_index("idx_events_site_ts", "events", ["site_id", sa.text("start_ts DESC")])
    op.create_index("idx_detections_event", "detections", ["event_id", "kind"])


def downgrade() -> None:
    # ย้อนกลับตามลำดับตรงข้ามกับ upgrade — ตารางที่มี FK ไปหาตัวอื่นต้องลบก่อน
    op.drop_table("retention_policies")
    op.drop_table("audit_log")
    op.drop_table("plate_registry")
    op.drop_table("vehicles")
    op.drop_table("face_embeddings")
    op.drop_table("plates")
    op.drop_table("persons")
    op.drop_table("detections")
    op.drop_table("events")
    op.drop_table("cameras")
    op.drop_table("sites")

    bind = op.get_bind()
    postgresql.ENUM(name="detection_kind").drop(bind, checkfirst=True)
    postgresql.ENUM(name="event_direction").drop(bind, checkfirst=True)
    postgresql.ENUM(name="camera_role").drop(bind, checkfirst=True)

    # ไม่ถอด extension ออกโดยตั้งใจ — vector/pg_trgm/btree_gin มักถูกใช้ร่วม
    # กับ migration อื่นด้วย ถอดออกตอน downgrade เสี่ยงกระทบสิ่งอื่นเกินจำเป็น
