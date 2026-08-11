# ★ ยังไม่เคย build จริง — เครื่องพัฒนาไม่มี Docker (ดู memory/docs เรื่องนี้)
# ต้องทดสอบ `docker compose build web` บนเครื่องเป้าหมายก่อนเชื่อว่าใช้ได้
#
# static file ล้วน (ไม่มี build step/npm) — แค่ copy เข้า nginx image
# ตรง ๆ ดู nginx.conf สำหรับการ proxy /api/ ไปหา search-api

FROM nginx:alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY index.html style.css app.js /usr/share/nginx/html/
