"""
FastAPI app factory — แยกจาก main.py (entrypoint จริงที่อ่าน env/รัน uvicorn)
เพื่อให้เทสต์สร้าง app ด้วย session_factory ปลอมได้โดยไม่ต้องมี Postgres จริง
(ดู tests/test_api.py)

ให้พนักงานค้นหาทะเบียน/รถ แล้วรู้ว่าต้องไปเปิดกล้องไหนช่องไหนเวลาไหนใน NVR
— ระบบนี้ไม่ใช่ตัวเล่นวิดีโอ (ดู docs/08-nvr-integration.md หัวข้อ 6)

★ auth ตอนนี้เป็น shared API key เดียว (services/api/auth.py) ยังไม่ใช่
RBAC เต็มรูปแบบตาม docs/06-pdpa-compliance.md หัวข้อ 4 — ดู docstring auth.py

★ ยังไม่เคยรันจริงกับ Postgres (ไม่มี Docker บนเครื่องพัฒนา) — ดู
docs/11-testing.md
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from services.api.auth import require_api_key
from services.api.config import Settings
from services.api.registry import (
    delete_registry_entry,
    list_registry,
    normalize_registry_plate,
    upsert_registry_entry,
)
from services.api.schemas import CameraInfo, NowEntry, PlateSearchResult, RegistryEntry, RegistryUpsertRequest, VehicleSearchResult
from services.common.db.models import Camera
from services.api.search import (
    build_now_query,
    build_plate_fuzzy_query,
    build_vehicle_search_query,
    rerank_plate_candidates,
    to_now_entry,
    to_plate_result,
    to_vehicle_result,
)

DEFAULT_SEARCH_WINDOW_DAYS = 7
NOW_WINDOW_HOURS = 24


def get_db(request: Request) -> Session:
    db = request.app.state.session_factory()
    try:
        yield db
    finally:
        db.close()


def create_app(settings: Settings, session_factory: sessionmaker) -> FastAPI:
    app = FastAPI(title="CCTV Search API — " + (settings.site_name or settings.site_id))
    app.state.settings = settings
    app.state.session_factory = session_factory

    @app.get("/health")
    def health():
        return {"status": "ok", "site_id": settings.site_id}

    @app.get(
        "/cameras",
        response_model=list[CameraInfo],
        dependencies=[Depends(require_api_key)],
    )
    def list_cameras(db: Session = Depends(get_db)):
        """รายชื่อกล้องของไซต์นี้ — web UI ใช้เติม dropdown ตัวกรองกล้อง
        (เดิม dropdown มีแค่ 'ทั้งหมด' เพราะไม่มี endpoint นี้)"""
        stmt = (
            select(Camera)
            .where(Camera.site_id == settings.site_id, Camera.enabled.is_(True))
            .order_by(Camera.id)
        )
        return [CameraInfo(id=c.id, name=c.name, nvr_channel=c.nvr_channel) for c in db.execute(stmt).scalars()]

    @app.get(
        "/search/plates",
        response_model=list[PlateSearchResult],
        dependencies=[Depends(require_api_key)],
    )
    def search_plates(
        q: str = Query(..., min_length=1, description="ทะเบียนที่ต้องการค้น เช่น 1กก1234"),
        from_ts: datetime | None = Query(None),
        to_ts: datetime | None = Query(None),
        db: Session = Depends(get_db),
    ):
        now = datetime.now(timezone.utc)
        from_ts = from_ts or (now - timedelta(days=DEFAULT_SEARCH_WINDOW_DAYS))
        to_ts = to_ts or now

        stmt = build_plate_fuzzy_query(settings.site_id, q, from_ts, to_ts)
        rows = db.execute(stmt).all()
        ranked = rerank_plate_candidates(rows, q)
        return [
            to_plate_result(score, plate, event, camera, settings.timezone, registry_entry)
            for score, plate, event, camera, registry_entry in ranked
        ]

    @app.get(
        "/search/vehicles",
        response_model=list[VehicleSearchResult],
        dependencies=[Depends(require_api_key)],
    )
    def search_vehicles(
        from_ts: datetime | None = Query(None),
        to_ts: datetime | None = Query(None),
        camera_id: str | None = Query(None),
        color: str | None = Query(None),
        vehicle_type: str | None = Query(None),
        direction: str | None = Query(None),
        db: Session = Depends(get_db),
    ):
        now = datetime.now(timezone.utc)
        from_ts = from_ts or (now - timedelta(days=DEFAULT_SEARCH_WINDOW_DAYS))
        to_ts = to_ts or now

        stmt = build_vehicle_search_query(
            settings.site_id, from_ts, to_ts,
            camera_id=camera_id, color=color, vehicle_type=vehicle_type, direction=direction,
        )
        rows = db.execute(stmt).all()
        return [to_vehicle_result(vehicle, event, camera, plate, settings.timezone) for vehicle, event, camera, plate in rows]

    @app.get(
        "/now",
        response_model=list[NowEntry],
        dependencies=[Depends(require_api_key)],
    )
    def now_in_warehouse(db: Session = Depends(get_db)):
        since_ts = datetime.now(timezone.utc) - timedelta(hours=NOW_WINDOW_HOURS)
        stmt = build_now_query(settings.site_id, since_ts)
        rows = db.execute(stmt).all()
        # แต่ละแถวคือ (plate_norm, province, direction, start_ts, camera_id, Camera)
        return [to_now_entry(row, row.Camera, settings.timezone) for row in rows]

    # ------------------------------------------------------
    #  ทะเบียนที่รู้จัก (plate_registry) — ดู services/api/registry.py
    #  ★ auth เดียวกับข้างบน (shared key) ไม่ใช่ RBAC เต็ม — ดู ADR-022
    # ------------------------------------------------------

    @app.get(
        "/registry",
        response_model=list[RegistryEntry],
        dependencies=[Depends(require_api_key)],
    )
    def get_registry(db: Session = Depends(get_db)):
        entries = list_registry(db, settings.site_id)
        return [RegistryEntry(plate_norm=e.plate_norm, owner_label=e.owner_label, notes=e.notes, watch=e.watch) for e in entries]

    @app.put(
        "/registry/{plate_norm}",
        response_model=RegistryEntry,
        dependencies=[Depends(require_api_key)],
    )
    def put_registry_entry(plate_norm: str, body: RegistryUpsertRequest, db: Session = Depends(get_db)):
        normalized = normalize_registry_plate(plate_norm)
        if not normalized:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ทะเบียนไม่ถูกต้อง (พิมพ์ว่างเปล่าหรืออ่านไม่ได้)")
        entry = upsert_registry_entry(db, settings.site_id, normalized, body.owner_label, body.notes, body.watch)
        return RegistryEntry(plate_norm=entry.plate_norm, owner_label=entry.owner_label, notes=entry.notes, watch=entry.watch)

    @app.delete(
        "/registry/{plate_norm}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(require_api_key)],
    )
    def delete_registry_entry_route(plate_norm: str, db: Session = Depends(get_db)):
        normalized = normalize_registry_plate(plate_norm)
        found = normalized and delete_registry_entry(db, settings.site_id, normalized)
        if not found:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ไม่พบทะเบียนนี้ในทะเบียนที่รู้จัก")

    return app
