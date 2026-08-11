# ★ ยังไม่เคย build จริง — เครื่องพัฒนาไม่มี Docker (ดู memory/docs เรื่องนี้)
# ต้องทดสอบ `docker compose build api` บนเครื่องเป้าหมายก่อนเชื่อว่าใช้ได้
#
# build context คือ services/ (ทั้งโฟลเดอร์ ดู docker-compose.yml) เพื่อให้
# COPY ทั้ง common/ และ api/ ได้ในคำสั่งเดียว รักษาโครงสร้าง
# services.common.xxx / services.api.xxx ให้ตรงกับตอนพัฒนา/ทดสอบ — ใช้
# common/requirements.txt เท่านั้น (ไม่ใช้ common/analytics/ — api ไม่เรียก
# thai_ocr.py/vehicle.py ตรง ๆ แค่ import plate_normalize.py ซึ่งเป็น
# pure Python ไม่มี dependency พิเศษ)

FROM python:3.11-slim

WORKDIR /app

COPY common/requirements.txt services/common/requirements.txt
COPY api/requirements.txt services/api/requirements.txt
RUN pip install --no-cache-dir -r services/api/requirements.txt

COPY common/ services/common/
COPY api/ services/api/
RUN touch services/__init__.py

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "services.api.main"]
