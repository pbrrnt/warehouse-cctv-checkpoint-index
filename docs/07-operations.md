# 07 — การดูแลระบบ (Operations)

> ระบบติดตั้งที่โกดังหลายแห่งที่ **ไม่มีคน IT ประจำ**
> ทุกอย่างในเอกสารนี้จึงต้องทำงานอัตโนมัติ และแจ้งเตือนเฉพาะเมื่อต้องมีคนเข้าไปแก้จริง ๆ

---

## 1. งานที่ต้องรันอัตโนมัติ

ตั้งใน crontab ของเครื่องที่ไซต์:

```cron
# ตรวจการซิงก์เวลา — สำคัญที่สุด
0 6 * * *   /opt/cctv-index/scripts/cron/check_time_sync.sh

# สำรองฐานข้อมูล
0 2 * * *   /opt/cctv-index/scripts/cron/backup_db.sh

# ลบข้อมูลตาม retention (รวมลบไฟล์จริง)
30 3 * * *  /opt/cctv-index/scripts/cron/apply_retention.sh

# ตรวจสุขภาพระบบ
*/15 * * * * /opt/cctv-index/scripts/cron/healthcheck.sh

# จับคู่รถ↔ป้าย (vehicles.plate_id) — รันบ่อยหน่อยเพื่อให้ผลค้นหาสดพอควร (ดู ADR-024)
*/10 * * * * /opt/cctv-index/scripts/cron/link_vehicle_plates.sh

# จัดกลุ่มใบหน้า (เฟส 4 เป็นต้นไป)
0 4 * * *   /opt/cctv-index/scripts/cron/cluster_faces.sh

# ตรวจไฟล์กำพร้าในดิสก์ (รายสัปดาห์ — รายงานอย่างเดียว ดูหัวข้อ 4.2)
0 5 * * 0   /opt/cctv-index/scripts/cron/find_orphans.sh
```

> `find_orphans.sh` เรียก `python scripts/find_orphans.py --database-url "$DATABASE_URL" --media-path "$MEDIA_PATH"` (ไม่ใส่ `--delete-orphans` — รายงานอย่างเดียวตามค่าเริ่มต้น ให้คนตัดสินใจลบเอง)

> `link_vehicle_plates.sh` เรียก `python scripts/link_vehicle_plates.py --database-url "$DATABASE_URL" --execute` (ดูวิธีจับคู่และข้อควรระวังเรื่อง window ใน ADR-024)

---

## 2. การเฝ้าระวัง

### 2.1 ตัวชี้วัดที่ต้องดู

| ตัวชี้วัด | แหล่ง | เกณฑ์เตือน |
|---|---|---|
| **ส่วนต่างเวลากับ NVR** | `check_time_sync.py` | **> 2 วินาที** |
| `skipped_fps` | Frigate `/api/stats` | > 0 |
| `inference_speed` | Frigate `/api/stats` | > 25 ms |
| `camera_fps` | Frigate `/api/stats` | ต่างจากที่ตั้ง > 20% |
| กล้อง offline | Frigate `/api/stats` | ขาดการเชื่อมต่อ > 5 นาที |
| ความยาว Redis Stream | `XLEN` | > 1000 (indexer ตามไม่ทัน) |
| พื้นที่ดิสก์ | `df` | เหลือ < 15% |
| เหตุการณ์ต่อวัน | คิวรี DB | **ลดลงผิดปกติ** = กล้องอาจเสียโดยไม่มีใครรู้ |
| ความหน่วงของคิวรีค้นหา | API | p95 > 2 วินาที |

### 2.2 ★ ตัวชี้วัดที่คนมักลืมดู: จำนวนเหตุการณ์ต่อวัน

```sql
SELECT camera_id, date_trunc('day', start_ts) AS d, count(*)
FROM events
WHERE start_ts > now() - INTERVAL '14 days'
GROUP BY 1, 2
ORDER BY 1, 2;
```

**ถ้ากล้องตัวหนึ่งมีเหตุการณ์ลดฮวบ แปลว่ามีปัญหาที่ไม่ทำให้ระบบ error**
เช่น กล้องถูกหันไปทางอื่น, เลนส์สกปรก/มีใยแมงมุม, ไฟส่องสว่างเสีย, มีของมาบัง

นี่คือประเภทของความล้มเหลวที่ **ระบบไม่แจ้งเตือนเอง** แต่ทำให้ระบบไร้ค่าเงียบ ๆ

### 2.3 ช่องทางแจ้งเตือน

ระบบอยู่ในวง LAN ที่อาจไม่มีอินเทอร์เน็ต จึงควรมีหลายชั้น:

1. **หน้า dashboard ในระบบ** แสดงสถานะสุขภาพ (ชั้นพื้นฐาน — ยังไม่ได้ทำ)
2. **อีเมล/LINE Notify** ถ้าไซต์มีอินเทอร์เน็ต (ยังไม่ได้ทำ)
3. **ไฟล์ log** ที่ช่างเข้าไปดูได้ตอนไปหน้างาน — ดูหัวข้อ 2.4

### 2.4 ไฟล์ log — ดูตอน error/พฤติกรรมแปลก ๆ

`indexer` และ `api` เขียน log ทั้งไปที่ stdout (Docker เก็บผ่าน `json-file`
driver — หมุนที่ 10MB × 3 ไฟล์ หายไปถ้า container ถูกลบ/สร้างใหม่) **และ**
ไฟล์ที่อยู่ทนกว่าแยกจากอายุ container (`services/common/logging_config.py`,
mount ผ่าน `${DATA_ROOT}/logs` — หมุนที่ 10MB × 5 ไฟล์ต่อ service):

```bash
# ดู log สด — จาก host (ไม่ต้องเข้า container)
tail -f ${DATA_ROOT}/logs/indexer.log
tail -f ${DATA_ROOT}/logs/api.log

# หรือผ่าน docker compose (เห็นเฉพาะที่ container ยังไม่ถูกลบ)
docker compose logs -f indexer
docker compose logs -f api

# หา error ย้อนหลัง
grep -i error ${DATA_ROOT}/logs/indexer.log
```

**★ ใช้ไฟล์ (`${DATA_ROOT}/logs/`) เป็นหลักเวลาต้องสืบย้อนหลังหลังจาก
redeploy/restart** เพราะ `docker compose logs` เห็นแค่ log ของ container
ปัจจุบัน ถ้า `down` แล้ว `up` ใหม่ log เก่าใน Docker หายไปแล้ว แต่ไฟล์ใน
`${DATA_ROOT}/logs/` ยังอยู่

---

## 3. การสำรองข้อมูล

### 3.1 อะไรต้องสำรอง

| ข้อมูล | ความสำคัญ | ความถี่ |
|---|---|---|
| PostgreSQL (metadata + embedding) | **สูงสุด** — สร้างใหม่ไม่ได้ | รายวัน |
| `.env` และ `frigate/config.yml` | สูง — สร้างใหม่ได้แต่เสียเวลา | เมื่อมีการแก้ไข |
| crop / snapshot | ปานกลาง | รายสัปดาห์ (ถ้าพื้นที่พอ) |
| คลิปวิดีโอ | ต่ำ — NVR มีของจริง | ไม่ต้อง |
| `samples/<SITE_ID>/` (ภาพตรวจรับ) | **สูง** — ใช้เป็น ground truth | ครั้งเดียวตอนสร้าง |

### 3.2 สคริปต์สำรอง

```bash
#!/bin/bash
# scripts/cron/backup_db.sh
set -euo pipefail

BACKUP_DIR=/data/backups
DATE=$(date +%Y%m%d)
KEEP_DAYS=30

docker compose exec -T postgres pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" \
  | gzip > "$BACKUP_DIR/db-$DATE.sql.gz"

# ลบ backup เก่า — ★ retention ของ backup ต้องสอดคล้องกับ PDPA
find "$BACKUP_DIR" -name 'db-*.sql.gz' -mtime +$KEEP_DAYS -delete
```

### 3.3 ★ ข้อควรระวัง

- **retention ของ backup ต้องไม่ยาวกว่า retention ของข้อมูลจริง** — ถ้า face embedding ลบที่ 180 วันแต่ backup เก็บ 1 ปี ก็ยังถือว่าไม่ได้ลบ (ดู [`06-pdpa-compliance.md`](06-pdpa-compliance.md) หัวข้อ 6.3)
- **เข้ารหัสก่อนส่ง backup ออกนอกไซต์**
- **ทดสอบกู้คืนจริงอย่างน้อยปีละครั้ง** — backup ที่ไม่เคยทดสอบกู้คืนคือ backup ที่ไม่มี

---

## 4. Retention และการลบข้อมูล

ทำโดย **`scripts/retention_cleanup.py`** — ตั้ง cron รายวัน (แทนที่ `apply_retention.sh` ในตัวอย่าง crontab หัวข้อ 1 หรือให้ `apply_retention.sh` เรียกสคริปต์นี้ด้วย `--execute`)

```bash
# ดูก่อนว่าจะลบอะไรบ้าง (dry-run — ปลอดภัย ไม่แตะอะไรเลย)
python scripts/retention_cleanup.py --database-url "$DATABASE_URL" --media-path /media

# ลบจริง
python scripts/retention_cleanup.py --database-url "$DATABASE_URL" --media-path /media --execute
```

### 4.1 ลำดับการลบที่ถูกต้อง (ที่สคริปต์ทำจริง)

```
ต่อแถวที่จะลบ:
1. ลบไฟล์ (crop_key / thumb_key) ผ่าน ContentStore ก่อน
2. ถ้าลบไฟล์สำเร็จ → ค่อยลบแถว DB (event ใช้ CASCADE ลบ detections/plates/embeddings ตาม)
3. ถ้าลบไฟล์ไม่สำเร็จ → ข้ามแถวนั้นไปก่อน ไม่ลบ DB (รอบหน้ารันใหม่จะ retry)
สุดท้าย: บันทึกจำนวนที่ลบลง audit_log
```

**ทำไมลบไฟล์ก่อนแถว (ไม่ใช่แถวก่อนไฟล์)** — ถ้า process ตายกลางคัน แถวใน DB จะยังชี้ไปหาไฟล์ที่ยังอยู่ (สถานะสอดคล้องกัน รอบหน้ารันใหม่ก็ลบต่อได้) ตรงข้ามกับลบแถวก่อน ที่ถ้าตายหลังลบแถวแต่ก่อนลบไฟล์ จะได้ไฟล์กำพร้าที่ไม่มีแถวอ้างถึงอีกเลย (หาไม่เจอ ลบไม่ได้ สะสมจนดิสก์เต็ม)

**ลบใบหน้าก่อนเหตุการณ์ทั่วไป** — retention ใบหน้าสั้นกว่า (เฟส 1 ลบ Detection(kind='face') ที่เกิน `RETENTION_FACE_EMBEDDINGS_DAYS` ก่อน แล้วเฟส 2 ค่อยลบ Event ทั้งแถวที่เกิน `RETENTION_EVENTS_DAYS`)

### 4.2 ตรวจไฟล์กำพร้า

ทำโดย **`scripts/find_orphans.py`** — รันรายสัปดาห์ (cron) เทียบไฟล์บนดิสก์กับ key ที่ DB อ้างถึง รายงาน 2 ทิศทาง:
- **ไฟล์กำพร้า** (มีบนดิสก์ ไม่มีใน DB) — กินพื้นที่เปล่า ลบได้
- **ไฟล์หาย** (มีใน DB ไม่มีบนดิสก์) — ร้ายแรงกว่า ผลค้นหาที่ชี้ไปหาไฟล์เหล่านี้จะเปิดภาพไม่ได้

```bash
# รายงานอย่างเดียว (ค่าเริ่มต้น — ไม่ลบ)
python scripts/find_orphans.py --database-url "$DATABASE_URL" --media-path /media

# ลบไฟล์กำพร้าจริง (หลังคนดูรายงานแล้วตัดสินใจ)
python scripts/find_orphans.py --database-url "$DATABASE_URL" --media-path /media --delete-orphans
```

**ค่าเริ่มต้นรายงานอย่างเดียว ไม่ลบ** — ให้คนดูก่อนว่าไฟล์กำพร้าที่เจอสมเหตุสมผลไหม (เช่นถ้าเจอกำพร้าเยอะผิดปกติ อาจแปลว่า retention มีบั๊ก) แล้วค่อยสั่ง `--delete-orphans` เอง (สคริปต์ลบเฉพาะไฟล์กำพร้า ไม่ยุ่งกับไฟล์หาย)

---

## 5. การอัปเดตระบบ

### 5.1 หลักการสำหรับหลายไซต์

| กฎ | เหตุผล |
|---|---|
| **ทุกไซต์รันเวอร์ชันเดียวกัน** | ไซต์ที่เวอร์ชันไม่ตรงกันทำให้ debug ไม่ได้ |
| **ปักหมุด image tag/digest ใน `.env`** | `latest` ทำให้แต่ละไซต์ได้เวอร์ชันต่างกันโดยไม่ตั้งใจ |
| **ทดสอบที่ไซต์นำร่องก่อนเสมอ** | อย่าอัปเดตทุกไซต์พร้อมกัน |
| **แก้ที่ต้นทาง ไม่แก้เฉพาะไซต์** | ถ้าต้องต่างกัน ให้ทำผ่าน `.env` เท่านั้น |

### 5.2 ขั้นตอนอัปเดต

```bash
# 1. สำรองก่อนเสมอ
./scripts/cron/backup_db.sh

# 2. แก้เวอร์ชันที่ปักหมุดใน .env

# 3. ดึง image ใหม่
docker compose pull

# 4. รัน migration ฐานข้อมูล
docker compose run --rm api alembic upgrade head

# 5. รีสตาร์ต
docker compose up -d

# 6. ตรวจสุขภาพ
./scripts/cron/healthcheck.sh
```

### 5.3 การย้อนกลับ (Rollback)

```bash
# 1. เปลี่ยนเวอร์ชันใน .env กลับเป็นค่าเดิม
# 2. docker compose up -d
# 3. ถ้า migration แก้ schema ไปแล้ว: alembic downgrade -1
```

> **เขียน migration ให้ย้อนกลับได้เสมอ** และหลีกเลี่ยงการลบคอลัมน์ในรุ่นเดียวกับที่เลิกใช้ — ให้เลิกใช้ก่อน แล้วค่อยลบในรุ่นถัดไป

---

## 6. คู่มือแก้ปัญหา

### 6.1 กล้องหลุด / ไม่มีภาพ

```
1. ping กล้องได้ไหม
2. เปิดหน้าเว็บกล้องได้ไหม
3. ffprobe RTSP URL ได้ไหม
4. ตรวจว่าจำนวน session เต็มหรือไม่ (NVR + Frigate + แอปมือถือ)
5. ตรวจ log:  docker compose logs frigate | grep <camera_id>
6. ตรวจว่าใช้ rtsp_transport tcp
7. ตรวจสายและ PoE switch
```

### 6.2 `skipped_fps` > 0

```
1. ตรวจว่า decode ใช้ iGPU จริง:  docker compose exec frigate intel_gpu_top
2. ตรวจ motion mask — mask ไม่ดีทำให้ detector ทำงานตลอดเวลาโดยไม่จำเป็น
   (สาเหตุที่พบบ่อยที่สุด)
3. ลด detect fps จาก 5 เป็น 3
4. ลดความละเอียด detect
5. พิจารณาเพิ่ม Coral TPU
```

### 6.3 ★ เวลาเพี้ยนจาก NVR

```
1. รัน check_time_sync.py ดูว่าอุปกรณ์ไหนเพี้ยน
2. ตรวจว่ากล้อง/NVR ยัง online และตั้ง NTP ถูก
3. Dahua: กดปุ่ม "Sync with NVR" เพื่อดันเวลาไปให้กล้องทุกตัว
4. ตรวจว่า firewall ไม่บล็อก NTP (UDP 123)
5. ถ้ากล้องรุ่นนั้นตั้ง NTP ไม่ได้ → วัด offset แล้วบันทึกใน cameras.clock_offset_ms
6. บันทึกไว้ใน 00-decisions.md ว่าไซต์ไหนมีกล้องที่ต้องชดเชย
```

### 6.4 ค้นหาแล้วไม่เจอทั้งที่ควรเจอ

```
1. ตรวจว่ามีเหตุการณ์นั้นใน events จริงไหม (คิวรีตามเวลาและกล้อง)
   ├─ ไม่มี → ปัญหาอยู่ที่การตรวจจับ (ดูข้อ 6.5)
   └─ มี    → ปัญหาอยู่ที่การค้นหา ไปข้อ 2
2. ตรวจว่ามี detections/plates ของเหตุการณ์นั้นไหม
   ├─ ไม่มี → indexer ไม่ได้ประมวลผล ตรวจ Redis Stream และ log ของ indexer
   └─ มี    → ตรวจ plate_norm ว่าอ่านได้เป็นอะไร
3. ถ้า plate_norm ผิด → ดู raw_text เทียบ แล้วปรับตารางอักษรสับสน
4. ตรวจว่า index ถูกใช้:  EXPLAIN ANALYZE <query>
```

### 6.5 ตรวจจับไม่เจอ / เจอน้อยลง

```
1. ★ ดูภาพสดจากกล้องด้วยตาเปล่าก่อนอย่างอื่น
   • กล้องหันไปทางอื่นหรือเปล่า
   • เลนส์สกปรก มีใยแมงมุมหรือเปล่า
   • ไฟส่องสว่างยังทำงานไหม
   • มีของมาบังหรือเปล่า
2. เทียบกับภาพใน samples/<SITE_ID>/ ว่าต่างจากตอนตรวจรับไหม
3. ตรวจว่า shutter ยังล็อกที่ 1/500 อยู่ (บางครั้งกล้องรีเซ็ตค่าเองหลังไฟดับ)
4. ตรวจ motion mask และ zone ว่าไม่ได้ถูกแก้
```

> **ข้อ 1 แก้ปัญหาได้เกินครึ่งของกรณีทั้งหมด** — ตรวจของจริงที่หน้างานก่อนไล่ดูโค้ด

### 6.6 ดิสก์เต็ม

```
1. du -sh /data/* ดูว่าอะไรกินพื้นที่
2. ตรวจว่า apply_retention.sh รันสำเร็จหรือไม่
3. ตรวจไฟล์กำพร้า (find_orphans.sh)
4. ลด retention ของคลิปลง — ดู 02-hardware-sizing.md หัวข้อ 3.3
5. ตรวจว่า docker log ไม่บวม:  ตั้ง log rotation ใน daemon.json
```

---

## 7. เมื่อต้องเปลี่ยนเครื่องหรือกู้ระบบ

```
1. ติดตั้ง OS และ Docker ตาม 09-multi-site-runbook.md ขั้นที่ 5.1
2. คัดลอกโปรเจกต์ + .env + frigate/config.yml จาก backup
3. docker compose up -d postgres
4. กู้ฐานข้อมูล:
      gunzip -c db-YYYYMMDD.sql.gz | docker compose exec -T postgres psql -U <user> <db>
5. docker compose up -d
6. รัน check_time_sync.py และ healthcheck.sh
7. ทดสอบ end-to-end 1 เหตุการณ์กับ NVR
```

**เป้าหมาย: กู้คืนได้ภายใน 2 ชั่วโมง** ถ้าทำไม่ได้ ให้ปรับปรุงเอกสารและสคริปต์จนทำได้

---

## 8. งานตรวจสอบตามรอบ

### รายเดือน
- [ ] ตรวจกราฟจำนวนเหตุการณ์ต่อกล้อง — มีตัวไหนลดผิดปกติไหม
- [ ] ตรวจว่า backup รันสำเร็จทุกวัน
- [ ] ตรวจพื้นที่ดิสก์และแนวโน้ม
- [ ] ทบทวน audit log หาการใช้งานผิดปกติ

### รายไตรมาส
- [ ] **ไปตรวจกล้องที่หน้างานด้วยตาเปล่า** — เลนส์ ไฟ มุม
- [ ] ทดสอบ end-to-end กับ NVR อีกครั้ง
- [ ] ทบทวนความแม่นของ LPR ด้วยข้อมูลใหม่
- [ ] ตรวจสอบว่ามีทางเข้า–ออกใหม่เกิดขึ้นที่ยังไม่ได้คลุมไหม

### รายปี
- [ ] **ทดสอบกู้คืนจาก backup จริง**
- [ ] ทบทวนนโยบาย retention กับฝ่ายกฎหมาย
- [ ] อัปเดต OS และ dependency รุ่นใหญ่
- [ ] ทบทวนสิทธิ์ผู้ใช้ — ลบบัญชีคนที่ลาออกแล้ว

---

## เอกสารที่เกี่ยวข้อง

- [`02-hardware-sizing.md`](02-hardware-sizing.md) — เกณฑ์สมรรถนะและการคำนวณพื้นที่
- [`06-pdpa-compliance.md`](06-pdpa-compliance.md) — ข้อกำหนด retention และ audit
- [`08-nvr-integration.md`](08-nvr-integration.md) — การแก้ปัญหาเวลาไม่ตรง
- [`09-multi-site-runbook.md`](09-multi-site-runbook.md) — การติดตั้งใหม่
