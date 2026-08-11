from __future__ import annotations

import base64

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from backend.core.models import FileRef
from backend.core.security import DB, CurrentUser
from backend.storage.base import StorageError
from backend.storage.factory import get_owned_storage

router = APIRouter(prefix="/files")

MAX_INLINE_BYTES = 10 * 1024 * 1024


def _serialize(f: FileRef) -> dict:
    return {
        "id": f.id, "storage_provider": f.storage_provider, "external_id": f.external_id,
        "path": f.path, "title": f.title, "mime_type": f.mime_type, "checksum": f.checksum,
        "size_bytes": f.size_bytes, "modified_at": f.modified_at,
        "indexing_status": f.indexing_status, "metadata": f.metadata_json,
        "created_at": f.created_at,
    }


@router.get("")
async def list_files(user: CurrentUser, db: DB):
    rows = (await db.execute(
        select(FileRef).where(FileRef.user_id == user.id).order_by(FileRef.path)
    )).scalars()
    return [_serialize(f) for f in rows]


class UploadRequest(BaseModel):
    path: str
    content_base64: str
    title: str | None = None
    mime_type: str | None = None


@router.post("/upload")
async def upload(body: UploadRequest, user: CurrentUser, db: DB):
    """Write to Moseisley.sh-owned storage under the user's isolated prefix and record metadata."""
    try:
        data = base64.b64decode(body.content_base64)
    except Exception as e:
        raise HTTPException(400, "invalid base64 content") from e
    if len(data) > MAX_INLINE_BYTES:
        raise HTTPException(413, "file too large (10MB inline limit)")
    if ".." in body.path or body.path.startswith("/"):
        raise HTTPException(400, "invalid path")
    storage = get_owned_storage()
    storage_path = f"users/{user.id}/{body.path}"
    try:
        st = await storage.write(storage_path, data)
    except StorageError as e:
        raise HTTPException(502, str(e)) from e
    ref = (await db.execute(select(FileRef).where(
        FileRef.user_id == user.id, FileRef.storage_provider == storage.provider_name,
        FileRef.path == storage_path,
    ))).scalar_one_or_none()
    if ref is None:
        ref = FileRef(user_id=user.id, storage_provider=storage.provider_name, path=storage_path)
        db.add(ref)
    ref.title = body.title or body.path.rsplit("/", 1)[-1]
    ref.mime_type = body.mime_type
    ref.checksum = st.checksum
    ref.size_bytes = st.size_bytes
    ref.modified_at = st.modified_at
    await db.commit()
    return _serialize(ref)


@router.get("/{file_id}/content")
async def download(file_id: str, user: CurrentUser, db: DB):
    ref = (await db.execute(select(FileRef).where(
        FileRef.id == file_id, FileRef.user_id == user.id
    ))).scalar_one_or_none()
    if ref is None:
        raise HTTPException(404, "file not found")
    storage = get_owned_storage()
    if ref.storage_provider != storage.provider_name:
        raise HTTPException(400, "file lives in external storage; use the storage integration")
    try:
        data = await storage.read(ref.path)
    except StorageError as e:
        raise HTTPException(404, str(e)) from e
    return {"path": ref.path, "content_base64": base64.b64encode(data).decode(),
            "mime_type": ref.mime_type}


@router.delete("/{file_id}")
async def delete_file(file_id: str, user: CurrentUser, db: DB):
    ref = (await db.execute(select(FileRef).where(
        FileRef.id == file_id, FileRef.user_id == user.id
    ))).scalar_one_or_none()
    if ref is None:
        raise HTTPException(404, "file not found")
    storage = get_owned_storage()
    if ref.storage_provider == storage.provider_name:
        try:
            await storage.delete(ref.path)
        except StorageError:
            pass
    await db.delete(ref)
    await db.commit()
    return {"ok": True}


class RegisterExternalRequest(BaseModel):
    """BYOS: register a reference to a file that stays in the user's own storage."""

    connection_id: str
    path: str
    title: str | None = None
    mime_type: str | None = None
    external_id: str | None = None
    size_bytes: int | None = None
    checksum: str | None = None


@router.post("/register-external")
async def register_external(body: RegisterExternalRequest, user: CurrentUser, db: DB):
    from backend.core.models import IntegrationConnection

    conn = (await db.execute(select(IntegrationConnection).where(
        IntegrationConnection.id == body.connection_id,
        IntegrationConnection.user_id == user.id,
    ))).scalar_one_or_none()
    if conn is None:
        raise HTTPException(404, "storage connection not found")
    provider = f"byos:{conn.id}"
    ref = (await db.execute(select(FileRef).where(
        FileRef.user_id == user.id, FileRef.storage_provider == provider,
        FileRef.path == body.path,
    ))).scalar_one_or_none()
    if ref is None:
        ref = FileRef(user_id=user.id, storage_provider=provider, path=body.path)
        db.add(ref)
    ref.title = body.title or body.path.rsplit("/", 1)[-1]
    ref.mime_type = body.mime_type
    ref.external_id = body.external_id
    ref.size_bytes = body.size_bytes
    ref.checksum = body.checksum
    await db.commit()
    return _serialize(ref)
