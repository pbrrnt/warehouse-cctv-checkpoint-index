@echo off
cd /d "C:\IP Camera RTSP Projects\scripts"

:: ตั้งค่า Default Subnet ไว้ก่อน (ถ้ากด Enter ข้ามไปเลยจะใช้ค่านิยมนี้)
set SUBNET=192.168.111.0/24
set /p SUBNET="input Subnet (press Enter if not change %SUBNET%): "

:: รับค่า Password จากผู้ใช้
::set /p PASS="input Password your IP Camera: "

:: รับ Password แบบซ่อนตัวอักษรด้วยดอกจัน (*)
for /f "delims=" %%a in ('powershell -Command "$p = Read-Host 'input Password your IP Camera: ' -AsSecureString; [System.Runtime.InteropServices.Marshal]::PtrToStringAuto([System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($p))"') do set "PASS=%%a"

:: รันสคริปต์ Python โดยใช้ค่าที่ผู้ใช้รับเข้ามา
python probe_cameras.py --subnet %SUBNET% --user admin --password "%PASS%" --csv result.csv
python build_csv_html.py

:: คำนวณวันที่และเวลาในฟอร์แมต YYYY.MM.DD HH.MM
for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value') do set datetime=%%I
set YYYY=%datetime:~0,4%
set MM=%datetime:~4,2%
set DD=%datetime:~6,2%
set HH=%datetime:~8,2%
set Min=%datetime:~10,2%

set NEW_NAME=result %YYYY%.%MM%.%DD% %HH%.%Min%.html

:: เปลี่ยนชื่อไฟล์ result.html เป็น วว.ดด.ปปปป ชช.นน.html
if exist result.html (
    ren result.html "%NEW_NAME%"
    echo Renamed result.html to "%NEW_NAME%"
)