from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.security import DB, CurrentUser
from backend.life_kernel import memory

router = APIRouter()


@router.get("/memory")
async def list_memory(user: CurrentUser, db: DB, memory_type: str | None = None,
                      q: str | None = None):
    rows = (await memory.search(db, user.id, q) if q
            else await memory.read(db, user.id, memory_type=memory_type))
    return [memory.serialize(m) for m in rows]


class MemoryUpsertRequest(BaseModel):
    memory_type: str = "fact"
    key: str
    value: str
    note: str | None = None


@router.post("/memory")
async def upsert_memory(body: MemoryUpsertRequest, user: CurrentUser, db: DB):
    try:
        row = await memory.upsert(db, user.id, memory_type=body.memory_type, key=body.key,
                                  value=body.value, note=body.note,
                                  provenance="USER_EXPLICIT")
    except memory.MemoryError as e:
        raise HTTPException(400, str(e)) from e
    await db.commit()
    return memory.serialize(row)


@router.delete("/memory/{memory_id}")
async def archive_memory(memory_id: str, user: CurrentUser, db: DB):
    try:
        await memory.archive(db, user.id, memory_id)
    except memory.MemoryError as e:
        raise HTTPException(404, str(e)) from e
    await db.commit()
    return {"ok": True}


@router.get("/workspace/export")
async def export_workspace(user: CurrentUser, db: DB):
    """The full logical JSON workspace (§23) — user-readable, AI-readable, portable."""
    return {"workspace": await memory.build_workspace(db, user)}


@router.get("/workspace/file")
async def workspace_file(path: str, user: CurrentUser, db: DB):
    workspace = await memory.build_workspace(db, user)
    if path not in workspace:
        raise HTTPException(404, f"no such workspace file; available: {sorted(workspace)}")
    return {"path": path, "content": workspace[path]}
