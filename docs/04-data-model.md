# 04 — โครงสร้างฐานข้อมูล

> PostgreSQL 16+ พร้อมส่วนขยาย `pgvector` และ `pg_trgm`
> จัดการ schema ด้วย Alembic ที่ `db/migrations/`

---

## 1. ส่วนขยายที่ต้องเปิด

```sql
CREATE EXTENSION IF NOT EXISTS vector;      -- pgvector: เก็บและค้น embedding
CREATE EXTENSION IF NOT EXISTS pg_trgm;     -- trigram: ค้นทะเบียนแบบ fuzzy
CREATE EXTENSION IF NOT EXISTS btree_gin;   -- index ผสมระหว่าง btree กับ gin
```

---

## 2. ตาราง

### 2.1 `sites` — ไซต์ (โกดัง)

```sql
CREATE TABLE sites (
    id          TEXT PRIMARY KEY,               -- 'WH01'
    name        TEXT NOT NULL,                  -- 'โกดังบางนา'
    timezone    TEXT NOT NULL DEFAULT 'Asia/Bangkok',
    nvr_host    TEXT,                           -- IP ของ NVR
    nvr_vendor  TEXT,                           -- 'dahua' | 'hikvision' | 'onvif'
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

> **มีตารางนี้ตั้งแต่วันแรกแม้จะมีไซต์เดียว** — เพิ่มทีหลังต้องแก้ทุกคิวรีในระบบ

### 2.2 `cameras` — กล้อง

```sql
CREATE TYPE camera_role AS ENUM ('lpr', 'overview', 'face', 'context');

CREATE TABLE cameras (
    id              TEXT PRIMARY KEY,           -- 'WH01-GATE-LPR'
    site_id         TEXT NOT NULL REFERENCES sites(id),
    name            TEXT NOT NULL,              -- 'ประตูใหญ่ - อ่านป้าย'
    tier            CHAR(1) NOT NULL,           -- 'A' | 'B' | 'C'
    role            camera_role,
    location        TEXT,
    vendor          TEXT,
    nvr_channel     INT,                        -- ★ ช่องใน NVR
    rtsp_profile    JSONB,                      -- ข้อมูลจาก probe_cameras.py
    clock_offset_ms INT NOT NULL DEFAULT 0,     -- ★ ชดเชยเวลากล้องที่เพี้ยน
    zone_config     JSONB,                      -- โซนตรวจทิศทางเข้า/ออก
    enabled         BOOLEAN NOT NULL DEFAULT true
);
```

| คอลัมน์สำคัญ | ทำไมต้องมี |
|---|---|
| `nvr_channel` | ถ้าไม่รู้ว่ากล้องนี้คือช่องไหนใน NVR เจ้าหน้าที่ไปเปิดต่อไม่ได้ → ผลการค้นหาไร้ประโยชน์ |
| `clock_offset_ms` | กล้องบางรุ่นตั้ง NTP ไม่ได้ ต้องชดเชยตอนแสดงผล (ดู [`08-nvr-integration.md`](08-nvr-integration.md)) |
| `tier` / `role` | กำหนดว่า indexer จะรันโมเดลอะไรกับกล้องตัวนี้ |

### 2.3 `events` — เหตุการณ์ (1 tracked object = 1 แถว)

```sql
CREATE TYPE event_direction AS ENUM ('in', 'out');

CREATE TABLE events (
    id           TEXT PRIMARY KEY,              -- ใช้ frigate event id ตรง ๆ
    site_id      TEXT NOT NULL REFERENCES sites(id),
    camera_id    TEXT NOT NULL REFERENCES cameras(id),
    label        TEXT NOT NULL,                 -- 'person' | 'car' | 'motorcycle'
    sub_label    TEXT,
    score        REAL,
    start_ts     TIMESTAMPTZ NOT NULL,          -- ★ UTC เสมอ
    end_ts       TIMESTAMPTZ,
    direction    event_direction,               -- ★ เข้า/ออก
    zones        TEXT[],
    has_clip     BOOLEAN NOT NULL DEFAULT false,
    thumb_key    TEXT,
    raw          JSONB,                         -- payload ดิบจาก Frigate
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**★ `direction`** ได้จากทิศทางการเคลื่อนที่ของ track ผ่านโซนที่กำหนดใน Frigate
ทำให้ตอบคำถามที่มีค่าที่สุดของโกดังได้: *"รถคันนี้เข้ากี่โมง ออกกี่โมง อยู่ในโกดังนานเท่าไร ตอนนี้ยังอยู่ข้างในไหม"*

**★ `raw`** เก็บ payload ดิบไว้เสมอ — ถ้าภายหลังพบว่าต้องใช้ฟิลด์ที่ไม่ได้ดึงออกมา จะได้ไม่ต้องประมวลผลใหม่

### 2.4 `detections` — สิ่งที่ตรวจพบในเหตุการณ์

```sql
CREATE TYPE detection_kind AS ENUM ('face', 'vehicle', 'plate');

CREATE TABLE detections (
    id          BIGSERIAL PRIMARY KEY,
    event_id    TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    kind        detection_kind NOT NULL,
    ts          TIMESTAMPTZ NOT NULL,
    bbox        INT[4],                         -- [x1, y1, x2, y2]
    crop_key    TEXT,                           -- path ใน object store
    quality     REAL                            -- คะแนนคุณภาพภาพ (blur, ขนาด, มุม)
);
```

`ON DELETE CASCADE` ทำให้การลบตาม retention ทำได้ที่ `events` ที่เดียว

### 2.5 ใบหน้า

```sql
CREATE TABLE persons (
    id            BIGSERIAL PRIMARY KEY,
    site_id       TEXT NOT NULL REFERENCES sites(id),
    display_name  TEXT,                         -- NULL = บุคคลนิรนามจาก clustering
    notes         TEXT,
    consent_basis TEXT,                         -- ★ ฐานทางกฎหมายตาม PDPA
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE face_embeddings (
    detection_id BIGINT PRIMARY KEY REFERENCES detections(id) ON DELETE CASCADE,
    embedding    VECTOR(512) NOT NULL,          -- ArcFace
    person_id    BIGINT REFERENCES persons(id) ON DELETE SET NULL
);
```

> **`consent_basis` ไม่ใช่ฟิลด์ประดับ** — เป็นหลักฐานตามกฎหมายว่าเก็บข้อมูลชีวมิติของคนนี้ด้วยฐานอะไร (ดู [`06-pdpa-compliance.md`](06-pdpa-compliance.md))

### 2.6 ยานพาหนะ

```sql
CREATE TABLE vehicles (
    detection_id BIGINT PRIMARY KEY REFERENCES detections(id) ON DELETE CASCADE,
    vehicle_type TEXT,                          -- 'car' | 'van' | 'truck' | 'bus'
    color        TEXT,                          -- 'white' | 'black' | ...
    embedding    VECTOR(256),                   -- vehicle-reid-0001
    plate_id     BIGINT REFERENCES plates(id) ON DELETE SET NULL
);
```

### 2.7 ป้ายทะเบียน

```sql
CREATE TABLE plates (
    id           BIGSERIAL PRIMARY KEY,
    detection_id BIGINT NOT NULL REFERENCES detections(id) ON DELETE CASCADE,
    raw_text     TEXT,                          -- ★ ข้อความดิบจาก OCR (ห้ามทิ้ง)
    plate_norm   TEXT,                          -- '1กก1234' ไม่มีช่องว่าง
    province     TEXT,
    plate_color  TEXT,                          -- จากกล้อง overview
    ocr_conf     REAL,
    char_confs   JSONB,                         -- ความมั่นใจรายตัวอักษร
    flags        TEXT[]                         -- ['template_mismatch', ...]
);

CREATE TABLE plate_registry (                   -- watchlist ทะเบียน
    site_id      TEXT NOT NULL REFERENCES sites(id),
    plate_norm   TEXT NOT NULL,
    owner_label  TEXT,                          -- 'รถส่งของบริษัท ก'
    notes        TEXT,
    watch        BOOLEAN NOT NULL DEFAULT false,
    PRIMARY KEY (site_id, plate_norm)
);
```

**★ ทำไมต้องเก็บ `raw_text` คู่กับ `plate_norm`**
- ใช้ debug ว่าอ่านผิดตรงไหน
- ใช้สร้าง confusion matrix จริงเพื่อปรับตารางอักษรสับสน
- ถ้าอัลกอริทึม normalize มีบั๊ก ยังกู้ได้โดยไม่ต้องประมวลผลภาพใหม่

### 2.8 PDPA

```sql
CREATE TABLE audit_log (
    id          BIGSERIAL PRIMARY KEY,
    actor       TEXT NOT NULL,                  -- ผู้ใช้ที่ทำ
    action      TEXT NOT NULL,                  -- 'search_face' | 'view_person' | ...
    target_type TEXT,
    target_id   TEXT,
    query       JSONB,                          -- เงื่อนไขที่ค้น
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    ip          INET
);

CREATE TABLE retention_policies (
    scope  TEXT PRIMARY KEY,                    -- 'clips' | 'snapshots' | 'crops'
                                                -- | 'face_embeddings' | 'events'
    days   INT NOT NULL
);
```

---

## 3. ★ Index และเหตุผล

```sql
-- ค้นใบหน้าที่คล้ายกัน
CREATE INDEX idx_face_emb_hnsw ON face_embeddings
    USING hnsw (embedding vector_cosine_ops);

-- ค้นรถคันเดิม
CREATE INDEX idx_vehicle_emb_hnsw ON vehicles
    USING hnsw (embedding vector_cosine_ops);

-- ★ ค้นทะเบียนแบบ fuzzy — สำคัญที่สุด
CREATE INDEX idx_plates_norm_trgm ON plates
    USING gin (plate_norm gin_trgm_ops);

-- ตารางเรียงตามเวลา BRIN กินพื้นที่น้อยกว่า btree หลายสิบเท่า
CREATE INDEX idx_events_start_brin ON events USING brin (start_ts);

-- คิวรีหลักของหน้า UI
CREATE INDEX idx_events_camera_ts ON events (camera_id, start_ts DESC);
CREATE INDEX idx_events_label_ts  ON events (label, start_ts DESC);
CREATE INDEX idx_events_site_ts   ON events (site_id, start_ts DESC);

-- หา detection ของ event
CREATE INDEX idx_detections_event ON detections (event_id, kind);
```

| Index | ทำไม |
|---|---|
| **HNSW** | ค้น vector ที่ใกล้เคียงในเวลา sub-linear — ถ้าไม่มี ต้องสแกนทั้งตาราง |
| **GIN + trgm** | ทำให้ `plate_norm % 'query'` เร็ว — เป็นหัวใจของการค้นทะเบียนที่ OCR อ่านผิด |
| **BRIN** | ข้อมูลเรียงตามเวลาโดยธรรมชาติ BRIN ใช้พื้นที่แค่เศษเสี้ยวของ btree |

### 3.1 พารามิเตอร์ HNSW

```sql
-- ตอนสร้าง index (ยิ่งสูง ยิ่งแม่น แต่สร้างช้าและกินแรม)
CREATE INDEX ... USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- ตอนค้นหา (ปรับได้ต่อ session)
SET hnsw.ef_search = 100;
```

ค่าเริ่มต้นนี้เหมาะกับปริมาณระดับล้าน vector ซึ่งเกินพอสำหรับไซต์จุดคอขวด

---

## 4. คิวรีตัวอย่าง

### 4.1 ค้นทะเบียนแบบ fuzzy (สองชั้น)

```sql
-- ชั้นที่ 1: กรองเร็วด้วย trigram index
SELECT p.id, p.plate_norm, p.province, p.ocr_conf,
       e.start_ts, e.camera_id, e.direction, c.nvr_channel,
       similarity(p.plate_norm, :q) AS sim
FROM plates p
JOIN detections d ON d.id = p.detection_id
JOIN events     e ON e.id = d.event_id
JOIN cameras    c ON c.id = e.camera_id
WHERE p.plate_norm % :q
  AND e.start_ts BETWEEN :from_ts AND :to_ts
ORDER BY sim DESC
LIMIT 200;

-- ชั้นที่ 2: จัดอันดับใหม่ในแอปด้วย weighted edit distance
--            ที่ใช้ตารางอักษรสับสนไทย (ดู 05-thai-lpr.md หัวข้อ 4)
```

### 4.2 ค้นใบหน้าจากรูปที่อัปโหลด

```sql
SELECT d.id, e.start_ts, e.camera_id, c.nvr_channel, d.crop_key,
       1 - (f.embedding <=> :query_vec) AS similarity
FROM face_embeddings f
JOIN detections d ON d.id = f.detection_id
JOIN events     e ON e.id = d.event_id
JOIN cameras    c ON c.id = e.camera_id
WHERE e.site_id = :site_id
  AND e.start_ts BETWEEN :from_ts AND :to_ts
ORDER BY f.embedding <=> :query_vec
LIMIT 50;
```

> `<=>` คือ cosine distance ของ pgvector — ตัวดำเนินการนี้ใช้ HNSW index ได้

### 4.3 ★ "ตอนนี้มีรถอะไรอยู่ในโกดัง"

```sql
WITH last_seen AS (
    SELECT DISTINCT ON (p.plate_norm)
           p.plate_norm, p.province, e.direction, e.start_ts, e.camera_id
    FROM plates p
    JOIN detections d ON d.id = p.detection_id
    JOIN events     e ON e.id = d.event_id
    WHERE e.site_id = :site_id
      AND e.start_ts > now() - INTERVAL '24 hours'
      AND p.plate_norm IS NOT NULL
    ORDER BY p.plate_norm, e.start_ts DESC
)
SELECT * FROM last_seen
WHERE direction = 'in'        -- เห็นล่าสุดคือขาเข้า = ยังไม่ออก
ORDER BY start_ts DESC;
```

### 4.4 ค้นแบบผสม

```sql
SELECT e.id, e.start_ts, c.name, c.nvr_channel,
       v.vehicle_type, v.color, p.plate_norm
FROM events e
JOIN cameras    c ON c.id = e.camera_id
JOIN detections d ON d.event_id = e.id AND d.kind = 'vehicle'
JOIN vehicles   v ON v.detection_id = d.id
LEFT JOIN plates p ON p.id = v.plate_id
WHERE e.site_id   = :site_id
  AND e.camera_id = :camera_id
  AND e.start_ts BETWEEN :from_ts AND :to_ts
  AND v.color        = :color
  AND v.vehicle_type = :vtype
  AND e.direction    = 'in'
ORDER BY e.start_ts DESC;
```

---

## 5. Retention และการลบข้อมูล

### 5.1 นโยบายที่แนะนำ

| ข้อมูล | ค่าเริ่มต้น | เหตุผล |
|---|---|---|
| คลิปวิดีโอ | 90 วัน | กินพื้นที่มากที่สุด และ NVR มีของจริงอยู่แล้ว |
| snapshot / crop | 365 วัน | กินพื้นที่น้อย มีค่าในการค้นย้อนหลัง |
| `events` / `detections` / `plates` | 365 วัน | metadata เล็กมาก |
| **`face_embeddings`** | **ตามที่ฝ่ายกฎหมายกำหนด** | ★ ข้อมูลชีวมิติ ต้องมีกำหนดลบชัดเจนตาม PDPA |
| `audit_log` | 3 ปี | เป็นหลักฐานการปฏิบัติตามกฎหมาย |

### 5.2 งานลบข้อมูลรายวัน

```sql
-- ลบที่ events แล้ว detections/plates/embeddings จะถูกลบตาม CASCADE
DELETE FROM events
WHERE start_ts < now() - (SELECT days FROM retention_policies WHERE scope='events') * INTERVAL '1 day';
```

**หลังลบแถวแล้วต้องลบไฟล์ใน object store ด้วย** — งานรายวันต้องเก็บ `crop_key` ที่จะลบไว้ก่อน แล้วจึงลบไฟล์ ไม่งั้นจะเหลือไฟล์กำพร้าสะสม

### 5.3 ★ retention ของดัชนีกับ NVR ต้องสัมพันธ์กัน

ถ้าดัชนีเก็บ 1 ปี แต่ NVR เก็บ 30 วัน ผู้ใช้จะค้นเจอเหตุการณ์ที่วิดีโอถูกลบไปแล้ว
→ **หน้าผลลัพธ์ต้องแสดงคำเตือนเมื่อเหตุการณ์เก่ากว่าที่ NVR เก็บ** เพื่อไม่ให้เสียเวลาไปหาวิดีโอที่ไม่มี

---

## 6. หลักการที่ยึดในการออกแบบ schema

| หลักการ | ทำไม |
|---|---|
| **`site_id` ในทุกตารางหลักตั้งแต่วันแรก** | เพิ่มทีหลังต้องแก้ทุกคิวรี ใส่ตอนนี้แทบไม่มีต้นทุน |
| **เวลาเก็บเป็น UTC เสมอ** (`TIMESTAMPTZ`) | แปลงเป็น `Asia/Bangkok` ตอนแสดงผล — ป้องกันบั๊กเวลาที่หายาก |
| **เก็บข้อมูลดิบไว้เสมอ** (`raw`, `raw_text`) | ปรับอัลกอริทึมภายหลังได้โดยไม่ต้องประมวลผลภาพใหม่ |
| **`ON DELETE CASCADE` ทุกความสัมพันธ์** | retention ทำที่จุดเดียวคือ `events` |
| **ใช้ `frigate event id` เป็น PK ของ `events`** | ทำให้ idempotent — รับ event ซ้ำแล้วไม่เกิดแถวซ้ำ |

---

## เอกสารที่เกี่ยวข้อง

- [`01-architecture.md`](01-architecture.md) — ภาพรวมการไหลของข้อมูล
- [`05-thai-lpr.md`](05-thai-lpr.md) — การค้นทะเบียนแบบ fuzzy
- [`06-pdpa-compliance.md`](06-pdpa-compliance.md) — ข้อกำหนดด้าน retention และ audit
