"""Storage resolution: Moseisley.sh-owned storage from env; BYOS from a user's
integration connection (credentials encrypted at rest)."""
from __future__ import annotations

import json

from backend.core.config import get_settings
from backend.core.crypto import decrypt_secret
from backend.core.models import IntegrationConnection
from backend.storage.base import StorageAdapter, StorageError
from backend.storage.local import LocalFilesystemStorage
from backend.storage.s3 import S3CompatibleStorage

_owned: StorageAdapter | None = None


def get_owned_storage() -> StorageAdapter:
    """Moseisley.sh-owned storage (drafts/exports/processing artifacts)."""
    global _owned
    if _owned is None:
        settings = get_settings()
        if settings.storage_backend == "s3":
            if not (settings.s3_bucket and settings.s3_access_key_id and settings.s3_secret_access_key):
                raise StorageError("s3 storage selected but credentials missing")
            _owned = S3CompatibleStorage(
                settings.s3_bucket, settings.s3_access_key_id, settings.s3_secret_access_key,
                settings.s3_endpoint_url, settings.s3_region,
            )
        else:
            _owned = LocalFilesystemStorage(settings.storage_local_path)
    return _owned


def reset_owned_storage() -> None:
    global _owned
    _owned = None


def storage_for_connection(connection: IntegrationConnection) -> StorageAdapter:
    """BYOS: build a StorageAdapter from a user's storage integration connection."""
    if connection.integration_type != "s3":
        raise StorageError(f"connection type {connection.integration_type} is not a storage source")
    cfg = connection.configuration_json or {}
    creds = json.loads(decrypt_secret(connection.encrypted_credentials)) \
        if connection.encrypted_credentials else {}
    return S3CompatibleStorage(
        cfg.get("bucket", ""), creds.get("access_key_id", ""), creds.get("secret_access_key", ""),
        cfg.get("endpoint_url"), cfg.get("region"),
    )
