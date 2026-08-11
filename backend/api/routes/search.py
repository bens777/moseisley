from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import or_, select

from backend.core.models import Document, DocumentChunk, Goal, XRayFinding
from backend.core.security import DB, CurrentUser

router = APIRouter()


@router.get("/search")
async def search(user: CurrentUser, db: DB, q: str = Query(min_length=2, max_length=200),
                 limit: int = Query(default=20, le=50)):
    """Retrieval order (architecture update): structured SQL + metadata filtering first;
    ILIKE/FTS text matching; vector similarity only when explicitly indexed."""
    pattern = f"%{q}%"
    docs = list((await db.execute(
        select(Document).where(Document.user_id == user.id,
                               or_(Document.path.ilike(pattern), Document.content_md.ilike(pattern)))
        .limit(limit)
    )).scalars())
    goals = list((await db.execute(
        select(Goal).where(Goal.user_id == user.id,
                           or_(Goal.title.ilike(pattern), Goal.metric.ilike(pattern)))
        .limit(limit)
    )).scalars())
    findings = list((await db.execute(
        select(XRayFinding).where(XRayFinding.user_id == user.id,
                                  or_(XRayFinding.title.ilike(pattern),
                                      XRayFinding.description.ilike(pattern)))
        .limit(limit)
    )).scalars())
    chunks = list((await db.execute(
        select(DocumentChunk).where(DocumentChunk.user_id == user.id,
                                    DocumentChunk.text.ilike(pattern))
        .limit(limit)
    )).scalars())
    return {
        "documents": [{"id": d.id, "path": d.path,
                       "snippet": d.content_md[:200]} for d in docs],
        "goals": [{"id": g.id, "title": g.title, "metric": g.metric} for g in goals],
        "findings": [{"id": f.id, "title": f.title, "type": f.type} for f in findings],
        "chunks": [{"id": c.id, "document_id": c.document_id,
                    "snippet": c.text[:200]} for c in chunks],
    }
