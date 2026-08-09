"""
อ่านตัวอักษรจากภาพครอปป้ายทะเบียนด้วย PaddleOCR — หัวข้อ 8 ใน 05-thai-lpr.md

ทำ 2 อย่าง:
  1. เรียก PaddleOCR (โมเดล th_PP-OCRv5_mobile_rec) หากล่องข้อความในภาพ
  2. จัดกลุ่มกล่องที่อยู่ "บรรทัดเดียวกัน" ตามตำแหน่งแนวตั้ง แล้วเรียงจาก
     บนลงล่าง ซ้ายไปขวา ให้ได้ lines: list[str] ที่ส่งต่อเข้า
     plate_normalize.normalize_plate() ได้ตรง ๆ

★ ทดสอบแล้วกับภาพสังเคราะห์ (ฟอนต์ Leelawadee) อ่านถูก 100% ความมั่นใจ
0.97-0.99 — ยังไม่เคยทดสอบกับภาพป้ายจริงจากกล้อง (แสง มุมเอียง ระยะ
ต่างจากภาพสังเคราะห์มาก) ต้องวัดผลจริงอีกครั้งกับภาพจากไซต์นำร่องตาม
05-thai-lpr.md หัวข้อ 10

★ เจอบั๊กจริงตอนพัฒนา: enable_mkldnn=True (ค่าเริ่มต้นของ PaddleOCR) ทำให้
เกิด NotImplementedError บนเครื่องพัฒนา (CPU เก่า รุ่น Broadwell) —
ปิดไว้เป็นค่าเริ่มต้นในโมดูลนี้ ไม่รู้ว่าเกิดเฉพาะ CPU รุ่นนี้หรือ
paddlepaddle 3.3.1 ทั่วไป ต้องทดสอบซ้ำบนเครื่องเป้าหมายจริง (i5-9500T)
ก่อนพิจารณาเปิดกลับ (เร็วกว่าถ้าใช้ได้จริง)
"""

from __future__ import annotations

from dataclasses import dataclass, field

TEXT_DETECTION_MODEL = "PP-OCRv5_mobile_det"
TEXT_RECOGNITION_MODEL = "th_PP-OCRv5_mobile_rec"
DEFAULT_ENABLE_MKLDNN = False  # ★ ดู docstring ด้านบน

# กล่องสองกล่องถือว่าอยู่ "บรรทัดเดียวกัน" ถ้าจุดกึ่งกลางแนวตั้งห่างกันไม่
# เกินสัดส่วนนี้ของความสูงกล่อง (เฉลี่ยของทั้งคู่)
LINE_GROUPING_Y_RATIO = 0.6


@dataclass
class OcrBox:
    text: str
    confidence: float
    y_center: float
    y_height: float
    x_start: float


@dataclass
class OcrResult:
    lines: list[str]
    char_confs: list[float]  # ยาวเท่ากับความยาวรวมของทุกบรรทัดต่อกัน (ไม่รวมช่องว่างคั่นบรรทัด)
    ocr_conf: float  # ค่าเฉลี่ยความมั่นใจของทุกกล่อง ถ่วงตามความยาวข้อความ
    box_count: int
    raw_boxes: list[OcrBox] = field(default_factory=list)


_engine = None  # cache instance เดียว — โหลดโมเดลครั้งแรกใช้เวลาหลายวินาที


def _build_engine(enable_mkldnn: bool = DEFAULT_ENABLE_MKLDNN):
    from paddleocr import PaddleOCR

    return PaddleOCR(
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        text_detection_model_name=TEXT_DETECTION_MODEL,
        text_recognition_model_name=TEXT_RECOGNITION_MODEL,
        enable_mkldnn=enable_mkldnn,
    )


def get_engine():
    """คืน PaddleOCR instance เดียวที่ใช้ซ้ำได้ตลอดอายุ process (โหลดโมเดลครั้งเดียว)"""
    global _engine
    if _engine is None:
        _engine = _build_engine()
    return _engine


def _extract_boxes(predict_result) -> list[OcrBox]:
    boxes: list[OcrBox] = []
    for page in predict_result:
        texts = page.get("rec_texts") or []
        scores = page.get("rec_scores") or []
        polys = page.get("rec_polys") or []
        for text, score, poly in zip(texts, scores, polys):
            ys = [pt[1] for pt in poly]
            xs = [pt[0] for pt in poly]
            boxes.append(
                OcrBox(
                    text=text,
                    confidence=float(score),
                    y_center=(min(ys) + max(ys)) / 2,
                    y_height=max(ys) - min(ys),
                    x_start=min(xs),
                )
            )
    return boxes


def _group_into_lines(boxes: list[OcrBox], y_ratio: float = LINE_GROUPING_Y_RATIO) -> list[list[OcrBox]]:
    """
    จัดกลุ่มกล่องที่ตรวจพบเป็นบรรทัด ตามความสูงจริงของกล่อง ไม่ใช่ค่าคงที่
    ตายตัว — ป้ายที่ถ่ายมาขนาดต่างกันแต่ละภาพ ระยะห่างบรรทัดเป็นพิกเซล
    ก็ต่างกันไปด้วย
    """
    if not boxes:
        return []

    ordered = sorted(boxes, key=lambda b: b.y_center)
    lines: list[list[OcrBox]] = [[ordered[0]]]

    for box in ordered[1:]:
        ref = lines[-1][-1]
        avg_height = (ref.y_height + box.y_height) / 2 or 1.0
        if abs(box.y_center - ref.y_center) <= avg_height * y_ratio:
            lines[-1].append(box)
        else:
            lines.append([box])

    for line in lines:
        line.sort(key=lambda b: b.x_start)

    return lines


def read_plate_text(image, engine=None) -> OcrResult:
    """
    อ่านข้อความจากภาพครอปป้ายทะเบียน 1 ภาพ

    image: path (str) หรือ numpy array (BGR ตาม convention ของ OpenCV/PaddleOCR)
    คืนค่า OcrResult ที่มี .lines ส่งเข้า
    plate_normalize.normalize_plate(lines=result.lines, char_confs=result.char_confs, ocr_conf=result.ocr_conf)
    ได้ตรง ๆ
    """
    ocr = engine or get_engine()
    predict_result = ocr.predict(image)
    boxes = _extract_boxes(predict_result)

    if not boxes:
        return OcrResult(lines=[], char_confs=[], ocr_conf=0.0, box_count=0)

    grouped = _group_into_lines(boxes)

    lines: list[str] = []
    char_confs: list[float] = []
    total_weighted_conf = 0.0
    total_len = 0

    for line_boxes in grouped:
        line_text = " ".join(b.text for b in line_boxes)
        lines.append(line_text)
        for b in line_boxes:
            # ★ PaddleOCR ให้ความมั่นใจระดับกล่องข้อความ ไม่ใช่รายตัวอักษร
            # ขยายค่าเดียวกันไปทุกตัวอักษรในกล่องนั้นเป็นค่าประมาณ — ไม่แม่น
            # เท่าความมั่นใจรายตัวจริง แต่ดีกว่าไม่มีข้อมูลเลยสำหรับ
            # majority_vote ใน plate_normalize.py
            char_confs.extend([b.confidence] * len(b.text))
            total_weighted_conf += b.confidence * len(b.text)
            total_len += len(b.text)
        char_confs.append(1.0)  # ตำแหน่งช่องว่างระหว่างกล่องในบรรทัดเดียวกัน

    ocr_conf = (total_weighted_conf / total_len) if total_len else 0.0

    return OcrResult(
        lines=lines,
        char_confs=char_confs,
        ocr_conf=round(ocr_conf, 4),
        box_count=len(boxes),
        raw_boxes=boxes,
    )
