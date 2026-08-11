"""
เทสต์ vehicle.py — แบ่ง 2 กลุ่มเหมือน test_thai_ocr.py

1. หน่วยทดสอบเร็ว (mock compiled model ทั้งหมด ไม่โหลดโมเดลจริง) — รันได้ทุก
   เครื่อง ทุกครั้ง ครอบคลุมการแปลง output softmax -> label/confidence และ
   การรวมผลของ analyze_vehicle()

2. เทสต์ end-to-end กับ OpenVINO ตัวจริง (TestVehicleIntegration) — ใช้ภาพ
   สังเคราะห์สร้างด้วย PIL (สี่เหลี่ยมสี ไม่ใช่ภาพรถจริง จึงวัดได้แค่ว่า
   pipeline รันครบวงจรไม่ error/ไม่ NaN ไม่ได้วัดความแม่นยำจริงกับรถจริง)
   ต้องมีไฟล์โมเดลอยู่ใต้ models/ (ดาวน์โหลดเองตาม vehicle.py docstring) —
   ถ้าไม่มีไฟล์หรือ import openvino ไม่ได้ จะ skip กลุ่มนี้อัตโนมัติ

รันด้วย:  python -m unittest tests.test_vehicle -v
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from services.common.analytics.vehicle import (  # noqa: E402
    ATTR_MODEL_PATH,
    COLOR_LABELS,
    REID_EMBEDDING_DIM,
    REID_MODEL_PATH,
    TYPE_LABELS,
    analyze_vehicle,
    read_vehicle_attrs,
    read_vehicle_embedding,
)


class _FakeCompiledModel:
    """จำลอง OpenVINO CompiledModel.__call__ — คืน dict คงที่ไม่สนใจ input จริง"""

    def __init__(self, outputs):
        self._outputs = outputs

    def __call__(self, inputs):
        return self._outputs


def _one_hot_scores(labels, winner, winner_conf=0.9, shape_4d=True):
    """สร้าง array softmax ปลอมที่ label ที่ต้องการชนะแน่ ๆ"""
    idx = labels.index(winner)
    n = len(labels)
    remaining = (1.0 - winner_conf) / max(n - 1, 1)
    scores = np.full(n, remaining, dtype=np.float32)
    scores[idx] = winner_conf
    if shape_4d:
        return scores.reshape(1, n, 1, 1)
    return scores.reshape(1, n)


_DUMMY_IMAGE = np.zeros((50, 50, 3), dtype=np.uint8)  # numpy array BGR ตรง ๆ ไม่ผ่าน cv2.imread


class TestReadVehicleAttrsMocked(unittest.TestCase):
    def test_picks_argmax_label_and_confidence(self):
        engine = _FakeCompiledModel(
            {
                "type": _one_hot_scores(TYPE_LABELS, "truck", 0.85),
                "color": _one_hot_scores(COLOR_LABELS, "blue", 0.7),
            }
        )
        attrs = read_vehicle_attrs(_DUMMY_IMAGE, engine=engine)
        self.assertEqual(attrs.vehicle_type, "truck")
        self.assertAlmostEqual(attrs.type_conf, 0.85, places=4)
        self.assertEqual(attrs.color, "blue")
        self.assertAlmostEqual(attrs.color_conf, 0.7, places=4)

    def test_different_winner_each_label_set(self):
        engine = _FakeCompiledModel(
            {
                "type": _one_hot_scores(TYPE_LABELS, "van", 0.6),
                "color": _one_hot_scores(COLOR_LABELS, "black", 0.55),
            }
        )
        attrs = read_vehicle_attrs(_DUMMY_IMAGE, engine=engine)
        self.assertEqual(attrs.vehicle_type, "van")
        self.assertEqual(attrs.color, "black")


class TestReadVehicleEmbeddingMocked(unittest.TestCase):
    def test_returns_flat_list_matching_model_output(self):
        raw = np.arange(REID_EMBEDDING_DIM, dtype=np.float32).reshape(1, REID_EMBEDDING_DIM)
        engine = _FakeCompiledModel({"output": raw})
        embedding = read_vehicle_embedding(_DUMMY_IMAGE, engine=engine)
        self.assertEqual(len(embedding), REID_EMBEDDING_DIM)
        self.assertEqual(embedding[:3], [0.0, 1.0, 2.0])

    def test_works_regardless_of_output_key_name(self):
        # ชื่อ output key ของ ONNX model นี้ไม่แน่นอนเท่า intel IR model —
        # ฟังก์ชันต้องไม่ hardcode ชื่อ key
        raw = np.ones((1, REID_EMBEDDING_DIM), dtype=np.float32)
        engine = _FakeCompiledModel({"some_other_name": raw})
        embedding = read_vehicle_embedding(_DUMMY_IMAGE, engine=engine)
        self.assertEqual(len(embedding), REID_EMBEDDING_DIM)


class TestAnalyzeVehicleMocked(unittest.TestCase):
    def test_combines_attrs_and_embedding(self):
        attr_engine = _FakeCompiledModel(
            {
                "type": _one_hot_scores(TYPE_LABELS, "bus", 0.99),
                "color": _one_hot_scores(COLOR_LABELS, "white", 0.99),
            }
        )
        reid_engine = _FakeCompiledModel(
            {"output": np.zeros((1, REID_EMBEDDING_DIM), dtype=np.float32)}
        )
        result = analyze_vehicle(_DUMMY_IMAGE, attr_engine=attr_engine, reid_engine=reid_engine)
        self.assertEqual(result.attrs.vehicle_type, "bus")
        self.assertEqual(result.attrs.color, "white")
        self.assertEqual(len(result.embedding), REID_EMBEDDING_DIM)


@unittest.skipUnless(
    Path(ATTR_MODEL_PATH).exists() and Path(REID_MODEL_PATH).exists(),
    "ต้องดาวน์โหลดไฟล์โมเดลไว้ใต้ models/ ก่อน (ดู vehicle.py docstring)",
)
class TestVehicleIntegration(unittest.TestCase):
    """เทสต์ end-to-end กับ OpenVINO ตัวจริง — ยืนยันว่า pipeline รันครบวงจร"""

    @classmethod
    def setUpClass(cls):
        try:
            import openvino  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("ไม่มี openvino")

    def test_analyze_vehicle_runs_end_to_end_no_nan(self):
        from PIL import Image, ImageDraw

        img = Image.new("RGB", (300, 200), color=(120, 120, 120))
        d = ImageDraw.Draw(img)
        d.rectangle([60, 50, 240, 150], fill=(200, 30, 30))

        arr = np.array(img)[:, :, ::-1].copy()  # RGB (PIL) -> BGR (OpenCV convention)

        result = analyze_vehicle(arr)
        self.assertIn(result.attrs.vehicle_type, TYPE_LABELS)
        self.assertIn(result.attrs.color, COLOR_LABELS)
        self.assertEqual(len(result.embedding), REID_EMBEDDING_DIM)
        self.assertFalse(any(np.isnan(x) for x in result.embedding))


if __name__ == "__main__":
    unittest.main()
