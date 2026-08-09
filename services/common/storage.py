"""
Object store adapter — ตาม ADR-007 หลักการ "filesystem + adapter"

เริ่มด้วย local disk เสมอ เปลี่ยนเป็น MinIO/S3 ทีหลังได้โดยแค่เพิ่มคลาสใหม่
ที่ implement ContentStore เดียวกัน โค้ดที่เรียกใช้ (indexer/api) ไม่ต้องแก้
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class ContentStore(Protocol):
    def save(self, key: str, data: bytes) -> str:
        """บันทึกข้อมูล คืนค่า key เดิม (เผื่อ implementation อื่นปรับ key)"""
        ...

    def read(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...


class LocalFileStore:
    """
    เก็บไฟล์บนดิสก์ในเครื่อง — ตัวเลือกเริ่มต้นตาม ADR-007

    key เป็น path สัมพัทธ์ เช่น "events/abc123/snapshot.jpg" จะถูกวางไว้ที่
    <root>/events/abc123/snapshot.jpg
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        # ★ กัน path traversal (key มาจาก event id ภายนอก ไม่ควรเชื่อ 100%)
        path = (self.root / key).resolve()
        if not str(path).startswith(str(self.root.resolve())):
            raise ValueError(f"key ไม่ปลอดภัย พยายามออกนอก root: {key!r}")
        return path

    def save(self, key: str, data: bytes) -> str:
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def read(self, key: str) -> bytes:
        return self._resolve(key).read_bytes()

    def delete(self, key: str) -> None:
        path = self._resolve(key)
        path.unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self._resolve(key).exists()
