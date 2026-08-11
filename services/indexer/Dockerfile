# ★ ยังไม่เคย build จริง — เครื่องพัฒนาไม่มี Docker (ดู memory/docs เรื่องนี้)
# ต้องทดสอบ `docker compose build indexer` บนเครื่องเป้าหมายก่อนเชื่อว่าใช้ได้
#
# build context คือ services/ (ทั้งโฟลเดอร์ ดู docker-compose.yml) เพื่อให้
# COPY ทั้ง common/ และ indexer/ ได้ในคำสั่งเดียว รักษาโครงสร้าง
# services.common.xxx / services.indexer.xxx ให้ตรงกับตอนพัฒนา/ทดสอบ

FROM python:3.11-slim

WORKDIR /app

# ติดตั้ง dependency ก่อน copy โค้ด เพื่อให้ docker cache layer นี้ไว้ใช้ซ้ำ
# ตอน rebuild ถ้าแก้แค่โค้ดแต่ requirements ไม่เปลี่ยน
COPY common/requirements.txt services/common/requirements.txt
COPY common/analytics/requirements.txt services/common/analytics/requirements.txt
COPY indexer/requirements.txt services/indexer/requirements.txt
RUN pip install --no-cache-dir -r services/indexer/requirements.txt

COPY common/ services/common/
COPY indexer/ services/indexer/
RUN touch services/__init__.py

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "services.indexer.main"]
