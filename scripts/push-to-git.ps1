# ============================================================
#  push-to-git.ps1 - push โปรเจกต์กล้องวงจรปิดขึ้น GitHub
#  ปรับจากสคริปต์ Agri-POS (repo เดียว ไม่ใช่ backend+frontend แยก)
#  - เช็กว่ามีอะไรเปลี่ยนไหม
#  - ถ้าเปลี่ยน: add + commit + push
#  - ถ้าไม่เปลี่ยน: บอกว่าไม่มีอะไรต้อง push
#  - commit message: พิมพ์เอง หรือกด Enter ให้สร้างอัตโนมัติ
# ============================================================
# ---- แก้ path ตรงนี้ถ้าย้ายโฟลเดอร์โปรเจกต์ ----
$ProjectRoot = "C:\IP Camera RTSP Projects"
# --------------------------------------------------------------

Write-Host ""
Write-Host "===================================================" -ForegroundColor Cyan
Write-Host " Push โปรเจกต์กล้องวงจรปิด  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Cyan
Write-Host "===================================================" -ForegroundColor Cyan

if (-not (Test-Path $ProjectRoot)) {
    Write-Host "  [หยุด] ไม่พบโฟลเดอร์ $ProjectRoot — แก้ `$ProjectRoot ในสคริปต์นี้" -ForegroundColor Red
    Read-Host "กด Enter เพื่อปิด"
    exit 1
}

Set-Location $ProjectRoot

$inside = (git rev-parse --is-inside-work-tree 2>$null)
if ($inside -ne "true") {
    Write-Host "  [หยุด] โฟลเดอร์นี้ไม่ใช่ git repo (ยังไม่ได้ git init)" -ForegroundColor Red
    Read-Host "กด Enter เพื่อปิด"
    exit 1
}

$status = git status --porcelain

# commit สิ่งที่เปลี่ยนในเครื่อง (ถ้ามี)
if (-not [string]::IsNullOrWhiteSpace($status)) {
    Write-Host ""
    Write-Host "ไฟล์ที่เปลี่ยน:" -ForegroundColor Green
    git status --short

    $msg = Read-Host "  ข้อความ commit (Enter = สร้างอัตโนมัติจากรายชื่อไฟล์)"
    if ([string]::IsNullOrWhiteSpace($msg)) {
        $names = git status --porcelain | ForEach-Object { ($_ -replace '^.{3}', '').Trim() }
        $joined = $names -join ", "
        if ($joined.Length -gt 100) { $joined = $joined.Substring(0, 100) + "..." }
        $msg = "update $(Get-Date -Format 'yyyy-MM-dd HH:mm') | $joined"
        Write-Host "  (ข้อความอัตโนมัติ: $msg)" -ForegroundColor DarkGray
    }

    git add -A
    git commit -m "$msg"
}

# นับ commit ที่ยังไม่ได้ push (ครอบคลุมกรณี commit จากที่อื่น เช่น Claude Code
# ที่ working tree สะอาดอยู่แล้วตอนสคริปต์นี้เริ่มทำงาน)
$ahead = 0
git rev-parse --abbrev-ref '@{u}' 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) {
    $ahead = [int](git rev-list --count '@{u}..HEAD' 2>$null)
}

if ([string]::IsNullOrWhiteSpace($status) -and $ahead -eq 0) {
    Write-Host ""
    Write-Host "  ไม่มีอะไรเปลี่ยน ไม่ต้อง push" -ForegroundColor DarkGray
    Write-Host ""
    Read-Host "กด Enter เพื่อปิด"
    exit 0
}

if ($ahead -gt 0) {
    Write-Host "  มี $ahead commit ที่ยังไม่ได้ push — กำลัง push" -ForegroundColor Green
}

git push
if ($LASTEXITCODE -eq 0) {
    Write-Host "  push สำเร็จ" -ForegroundColor Green
    Write-Host ""
    Write-Host "===================================================" -ForegroundColor Magenta
    Write-Host " เตือน: ถ้าเซิร์ฟเวอร์ปลายทางตั้งไว้แล้ว อย่าลืม pull + restart" -ForegroundColor Magenta
    Write-Host " (ยังไม่มีคำสั่ง deploy สำเร็จรูปตอนนี้ — เพิ่มให้ทีหลังตอนตั้งเซิร์ฟเวอร์จริง)" -ForegroundColor Magenta
    Write-Host "===================================================" -ForegroundColor Magenta
} else {
    Write-Host "  push ไม่สำเร็จ (exit $LASTEXITCODE) — ดูข้อความ error ด้านบน" -ForegroundColor Red
}

Write-Host ""
Read-Host "กด Enter เพื่อปิด"
