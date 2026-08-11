"""
วิเคราะห์ลักษณะรถ (ประเภท/สี) + สร้าง embedding สำหรับ re-identification ด้วย
OpenVINO Model Zoo — ดู docs/01-architecture.md ตาราง AI stack และ
docs/04-data-model.md หัวข้อ 2.6 (ตาราง vehicles)

รับ crop ภาพรถ 1 ภาพ (ตัดจากกรอบที่ Frigate ตรวจพบ label='car'/'motorcycle' ฯลฯ)
ทำ 2 อย่างแยกกัน (เรียกทีละอย่างหรือรวมผ่าน analyze_vehicle() ก็ได้):
  1. vehicle-attributes-recognition-barrier-0039 -> vehicle_type + color
  2. vehicle-reid-0001 -> embedding 512 มิติ สำหรับ vector search "หารถคันเดิม"
     ข้ามหลายกล้อง/หลายครั้ง (ตรงกับ vehicles.embedding VECTOR(512))

★ ไฟล์โมเดลไม่ได้เก็บใน git (เหมือน samples/ media/ อื่น ๆ) ต้องดาวน์โหลดเอง
ไปไว้ที่ models/<model-name>/... ก่อนใช้งาน — path override ได้ด้วย env var
MODELS_PATH (ดู MODELS_DIR ด้านล่าง)

★ barrier-0039 เป็นโมเดล IR สำเร็จรูปจาก Intel (Open Model Zoo หมวด "intel")
โหลด .xml/.bin ตรงได้เลย ไม่ต้องแปลง preprocessing มาพร้อมในโมเดลแล้ว (ป้อน
BGR ดิบ 0-255 ตรง ๆ พอ)

★ vehicle-reid-0001 เป็นโมเดล "public" (ต้นทาง ONNX สถาปัตยกรรม OSNet-AIN, ไม่มี
IR สำเร็จรูปให้โหลด) — OpenVINO runtime อ่าน .onnx ตรงได้โดยไม่ต้องแปลงก่อน
ตรวจสอบกราฟแล้วพบว่า layer แรกสุดต่อจาก input คือ MVN (mean-variance norm,
ส่วนหนึ่งของ "IN" ใน OSNet-AIN เอง ไม่ใช่ preprocessing ภายนอก) ดังนั้นป้อน
BGR ดิบ 0-255 ตรง ๆ ได้เหมือนกัน ไม่ต้องหาร 255 หรือลบ mean เอง (README ของ
Open Model Zoo ไม่ได้ระบุ preprocessing ไว้ชัด ต้องตรวจกราฟเองเพื่อยืนยัน)
สี BGR (ไม่ใช่ RGB) — README ระบุ "converted model: channel order is BGR"
ตรงกับไฟล์ที่ดาวน์โหลดมา (เวอร์ชัน converted จาก public/2022.1)

★ ทดสอบแล้ว: โหลด + compile โมเดลทั้งสองสำเร็จ, input/output shape ตรงตาม
เอกสาร Open Model Zoo ทุกจุด, infer ด้วยภาพสังเคราะห์ไม่ error/ไม่ NaN — ยัง
ไม่เคยทดสอบกับภาพรถจริงจากกล้อง หรือวัดความแม่นยำจริง (แค่ยืนยันว่า
pipeline รันได้ครบวงจร)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

_DEFAULT_MODELS_DIR = Path(__file__).resolve().parents[3] / "models"
MODELS_DIR = Path(os.environ.get("MODELS_PATH", str(_DEFAULT_MODELS_DIR)))

ATTR_MODEL_PATH = (
    MODELS_DIR
    / "vehicle-attributes-recognition-barrier-0039"
    / "FP16"
    / "vehicle-attributes-recognition-barrier-0039.xml"
)
REID_MODEL_PATH = MODELS_DIR / "vehicle-reid-0001" / "vehicle-reid-0001.onnx"

ATTR_INPUT_SIZE = (72, 72)  # (width, height) ตาม README barrier-0039
REID_INPUT_SIZE = (208, 208)  # ตาม README vehicle-reid-0001
REID_EMBEDDING_DIM = 512  # ต้องตรงกับ vehicles.embedding VECTOR(512) เสมอ

# ลำดับ label ต้องตรงกับลำดับ output softmax ของโมเดลเป๊ะ (README Open Model Zoo)
TYPE_LABELS = ["car", "bus", "truck", "van"]
COLOR_LABELS = ["white", "gray", "yellow", "red", "green", "blue", "black"]


@dataclass
class VehicleAttrs:
    vehicle_type: str
    type_conf: float
    color: str
    color_conf: float


@dataclass
class VehicleResult:
    attrs: VehicleAttrs | None
    embedding: list[float] | None


_core = None
_attr_model = None
_reid_model = None


def _get_core():
    global _core
    if _core is None:
        import openvino as ov

        _core = ov.Core()
    return _core


def _get_attr_model(device: str = "CPU"):
    """โมเดลจำแนก vehicle_type/color — โหลดครั้งเดียว cache ไว้ตลอดอายุ process"""
    global _attr_model
    if _attr_model is None:
        core = _get_core()
        model = core.read_model(str(ATTR_MODEL_PATH))
        _attr_model = core.compile_model(model, device)
    return _attr_model


def _get_reid_model(device: str = "CPU"):
    """โมเดล re-id embedding — โหลดครั้งเดียว cache ไว้ตลอดอายุ process"""
    global _reid_model
    if _reid_model is None:
        core = _get_core()
        model = core.read_model(str(REID_MODEL_PATH))
        _reid_model = core.compile_model(model, device)
    return _reid_model


def _load_bgr(image):
    """image: path (str/Path) หรือ numpy array BGR (convention เดียวกับ OpenCV) -> numpy array BGR"""
    if isinstance(image, (str, Path)):
        import cv2

        img = cv2.imread(str(image))
        if img is None:
            raise ValueError(f"อ่านภาพไม่ได้: {image}")
        return img
    return image


def _preprocess(image, size: tuple[int, int]) -> np.ndarray:
    """resize + แปลงเป็น NCHW float32 blob (ไม่ normalize เพิ่ม — ดู docstring ด้านบน)"""
    import cv2

    img = _load_bgr(image)
    resized = cv2.resize(img, size)
    return resized.transpose(2, 0, 1)[np.newaxis, ...].astype(np.float32)


def read_vehicle_attrs(image, engine=None) -> VehicleAttrs:
    """
    คืน vehicle_type + color จาก crop ภาพรถ 1 ภาพ

    image: path (str/Path) หรือ numpy array BGR
    engine: compiled model override (ใช้ตอนเทสต์เพื่อไม่ต้องโหลดโมเดลจริง)
    """
    compiled = engine or _get_attr_model()
    blob = _preprocess(image, ATTR_INPUT_SIZE)
    result = compiled([blob])

    type_probs = np.asarray(result["type"]).reshape(-1)
    color_probs = np.asarray(result["color"]).reshape(-1)
    type_idx = int(np.argmax(type_probs))
    color_idx = int(np.argmax(color_probs))

    return VehicleAttrs(
        vehicle_type=TYPE_LABELS[type_idx],
        type_conf=float(type_probs[type_idx]),
        color=COLOR_LABELS[color_idx],
        color_conf=float(color_probs[color_idx]),
    )


def read_vehicle_embedding(image, engine=None) -> list[float]:
    """
    คืน embedding 512 มิติ สำหรับ vector search (ตรงกับ vehicles.embedding)

    image: path (str/Path) หรือ numpy array BGR
    engine: compiled model override (ใช้ตอนเทสต์เพื่อไม่ต้องโหลดโมเดลจริง)
    """
    compiled = engine or _get_reid_model()
    blob = _preprocess(image, REID_INPUT_SIZE)
    result = compiled([blob])
    output_key = list(result.keys())[0]
    embedding = np.asarray(result[output_key]).reshape(-1)
    return embedding.tolist()


def analyze_vehicle(image, attr_engine=None, reid_engine=None) -> VehicleResult:
    """เรียกทั้งสองโมเดล คืนผลรวมพร้อม insert ลงตาราง vehicles ได้ตรง ๆ"""
    attrs = read_vehicle_attrs(image, engine=attr_engine)
    embedding = read_vehicle_embedding(image, engine=reid_engine)
    return VehicleResult(attrs=attrs, embedding=embedding)
