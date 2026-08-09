"""
ทำความสะอาดและตรวจสอบผลลัพธ์ OCR ป้ายทะเบียนไทย

อ้างอิงสเปกทั้งหมดจาก docs/05-thai-lpr.md — อ่านเอกสารนั้นก่อนแก้ไฟล์นี้
โดยเฉพาะหัวข้อ 4 (ตารางอักษรสับสน) และหัวข้อ 5 (อัลกอริทึม)

ขอบเขตของโมดูลนี้ (สิ่งที่ "repair" ทำได้และทำไม่ได้):

  ทำได้ — แก้กรณีที่ OCR อ่านผิด "ประเภทอักขระ" จนไม่เข้ารูปแบบป้ายเลย
          เช่น อ่านตัวเลขไทย, อ่านตัวอักษรละตินปนมาในตำแหน่งที่ควรเป็นเลข
          (O/I/l/Z/B/S/G ที่หน้าตาคล้ายเลข), หรือมีอักขระขยะปนมา

  ทำไม่ได้ — "เดา" ว่าพยัญชนะไทยตัวไหนถูกต้องเมื่อ OCR ให้พยัญชนะไทยที่ถูก
             ประเภทแต่ผิดตัว (เช่น อ่าน "ถ" แทน "ก") เพราะทั้งคู่เข้ารูปแบบ
             [ก-ฮ] เหมือนกัน ไม่มีทางรู้ได้จากป้ายใบเดียวว่าตัวไหนถูก —
             นี่คือหน้าที่ของการค้นหาแบบ fuzzy ตอน query (ดูหัวข้อ 7 และ
             CONFUSION_COST/weighted_edit_distance ด้านล่าง) ไม่ใช่ตอน
             normalize ขาเข้า
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ============================================================
#  ตารางอักขระ — หัวข้อ 3.1
# ============================================================

# ฃ (ขวด) และ ฅ (คน) เลิกใช้แล้ว ไม่ปรากฏบนป้ายจริง — ตัดออกจาก charset
# เพื่อลดโอกาส OCR อ่านผิดเป็นตัวเหล่านี้โดยไม่มีทางเป็นไปได้จริง
THAI_CONSONANTS = "กขคฆงจฉชซฌญฎฏฐฑฒณดตถทธนบปผฝพฟภมยรลวศษสหฬอฮ"

THAI_DIGIT_MAP = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")

# อักษรละตินที่ OCR มักอ่านสลับกับตัวเลข (หัวข้อ 4.2)
# ใช้แก้กรณีตัวเลขในป้ายถูกอ่านเป็นตัวอักษรละตินหน้าตาคล้ายกัน
LATIN_TO_DIGIT = {
    "O": "0", "o": "0",
    "I": "1", "l": "1",
    "Z": "2",
    "B": "8",
    "S": "5",
    "G": "6",
}

# รูปแบบป้ายไทยหลัง normalize แล้ว — หัวข้อ 2.2
#   กลุ่ม 1 = เลขนำหน้า (ไม่บังคับ)
#   กลุ่ม 2 = หมวดอักษร 1-3 ตัว
#   กลุ่ม 3 = เลขทะเบียน 1-4 หลัก
PLATE_PATTERN = re.compile(rf"^([0-9]?)([{THAI_CONSONANTS}]{{1,3}})([0-9]{{1,4}})$")

# ============================================================
#  ตารางอักษรไทยที่ OCR สับสนบ่อย — หัวข้อ 4.1
#
#  ใช้เป็นต้นทุนการแทนที่ (substitution cost) สำหรับ weighted edit
#  distance แทนค่าเริ่มต้น 1.0 — ยิ่งค่าต่ำยิ่งสับสนกันบ่อย
#
#  ★ นี่คือจุดเริ่มต้นจากทฤษฎี ไม่ใช่ค่าสุดท้าย — ปรับจากข้อมูลจริงของ
#  แต่ละไซต์ในเฟส 3 ตามที่ระบุไว้ใน 05-thai-lpr.md หัวข้อ 4.3
# ============================================================
CONFUSION_COST: dict[frozenset[str], float] = {
    # สูงมาก
    frozenset("กถ"): 0.2,
    frozenset("กภ"): 0.2,
    frozenset("ถภ"): 0.2,
    frozenset("ขช"): 0.2,
    frozenset("บป"): 0.2,
    frozenset("ผฝ"): 0.2,
    frozenset("พฟ"): 0.2,
    frozenset("ลส"): 0.2,
    # สูง
    frozenset("ดต"): 0.3,
    frozenset("ณฌ"): 0.3,
    frozenset("ฑท"): 0.3,
    # กลาง
    frozenset("มฆ"): 0.5,
    frozenset("รธ"): 0.5,
    frozenset("นม"): 0.5,
    frozenset("วร"): 0.5,
    frozenset("ยผ"): 0.5,
    frozenset("ซช"): 0.5,
    frozenset("หษ"): 0.5,
    frozenset("ฎฏ"): 0.5,
    frozenset("ฐฎ"): 0.5,
}

DEFAULT_SUBSTITUTION_COST = 1.0
INSERT_DELETE_COST = 1.0

# รายชื่อ 77 จังหวัด + กรุงเทพมหานคร — หัวข้อ 6
PROVINCES = [
    # ภาคเหนือ
    "เชียงราย", "เชียงใหม่", "น่าน", "พะเยา", "แพร่", "แม่ฮ่องสอน", "ลำปาง", "ลำพูน", "อุตรดิตถ์",
    # ภาคตะวันออกเฉียงเหนือ
    "กาฬสินธุ์", "ขอนแก่น", "ชัยภูมิ", "นครพนม", "นครราชสีมา", "บึงกาฬ", "บุรีรัมย์", "มหาสารคาม",
    "มุกดาหาร", "ยโสธร", "ร้อยเอ็ด", "เลย", "ศรีสะเกษ", "สกลนคร", "สุรินทร์", "หนองคาย",
    "หนองบัวลำภู", "อำนาจเจริญ", "อุดรธานี", "อุบลราชธานี",
    # ภาคกลาง + กรุงเทพมหานคร
    "กรุงเทพมหานคร", "กำแพงเพชร", "ชัยนาท", "นครนายก", "นครปฐม", "นครสวรรค์", "นนทบุรี",
    "ปทุมธานี", "พระนครศรีอยุธยา", "พิจิตร", "พิษณุโลก", "เพชรบูรณ์", "ลพบุรี", "สมุทรปราการ",
    "สมุทรสงคราม", "สมุทรสาคร", "สระบุรี", "สิงห์บุรี", "สุโขทัย", "สุพรรณบุรี", "อ่างทอง", "อุทัยธานี",
    # ภาคตะวันออก
    "จันทบุรี", "ฉะเชิงเทรา", "ชลบุรี", "ตราด", "ปราจีนบุรี", "ระยอง", "สระแก้ว",
    # ภาคตะวันตก
    "กาญจนบุรี", "ตาก", "ประจวบคีรีขันธ์", "เพชรบุรี", "ราชบุรี",
    # ภาคใต้
    "กระบี่", "ชุมพร", "ตรัง", "นครศรีธรรมราช", "นราธิวาส", "ปัตตานี", "พังงา", "พัทลุง",
    "ภูเก็ต", "ยะลา", "ระนอง", "สงขลา", "สตูล", "สุราษฎร์ธานี",
]

PROVINCE_MATCH_MAX_DISTANCE = 3  # หัวข้อ 5.1 ขั้นตอน 5


# ============================================================
#  โครงสร้างผลลัพธ์
# ============================================================

@dataclass
class PlateReading:
    """ผลการ normalize ป้ายทะเบียน 1 ครั้ง (จากภาพ/เฟรมเดียว)"""

    raw_text: str  # ข้อความดิบจาก OCR ต่อกัน — ★ ห้ามทิ้ง ใช้ debug/ปรับจูน (หัวข้อ 5.2)
    plate_norm: str | None  # เช่น "1กก1234" ไม่มีช่องว่าง หรือ None ถ้าไม่เข้ารูปแบบ
    province: str | None
    province_raw: str | None
    ocr_conf: float
    char_confs: list[float] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


# ============================================================
#  ขั้นตอนที่ 1-2: ทำความสะอาดและแยกบรรทัด — หัวข้อ 5.1
# ============================================================

def to_arabic_digits(text: str) -> str:
    return text.translate(THAI_DIGIT_MAP)


def map_confusable_latin(text: str) -> str:
    """แปลงอักษรละตินที่หน้าตาคล้ายเลข (O/I/l/Z/B/S/G) เป็นเลข"""
    return "".join(LATIN_TO_DIGIT.get(ch, ch) for ch in text)


def strip_junk(text: str) -> str:
    """เก็บเฉพาะเลขและพยัญชนะไทย ตัดช่องว่าง/ขีด/จุด/อักขระอื่นทั้งหมด"""
    return "".join(ch for ch in text if ch.isdigit() or ch in THAI_CONSONANTS)


def clean_code_text(text: str) -> str:
    """ท่อทำความสะอาดสำหรับส่วนหมวด+เลขทะเบียน (บรรทัดบน) ตามลำดับในหัวข้อ 5.1"""
    text = to_arabic_digits(text)
    text = map_confusable_latin(text)
    text = strip_junk(text)
    return text


def _line_has_digit(line: str) -> bool:
    return any(ch.isdigit() for ch in to_arabic_digits(line))


def split_lines(lines: list[str]) -> tuple[str, str]:
    """
    แยกบรรทัดที่มีตัวเลข (หมวด+เลขทะเบียน) ออกจากบรรทัดจังหวัด

    รองรับทั้งป้าย 2 บรรทัด (รถยนต์ทั่วไป) และ 3 บรรทัด (มอเตอร์ไซค์บาง
    รุ่น: หมวด / เลข / จังหวัด — หัวข้อ 2.3)

    ป้าย 3 บรรทัดขึ้นไปใช้ตำแหน่งตัดสิน (บรรทัดสุดท้าย = จังหวัด, ที่เหลือ
    รวมกันเป็นโค้ด) เพราะบรรทัด "หมวด" ล้วน ๆ อย่าง "กท" อาจไม่มีเลขปนเลย
    (รูปแบบป้ายเดิมไม่มีเลขนำหน้า) ทำให้แยกด้วยเนื้อหาอย่างเดียวไม่ได้

    ป้าย 2 บรรทัดหรือน้อยกว่าใช้เนื้อหาตัดสิน (บรรทัดมีเลข = โค้ด) เพราะ
    ป้ายรถยนต์ทั่วไปบรรทัดบนมีเลขทะเบียนรวมอยู่เสมอ

    ★ ข้อจำกัดที่ทราบ: ถ้า OCR อ่านป้ายมอเตอร์ไซค์ได้แค่ 2 บรรทัด (หมวด+เลข
    แต่พลาดบรรทัดจังหวัดไปทั้งบรรทัด) และบรรทัดหมวดไม่มีเลขปนเลย จะแยกผิด
    เป็นกรณีหายาก ยังไม่ได้ทำกรณีนี้ในเวอร์ชันแรก

    คืนค่า (ข้อความส่วนโค้ดรวมกัน, ข้อความบรรทัดจังหวัด)
    """
    non_empty = [line for line in lines if line and line.strip()]

    if len(non_empty) >= 3:
        *code_lines, province_line = non_empty
        return "".join(code_lines), province_line

    code_parts = [line for line in non_empty if _line_has_digit(line)]
    province_parts = [line for line in non_empty if not _line_has_digit(line)]

    code_text = "".join(code_parts)
    # ถ้ามีหลายบรรทัดไม่มีเลข ใช้ตัวที่ยาวที่สุด (เดาว่าเป็นจังหวัดที่อ่านได้ครบกว่า)
    province_text = max(province_parts, key=len, default="")
    return code_text, province_text


# ============================================================
#  ขั้นตอนที่ 3-4: ตรวจเทมเพลตและซ่อม — หัวข้อ 5.1
# ============================================================

def matches_template(text: str) -> bool:
    return PLATE_PATTERN.match(text) is not None


# ============================================================
#  weighted edit distance — หัวข้อ 4.3 และ 7.1
#
#  ใช้ทั้งตอนจับคู่จังหวัด (ด้านล่าง) และควรถูก import ไปใช้ที่ชั้น
#  search API ตอนค้นทะเบียนแบบ fuzzy (05-thai-lpr.md หัวข้อ 7) — โมดูล
#  นี้เป็น single source of truth ของ CONFUSION_COST
# ============================================================

def substitution_cost(a: str, b: str) -> float:
    if a == b:
        return 0.0
    return CONFUSION_COST.get(frozenset((a, b)), DEFAULT_SUBSTITUTION_COST)


def weighted_edit_distance(a: str, b: str) -> float:
    """Levenshtein distance ที่ถ่วงน้ำหนักด้วย CONFUSION_COST แทนต้นทุน 1.0 คงที่"""
    if a == b:
        return 0.0
    n, m = len(a), len(b)
    if n == 0:
        return m * INSERT_DELETE_COST
    if m == 0:
        return n * INSERT_DELETE_COST

    prev = [j * INSERT_DELETE_COST for j in range(m + 1)]
    curr = [0.0] * (m + 1)

    for i in range(1, n + 1):
        curr[0] = i * INSERT_DELETE_COST
        for j in range(1, m + 1):
            cost_sub = prev[j - 1] + substitution_cost(a[i - 1], b[j - 1])
            cost_del = prev[j] + INSERT_DELETE_COST
            cost_ins = curr[j - 1] + INSERT_DELETE_COST
            curr[j] = min(cost_sub, cost_del, cost_ins)
        prev, curr = curr, prev

    return prev[m]


def repair_by_template(code_text: str) -> tuple[str, bool]:
    """
    พยายามซ่อม code_text ที่ยังไม่เข้าเทมเพลตให้เข้ารูปแบบ

    ทำได้เฉพาะกรณีอักขระ "ผิดประเภท" ที่แก้ได้แน่นอน (ดู docstring บนสุด
    ของไฟล์) — คืนค่า (ข้อความที่ได้, ซ่อมสำเร็จไหม)
    """
    if matches_template(code_text):
        return code_text, True

    # ทำความสะอาดซ้ำอีกรอบเผื่อมีอักขระหลุดมา (กันเคสเรียกฟังก์ชันนี้ตรง ๆ)
    retried = clean_code_text(code_text)
    if matches_template(retried):
        return retried, True

    return code_text, False


# ============================================================
#  จับคู่จังหวัด — หัวข้อ 5.1 ขั้นตอน 5, หัวข้อ 6
# ============================================================

def match_province(text: str) -> str | None:
    if not text:
        return None

    cleaned = "".join(ch for ch in text if not ch.isdigit() and not ch.isspace())
    if not cleaned:
        return None

    best_match: str | None = None
    best_distance = float("inf")

    for province in PROVINCES:
        # เทียบทั้งคำเสมอ ห้ามเทียบแค่บางส่วน — กันจังหวัดกลุ่มคล้ายกัน
        # สลับกัน เช่น นครปฐม/นครพนม/นครนายก (หัวข้อ 6.1)
        distance = weighted_edit_distance(cleaned, province)
        if distance < best_distance:
            best_distance = distance
            best_match = province

    if best_match is not None and best_distance <= PROVINCE_MATCH_MAX_DISTANCE:
        return best_match
    return None


# ============================================================
#  ฟังก์ชันหลัก — หัวข้อ 5.1
# ============================================================

def normalize_plate(
    lines: list[str],
    char_confs: list[float] | None = None,
    ocr_conf: float = 1.0,
) -> PlateReading:
    """
    normalize ผลลัพธ์ OCR ป้ายทะเบียน 1 ครั้ง (1 ภาพ/เฟรม)

    lines: บรรทัดข้อความจาก OCR เรียงบนลงล่าง ปกติ 2 บรรทัด (รถยนต์)
           หรือ 3 บรรทัด (มอเตอร์ไซค์บางรุ่น — หมวด/เลข/จังหวัด)
    """
    raw_text = " / ".join(line for line in lines if line)

    if not lines or not any(line and line.strip() for line in lines):
        return PlateReading(
            raw_text=raw_text,
            plate_norm=None,
            province=None,
            province_raw=None,
            ocr_conf=ocr_conf,
            char_confs=char_confs or [],
            flags=["empty"],
        )

    raw_code_text, raw_province_text = split_lines(lines)
    cleaned_code = clean_code_text(raw_code_text)

    flags: list[str] = []
    plate_norm: str | None

    if matches_template(cleaned_code):
        plate_norm = cleaned_code
    else:
        repaired, ok = repair_by_template(cleaned_code)
        if ok:
            plate_norm = repaired
            flags.append("repaired")
        else:
            # ไม่เข้าเทมเพลตแม้พยายามซ่อมแล้ว — เก็บของที่ทำความสะอาดได้
            # ไว้เผื่อ debug แต่ตั้ง flag ให้รู้ว่าไม่ใช่ผลที่เชื่อถือได้
            plate_norm = cleaned_code or None
            flags.append("template_mismatch")

    province = match_province(raw_province_text)
    if raw_province_text and province is None:
        flags.append("province_unmatched")

    return PlateReading(
        raw_text=raw_text,
        plate_norm=plate_norm,
        province=province,
        province_raw=raw_province_text or None,
        ocr_conf=ocr_conf,
        char_confs=char_confs or [],
        flags=flags,
    )


# ============================================================
#  Majority vote ต่อ track — หัวข้อ 5.3
# ============================================================

def majority_vote(readings: list[PlateReading]) -> PlateReading:
    """
    รวมผล normalize หลายเฟรมของ track เดียวกัน (รถคันเดียวกัน) เป็นผลเดียว

    วิธี: โหวตทีละตำแหน่งตัวอักษร ถ่วงน้ำหนักด้วย char_confs ของแต่ละเฟรม
    เฟรมที่ plate_norm เป็น None หรือความยาวไม่ตรงกับความยาวที่พบบ่อย
    ที่สุด (weighted mode) จะถูกตัดออกจากการโหวตตำแหน่ง เพราะจัดตำแหน่ง
    อักขระเทียบกันไม่ได้ตรง ๆ — เป็นข้อจำกัดที่ยอมรับได้สำหรับเวอร์ชันแรก
    (ดู docstring ด้านบนของไฟล์เรื่องขอบเขตของโมดูลนี้)
    """
    valid = [r for r in readings if r.plate_norm]

    if not valid:
        # ไม่มีเฟรมไหนอ่านได้เลย — คืนเฟรมแรกสุด (มี raw_text ให้ debug)
        # หรือผลลัพธ์ว่างถ้าไม่มีเฟรมเลย
        if readings:
            return readings[0]
        return PlateReading(
            raw_text="", plate_norm=None, province=None, province_raw=None,
            ocr_conf=0.0, char_confs=[], flags=["no_frames"],
        )

    if len(valid) == 1:
        return valid[0]

    # ความยาวที่พบบ่อยที่สุด ถ่วงน้ำหนักด้วย ocr_conf ของแต่ละเฟรม
    length_weight: dict[int, float] = {}
    for r in valid:
        length_weight[len(r.plate_norm)] = length_weight.get(len(r.plate_norm), 0.0) + r.ocr_conf
    target_len = max(length_weight, key=lambda l: length_weight[l])

    aligned = [r for r in valid if len(r.plate_norm) == target_len]
    total_weight = sum(r.ocr_conf for r in aligned) or 1.0

    voted_chars: list[str] = []
    for pos in range(target_len):
        votes: dict[str, float] = {}
        for r in aligned:
            ch = r.plate_norm[pos]
            # ใช้ char_conf รายตัวถ้ามี ไม่งั้น fallback เป็น ocr_conf รวมของเฟรมนั้น
            w = r.char_confs[pos] if pos < len(r.char_confs) else r.ocr_conf
            votes[ch] = votes.get(ch, 0.0) + w
        voted_chars.append(max(votes, key=lambda c: votes[c]))

    voted_plate = "".join(voted_chars)
    agreement = sum(
        1 for r in aligned if r.plate_norm == voted_plate
    ) / len(aligned)

    flags: list[str] = []
    if not matches_template(voted_plate):
        flags.append("template_mismatch")
    if len(aligned) < len(valid):
        flags.append("mixed_length_frames_excluded")

    # จังหวัด: ใช้ตัวที่เจอบ่อยที่สุดในบรรดาเฟรมที่จับคู่จังหวัดได้
    province_votes: dict[str, float] = {}
    for r in valid:
        if r.province:
            province_votes[r.province] = province_votes.get(r.province, 0.0) + r.ocr_conf
    province = max(province_votes, key=lambda p: province_votes[p]) if province_votes else None

    return PlateReading(
        raw_text=" | ".join(r.raw_text for r in readings),
        plate_norm=voted_plate,
        province=province,
        province_raw=next((r.province_raw for r in valid if r.province_raw), None),
        ocr_conf=round((sum(r.ocr_conf for r in aligned) / len(aligned)) * agreement, 4),
        char_confs=[],
        flags=flags,
    )
