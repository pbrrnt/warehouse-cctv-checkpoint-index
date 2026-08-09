#!/usr/bin/env python3
"""
สำรวจกล้องหน้างาน — ใช้ตอนสำรวจไซต์ตาม docs/03-camera-onboarding.md

ทำ 3 อย่าง:
  1. หา IP กล้องที่เปิดพอร์ต RTSP (554) อยู่ในซับเน็ตที่ระบุ
  2. ต่อ RTSP ตามรูปแบบ URL ของ Dahua ทั้ง mainstream (subtype=0) และ
     substream (subtype=1) ต่อกล้อง
  3. เรียก ffprobe เพื่ออ่านค่าจริง (ความละเอียด, fps, codec) แล้วสรุปเป็นตาราง
     พร้อมเตือนตามเกณฑ์ใน docs/03-camera-onboarding.md และ
     docs/10-chokepoint-design.md

จำกัดเฉพาะกล้อง Dahua ตาม ADR-020 (docs/00-decisions.md) — ถ้าต้องสำรวจกล้อง
ยี่ห้ออื่น สคริปต์นี้จะบอกว่าต่อไม่ติดแต่ไม่รู้จักรูปแบบ URL ของยี่ห้อนั้น

ก่อนใช้งานต้องมี ffprobe (มากับ FFmpeg) อยู่ใน PATH:
  Windows:  winget install Gyan.FFmpeg   (หรือดาวน์โหลดจาก ffmpeg.org แล้วเพิ่ม PATH)
  ตรวจว่ามีแล้ว:  ffprobe -version

ตัวอย่างการใช้งาน:
  # สแกนทั้งซับเน็ตหาไอพีที่เปิดพอร์ต 554
  python probe_cameras.py --subnet 192.168.1.0/24 --user admin --password "P@ss/w0rd"

  # ระบุไอพีเองตรง ๆ (เร็วกว่าถ้ารู้ไอพีอยู่แล้ว)
  python probe_cameras.py --ips 192.168.1.51,192.168.1.52 --user admin --password "P@ss/w0rd"

  # บันทึกผลเป็น CSV ไว้แปะเข้าตาราง cameras ทีหลัง
  python probe_cameras.py --ips 192.168.1.51 --user admin --password "xxx" --csv result.csv

หมายเหตุ: รหัสผ่านที่มีอักขระพิเศษ (@ : / # ฯลฯ) ใส่มาตรง ๆ ได้เลย
สคริปต์จะ URL-encode ให้เอง ไม่ต้องเข้ารหัสเอง (ดูเหตุผลใน 03-camera-onboarding.md หัวข้อ 2.4)
"""

from __future__ import annotations

import argparse
import concurrent.futures
import ipaddress
import json
import re
import socket
import subprocess
import sys
import urllib.parse
from dataclasses import dataclass, field

# คอนโซลบางตัวบน Windows (cp874/cp1252) เข้ารหัสอักษรไทยหรือ ★ ไม่ได้แล้ว crash
# บังคับ UTF-8 ทันทีตั้งแต่ต้นสคริปต์ ก่อนมีการ print ใด ๆ
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

RTSP_PORT = 554
CONNECT_TIMEOUT = 0.8  # วินาที ต่อไอพีตอนสแกนรอบแรก — รอบสองจะยาวขึ้นอัตโนมัติ (ดู scan_subnet)
FFPROBE_TIMEOUT = 8  # วินาที ต่อ RTSP URL หนึ่งเส้น

# รูปแบบ URL ของ Dahua — channel=1 เสมอเมื่อต่อตรงเข้ากล้อง (ไม่ใช่ผ่าน NVR)
# ดู docs/03-camera-onboarding.md หัวข้อ 2.1
DAHUA_PROFILES = {
    "main": "subtype=0",
    "sub": "subtype=1",
}

# เกณฑ์เตือนจาก docs/03-camera-onboarding.md และ docs/10-chokepoint-design.md
MIN_SUB_WIDTH = 640
MIN_SUB_HEIGHT = 480


@dataclass
class StreamResult:
    profile: str  # "main" | "sub"
    url: str
    ok: bool = False
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    codec: str | None = None
    error: str | None = None
    warnings: list[str] = field(default_factory=list)


_CREDENTIAL_IN_URL = re.compile(r"(rtsp://[^:/@]+:)[^@]+(@)")


def redact_url(text: str) -> str:
    """
    ปิดบังรหัสผ่านใน URL ก่อนแสดงผล/บันทึกทุกครั้ง

    ★ พบเคสจริง: ffprobe เอง echo URL เต็ม (มีรหัสผ่านฝัง) กลับมาใน stderr
    ตอน error (เช่น 401 Unauthorized) ถ้าไม่ปิดตรงนี้ รหัสผ่านจะหลุดไปอยู่
    ใน error message ที่ print และเขียนลง CSV ทันที — เคยเกิดขึ้นแล้วจริง
    ตอนสำรวจหน้าร้าน (ไฟล์ผลลัพธ์เกือบหลุดเข้า git)
    """
    return _CREDENTIAL_IN_URL.sub(r"\1***\2", text)


def build_dahua_url(ip: str, user: str, password: str, profile: str, channel: int = 1) -> str:
    """ประกอบ RTSP URL ตามรูปแบบ Dahua พร้อม URL-encode รหัสผ่านให้อัตโนมัติ"""
    safe_user = urllib.parse.quote(user, safe="")
    safe_pass = urllib.parse.quote(password, safe="")
    subtype = DAHUA_PROFILES[profile]
    return f"rtsp://{safe_user}:{safe_pass}@{ip}:{RTSP_PORT}/cam/realmonitor?channel={channel}&{subtype}"


def port_is_open(ip: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def _probe_batch(ips: list[str], timeout: float, max_workers: int) -> set[str]:
    found: set[str] = set()
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(port_is_open, ip, RTSP_PORT, timeout): ip for ip in ips}
        for future in concurrent.futures.as_completed(futures):
            ip = futures[future]
            try:
                if future.result():
                    found.add(ip)
                    print(f"  พบ {ip}")
            except Exception:
                pass
    return found


def scan_subnet(subnet: str, timeout: float = CONNECT_TIMEOUT) -> list[str]:
    """
    สแกนหาไอพีที่เปิดพอร์ต 554 (RTSP) ในซับเน็ต — ไม่ต้องพึ่งไลบรารี ONVIF

    ★ สแกน 2 รอบเสมอ: รอบแรกเร็ว (timeout สั้น, ยิงพร้อมกันเยอะ) รอบสอง
    ยืนยันซ้ำเฉพาะไอพีที่ยังไม่พบ ด้วย timeout ยาวขึ้นและยิงพร้อมกันน้อยลง
    — เราท์เตอร์ขนาดเล็ก (เช่น ตามหน้าร้าน/โกดัง) มักสำลักถ้ายิง TCP
    พร้อมกันเยอะเกินไป ทำให้กล้องที่เปิดอยู่จริงถูกมองข้ามไปในรอบแรก
    (พบปัญหานี้จริงตอนสำรวจหน้างาน — กล้องที่ต่อติดปกติ 100% เมื่อระบุ
    ไอพีตรง ๆ กลับไม่โผล่ตอนสแกนทั้งซับเน็ต)
    """
    network = ipaddress.ip_network(subnet, strict=False)
    hosts = [str(h) for h in network.hosts()]

    print(f"กำลังสแกน {len(hosts)} ไอพีใน {subnet} หาพอร์ต {RTSP_PORT} (รอบที่ 1) ...")
    found = _probe_batch(hosts, timeout, max_workers=32)

    remaining = [ip for ip in hosts if ip not in found]
    if remaining:
        print(f"กำลังยืนยันซ้ำ {len(remaining)} ไอพีที่ยังไม่พบ (รอบที่ 2, timeout นานขึ้น) ...")
        found |= _probe_batch(remaining, timeout * 3, max_workers=8)

    return sorted(found, key=lambda x: ipaddress.ip_address(x))


def ffprobe_stream(url: str) -> tuple[bool, dict | None, str | None]:
    """เรียก ffprobe อ่าน stream แรกของ URL คืน (สำเร็จไหม, ข้อมูล, ข้อความ error)"""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-rtsp_transport", "tcp",
        "-timeout", str(FFPROBE_TIMEOUT * 1_000_000),  # ffprobe ใช้หน่วยไมโครวินาที
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate,codec_name",
        "-of", "json",
        url,
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=FFPROBE_TIMEOUT + 3,
        )
    except FileNotFoundError:
        print("\n★ ไม่พบคำสั่ง ffprobe — ต้องติดตั้ง FFmpeg ก่อนใช้สคริปต์นี้")
        print("  Windows: winget install Gyan.FFmpeg  แล้วเปิด terminal ใหม่")
        sys.exit(1)
    except subprocess.TimeoutExpired:
        return False, None, f"หมดเวลา ({FFPROBE_TIMEOUT}s) — ต่อไม่ติดหรือกล้องไม่ตอบสนอง"

    if proc.returncode != 0:
        err = proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "ffprobe คืนค่า error"
        # ★ ffprobe เอง echo URL (มีรหัสผ่านฝัง) กลับมาใน stderr ตอน error
        # ต้องปิดบังตรงนี้ก่อนส่งค่ากลับ ไม่งั้นรหัสผ่านหลุดไปอยู่ในทุกที่
        # ที่ error นี้ถูกใช้ต่อ (print, CSV)
        return False, None, redact_url(err)

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return False, None, "อ่านผลจาก ffprobe ไม่ได้ (JSON เสีย)"

    streams = data.get("streams") or []
    if not streams:
        return False, None, "ต่อได้แต่ไม่มี video stream (ตรวจ user/password หรือ channel)"

    return True, streams[0], None


def parse_fps(rate_str: str | None) -> float | None:
    if not rate_str or "/" not in rate_str:
        return None
    try:
        num, den = rate_str.split("/")
        den = float(den)
        return round(float(num) / den, 2) if den else None
    except (ValueError, ZeroDivisionError):
        return None


def probe_camera(ip: str, user: str, password: str, channel: int) -> list[StreamResult]:
    results = []
    for profile in ("main", "sub"):
        url = build_dahua_url(ip, user, password, profile, channel)
        # ★ เก็บ URL แบบปิดบังรหัสผ่านไว้ใน result เสมอ — ตัว url จริง (มี
        # รหัสผ่าน) ใช้แค่ตอนเรียก ffprobe_stream() ด้านล่างเท่านั้น ไม่ควร
        # ค้างอยู่ใน object ที่อาจถูก print/เขียนไฟล์ทีหลัง
        result = StreamResult(profile=profile, url=redact_url(url))

        ok, stream, error = ffprobe_stream(url)
        result.ok = ok
        if ok and stream:
            result.width = stream.get("width")
            result.height = stream.get("height")
            result.fps = parse_fps(stream.get("r_frame_rate"))
            result.codec = stream.get("codec_name")
        else:
            result.error = error

        results.append(result)

    # ★ เกณฑ์เตือนตามเอกสาร — ดู docs/03-camera-onboarding.md และ 10-chokepoint-design.md
    main = next(r for r in results if r.profile == "main")
    sub = next(r for r in results if r.profile == "sub")

    if main.ok and not sub.ok:
        sub.warnings.append("★ ไม่มี substream — detect จะกิน CPU มาก หรือใช้กล้องนี้ไม่ได้ตามที่ออกแบบไว้")

    if sub.ok and sub.width and sub.height:
        if sub.width < MIN_SUB_WIDTH or sub.height < MIN_SUB_HEIGHT:
            sub.warnings.append(
                f"substream เล็กเกินไป ({sub.width}x{sub.height}) "
                f"ต่ำกว่าเกณฑ์ {MIN_SUB_WIDTH}x{MIN_SUB_HEIGHT} — ตรวจจับวัตถุจะไม่แม่น"
            )
        elif main.ok and main.width and main.height and (sub.width, sub.height) == (main.width, main.height):
            # ★ พบจริงตอนสำรวจหน้างาน: กล้องส่วนใหญ่ไม่ได้ตั้งค่า sub stream
            # แยกจาก main เลย ทำให้ Frigate ต้อง decode เต็มความละเอียดเสมอ
            # เสียจุดประสงค์ทั้งหมดของการใช้ substream สำหรับ detect
            sub.warnings.append(
                f"★ substream ความละเอียดเท่า mainstream ({sub.width}x{sub.height}) — "
                "ยังไม่ได้ตั้งค่า sub stream แยกในกล้อง ต้องเข้าไปตั้งเอง "
                "(ดูตัวอย่างกล้องที่ตั้งถูกแล้วในไซต์เดียวกันเทียบได้)"
            )

    if sub.ok and sub.codec == "hevc":
        sub.warnings.append("substream เป็น H.265 — แนะนำเปลี่ยนเป็น H.264 ในตัวกล้อง (ดูหัวข้อ 3.8)")

    if sub.ok and sub.fps and sub.fps < 3:
        sub.warnings.append(f"fps substream ต่ำมาก ({sub.fps}) — ตรวจการตั้งค่ากล้อง")

    return results


def print_report(ip: str, results: list[StreamResult]) -> None:
    print(f"\n{'=' * 60}")
    print(f"กล้อง {ip}")
    print(f"{'=' * 60}")
    for r in results:
        label = {"main": "mainstream", "sub": "substream "}[r.profile]
        if r.ok:
            print(f"  {label}  OK   {r.width}x{r.height}  {r.fps} fps  {r.codec}")
        else:
            print(f"  {label}  FAIL {r.error}")
        for w in r.warnings:
            print(f"      ⚠ {w}")


def write_csv(path: str, rows: list[dict]) -> None:
    import csv

    if not rows:
        print("ไม่มีข้อมูลจะเขียน CSV")
        return

    fieldnames = ["ip", "profile", "ok", "width", "height", "fps", "codec", "error", "warnings"]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nบันทึกผลลง {path} แล้ว")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="สำรวจกล้อง Dahua หน้างาน — ต่อ RTSP + ตรวจสเปกจริงด้วย ffprobe",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--subnet", help="ซับเน็ตที่จะสแกนหากล้อง เช่น 192.168.1.0/24")
    target.add_argument("--ips", help="รายการไอพีกล้อง คั่นด้วยจุลภาค เช่น 192.168.1.51,192.168.1.52")

    parser.add_argument("--user", required=True, help="บัญชีกล้อง (เช่น admin)")
    parser.add_argument("--password", required=True, help="รหัสผ่านกล้อง — ใส่ตรง ๆ ได้ สคริปต์ encode ให้เอง")
    parser.add_argument("--channel", type=int, default=1, help="เลขช่อง (ค่าเริ่มต้น 1 สำหรับต่อตรงเข้ากล้อง)")
    parser.add_argument("--csv", help="บันทึกผลเป็นไฟล์ CSV ที่ระบุ")

    args = parser.parse_args()

    if args.subnet:
        ips = scan_subnet(args.subnet)
        if not ips:
            print("\nไม่พบกล้องที่เปิดพอร์ต 554 ในซับเน็ตนี้")
            print("เช็ก: อยู่วง LAN เดียวกันไหม, ไฟร์วอลล์บล็อกไหม, กล้องเปิดอยู่ไหม")
            sys.exit(1)
    else:
        ips = [ip.strip() for ip in args.ips.split(",") if ip.strip()]

    all_rows: list[dict] = []
    ok_count = 0

    for ip in ips:
        results = probe_camera(ip, args.user, args.password, args.channel)
        print_report(ip, results)

        if any(r.ok for r in results):
            ok_count += 1

        for r in results:
            all_rows.append({
                "ip": ip,
                "profile": r.profile,
                "ok": r.ok,
                "width": r.width,
                "height": r.height,
                "fps": r.fps,
                "codec": r.codec,
                "error": r.error or "",
                "warnings": "; ".join(r.warnings),
            })

    print(f"\n{'=' * 60}")
    print(f"สรุป: ต่อได้ {ok_count}/{len(ips)} กล้อง")
    print(f"{'=' * 60}")
    print("ขั้นตอนถัดไป: บันทึกผลลงตาราง cameras ตาม docs/04-data-model.md")
    print("และเช็กเวลาแต่ละกล้องด้วย scripts/check_time_sync.py (ยังไม่ได้เขียน)")

    if args.csv:
        write_csv(args.csv, all_rows)


if __name__ == "__main__":
    main()
