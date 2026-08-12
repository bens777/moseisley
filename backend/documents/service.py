"""Markdown-first context documents (§12-13).

Documents live in PostgreSQL but are plain Markdown at fixed logical paths.
Users can export everything at any time — no lock-in.
"""
from __future__ import annotations

from sqlalchemy import delete as sa_delete
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.models import Document, DocumentChunk

# Knowledge the user pastes or uploads lives under this prefix; the structural
# context documents keep their own fixed paths below.
KNOWLEDGE_PREFIX = "/knowledge/"

CONSTITUTION_PATH = "/context/constitution.md"
IDEAL_STATE_PATH = "/context/ideal-state.md"
FOCUS_PATH = "/context/focus.md"

DEFAULT_DOCS: dict[str, str] = {
    CONSTITUTION_PATH: (
        "# Constitution\n\n"
        "Hard constraints and non-negotiables. Your crew may read and discuss this document\n"
        "but can never modify it autonomously (L0).\n\n"
        "## Values\n\n- (add your values)\n\n"
        "## Hard constraints\n\n- Never enter legal commitments on my behalf.\n"
        "- Never exceed Treasury limits.\n\n"
        "## Forbidden states\n\n- (add forbidden states)\n"
    ),
    IDEAL_STATE_PATH: (
        "# Ideal State\n\n"
        "A multidimensional description of the desired future (L1). Your crew may propose\n"
        "changes; material changes require your confirmation.\n\n"
        "## Dimensions\n\n"
        "- Monthly income: (target)\n- Working hours: (target)\n- Geographic freedom: (target)\n"
    ),
    FOCUS_PATH: (
        "# Focus\n\n_No active goals yet. Tell your crew what you want._\n"
    ),
}

# L0: the Constitution can never be modified through AI/system actors (§10).
AI_IMMUTABLE_PATHS = {CONSTITUTION_PATH}


class DocumentError(ValueError):
    pass


async def get_document(db: AsyncSession, user_id: str, path: str) -> Document | None:
    return (
        await db.execute(select(Document).where(Document.user_id == user_id, Document.path == path))
    ).scalar_one_or_none()


async def get_or_create(db: AsyncSession, user_id: str, path: str) -> Document:
    doc = await get_document(db, user_id, path)
    if doc is None:
        doc = Document(user_id=user_id, path=path, content_md=DEFAULT_DOCS.get(path, ""))
        db.add(doc)
        await db.flush()
    return doc


async def upsert_document(
    db: AsyncSession, user_id: str, path: str, content_md: str, *,
    actor_type: str = "user", metadata: dict | None = None,
) -> Document:
    if actor_type != "user" and path in AI_IMMUTABLE_PATHS:
        raise PermissionError("the Constitution can only be modified by the user")
    doc = await get_document(db, user_id, path)
    if doc is None:
        doc = Document(user_id=user_id, path=path, content_md=content_md, metadata_json=metadata or {})
        db.add(doc)
    else:
        doc.content_md = content_md
        if metadata is not None:
            doc.metadata_json = metadata
    await db.flush()
    return doc


async def delete_document(db: AsyncSession, user_id: str, document_id: str) -> Document:
    """Remove a user-added document. The structural context docs are not
    deletable — they are the shape of the workspace, not content in it."""
    doc = (await db.execute(select(Document).where(
        Document.id == document_id, Document.user_id == user_id))).scalar_one_or_none()
    if doc is None:
        raise DocumentError("document not found")
    if doc.path in DEFAULT_DOCS:
        raise DocumentError(f"{doc.path} is a built-in context document — edit it instead")
    await db.execute(sa_delete(DocumentChunk).where(
        DocumentChunk.user_id == user_id, DocumentChunk.document_id == doc.id))
    await db.delete(doc)
    await db.flush()
    return doc


async def list_documents(db: AsyncSession, user_id: str, prefix: str | None = None) -> list[Document]:
    q = select(Document).where(Document.user_id == user_id)
    if prefix:
        q = q.where(Document.path.like(f"{prefix}%"))
    return list((await db.execute(q.order_by(Document.path))).scalars())


async def search(db: AsyncSession, user_id: str, query: str, limit: int = 5) -> list[Document]:
    """Text match over the user's documents — the same ILIKE retrieval /search uses."""
    pattern = f"%{query.strip()}%"
    return list((await db.execute(
        select(Document).where(
            Document.user_id == user_id,
            or_(Document.path.ilike(pattern), Document.content_md.ilike(pattern)),
        ).order_by(Document.path).limit(limit)
    )).scalars())


async def export_all(db: AsyncSession, user_id: str) -> list[dict]:
    return [
        {"path": d.path, "content_md": d.content_md, "metadata": d.metadata_json,
         "updated_at": d.updated_at.isoformat()}
        for d in await list_documents(db, user_id)
    ]


async def import_documents(db: AsyncSession, user_id: str, docs: list[dict]) -> int:
    count = 0
    for d in docs:
        path, content = d.get("path"), d.get("content_md")
        if not path or content is None or not str(path).startswith("/"):
            continue
        await upsert_document(db, user_id, str(path), str(content), metadata=d.get("metadata"))
        count += 1
    return count
