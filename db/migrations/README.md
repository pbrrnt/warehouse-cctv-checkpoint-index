# การ migrate ฐานข้อมูล

Schema จริงอยู่ที่ [`docs/04-data-model.md`](../../docs/04-data-model.md) —
ไฟล์ในโฟลเดอร์นี้แค่แปลงเป็นโค้ด Alembic ตรงตามนั้น

## สถานะการตรวจสอบ (สำคัญ — อ่านก่อนเชื่อว่าใช้ได้)

เขียนบนเครื่องพัฒนาที่ **ไม่มี Docker/Postgres ให้ทดสอบจริง** สิ่งที่ตรวจสอบแล้ว:

- โมเดล `services/common/db/models.py` import และ `configure_mappers()` ผ่าน
  (ไม่มี FK/relationship ที่ตั้งค่าผิด)
- คอมไพล์ DDL ของทุกตารางด้วย SQLAlchemy เทียบกับ PostgreSQL dialect แล้ว —
  ตรงกับ SQL ในเอกสารทุกตาราง
- รัน `alembic upgrade head --sql` (โหมด offline ไม่ต้องต่อฐานข้อมูลจริง) แล้ว
  ตรวจ SQL ที่ได้ทีละบรรทัด — ครบทั้ง extension, ENUM, ตาราง 11 ตัว, HNSW/GIN
  trgm/BRIN index ตามหัวข้อ 3 ของเอกสาร
- รัน `alembic downgrade base --sql` แล้วเช็กว่า DROP ครบตามลำดับย้อนกลับ

**สิ่งที่ยังไม่เคยทดสอบ** เพราะไม่มี Postgres จริง:
- รัน `alembic upgrade head` จริงกับ Postgres+pgvector แล้วสำเร็จจริงไหม
- Index HNSW/GIN สร้างได้จริงไหม (ต้องมี extension `vector`/`pg_trgm` ติดตั้ง
  ในอิมเมจ Postgres ก่อน — `pgvector/pgvector` image ใน `docker-compose.yml`
  มีมาให้แล้ว แต่ยังไม่เคย build/start จริง)
- Query จริงผ่าน SQLAlchemy session (insert/select) ยังไม่ได้ทดสอบเลย

**ต้องทำตอนมีเครื่องเป้าหมาย (ดู [`02-hardware-sizing.md`](../../docs/02-hardware-sizing.md)):**

```bash
docker compose up -d postgres
cd db
export DATABASE_URL="postgresql+psycopg://cctv:password@localhost:5432/cctv_index"
python -m alembic upgrade head
```

ถ้าผ่าน ให้ต่อด้วย `psql` เช็กว่า index สร้างจริง:

```sql
\d+ face_embeddings
\d+ plates
```

## คำสั่งที่ใช้บ่อย

```bash
# ติดตั้ง dependency ก่อน
pip install -r ../services/common/requirements.txt

# ดู SQL ที่จะรันโดยไม่ต้องต่อฐานข้อมูลจริง (ปลอดภัย ใช้ตรวจก่อนรันจริงได้เสมอ)
python -m alembic upgrade head --sql

# รันจริง
python -m alembic upgrade head

# ย้อนกลับ 1 ขั้น
python -m alembic downgrade -1

# สร้าง migration ใหม่ (หลังแก้ models.py) — ต้องมี Postgres จริงต่ออยู่
# เพราะ autogenerate ต้องเทียบ schema ปัจจุบันกับ models
python -m alembic revision --autogenerate -m "คำอธิบายสั้น ๆ"
```

## ★ ข้อควรระวังที่เจอระหว่างเขียน (กันคนต่อไปเจอซ้ำ)

**`alembic.ini` ต้องเป็น ASCII ล้วน ห้ามมีภาษาไทย** — Alembic อ่านไฟล์ config
ด้วย locale encoding ของเครื่อง (ไม่ใช่ UTF-8 เสมอ) บน Windows locale ไทย
จะได้ `UnicodeDecodeError` ทันทีถ้ามีอักษรไทยปนอยู่ในไฟล์นี้ — คอมเมนต์อธิบาย
ให้เขียนเป็นภาษาอังกฤษแทน (ไฟล์ `.py` และ `.md` อื่น ๆ ใช้ภาษาไทยได้ปกติ
ปัญหานี้เกิดเฉพาะกับ `alembic.ini` เท่านั้น)

## ลำดับการสร้างตาราง

ต้องเรียงตาม FK dependency: `sites → cameras → events → detections →
{persons, plates} → face_embeddings → vehicles → plate_registry → audit_log
→ retention_policies` — `vehicles` ต้องมาหลัง `plates` เพราะ `vehicles.plate_id`
อ้างถึง `plates(id)`
