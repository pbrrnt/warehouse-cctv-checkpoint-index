"""
เทสต์ thai_ocr.py — แบ่ง 2 กลุ่ม

1. หน่วยทดสอบเร็ว (mock ทั้งหมด ไม่โหลดโมเดลจริง) — รันได้ทุกเครื่อง ทุกครั้ง
   ครอบคลุม _extract_boxes / _group_into_lines / read_plate_text (engine ปลอม)

2. เทสต์ end-to-end กับ PaddleOCR ตัวจริง (TestReadPlateTextIntegration) — สร้าง
   ภาพป้ายทะเบียนสังเคราะห์ด้วย PIL ที่รันเวลานั้นเลย (ไม่ commit ภาพเข้า git
   ตาม .gitignore ที่กัน *.png ทั้งหมดด้วยเหตุผล PDPA) แล้วเรียก pipeline จริง
   ช้ากว่ากลุ่ม 1 มาก (โหลดโมเดลครั้งแรกหลายวินาที) และต้องมีฟอนต์
   Leelawad บนเครื่อง — ถ้าไม่มีฟอนต์หรือ import paddleocr ไม่ได้ จะ skip
   กลุ่มนี้อัตโนมัติแทนที่จะ fail

รันด้วย:  python -m unittest tests.test_thai_ocr -v
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.common.analytics.thai_ocr import (  # noqa: E402
    OcrBox,
    _extract_boxes,
    _group_into_lines,
    read_plate_text,
)


# ============================================================
#  ตัวช่วยสร้าง predict_result ปลอม (โครงสร้างเดียวกับที่ PaddleOCR.predict() คืน)
# ============================================================

def _fake_page(texts, scores, polys):
    return {"rec_texts": texts, "rec_scores": scores, "rec_polys": polys}


def _box_poly(x_start, y_top, width, height):
    """สร้างกล่องสี่เหลี่ยม (poly 4 จุด) จากมุมซ้ายบน + ขนาด"""
    return [
        [x_start, y_top],
        [x_start + width, y_top],
        [x_start + width, y_top + height],
        [x_start, y_top + height],
    ]


class TestExtractBoxes(unittest.TestCase):
    def test_single_page_single_box(self):
        poly = _box_poly(10, 20, 100, 40)
        page = _fake_page(["1กก 1234"], [0.95], [poly])
        boxes = _extract_boxes([page])
        self.assertEqual(len(boxes), 1)
        b = boxes[0]
        self.assertEqual(b.text, "1กก 1234")
        self.assertAlmostEqual(b.confidence, 0.95)
        self.assertAlmostEqual(b.y_center, 40.0)  # (20+60)/2
        self.assertAlmostEqual(b.y_height, 40.0)
        self.assertAlmostEqual(b.x_start, 10.0)

    def test_multiple_boxes_across_pages(self):
        page1 = _fake_page(["กท 5678"], [0.9], [_box_poly(0, 0, 80, 30)])
        page2 = _fake_page(["นนทบุรี"], [0.8], [_box_poly(0, 50, 80, 20)])
        boxes = _extract_boxes([page1, page2])
        self.assertEqual(len(boxes), 2)

    def test_empty_predict_result(self):
        self.assertEqual(_extract_boxes([]), [])

    def test_page_with_missing_keys(self):
        # หน้าที่ตรวจไม่พบข้อความเลย — PaddleOCR อาจคืนลิสต์ว่างหรือไม่มีคีย์เลย
        self.assertEqual(_extract_boxes([{}]), [])


class TestGroupIntoLines(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(_group_into_lines([]), [])

    def test_single_box_single_line(self):
        boxes = [OcrBox("1234", 0.9, y_center=50, y_height=40, x_start=0)]
        lines = _group_into_lines(boxes)
        self.assertEqual(len(lines), 1)
        self.assertEqual(len(lines[0]), 1)

    def test_two_boxes_same_line_merge_and_sort_by_x(self):
        # จำลอง "1กก 1234" ที่ตรวจพบเป็น 2 กล่องแยก x ต่างกัน แต่ y ใกล้กันมาก
        box_right = OcrBox("1234", 0.9, y_center=52, y_height=40, x_start=120)
        box_left = OcrBox("1กก", 0.9, y_center=50, y_height=40, x_start=10)
        lines = _group_into_lines([box_right, box_left])
        self.assertEqual(len(lines), 1)
        self.assertEqual([b.text for b in lines[0]], ["1กก", "1234"])

    def test_two_lines_separated_by_y(self):
        top = OcrBox("1กก 1234", 0.9, y_center=40, y_height=60, x_start=10)
        bottom = OcrBox("กรุงเทพมหานคร", 0.9, y_center=180, y_height=35, x_start=15)
        lines = _group_into_lines([top, bottom])
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0][0].text, "1กก 1234")
        self.assertEqual(lines[1][0].text, "กรุงเทพมหานคร")

    def test_three_line_motorcycle_layout_stays_separate(self):
        # เคสเสี่ยงสุด: ป้ายมอไซค์ 3 บรรทัดต้องไม่ถูกจัดรวมกันผิด แม้ระยะห่าง
        # บรรทัดจะสั้นกว่าป้ายรถยนต์ (ฟอนต์เล็กกว่า สัดส่วนใกล้เคียงกัน)
        line1 = OcrBox("1กก", 0.9, y_center=45, y_height=55, x_start=50)
        line2 = OcrBox("1234", 0.9, y_center=125, y_height=55, x_start=50)
        line3 = OcrBox("ชลบุรี", 0.85, y_center=235, y_height=35, x_start=30)
        lines = _group_into_lines([line3, line1, line2])  # ใส่ลำดับสุ่มโดยตั้งใจ
        self.assertEqual(len(lines), 3)
        self.assertEqual([l[0].text for l in lines], ["1กก", "1234", "ชลบุรี"])

    def test_dynamic_threshold_uses_box_height_not_fixed_pixels(self):
        # กล่องเล็กมาก (ฟอนต์เล็ก) ต้องใช้ threshold เล็กตาม ไม่ใช่ค่าคงที่ใหญ่ ๆ
        # ที่จะกลืนบรรทัดที่ควรแยกกันในภาพความละเอียดต่ำ
        top = OcrBox("A", 0.9, y_center=10, y_height=8, x_start=0)
        bottom = OcrBox("B", 0.9, y_center=20, y_height=8, x_start=0)
        # ห่างกัน 10px ขณะที่สูงกล่องเฉลี่ย 8px * 0.6 = 4.8 -> ต้องแยกบรรทัด
        lines = _group_into_lines([top, bottom])
        self.assertEqual(len(lines), 2)


class _FakeEngine:
    def __init__(self, pages):
        self._pages = pages

    def predict(self, image):
        return self._pages


class TestReadPlateTextMocked(unittest.TestCase):
    def test_two_line_plate(self):
        pages = [
            _fake_page(
                ["1กก", "1234", "กรุงเทพมหานคร"],
                [0.98, 0.97, 0.95],
                [
                    _box_poly(10, 0, 60, 60),
                    _box_poly(90, 5, 70, 55),
                    _box_poly(15, 150, 150, 35),
                ],
            )
        ]
        engine = _FakeEngine(pages)
        result = read_plate_text("dummy.png", engine=engine)
        self.assertEqual(result.lines, ["1กก 1234", "กรุงเทพมหานคร"])
        self.assertEqual(result.box_count, 3)
        self.assertGreater(result.ocr_conf, 0.9)
        # char_confs ต้องยาวอย่างน้อยเท่าจำนวนตัวอักษรรวมทุกกล่อง
        total_chars = len("1กก") + len("1234") + len("กรุงเทพมหานคร")
        self.assertGreaterEqual(len(result.char_confs), total_chars)

    def test_no_boxes_detected(self):
        engine = _FakeEngine([_fake_page([], [], [])])
        result = read_plate_text("dummy.png", engine=engine)
        self.assertEqual(result.lines, [])
        self.assertEqual(result.ocr_conf, 0.0)
        self.assertEqual(result.box_count, 0)

    def test_ocr_conf_weighted_by_text_length(self):
        # ข้อความยาวความมั่นใจต่ำ ต้องถ่วงน้ำหนักมากกว่าข้อความสั้นความมั่นใจสูง
        pages = [
            _fake_page(
                ["ก", "กรุงเทพมหานคร"],
                [1.0, 0.5],
                [_box_poly(0, 0, 10, 30), _box_poly(0, 40, 150, 30)],
            )
        ]
        result = read_plate_text("dummy.png", engine=_FakeEngine(pages))
        # ถ่วงตามความยาว: ควรเอนเอียงไปทาง 0.5 (ข้อความยาวกว่ามาก) มากกว่าค่าเฉลี่ยธรรมดา 0.75
        self.assertLess(result.ocr_conf, 0.75)


@unittest.skipUnless(
    Path("C:/Windows/Fonts/leelawad.ttf").exists(),
    "ต้องมีฟอนต์ Leelawadee บนเครื่องถึงจะสร้างภาพทดสอบสังเคราะห์ได้",
)
class TestReadPlateTextIntegration(unittest.TestCase):
    """
    เทสต์ end-to-end กับ PaddleOCR ตัวจริง — ช้า (โหลดโมเดลครั้งแรกหลายวินาที)
    ยืนยันแล้วว่าอ่านถูก 100% ทั้ง 3 รูปแบบ (ดู docs/05-thai-lpr.md หัวข้อ 10
    และ thai_ocr.py docstring) แต่ยังเป็นภาพสังเคราะห์ ไม่ใช่ภาพจากกล้องจริง
    """

    @classmethod
    def setUpClass(cls):
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            raise unittest.SkipTest("ไม่มี Pillow")
        try:
            import paddleocr  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("ไม่มี paddleocr")

        cls._Image = Image
        cls._ImageDraw = ImageDraw
        cls._font_top = ImageFont.truetype("C:/Windows/Fonts/leelawad.ttf", 70)
        cls._font_bottom = ImageFont.truetype("C:/Windows/Fonts/leelawad.ttf", 40)
        cls._font_moto = ImageFont.truetype("C:/Windows/Fonts/leelawad.ttf", 55)

    def _render_to_temp_file(self, size, lines_with_pos):
        # เซฟลงไฟล์ชั่วคราวก่อนอ่าน (ไม่ส่ง PIL Image เข้า predict() ตรง ๆ)
        # ให้ตรงกับวิธีที่ยืนยันแล้วว่าใช้งานได้จริงตอน validate ด้วยมือ
        img = self._Image.new("RGB", size, color="white")
        d = self._ImageDraw.Draw(img)
        d.rectangle([5, 5, size[0] - 6, size[1] - 6], outline="black", width=3)
        for text, pos, use_font in lines_with_pos:
            d.text(pos, text, fill="black", font=use_font)
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        img.save(tmp.name)
        tmp.close()
        self.addCleanup(lambda: Path(tmp.name).unlink(missing_ok=True))
        return tmp.name

    def test_prefix_digit_two_lines(self):
        from services.common.analytics.plate_normalize import normalize_plate

        path = self._render_to_temp_file(
            (340, 220),
            [("1กก 1234", (30, 20), self._font_top), ("กรุงเทพมหานคร", (25, 150), self._font_bottom)],
        )
        result = read_plate_text(path)
        norm = normalize_plate(result.lines, char_confs=result.char_confs, ocr_conf=result.ocr_conf)
        self.assertEqual(norm.plate_norm, "1กก1234")
        self.assertEqual(norm.province, "กรุงเทพมหานคร")
        self.assertGreater(result.ocr_conf, 0.9)

    def test_no_prefix_digit_two_lines(self):
        from services.common.analytics.plate_normalize import normalize_plate

        path = self._render_to_temp_file(
            (340, 220),
            [("กท 5678", (60, 20), self._font_top), ("นนทบุรี", (70, 150), self._font_bottom)],
        )
        result = read_plate_text(path)
        norm = normalize_plate(result.lines, char_confs=result.char_confs, ocr_conf=result.ocr_conf)
        self.assertEqual(norm.plate_norm, "กท5678")
        self.assertEqual(norm.province, "นนทบุรี")

    def test_motorcycle_three_lines(self):
        from services.common.analytics.plate_normalize import normalize_plate

        path = self._render_to_temp_file(
            (220, 300),
            [
                ("1กก", (50, 20), self._font_moto),
                ("1234", (50, 100), self._font_moto),
                ("ชลบุรี", (30, 220), self._font_bottom),
            ],
        )
        result = read_plate_text(path)
        self.assertEqual(len(result.lines), 3)
        norm = normalize_plate(result.lines, char_confs=result.char_confs, ocr_conf=result.ocr_conf)
        self.assertEqual(norm.plate_norm, "1กก1234")
        self.assertEqual(norm.province, "ชลบุรี")


if __name__ == "__main__":
    unittest.main()
