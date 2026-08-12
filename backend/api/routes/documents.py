from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.security import DB, CurrentUser
from backend.documents import service as documents
from backend.ledger import service as ledger

router = APIRouter(prefix="/documents")


def _serialize(d) -> dict:
    return {
        "id": d.id, "path": d.path, "content_md": d.content_md,
        "metadata": d.metadata_json, "created_at": d.created_at, "updated_at": d.updated_at,
    }


@router.get("")
async def list_docs(user: CurrentUser, db: DB, prefix: str | None = None):
    docs = await documents.list_documents(db, user.id, prefix)
    if not docs:
        for path in documents.DEFAULT_DOCS:
            await documents.get_or_create(db, user.id, path)
        await db.commit()
        docs = await documents.list_documents(db, user.id, prefix)
    return [_serialize(d) for d in docs]


@router.get("/export")
async def export_docs(user: CurrentUser, db: DB):
    return {"documents": await documents.export_all(db, user.id)}


class ImportRequest(BaseModel):
    documents: list[dict]


@router.post("/import")
async def import_docs(body: ImportRequest, user: CurrentUser, db: DB):
    count = await documents.import_documents(db, user.id, body.documents)
    await db.commit()
    return {"imported": count}


class UpsertRequest(BaseModel):
    path: str
    content_md: str
    metadata: dict | None = None


@router.put("")
async def upsert_doc(body: UpsertRequest, user: CurrentUser, db: DB):
    if not body.path.startswith("/"):
        raise HTTPException(400, "path must start with /")
    doc = await documents.upsert_document(
        db, user.id, body.path, body.content_md, actor_type="user", metadata=body.metadata
    )
    await ledger.record(db, user.id, "document_updated", actor_type="user",
                        entity_type="document", entity_id=doc.id, payload={"path": doc.path})
    await db.commit()
    return _serialize(doc)


@router.delete("/{document_id}")
async def delete_doc(document_id: str, user: CurrentUser, db: DB):
    try:
        doc = await documents.delete_document(db, user.id, document_id)
    except documents.DocumentError as e:
        raise HTTPException(404 if "not found" in str(e) else 400, str(e)) from e
    await ledger.record(db, user.id, "document_updated", actor_type="user",
                        entity_type="document", entity_id=document_id,
                        payload={"path": doc.path, "deleted": True})
    await db.commit()
    return {"deleted": True, "path": doc.path}


@router.get("/by-path")
async def get_doc(user: CurrentUser, db: DB, path: str):
    doc = await documents.get_document(db, user.id, path)
    if doc is None:
        if path in documents.DEFAULT_DOCS:
            doc = await documents.get_or_create(db, user.id, path)
            await db.commit()
        else:
            raise HTTPException(404, "document not found")
    return _serialize(doc)
