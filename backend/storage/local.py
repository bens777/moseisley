"""Local filesystem storage — development and simple self-hosted installations."""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from backend.storage.base import StatResult, StorageAdapter, StorageError


class LocalFilesystemStorage(StorageAdapter):
    provider_name = "local"

    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, path: str) -> Path:
        candidate = (self.root / path.lstrip("/")).resolve()
        if not candidate.is_relative_to(self.root):
            raise StorageError("path escapes storage root")
        return candidate

    async def list(self, prefix: str = "") -> list[str]:
        base = self._resolve(prefix) if prefix else self.root
        if not base.exists():
            return []
        return sorted(
            str(p.relative_to(self.root))
            for p in base.rglob("*") if p.is_file()
        )

    async def stat(self, path: str) -> StatResult:
        p = self._resolve(path)
        if not p.is_file():
            raise StorageError(f"not found: {path}")
        st = p.stat()
        return StatResult(
            path=path, size_bytes=st.st_size,
            modified_at=datetime.fromtimestamp(st.st_mtime, tz=UTC),
            checksum=hashlib.sha256(p.read_bytes()).hexdigest(),
        )

    async def read(self, path: str) -> bytes:
        p = self._resolve(path)
        if not p.is_file():
            raise StorageError(f"not found: {path}")
        return p.read_bytes()

    async def write(self, path: str, data: bytes) -> StatResult:
        p = self._resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return await self.stat(path)

    async def delete(self, path: str) -> None:
        p = self._resolve(path)
        if p.is_file():
            p.unlink()

    async def health_check(self) -> bool:
        return self.root.exists()
