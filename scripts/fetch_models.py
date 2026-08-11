#!/usr/bin/env python3
"""
ดาวน์โหลดไฟล์โมเดล AI ที่ services/common/analytics/*.py ใช้ — ไฟล์เหล่านี้
ไม่เก็บใน git (ดู .gitignore: models/*) เพราะเป็นไบนารีหลาย MB และดาวน์โหลด
ซ้ำได้จากต้นทางเปิด ต้องรันสคริปต์นี้ทุกเครื่องใหม่ (dev หรือ server จริง)
ก่อนเรียก services/common/analytics/vehicle.py

โมเดลที่ดาวน์โหลด (ที่มา: Intel Open Model Zoo, Apache-2.0):
  - vehicle-attributes-recognition-barrier-0039 (IR สำเร็จรูป จาก "intel")
  - vehicle-reid-0001 (ONNX ต้นทาง จาก "public" — ไม่มี IR สำเร็จรูป,
    OpenVINO runtime อ่าน .onnx ตรงได้ ดู vehicle.py หัวข้อ ★ preprocessing)

PaddleOCR (thai_ocr.py ใช้) ไม่ต้องดาวน์โหลดเอง — ตัว paddleocr library
ดาวน์โหลด/แคชโมเดลอัตโนมัติที่ ~/.paddlex ตอนเรียกใช้ครั้งแรก

ใช้งาน:
  python scripts/fetch_models.py            (ข้ามไฟล์ที่มีอยู่แล้ว)
  python scripts/fetch_models.py --force    (โหลดทับใหม่ทุกไฟล์)
"""

from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = PROJECT_ROOT / "models"

_OMZ_BIN = "https://storage.openvinotoolkit.org/repositories/open_model_zoo/2023.0/models_bin/1"
_OMZ_PUBLIC = "https://storage.openvinotoolkit.org/repositories/open_model_zoo/public/2022.1"

# (url, ปลายทางสัมพัทธ์กับ MODELS_DIR)
FILES = [
    (
        f"{_OMZ_BIN}/vehicle-attributes-recognition-barrier-0039/FP16/vehicle-attributes-recognition-barrier-0039.xml",
        "vehicle-attributes-recognition-barrier-0039/FP16/vehicle-attributes-recognition-barrier-0039.xml",
    ),
    (
        f"{_OMZ_BIN}/vehicle-attributes-recognition-barrier-0039/FP16/vehicle-attributes-recognition-barrier-0039.bin",
        "vehicle-attributes-recognition-barrier-0039/FP16/vehicle-attributes-recognition-barrier-0039.bin",
    ),
    (
        f"{_OMZ_PUBLIC}/vehicle-reid-0001/osnet_ain_x1_0_vehicle_reid.onnx",
        "vehicle-reid-0001/vehicle-reid-0001.onnx",
    ),
    (
        f"{_OMZ_PUBLIC}/vehicle-reid-0001/LICENSE",
        "vehicle-reid-0001/LICENSE",
    ),
]

# ไฟล์เล็กผิดปกติ = โดน redirect ไปหน้า HTML "ไม่พบไฟล์" ของ storage host
# (เจอเคสนี้จริงตอนพัฒนา vehicle.py — url ผิด path แต่ตอบ 200 พร้อม HTML)
_MIN_SIZE_BYTES = 10_000


def fetch(url: str, dest: Path, force: bool) -> bool:
    if dest.exists() and not force:
        print(f"  ข้าม (มีอยู่แล้ว): {dest.relative_to(PROJECT_ROOT)}")
        return True

    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"  ดาวน์โหลด: {url}")
    try:
        urllib.request.urlretrieve(url, dest)
    except Exception as exc:  # noqa: BLE001 — สคริปต์หน้างาน ต้องรายงานทุก error ให้ผู้ใช้เห็น
        print(f"  [FAIL] {dest.name}: {exc}")
        return False

    size = dest.stat().st_size
    if size < _MIN_SIZE_BYTES:
        print(f"  [WARN] {dest.name} เล็กผิดปกติ ({size} bytes) — เช็ค url ว่ายังถูกต้องไหม (host อาจเปลี่ยน path)")
        return False

    print(f"  OK ({size:,} bytes)")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="โหลดทับใหม่แม้มีไฟล์อยู่แล้ว")
    args = parser.parse_args()

    print(f"MODELS_DIR = {MODELS_DIR}")
    ok = True
    for url, rel_path in FILES:
        ok = fetch(url, MODELS_DIR / rel_path, args.force) and ok

    if ok:
        print("\nเสร็จ — ไฟล์โมเดลครบตามที่ vehicle.py ต้องการ")
    else:
        print("\nมีไฟล์ดาวน์โหลดไม่สำเร็จ ดู [FAIL]/[WARN] ด้านบน")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
