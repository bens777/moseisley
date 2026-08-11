"""Instruction / automation control-layer API (third pass §15-§16).

Human view + JSON view are both fed by the same canonical serialization.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.core.security import DB, CurrentUser
from backend.ops import instructions as svc

router = APIRouter(prefix="/instructions")


class InstructionRequest(BaseModel):
    name: str
    kind: str
    config: dict = {}
    schedule: dict = {}
    delivery: list[str] = []
    assigned_role: str | None = None
    provider: str | None = None
    model: str | None = None
    project_id: str | None = None
    enabled: bool = True
    reason: str | None = None


class ToggleRequest(BaseModel):
    enabled: bool


class RollbackRequest(BaseModel):
    version: int


async def _serialized(db, instruction) -> dict:
    return svc.serialize(instruction,
                         next_run_at=await svc.next_run_at(db, instruction.id))


@router.get("")
async def list_instructions(user: CurrentUser, db: DB, kind: str | None = None,
                            project_id: str | None = None):
    rows = await svc.list_for(db, user.id, kind=kind, project_id=project_id)
    return [await _serialized(db, i) for i in rows]


@router.post("")
async def create_instruction(body: InstructionRequest, user: CurrentUser, db: DB):
    try:
        instruction = await svc.create(
            db, user, name=body.name, kind=body.kind, config=body.config,
            schedule=body.schedule, delivery=body.delivery,
            assigned_role=body.assigned_role, provider=body.provider,
            model=body.model, project_id=body.project_id, enabled=body.enabled,
            created_by="user", reason=body.reason)
    except svc.InstructionError as e:
        raise HTTPException(400, str(e)) from e
    await db.commit()
    return await _serialized(db, instruction)


@router.get("/{instruction_id}")
async def get_instruction(instruction_id: str, user: CurrentUser, db: DB):
    try:
        instruction = await svc.get(db, user.id, instruction_id)
    except svc.InstructionError as e:
        raise HTTPException(404, str(e)) from e
    return {**(await _serialized(db, instruction)),
            "versions": [
                {"version": v.version, "changed_by": v.changed_by, "reason": v.reason,
                 "created_at": v.created_at}
                for v in await svc.versions(db, user.id, instruction_id)
            ]}


@router.put("/{instruction_id}")
async def update_instruction(instruction_id: str, body: InstructionRequest,
                             user: CurrentUser, db: DB):
    try:
        instruction = await svc.update(
            db, user, instruction_id, changed_by="user", reason=body.reason,
            name=body.name, kind=body.kind, config=body.config,
            schedule=body.schedule, delivery=body.delivery,
            assigned_role=body.assigned_role, provider=body.provider,
            model=body.model, project_id=body.project_id, enabled=body.enabled)
    except svc.InstructionError as e:
        raise HTTPException(400, str(e)) from e
    await db.commit()
    return await _serialized(db, instruction)


@router.post("/{instruction_id}/toggle")
async def toggle_instruction(instruction_id: str, body: ToggleRequest,
                             user: CurrentUser, db: DB):
    try:
        instruction = await svc.toggle(db, user, instruction_id, body.enabled)
    except svc.InstructionError as e:
        raise HTTPException(404, str(e)) from e
    await db.commit()
    return await _serialized(db, instruction)


@router.post("/{instruction_id}/duplicate")
async def duplicate_instruction(instruction_id: str, user: CurrentUser, db: DB):
    try:
        copy = await svc.duplicate(db, user, instruction_id)
    except svc.InstructionError as e:
        raise HTTPException(404, str(e)) from e
    await db.commit()
    return await _serialized(db, copy)


@router.post("/{instruction_id}/rollback")
async def rollback_instruction(instruction_id: str, body: RollbackRequest,
                               user: CurrentUser, db: DB):
    try:
        instruction = await svc.rollback(db, user, instruction_id, body.version)
    except svc.InstructionError as e:
        raise HTTPException(400, str(e)) from e
    await db.commit()
    return await _serialized(db, instruction)


@router.delete("/{instruction_id}")
async def delete_instruction(instruction_id: str, user: CurrentUser, db: DB):
    try:
        await svc.remove(db, user, instruction_id)
    except svc.InstructionError as e:
        raise HTTPException(404, str(e)) from e
    await db.commit()
    return {"deleted": True}


@router.get("/{instruction_id}/export")
async def export_instruction(instruction_id: str, user: CurrentUser, db: DB):
    try:
        instruction = await svc.get(db, user.id, instruction_id)
    except svc.InstructionError as e:
        raise HTTPException(404, str(e)) from e
    from fastapi.encoders import jsonable_encoder

    payload = jsonable_encoder(await _serialized(db, instruction))
    return JSONResponse(payload, headers={
        "Content-Disposition": f'attachment; filename="instruction-{instruction.id}.json"'})


@router.post("/{instruction_id}/run")
async def run_instruction_now(instruction_id: str, user: CurrentUser, db: DB):
    """Manual trigger — executes the instruction immediately (same code path
    as the scheduler)."""
    try:
        instruction = await svc.get(db, user.id, instruction_id)
    except svc.InstructionError as e:
        raise HTTPException(404, str(e)) from e
    if instruction.kind == "market_watch":
        from backend.market.watches import run_watch

        result = await run_watch(db, user, instruction)
    elif instruction.kind == "dev_review":
        from backend.agents.dev import run_dev_review

        result = await run_dev_review(db, user, instruction)
    else:
        from backend.agents import crew

        role = instruction.assigned_role or "strategist"
        task = (instruction.config_json or {}).get("instruction") or instruction.name
        run = await crew.delegate(db, user, role, task, orchestrator_run_id=None)
        result = {"crew_run_id": run.id, "status": run.status}
        await svc.record_run_result(db, instruction, result)
    await db.commit()
    return result
