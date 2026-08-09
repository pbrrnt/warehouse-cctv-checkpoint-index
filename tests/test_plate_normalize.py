"""
เทสต์ plate_normalize.py — ครอบคลุมทุกกลุ่มเคสตาม docs/05-thai-lpr.md หัวข้อ 9

รันด้วย:  python -m unittest tests.test_plate_normalize -v
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "common" / "analytics"))

from plate_normalize import (  # noqa: E402
    PlateReading,
    majority_vote,
    match_province,
    matches_template,
    normalize_plate,
    weighted_edit_distance,
)


class TestRupbaebPatchuban(unittest.TestCase):
    """รูปแบบปัจจุบัน: เลขนำหน้า + พยัญชนะ 2 ตัว + เลข 4 หลัก"""

    def test_basic(self):
        r = normalize_plate(["1กก 1234", "กรุงเทพมหานคร"])
        self.assertEqual(r.plate_norm, "1กก1234")
        self.assertEqual(r.province, "กรุงเทพมหานคร")
        self.assertNotIn("template_mismatch", r.flags)


class TestRupbaebDoem(unittest.TestCase):
    """รูปแบบเดิม: พยัญชนะ 2 ตัวไม่มีเลขนำหน้า"""

    def test_no_prefix_digit(self):
        r = normalize_plate(["กท 5678"])
        self.assertEqual(r.plate_norm, "กท5678")


class TestLekTabianSan(unittest.TestCase):
    """เลขทะเบียนสั้น (1 หลัก)"""

    def test_single_digit_suffix(self):
        r = normalize_plate(["1ขค 7"])
        self.assertEqual(r.plate_norm, "1ขค7")


class TestLekThai(unittest.TestCase):
    """เลขไทยต้องแปลงเป็นเลขอารบิก"""

    def test_thai_digits(self):
        r = normalize_plate(["๑กก ๑๒๓๔"])
        self.assertEqual(r.plate_norm, "1กก1234")


class TestChongWangKoen(unittest.TestCase):
    """ช่องว่างเกิน ต้องตัดออกให้หมด ไม่ใช่แค่ trim หัวท้าย"""

    def test_internal_spaces(self):
        r = normalize_plate(["1 ก ก  1234"])
        self.assertEqual(r.plate_norm, "1กก1234")


class TestAksaraKhaya(unittest.TestCase):
    """อักขระขยะ (ขีด, จุด) ต้องถูกตัดทิ้ง"""

    def test_junk_characters(self):
        r = normalize_plate(["1กก-1234."])
        self.assertEqual(r.plate_norm, "1กก1234")


class TestAksonLatinPon(unittest.TestCase):
    """อักษรละตินที่หน้าตาคล้ายเลขปนมา ต้องแปลงเป็นเลข ไม่ใช่ถูกตัดทิ้งเฉย ๆ"""

    def test_latin_look_alike_digit(self):
        r = normalize_plate(["1กกI234"])
        self.assertEqual(r.plate_norm, "1กก1234")

    def test_multiple_latin_confusions(self):
        # O->0, S->5, B->8, Z->2, G->6, l->1
        self.assertEqual(normalize_plate(["1กกO234"]).plate_norm, "1กก0234")
        self.assertEqual(normalize_plate(["1กกS234"]).plate_norm, "1กก5234")


class TestAksonSabson(unittest.TestCase):
    """
    อักษรสับสน 1 ตัว — โมดูลนี้ "ไม่เดา" ว่าตัวไหนถูก เพราะทั้งคู่เข้า
    เทมเพลตเหมือนกัน (ทั้ง ก และ ถ อยู่ใน [ก-ฮ]) หน้าที่คือให้แน่ใจว่า
    ตัวที่ถูกต้อง "ยังติดอันดับ" ตอนค้นหาแบบ fuzzy — ทดสอบผ่าน
    weighted_edit_distance แทน ไม่ใช่ผ่าน normalize_plate
    """

    def test_confused_pair_still_matches_template(self):
        # ระบบอ่านผิดเป็น "1ถก1234" (ก สับสนกับ ถ) — ต้องยังเข้าเทมเพลต
        # เพราะ OCR ไม่ได้ให้อักขระนอกชุด แค่ให้พยัญชนะไทยตัวอื่น
        r = normalize_plate(["1ถก1234"])
        self.assertEqual(r.plate_norm, "1ถก1234")
        self.assertTrue(matches_template(r.plate_norm))

    def test_weighted_distance_ranks_confused_pair_close(self):
        # คำค้น "1กก1234" ต้องอยู่ใกล้ "1ถก1234" กว่าป้ายที่ไม่เกี่ยวข้อง
        # อย่างชัดเจน เพราะ ก<->ถ อยู่ในตารางสับสนสูงมาก (cost 0.2)
        d_confused = weighted_edit_distance("1กก1234", "1ถก1234")
        d_unrelated = weighted_edit_distance("1กก1234", "9ฮฮ9999")
        self.assertLess(d_confused, 1.0)
        self.assertLess(d_confused, d_unrelated)

    def test_confusable_pair_cheaper_than_default(self):
        # ก<->ถ (สับสนสูงมาก) ต้องถูกกว่าคู่ที่ไม่อยู่ในตาราง (ต้นทุนเริ่มต้น 1.0)
        self.assertEqual(weighted_edit_distance("ก", "ถ"), 0.2)
        self.assertEqual(weighted_edit_distance("ก", "ฮ"), 1.0)


class TestChangwatPiaBiat(unittest.TestCase):
    """จังหวัดอ่านเพี้ยน แต่ยังอยู่ในระยะที่ยอมรับได้"""

    def test_province_typo_within_threshold(self):
        r = normalize_plate(["1กก 1234", "กรุงเทพมหานคว"])
        self.assertEqual(r.province, "กรุงเทพมหานคร")


class TestChangwatKlumKlaiKan(unittest.TestCase):
    """จังหวัดกลุ่มชื่อคล้ายกัน ต้องไม่สลับกัน — หัวข้อ 6.1"""

    def test_nakhon_pathom_not_confused_with_nakhon_phanom(self):
        self.assertEqual(match_province("นครปฐม"), "นครปฐม")
        self.assertEqual(match_province("นครพนม"), "นครพนม")

    def test_samut_group(self):
        self.assertEqual(match_province("สมุทรปราการ"), "สมุทรปราการ")
        self.assertEqual(match_province("สมุทรสงคราม"), "สมุทรสงคราม")
        self.assertEqual(match_province("สมุทรสาคร"), "สมุทรสาคร")


class TestChangwatAnMaiOok(unittest.TestCase):
    """จังหวัดอ่านไม่ออกเลย — province ต้องเป็น None แต่บรรทัดบนยังใช้ได้"""

    def test_unreadable_province_does_not_break_code(self):
        r = normalize_plate(["1กก 1234", "xxxxx"])
        self.assertEqual(r.plate_norm, "1กก1234")
        self.assertIsNone(r.province)
        self.assertIn("province_unmatched", r.flags)

    def test_no_province_line_at_all(self):
        r = normalize_plate(["1กก 1234"])
        self.assertEqual(r.plate_norm, "1กก1234")
        self.assertIsNone(r.province)
        self.assertNotIn("province_unmatched", r.flags)  # ไม่มีบรรทัดจังหวัดเลย ไม่ใช่ province อ่านไม่ออก


class TestMaiChaiPai(unittest.TestCase):
    """ข้อความที่ไม่ใช่ป้ายทะเบียนเลย ต้องถูกปฏิเสธ"""

    def test_non_plate_text_rejected(self):
        r = normalize_plate(["SUZUKI"])
        self.assertIsNone(r.plate_norm)
        self.assertIn("template_mismatch", r.flags)

    def test_pure_thai_text_no_digits_rejected(self):
        r = normalize_plate(["สวัสดีครับ"])
        self.assertIsNone(r.plate_norm)


class TestPaiMotorcycleSamBantad(unittest.TestCase):
    """ป้ายมอเตอร์ไซค์ 3 บรรทัด: หมวด / เลข / จังหวัด — หัวข้อ 2.3"""

    def test_three_line_plate(self):
        r = normalize_plate(["1กก", "1234", "ชลบุรี"])
        self.assertEqual(r.plate_norm, "1กก1234")
        self.assertEqual(r.province, "ชลบุรี")

    def test_three_line_plate_no_prefix(self):
        r = normalize_plate(["กท", "5678", "นนทบุรี"])
        self.assertEqual(r.plate_norm, "กท5678")
        self.assertEqual(r.province, "นนทบุรี")


class TestPaiWangAnMaiDai(unittest.TestCase):
    """ป้ายว่าง/อ่านไม่ได้เลย — ห้าม crash ต้องคืน None"""

    def test_empty_string(self):
        r = normalize_plate([""])
        self.assertIsNone(r.plate_norm)
        self.assertIn("empty", r.flags)

    def test_empty_list(self):
        r = normalize_plate([])
        self.assertIsNone(r.plate_norm)
        self.assertIn("empty", r.flags)

    def test_whitespace_only(self):
        r = normalize_plate(["   ", "\t"])
        self.assertIsNone(r.plate_norm)
        self.assertIn("empty", r.flags)

    def test_none_lines_do_not_crash(self):
        # กัน edge case ที่ list มีบรรทัดว่างปนกับบรรทัดจริง
        r = normalize_plate(["", "1กก 1234", ""])
        self.assertEqual(r.plate_norm, "1กก1234")


class TestRawTextPreserved(unittest.TestCase):
    """★ raw_text ห้ามหาย ต้องเก็บของดิบไว้เสมอสำหรับ debug/ปรับจูน"""

    def test_raw_text_kept_even_when_repaired(self):
        r = normalize_plate(["1กก-1234."])
        self.assertIn("1กก-1234.", r.raw_text)

    def test_raw_text_kept_when_rejected(self):
        r = normalize_plate(["SUZUKI"])
        self.assertEqual(r.raw_text, "SUZUKI")


# ============================================================
#  Majority vote — หัวข้อ 5.3
# ============================================================

class TestMajorityVote(unittest.TestCase):
    def test_majority_wins_over_minority(self):
        readings = [
            normalize_plate(["1กก 1234"], ocr_conf=0.9),
            normalize_plate(["1กก 1234"], ocr_conf=0.85),
            normalize_plate(["1ถก 1234"], ocr_conf=0.4),  # อ่านผิด ความมั่นใจต่ำกว่า
        ]
        result = majority_vote(readings)
        self.assertEqual(result.plate_norm, "1กก1234")

    def test_unanimous_agreement_high_confidence(self):
        readings = [normalize_plate(["1กก 1234"], ocr_conf=0.9) for _ in range(5)]
        result = majority_vote(readings)
        self.assertEqual(result.plate_norm, "1กก1234")
        self.assertAlmostEqual(result.ocr_conf, 0.9, places=2)

    def test_all_disagree_still_returns_something_with_flag(self):
        # ทุกเฟรมผิดคนละแบบ ความยาวเท่ากันหมด (7 ตัวอักษร) — ต้องไม่ crash
        # และคืนผลโหวตแม้ agreement ต่ำ
        readings = [
            normalize_plate(["1กก 1234"], ocr_conf=0.5),
            normalize_plate(["1ขข 1234"], ocr_conf=0.5),
            normalize_plate(["1คค 1234"], ocr_conf=0.5),
        ]
        result = majority_vote(readings)
        self.assertIsNotNone(result.plate_norm)
        self.assertEqual(len(result.plate_norm), 7)

    def test_single_reading_passthrough(self):
        readings = [normalize_plate(["1กก 1234"])]
        result = majority_vote(readings)
        self.assertEqual(result.plate_norm, "1กก1234")

    def test_no_readable_frames(self):
        readings = [normalize_plate(["SUZUKI"]), normalize_plate([""])]
        result = majority_vote(readings)
        self.assertIsNone(result.plate_norm)

    def test_empty_reading_list(self):
        result = majority_vote([])
        self.assertIsNone(result.plate_norm)
        self.assertIn("no_frames", result.flags)

    def test_province_voted_by_frequency(self):
        readings = [
            normalize_plate(["1กก 1234", "ชลบุรี"], ocr_conf=0.9),
            normalize_plate(["1กก 1234", "ชลบุรี"], ocr_conf=0.8),
            normalize_plate(["1กก 1234", "xxxxx"], ocr_conf=0.3),  # จังหวัดอ่านไม่ออก
        ]
        result = majority_vote(readings)
        self.assertEqual(result.province, "ชลบุรี")

    def test_mixed_length_frames_flagged(self):
        # เฟรมหนึ่งอ่านสั้นผิดปกติ (ตกตัวอักษรไป) ต้องไม่ถูกเอามาโหวตร่วม
        # ตำแหน่งกับเฟรมที่ความยาวปกติ
        readings = [
            normalize_plate(["1กก 1234"], ocr_conf=0.9),
            normalize_plate(["1กก 1234"], ocr_conf=0.9),
            normalize_plate(["1กก 123"], ocr_conf=0.5),  # สั้นกว่า 1 ตัว
        ]
        result = majority_vote(readings)
        self.assertEqual(result.plate_norm, "1กก1234")
        self.assertIn("mixed_length_frames_excluded", result.flags)


class TestWeightedEditDistance(unittest.TestCase):
    def test_identical_strings_zero_distance(self):
        self.assertEqual(weighted_edit_distance("1กก1234", "1กก1234"), 0.0)

    def test_empty_string_distance(self):
        self.assertEqual(weighted_edit_distance("", "1234"), 4.0)
        self.assertEqual(weighted_edit_distance("1234", ""), 4.0)
        self.assertEqual(weighted_edit_distance("", ""), 0.0)

    def test_insertion_deletion_cost(self):
        # ต่างกัน 1 ตัวอักษรที่หายไป (ไม่ใช่การแทนที่) ต้องเสียค่า insert/delete
        self.assertEqual(weighted_edit_distance("1234", "123"), 1.0)


if __name__ == "__main__":
    unittest.main()
