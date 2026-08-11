"""S3-compatible object storage (protocol-level, NOT AWS-specific).

Works against any S3-compatible endpoint (MinIO, Ceph, Cloudflare R2, Backblaze B2,
AWS...) via boto3 with a configurable endpoint_url. Used both for Moseisley.sh-owned
object storage and for BYOS user buckets (credentials encrypted per connection).
"""
from __future__ import annotations

import asyncio
import hashlib

from backend.storage.base import StatResult, StorageAdapter, StorageError


class S3CompatibleStorage(StorageAdapter):
    provider_name = "s3"

    def __init__(self, bucket: str, access_key_id: str, secret_access_key: str,
                 endpoint_url: str | None = None, region: str | None = None):
        import boto3

        self.bucket = bucket
        self._client = boto3.client(
            "s3", aws_access_key_id=access_key_id, aws_secret_access_key=secret_access_key,
            endpoint_url=endpoint_url, region_name=region,
        )

    async def list(self, prefix: str = "") -> list[str]:
        def _list():
            keys = []
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix.lstrip("/")):
                keys.extend(o["Key"] for o in page.get("Contents", []))
            return sorted(keys)
        try:
            return await asyncio.to_thread(_list)
        except Exception as e:
            raise StorageError(f"s3 list failed: {type(e).__name__}") from e

    async def stat(self, path: str) -> StatResult:
        def _stat():
            return self._client.head_object(Bucket=self.bucket, Key=path.lstrip("/"))
        try:
            head = await asyncio.to_thread(_stat)
        except Exception as e:
            raise StorageError(f"not found: {path}") from e
        return StatResult(path=path, size_bytes=head["ContentLength"],
                          modified_at=head.get("LastModified"),
                          checksum=head.get("ETag", "").strip('"') or None)

    async def read(self, path: str) -> bytes:
        def _read():
            return self._client.get_object(Bucket=self.bucket, Key=path.lstrip("/"))["Body"].read()
        try:
            return await asyncio.to_thread(_read)
        except Exception as e:
            raise StorageError(f"s3 read failed: {type(e).__name__}") from e

    async def write(self, path: str, data: bytes) -> StatResult:
        def _write():
            self._client.put_object(Bucket=self.bucket, Key=path.lstrip("/"), Body=data)
        try:
            await asyncio.to_thread(_write)
        except Exception as e:
            raise StorageError(f"s3 write failed: {type(e).__name__}") from e
        return StatResult(path=path, size_bytes=len(data), modified_at=None,
                          checksum=hashlib.sha256(data).hexdigest())

    async def delete(self, path: str) -> None:
        def _delete():
            self._client.delete_object(Bucket=self.bucket, Key=path.lstrip("/"))
        try:
            await asyncio.to_thread(_delete)
        except Exception as e:
            raise StorageError(f"s3 delete failed: {type(e).__name__}") from e

    async def health_check(self) -> bool:
        def _head():
            self._client.head_bucket(Bucket=self.bucket)
        try:
            await asyncio.to_thread(_head)
            return True
        except Exception:
            return False
