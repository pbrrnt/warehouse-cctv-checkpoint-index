"""
auth แบบง่าย (เฟสเริ่มต้น) — เทียบ header X-API-Key กับ API_SECRET_KEY ตัว
เดียว ทุกคนที่มีคีย์เห็นข้อมูลเท่ากันหมด (เทียบเท่า role 'viewer' — ค้น
ทะเบียน+ดูภาพ)

★ ยังไม่ใช่ RBAC เต็มรูปแบบตาม docs/06-pdpa-compliance.md หัวข้อ 4 (บัญชี
รายบุคคล, role viewer/investigator/manager/admin, audit log ทุกการเข้าถึง)
— ตั้งใจพักไว้ก่อนเพราะยังไม่มีระบบค้นใบหน้าที่ต้องคุมสิทธิ์เข้ม (เหตุผลที่
RBAC เต็มรูปแบบมีไว้) ทำตอน face pipeline (เฟส 4) จริงจัง
"""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, Request, status


def require_api_key(request: Request, x_api_key: str | None = Header(default=None)) -> None:
    expected = request.app.state.settings.api_secret_key
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API key ไม่ถูกต้องหรือไม่มี (header X-API-Key)")
