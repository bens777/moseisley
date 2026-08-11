"""Structured memory + JSON workspace (owner directive §23-25).

PostgreSQL is canonical. Provenance is preserved (USER_EXPLICIT / INTEGRATION_OBSERVATION /
SYSTEM_INFERENCE / CREW_ANALYSIS); AI inference never silently becomes FACT: non-user
provenance writing memory_type='fact' is downgraded to 'belief' deterministically.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.models import (
    Decision,
    Experiment,
    Goal,
    Memory,
    Opportunity,
    Prediction,
    Project,
    User,
)
from backend.ledger import service as ledger

MEMORY_TYPES = ("fact", "preference", "belief", "prediction", "decision", "result")
PROVENANCES = ("USER_EXPLICIT", "INTEGRATION_OBSERVATION", "SYSTEM_INFERENCE", "CREW_ANALYSIS")


class MemoryError(Exception):
    pass


async def upsert(
    db: AsyncSession, user_id: str, *, memory_type: str, key: str, value,
    note: str | None = None, provenance: str = "USER_EXPLICIT",
) -> Memory:
    if memory_type not in MEMORY_TYPES:
        raise MemoryError(f"invalid memory_type: {memory_type}")
    if provenance not in PROVENANCES:
        raise MemoryError(f"invalid provenance: {provenance}")
    key = key.strip().lower().replace(" ", "_")[:200]
    if not key:
        raise MemoryError("memory key required")
    # §25: an AI inference must not silently become a FACT
    if memory_type == "fact" and provenance != "USER_EXPLICIT":
        memory_type = "belief"

    row = (await db.execute(select(Memory).where(
        Memory.user_id == user_id, Memory.memory_type == memory_type, Memory.key == key
    ))).scalar_one_or_none()
    created = row is None
    if row is None:
        row = Memory(user_id=user_id, memory_type=memory_type, key=key)
        db.add(row)
    row.value_json = {"value": value, **({"note": note} if note else {})}
    row.provenance = provenance
    row.status = "active"
    await db.flush()
    await ledger.record(db, user_id, "memory_created" if created else "memory_updated",
                        actor_type="agent", entity_type="memory", entity_id=row.id,
                        payload={"memory_type": memory_type, "key": key,
                                 "provenance": provenance})
    return row


async def read(db: AsyncSession, user_id: str, *, memory_type: str | None = None,
               key: str | None = None) -> list[Memory]:
    q = select(Memory).where(Memory.user_id == user_id, Memory.status == "active")
    if memory_type:
        q = q.where(Memory.memory_type == memory_type)
    if key:
        q = q.where(Memory.key == key.strip().lower().replace(" ", "_"))
    return list((await db.execute(q.order_by(Memory.memory_type, Memory.key))).scalars())


async def search(db: AsyncSession, user_id: str, query: str, limit: int = 20) -> list[Memory]:
    rows = await read(db, user_id)
    needle = query.strip().lower()
    hits = [m for m in rows
            if needle in m.key.lower()
            or needle in str((m.value_json or {}).get("value", "")).lower()
            or needle in str((m.value_json or {}).get("note", "")).lower()]
    return hits[:limit]


async def archive(db: AsyncSession, user_id: str, memory_id: str) -> Memory:
    row = (await db.execute(select(Memory).where(
        Memory.id == memory_id, Memory.user_id == user_id
    ))).scalar_one_or_none()
    if row is None:
        raise MemoryError("memory not found")
    row.status = "archived"
    await db.flush()
    await ledger.record(db, user_id, "memory_archived", entity_type="memory",
                        entity_id=row.id, payload={"key": row.key})
    return row


def serialize(m: Memory) -> dict:
    return {
        "id": m.id, "type": m.memory_type, "key": m.key,
        "value": (m.value_json or {}).get("value"),
        "note": (m.value_json or {}).get("note"),
        "provenance": m.provenance, "status": m.status,
        "updated_at": m.updated_at.isoformat() if m.updated_at else None,
    }


# --- JSON workspace (§23): logical files backed by canonical PostgreSQL state ---

async def build_workspace(db: AsyncSession, user: User) -> dict[str, object]:
    """Assemble the logical JSON workspace. Keys are logical file paths."""
    memories = await read(db, user.id)
    by_type: dict[str, list[dict]] = {}
    for m in memories:
        by_type.setdefault(m.memory_type, []).append(serialize(m))

    goals = list((await db.execute(select(Goal).where(Goal.user_id == user.id))).scalars())
    projects = list((await db.execute(select(Project).where(Project.user_id == user.id))).scalars())
    experiments = list((await db.execute(
        select(Experiment).where(Experiment.user_id == user.id))).scalars())
    decisions = list((await db.execute(
        select(Decision).where(Decision.user_id == user.id))).scalars())
    predictions = list((await db.execute(
        select(Prediction).where(Prediction.user_id == user.id))).scalars())
    opportunities = list((await db.execute(
        select(Opportunity).where(Opportunity.user_id == user.id))).scalars())

    return {
        "/memory/profile.json": {
            "email": user.email, "display_name": user.display_name,
            "timezone": user.timezone, "autonomy_mode": user.autonomy_mode,
        },
        "/memory/preferences.json": by_type.get("preference", []),
        "/memory/facts.json": by_type.get("fact", []),
        "/memory/beliefs.json": by_type.get("belief", []),
        "/goals/goals.json": [
            {"id": g.id, "title": g.title, "metric": g.metric, "target": g.target_value,
             "unit": g.unit, "currency": g.currency, "deadline": g.deadline,
             "constraints": g.constraints_json, "status": g.status, "progress": g.progress}
            for g in goals
        ],
        "/projects/projects.json": [
            {"id": p.id, "name": p.name, "status": p.status, "strategy": p.strategy}
            for p in projects
        ],
        "/experiments/experiments.json": [
            {"id": e.id, "hypothesis": e.hypothesis, "status": e.status,
             "success_criterion": e.success_criterion, "kill_criterion": e.kill_criterion,
             "result": e.result_json}
            for e in experiments
        ],
        "/decisions/decisions.json": [
            {"id": d.id, "reason": d.reason, "selected_action": d.selected_action,
             "confidence": d.confidence, "created_at": d.created_at.isoformat()}
            for d in decisions
        ],
        "/decisions/predictions.json": [
            {"id": p.id, "statement": p.statement, "probability": p.probability,
             "status": p.status} for p in predictions
        ],
        "/market/opportunities.json": [
            {"id": o.id, "title": o.title, "status": o.status, "confidence": o.confidence}
            for o in opportunities
        ],
    }
