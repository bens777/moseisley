"""StorageAdapter abstraction (architecture update).

Moseisley.sh Core never cares where bytes physically live: local disk, an S3-compatible
bucket, Google Drive (optional future integration), NAS, etc. BYOS: users may connect
their own storage; Moseisley.sh keeps metadata references (FileRef) in PostgreSQL.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


class StorageError(Exception):
    pass


@dataclass
class StatResult:
    path: str
    size_bytes: int
    modified_at: datetime | None
    checksum: str | None = None


class StorageAdapter:
    provider_name = "base"

    async def list(self, prefix: str = "") -> list[str]:
        raise NotImplementedError

    async def stat(self, path: str) -> StatResult:
        raise NotImplementedError

    async def read(self, path: str) -> bytes:
        raise NotImplementedError

    async def write(self, path: str, data: bytes) -> StatResult:
        raise NotImplementedError

    async def delete(self, path: str) -> None:
        raise NotImplementedError

    async def health_check(self) -> bool:
        raise NotImplementedError
