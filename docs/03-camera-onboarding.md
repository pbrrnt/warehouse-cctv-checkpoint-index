# 03 — การนำกล้องเข้าระบบ (Camera Onboarding)

> เอกสารนี้เป็นงาน **เทคนิคการต่อกล้อง** ส่วนการเลือกตำแหน่งและตั้งค่ากล้องให้ได้ภาพดี
> อยู่ที่ [`10-chokepoint-design.md`](10-chokepoint-design.md) — **อ่านเล่มนั้นก่อน**

---

## 1. หลักการ: ต่อตรงเข้ากล้อง ไม่ดึงผ่าน NVR

| | ต่อตรงเข้ากล้อง | ดึงผ่าน NVR |
|---|---|---|
| คุณภาพภาพ | เต็ม | อาจถูก re-encode |
| Latency | ต่ำ | สูงกว่า |
| ภาระ NVR | ไม่มี | เพิ่ม |
| ข้อจำกัด session | ของกล้อง | NVR จำกัดมากกว่า |

**→ ต่อตรงเข้ากล้องเสมอ** และปล่อยให้ NVR ทำหน้าที่บันทึกของมันไป

### 1.1 ★ ระวังจำนวน RTSP session

```
NVR ดึง mainstream    →  1 session
Frigate ดึง substream →  1 session
แอปมือถือ/SmartPSS    →  1 session (บางครั้ง)
                         ─────────
                         2–3 session ต่อกล้อง
```

**กล้องจีนราคาประหยัดบางรุ่นรองรับแค่ 2–3 session** เมื่อเต็มแล้วจะปฏิเสธการเชื่อมต่อใหม่ หรือแย่กว่านั้นคือ **ตัดของเดิมทิ้ง** ทำให้ NVR หยุดบันทึกโดยไม่มีใครรู้

**→ ต้องทดสอบข้อนี้ตอนสำรวจ ไม่ใช่ตอนติดตั้งจริง** (วิธีทดสอบอยู่ในหัวข้อ 4.3)

---

## 2. รูปแบบ RTSP URL

### 2.1 Dahua (และ OEM เช่น Imou, Lorex)

```
rtsp://user:pass@<ip>:554/cam/realmonitor?channel=1&subtype=0   ← mainstream
rtsp://user:pass@<ip>:554/cam/realmonitor?channel=1&subtype=1   ← substream
```

- `channel=1` เสมอเมื่อต่อตรงเข้ากล้อง (ใช้เลขช่องอื่นเฉพาะตอนต่อผ่าน NVR)
- บางรุ่นมี `subtype=2` เป็น substream ตัวที่สอง

### 2.2 Hikvision (และ OEM เช่น HiLook)

```
rtsp://user:pass@<ip>:554/Streaming/Channels/101    ← mainstream
rtsp://user:pass@<ip>:554/Streaming/Channels/102    ← substream
```

### 2.3 กล้องจีน ONVIF ทั่วไป

**ไม่มีรูปแบบมาตรฐาน** ต้อง discover ด้วย ONVIF Profile S:

```
1. WS-Discovery หาอุปกรณ์ในวง LAN
2. GetProfiles     → ได้รายการ profile (mainstream/substream)
3. GetStreamUri    → ได้ RTSP URL จริงของแต่ละ profile
```

สคริปต์ `scripts/probe_cameras.py` จะทำขั้นตอนนี้ให้อัตโนมัติ

รูปแบบที่พบบ่อยในกล้องจีน (ลองไล่ดูถ้า ONVIF ใช้ไม่ได้):

```
rtsp://user:pass@<ip>:554/stream1
rtsp://user:pass@<ip>:554/live/ch0
rtsp://user:pass@<ip>:554/h264/ch1/main/av_stream
rtsp://user:pass@<ip>:554/user=admin_password=xxx_channel=1_stream=0.sdp
```

### 2.4 ★ URL-encode รหัสผ่านที่มีอักขระพิเศษ

| อักขระ | ต้องเขียนเป็น |
|---|---|
| `@` | `%40` |
| `:` | `%3A` |
| `/` | `%2F` |
| `#` | `%23` |
| `?` | `%3F` |
| `&` | `%26` |
| ช่องว่าง | `%20` |

ตัวอย่าง: รหัสผ่าน `P@ss/w0rd` → `rtsp://admin:P%40ss%2Fw0rd@192.168.1.50:554/...`

> **นี่คือสาเหตุอันดับหนึ่งของ "ต่อกล้องไม่ได้ทั้งที่รหัสผ่านถูก"**

---

## 3. กับดักที่พบบ่อยกับกล้องจีน

| ปัญหา | อาการ | วิธีแก้ |
|---|---|---|
| **ไม่มี substream** | probe เจอแค่ 1 profile | ใช้ mainstream แล้วให้ Frigate ย่อภาพ (กิน CPU มากขึ้น) หรือเปลี่ยนกล้อง |
| **substream เล็กเกินไป** | 352×288 หรือ 640×360 | detect ไม่แม่น — ตั้ง substream ใหม่ในกล้องให้เป็น 640×480 ขึ้นไป |
| **UDP หลุดบ่อย** | ภาพแตก เขียว กระตุก | **บังคับ `rtsp_transport: tcp`** ใน Frigate |
| **fps ที่รายงานไม่ตรงจริง** | Frigate เตือน fps mismatch | วัดจริงด้วย `ffprobe` แล้วตั้งตามค่าที่วัดได้ |
| **track เสียงทำ ffmpeg พัง** | ffmpeg ตายเป็นระยะ | เพิ่ม `-an` ใน input args |
| **timestamp drift** | คลิปยาวผิดปกติ เวลาเพี้ยน | ให้ go2rtc restream เป็นตัวกลาง |
| **กล้องรีบูตเองเป็นระยะ** | RTSP หลุดตามรอบเวลา | ปิด auto-reboot ในกล้อง หรือตั้งให้รีบูตตอนตี 3 |
| **สิทธิ์บัญชีไม่พอ** | ต่อได้แต่ไม่มีภาพ | สร้าง user ระดับ admin เฉพาะสำหรับ Frigate |
| **H.265 บน substream** | decode กินซีพียูมากกว่าที่ควร | ตั้ง substream เป็น **H.264** |

---

## 4. ขั้นตอนการนำกล้องเข้าระบบ

### 4.1 สำรวจด้วยสคริปต์

```bash
python scripts/probe_cameras.py --subnet 192.168.1.0/24
```

**สิ่งที่สคริปต์ต้องรายงาน** (จะเขียนในเฟส 2):

```
IP              ยี่ห้อ      Profile      ความละเอียด  fps  codec  สถานะ
──────────────────────────────────────────────────────────────────────
192.168.1.51    Dahua      main         2560x1440    25   H.265  OK
192.168.1.51    Dahua      sub          704x576      15   H.264  OK
192.168.1.52    ONVIF จีน   main         1920x1080    20   H.264  OK
192.168.1.52    ONVIF จีน   (ไม่มี sub)                          ★ เตือน
192.168.1.53    Dahua      main         1920x1080    25   H.264  OK
192.168.1.53    Dahua      sub          640x480      10   H.264  OK
──────────────────────────────────────────────────────────────────────
พบ 3 กล้อง · เตือน 1 รายการ
```

### 4.2 ทดสอบด้วยมือ

```bash
ffprobe -rtsp_transport tcp -i "rtsp://user:pass@192.168.1.51:554/cam/realmonitor?channel=1&subtype=1"
```

ตรวจว่าได้ค่าตรงกับที่ตั้งไว้ในกล้อง โดยเฉพาะ **fps จริง**

### 4.3 ★ ทดสอบจำนวน RTSP session

เปิดหลายหน้าต่างพร้อมกันขณะที่ NVR ยังทำงานอยู่:

```bash
ffplay -rtsp_transport tcp -i "rtsp://user:pass@192.168.1.51:554/cam/realmonitor?channel=1&subtype=1"
```

**ต้องเปิดได้อย่างน้อย 2 ตัวพร้อมกัน** โดยที่ NVR ไม่หลุด
ถ้าเปิดตัวที่ 2 แล้ว NVR หยุดบันทึก → **กล้องตัวนี้ใช้ไม่ได้ ต้องเปลี่ยน**

### 4.4 ทดสอบความเสถียร 24 ชั่วโมง

```bash
ffmpeg -rtsp_transport tcp -i "rtsp://..." -f null - 2>&1 | tee /tmp/rtsp-test.log
```

ปล่อยทิ้งไว้ 24 ชม. แล้วตรวจ log ว่ามี error หรือ reconnect กี่ครั้ง
**หลุดเกิน 2–3 ครั้งต่อวัน = มีปัญหา** ตรวจสายและ transport

### 4.5 เพิ่มเข้า Frigate

แก้ `frigate/config.yml` (ดูเทมเพลตในไฟล์นั้น) แล้ว:

```bash
docker compose restart frigate
```

ตรวจ log:

```bash
docker compose logs -f frigate --tail=50
```

### 4.6 บันทึกลงฐานข้อมูล

เพิ่มแถวใน `cameras` พร้อมข้อมูลที่ขาดไม่ได้:

| คอลัมน์ | ต้องกรอก |
|---|---|
| `id` | `<SITE_ID>-<จุด>-<บทบาท>` เช่น `WH01-GATE-LPR` |
| `nvr_channel` | ★ ช่องใน NVR — **ถ้าไม่มี ผลการค้นหาจะใช้ไม่ได้** |
| `tier` / `role` | กำหนดว่า indexer จะรันโมเดลอะไร |
| `clock_offset_ms` | 0 ถ้า NTP ปกติ |

### 4.7 ตรวจการซิงก์เวลา

```bash
python scripts/check_time_sync.py
```

ต้องต่างกัน < 2 วินาที (ดู [`08-nvr-integration.md`](08-nvr-integration.md))

---

## 5. การตั้งค่าใน Frigate

### 5.1 หลักการ: substream สำหรับ detect, mainstream สำหรับ record

```yaml
cameras:
  WH01-GATE-LPR:
    ffmpeg:
      inputs:
        # substream: เล็ก เร็ว ใช้ตรวจจับ
        - path: rtsp://...&subtype=1
          roles: [detect]
        # mainstream: คมชัด ใช้บันทึกเฉพาะตอนมีเหตุการณ์
        - path: rtsp://...&subtype=0
          roles: [record]
```

**ทำไมต้องแยก:** detect ที่ความละเอียดต่ำเร็วกว่ามากและแม่นพอ ๆ กัน ส่วนคลิปที่บันทึกต้องคมพอให้คนดูยืนยันได้

### 5.2 Hardware acceleration สำหรับ Intel

| รุ่น CPU | preset |
|---|---|
| Gen 1–7 | `preset-vaapi` |
| Gen 8–12 | `preset-vaapi` หรือ `preset-intel-qsv-h264` |
| Gen 13+ / Core Ultra / N100 | `preset-intel-qsv-h264` |

ตรวจว่าใช้ iGPU จริง:

```bash
docker compose exec frigate intel_gpu_top
```

ถ้าค่า Video/Render เป็น 0 ตลอด แปลว่ายังใช้ CPU decode อยู่

### 5.3 ค่าที่แนะนำสำหรับกล้องจีน

```yaml
ffmpeg:
  input_args: preset-rtsp-restream
  # หรือถ้ามีปัญหา ใช้ค่านี้แทน
  # input_args: -avoid_negative_ts make_zero -fflags +genpts+discardcorrupt
  #             -rtsp_transport tcp -timeout 5000000 -use_wallclock_as_timestamps 1 -an
```

`-an` ตัดเสียงออก แก้ปัญหา ffmpeg พังจาก track เสียงเสีย

---

## 6. เช็กลิสต์ก่อนถือว่ากล้องพร้อมใช้

- [ ] `probe_cameras.py` รายงาน OK มี substream
- [ ] `ffprobe` ได้ค่า fps/codec ตรงกับที่ตั้งในกล้อง
- [ ] เปิด RTSP พร้อมกัน 2 session ได้โดย NVR ไม่หลุด
- [ ] ทดสอบ 24 ชม. หลุดไม่เกิน 2 ครั้ง
- [ ] Frigate แสดงภาพสดได้ ไม่มี error ใน log
- [ ] `intel_gpu_top` ยืนยันว่า decode ผ่าน iGPU
- [ ] `skipped_fps` = 0 ใน `/api/stats`
- [ ] เพิ่มแถวใน `cameras` ครบ **โดยเฉพาะ `nvr_channel`**
- [ ] `check_time_sync.py` ผ่าน (< 2 วินาที)
- [ ] **คุณภาพภาพผ่านเกณฑ์ใน [`10-chokepoint-design.md`](10-chokepoint-design.md) หัวข้อ 7** (สำคัญที่สุด)

---

## เอกสารที่เกี่ยวข้อง

- [`10-chokepoint-design.md`](10-chokepoint-design.md) — ตำแหน่งและการตั้งค่ากล้องให้ได้ภาพดี
- [`08-nvr-integration.md`](08-nvr-integration.md) — การซิงก์เวลา
- [`09-multi-site-runbook.md`](09-multi-site-runbook.md) — ขั้นตอนเปิดไซต์ใหม่ทั้งกระบวนการ
