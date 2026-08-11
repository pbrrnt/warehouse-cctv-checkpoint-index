# 11. วิธีทดสอบโค้ด

> อัปเดตล่าสุด: หลังต่อ `vehicle.py`/`thai_ocr.py` เข้า `worker.py` จริง (แยกตาม `cameras.role`, ADR-021) — ดู [`00-decisions.md`](00-decisions.md) สำหรับบริบทการตัดสินใจแต่ละเรื่อง

## 1. รันชุดเทสต์ทั้งหมด

```bash
python -m unittest discover -s tests
```

รันจาก root โปรเจกต์ (`C:\IP Camera RTSP Projects`) ไม่ต้องมี Postgres/MQTT/Redis/Frigate จริง — ทุกเทสต์ mock ส่วนที่คุยกับ infra ภายนอก

รันไฟล์เดียว (เร็วกว่าตอนแก้เฉพาะจุด):

```bash
python -m unittest tests.test_plate_normalize -v
python -m unittest tests.test_indexer -v
python -m unittest tests.test_thai_ocr -v
python -m unittest tests.test_vehicle -v
python -m unittest tests.test_api -v
python -m unittest tests.test_logging_config -v
python -m unittest tests.test_seed_site -v
python -m unittest tests.test_check_time_sync -v
```

## 2. ไฟล์เทสต์มีอะไรบ้าง

| ไฟล์ | ทดสอบอะไร | ต้องมีอะไรก่อนรัน |
|---|---|---|
| `tests/test_plate_normalize.py` | ทำความสะอาด/ตรวจสอบข้อความ OCR ป้ายทะเบียนไทย (37 เคส) | ไม่ต้องมีอะไรเพิ่ม |
| `tests/test_indexer.py` | MQTT→Redis Stream→Postgres orchestration ทั้งเส้นทาง (parse event, direction, snapshot, upsert, แยกงาน vehicle/plate ตาม `cameras.role`) — ล้วน mock session/frigate client/store/vehicle_analyzer/plate_reader | ไม่ต้องมีอะไรเพิ่ม (inject fake analyzer/reader เข้า `EventWorker` โดยตรง ไม่โหลดโมเดลจริงเลย) |
| `tests/test_thai_ocr.py` | อ่านตัวอักษรป้ายทะเบียนด้วย PaddleOCR | กลุ่ม integration ต้องมีฟอนต์ Leelawadee (`C:/Windows/Fonts/leelawad.ttf`) — ถ้าไม่มีจะ skip อัตโนมัติ |
| `tests/test_vehicle.py` | จำแนกประเภท/สีรถ + สร้าง embedding ด้วย OpenVINO | กลุ่ม integration ต้องรัน `python scripts/fetch_models.py` ก่อน — ถ้าไม่มีไฟล์โมเดลจะ skip อัตโนมัติ |
| `tests/test_api.py` | search API — pure function (rerank, timefmt, auth), query builder (compile เป็น SQL เช็คโครงสร้าง), FastAPI route ผ่าน `TestClient` (mock session), **CRUD ทะเบียนที่รู้จัก (`plate_registry`) กับ SQLite จริง** (in-memory ไม่ mock — ตารางนี้ไม่มี JSONB/pgvector เลยรันบน SQLite ได้ตรง ๆ) | ต้องมี `httpx` ติดตั้งด้วย (`pip install httpx`) — `TestClient` ของ FastAPI ต้องการ ไม่ได้อยู่ใน `services/api/requirements.txt` เพราะตัว service จริงตอนรันไม่ต้องใช้ |
| `tests/test_logging_config.py` | `setup_logging()` เขียน log ไปไฟล์ที่อยู่ทนได้จริง, สร้างโฟลเดอร์ให้เอง, ไม่ raise ถ้าเขียนไม่ได้ | ไม่ต้องมีอะไรเพิ่ม — **ระวังถ้าจะแก้ไฟล์นี้:** setup_logging() แก้ root logger (global) ต้อง snapshot/restore handlers ใน setUp/tearDown ไม่งั้นกวน test module อื่น (ดู docstring ในไฟล์เทสต์) |
| `tests/test_seed_site.py` | `scripts/seed_site.py` — validate_config() (pure), seed() upsert site/camera (mock session ไม่ใช้ SQLite เพราะ Camera มี JSONB), ยืนยันว่า `site_config.example.json` เองผ่าน validate จริง | ไม่ต้องมีอะไรเพิ่ม |
| `tests/test_check_time_sync.py` | `scripts/check_time_sync.py` — parse ONVIF SOAP response (รวม prefix คนละแบบ), คำนวณ offset หัก round-trip, จัดรูปแบบรายงาน+เกณฑ์ผ่าน/ไม่ผ่าน, อ่าน device list จาก config | ไม่ต้องมีอะไรเพิ่ม — **`query_onvif_time()` (ยิง HTTP จริง) ไม่มีเทสต์เลย** ไม่มีอุปกรณ์ ONVIF จริงให้ทดสอบตอนเขียน ใช้ `--single <ip>` ทดสอบเองตอนมีอุปกรณ์จริง |

**สถานะล่าสุด (เช็คก่อนอ้างอิง — ไฟล์อาจเปลี่ยนหลังจากนี้):** รวม 174 เคส ผ่านหมด ใช้เวลา ~15-25 วินาที (ส่วนใหญ่หมดไปกับโหลดโมเดล PaddleOCR/OpenVINO ในกลุ่ม integration ครั้งแรก)

## 3. แต่ละไฟล์แบ่ง 2 กลุ่มเทสต์เสมอ (รูปแบบที่ใช้ซ้ำทุกโมดูล AI)

1. **หน่วยทดสอบเร็ว (mock ทั้งหมด)** — ไม่โหลดโมเดลจริง รันได้ทุกเครื่องทุกครั้ง ทดสอบ logic ล้วน (การแปลง output โมเดล, การจัดกลุ่ม, การตัดสินใจ) ผ่าน fake object ที่ inject เข้าไปแทนโมเดล/session/client จริง
2. **เทสต์ end-to-end กับของจริง (integration)** — โหลดโมเดลจริง (PaddleOCR/OpenVINO) รันกับภาพสังเคราะห์ (สร้างด้วย PIL ตอนรันเทสต์เลย ไม่ commit ภาพเข้า git) ยืนยันว่า pipeline ทั้งสายรันได้ไม่ error — **ไม่ได้วัดความแม่นยำกับภาพจริงจากกล้อง** ใช้ `@unittest.skipUnless(...)` ข้ามอัตโนมัติถ้าเครื่องไม่พร้อม (ไม่มีฟอนต์/ไม่มีไฟล์โมเดล/import ไม่ได้) แทนที่จะ fail

เพิ่มเทสต์ให้โมดูล AI ใหม่ในอนาคต ทำตามรูปแบบนี้เพื่อความสม่ำเสมอ

## 4. สิ่งที่ชุดเทสต์นี้ "ไม่" ครอบคลุม — ต้องพิสูจน์ตอนขึ้นระบบจริง

- **Postgres จริง** — `services/common/db/models.py` ตรวจสอบแค่ว่า DDL compile ถูกต้อง (`db/migrations/README.md`) ยังไม่เคยรัน migration กับ Postgres จริง (เครื่องพัฒนาไม่มี Docker)
- **MQTT/Redis/Frigate จริง** — `services/indexer/*.py` mock ทั้งหมด ยัง**ไม่เคย**ทดสอบกับ Frigate ที่รันจริง โดยเฉพาะ:
  - payload MQTT จริงจาก `frigate/events` (ดูหมายเหตุความน่าเชื่อถือใน `frigate_events.py`)
  - query param `crop=1` ของ `GET /api/events/<id>/snapshot.jpg` (ใช้ป้อนภาพเข้า `vehicle.py` และ `thai_ocr.py`) — อ้างอิงจากเอกสาร Frigate ยังไม่เคยยิงจริง
  - `cameras.role` ('lpr'/'overview') ต้องตั้งถูกต้องจริงตอน onboard กล้อง ไม่งั้น `worker.py` จะไม่วิเคราะห์อะไรเลย (ดู `03-camera-onboarding.md`)
- **ความแม่นยำของโมเดล AI กับภาพจริง** — `thai_ocr.py`/`vehicle.py` ทดสอบกับภาพสังเคราะห์เท่านั้น (ฟอนต์/สี่เหลี่ยมสีวาดเอง) แสง/มุม/ระยะจากกล้องจริงต่างจากภาพสังเคราะห์มาก ต้องวัดผลซ้ำกับภาพจากไซต์นำร่อง — โดยเฉพาะการอ่านป้ายตรงจากภาพครอปกล้อง lpr โดยไม่มี `plate_detect.py` แยก (ADR-021) ยังไม่เคยพิสูจน์เลยว่าพอหรือไม่
- **กล้องจริง/เครือข่ายจริง** — ดู checklist ทดสอบหน้างาน (day/night) ใน [`10-chokepoint-design.md`](10-chokepoint-design.md) และบันทึกปัญหาที่เจอจริงแล้วใน [`00-decisions.md`](00-decisions.md) ท้ายเอกสาร
- **`services/api/search.py` คิวรีจริง** — `tests/test_api.py` compile query เป็น Postgres SQL text เช็คโครงสร้างเท่านั้น (join ถูกตาราง, operator ถูก) ไม่เคยรันจริงกับ Postgres โดยเฉพาะ pg_trgm operator `%` และ `DISTINCT ON` ที่ SQLAlchemy สร้างให้ — ต้องพิสูจน์ด้วยข้อมูลจริงตอนขึ้นระบบ

## 5. ก่อนรันเทสต์กลุ่ม integration ของโมดูล AI

```bash
python scripts/fetch_models.py     # โหลดไฟล์โมเดล OpenVINO (vehicle.py ใช้)
```

PaddleOCR (`thai_ocr.py`) ไม่ต้องรันอะไรเพิ่ม — ตัว library ดาวน์โหลด/แคชโมเดลเองอัตโนมัติที่ `~/.paddlex` ตอนเรียกใช้ครั้งแรก (ครั้งแรกจะช้ากว่าปกติเพราะต้องโหลด)
