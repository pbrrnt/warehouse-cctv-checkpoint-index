@echo off
REM ============================================================
REM  push-to-git.bat — launcher สำหรับ double-click จาก desktop
REM  เรียก push-to-git.ps1 (ข้าม ExecutionPolicy เฉพาะครั้งนี้)
REM
REM  ทำ shortcut ของไฟล์นี้ไปวางที่ desktop ได้เลย ไม่ต้องย้ายตัวไฟล์จริง
REM  (ต้องอยู่โฟลเดอร์เดียวกับ push-to-git.ps1 เสมอ)
REM ============================================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0push-to-git.ps1"
