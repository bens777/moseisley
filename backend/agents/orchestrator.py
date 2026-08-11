"""The LLM Orchestrator (owner directive §8, §20): the user's single AI intelligence.

Web Chat and Telegram both route here. The model interprets intent and emits structured
tool calls; deterministic application code validates, authorizes and executes them.
The LLM never mutates PostgreSQL directly. The loop is bounded (§18).
"""
from __future__ import annotations

import json
import logging
import uuid

from pydantic import BaseModel, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents import crew
from backend.core.models import AgentSession, ChatMessage, CrewRun, Goal, User
from backend.ledger import service as ledger
from backend.life_kernel import goal_compiler, memory
from backend.life_kernel.context import load_agent_context
from backend.providers import registry

logger = logging.getLogger("mychief.orchestrator")

MAX_TOOL_STEPS = 4  # bounded loop: tool calls per user turn (plus the final reply)
MAX_HISTORY = 16


# --- Tool argument schemas: every tool call is validated before execution (§20) ---

class MemoryUpsertArgs(BaseModel):
    memory_type: str = "fact"
    key: str
    value: str
    note: str | None = None

    @field_validator("memory_type")
    @classmethod
    def _type_ok(cls, v):
        if v not in ("fact", "preference", "belief"):
            raise ValueError("memory_type must be fact|preference|belief")
        return v


class MemoryReadArgs(BaseModel):
    memory_type: str | None = None


class MemorySearchArgs(BaseModel):
    query: str


class GoalsCreateArgs(BaseModel):
    text: str


class GoalsUpdateArgs(BaseModel):
    goal_id: str
    target_value: float | None = None
    deadline: str | None = None
    status: str | None = None
    progress: float | None = None


class CrewDelegateArgs(BaseModel):
    role: str
    task: str = ""

    @field_validator("role")
    @classmethod
    def _role_ok(cls, v):
        if v not in crew.DELEGATABLE:
            raise ValueError(f"role must be one of {crew.DELEGATABLE}")
        return v


class EmptyArgs(BaseModel):
    pass


class UsageReadArgs(BaseModel):
    window: str = "week"

    @field_validator("window")
    @classmethod
    def _window_ok(cls, v):
        if v not in ("today", "week", "month"):
            raise ValueError("window must be today|week|month")
        return v


class InstructionsReadArgs(BaseModel):
    kind: str | None = None


class InstructionsDraftArgs(BaseModel):
    name: str
    kind: str
    config: dict = {}
    schedule: dict = {}
    delivery: list[str] = []
    assigned_role: str | None = None
    project_id: str | None = None
    instruction_id: str | None = None  # set → draft updates an existing instruction


TOOL_SCHEMAS: dict[str, type[BaseModel]] = {
    "memory.upsert": MemoryUpsertArgs,
    "memory.read": MemoryReadArgs,
    "memory.search": MemorySearchArgs,
    "goals.create": GoalsCreateArgs,
    "goals.read": EmptyArgs,
    "goals.update": GoalsUpdateArgs,
    "crew.delegate": CrewDelegateArgs,
    "crew.status": EmptyArgs,
    # third pass: deterministic read tools over canonical operational data (§54)
    "metrics.overview": EmptyArgs,
    "projects.read": EmptyArgs,
    "usage.read": UsageReadArgs,
    "instructions.read": InstructionsReadArgs,
    "approvals.read": EmptyArgs,
    "devproposals.read": EmptyArgs,
    # manager-only draft/save flow (§14, §17) — gated in _execute_tool
    "instructions.draft": InstructionsDraftArgs,
    "instructions.save": EmptyArgs,
}

MANAGER_ONLY_TOOLS = {"instructions.draft", "instructions.save"}


async def _execute_tool(db: AsyncSession, user: User, tool: str, args: BaseModel,
                        orchestrator_run_id: str, *, role: str = "orchestrator") -> dict:
    """Deterministic tool execution. Authorization: the authenticated user owns all data."""
    if tool in MANAGER_ONLY_TOOLS and role != "manager":
        return {"error": f"{tool} is only available to the Manager"}
    if tool == "metrics.overview":
        from backend.ops import metrics as metrics_svc

        return await metrics_svc.overview(db, user.id)
    if tool == "projects.read":
        from backend.api.routes.projects import project_metrics
        from backend.core.models import Project

        rows = list((await db.execute(select(Project).where(
            Project.user_id == user.id))).scalars())
        return {"projects": [
            {"id": p.id, "name": p.name, "status": p.status, "urls": p.urls_json or {},
             "capital_allocated_cents": p.capital_allocated_cents,
             "metrics": await project_metrics(db, user.id, p.id)} for p in rows]}
    if tool == "usage.read":
        from datetime import UTC as _UTC
        from datetime import datetime as _dt
        from datetime import timedelta as _td

        from backend.ops import metrics as metrics_svc

        now = _dt.now(_UTC)
        since = (now.replace(hour=0, minute=0, second=0, microsecond=0)
                 if args.window == "today"
                 else now - _td(days=7 if args.window == "week" else 30))
        totals = await metrics_svc.usage_totals(db, user.id, since=since)
        by_agent = await metrics_svc.usage_breakdown(db, user.id, since=since,
                                                     dimension="agent")
        return {"window": args.window, "totals": totals, "by_agent": by_agent}
    if tool == "instructions.read":
        from backend.ops import instructions as instructions_svc

        rows = await instructions_svc.list_for(db, user.id, kind=args.kind)
        return {"instructions": [
            json.loads(json.dumps(instructions_svc.serialize(i), default=str))
            for i in rows[:30]]}
    if tool == "approvals.read":
        from backend.core.models import ApprovalRequest

        rows = list((await db.execute(select(ApprovalRequest).where(
            ApprovalRequest.user_id == user.id, ApprovalRequest.status == "pending"
        ))).scalars())
        return {"pending": [
            {"id": a.id, "action_type": a.action_type, "payload": a.action_payload_json,
             "risk_level": a.risk_level, "created_at": a.created_at.isoformat()}
            for a in rows[:20]]}
    if tool == "devproposals.read":
        from backend.core.models import DevProposal

        rows = list((await db.execute(select(DevProposal).where(
            DevProposal.user_id == user.id).order_by(DevProposal.created_at.desc())
        )).scalars())
        return {"proposals": [
            {"id": p.id, "title": p.title, "status": p.status, "risk": p.risk,
             "patch_hash": p.patch_hash} for p in rows[:20]]}
    if tool == "instructions.draft":
        from backend.agents import manager as manager_svc

        return await manager_svc.store_draft(db, user, args)
    if tool == "instructions.save":
        from backend.agents import manager as manager_svc

        return await manager_svc.apply_draft(db, user)
    if tool == "memory.upsert":
        a = args  # type: MemoryUpsertArgs
        row = await memory.upsert(db, user.id, memory_type=a.memory_type, key=a.key,
                                  value=a.value, note=a.note, provenance="USER_EXPLICIT")
        return {"stored": memory.serialize(row)}
    if tool == "memory.read":
        rows = await memory.read(db, user.id, memory_type=args.memory_type)
        return {"memories": [memory.serialize(m) for m in rows[:50]]}
    if tool == "memory.search":
        rows = await memory.search(db, user.id, args.query)
        return {"memories": [memory.serialize(m) for m in rows]}
    if tool == "goals.create":
        result = await goal_compiler.compile_goal(db, user.id, args.text)
        if result.status == "created" and result.goal:
            g = result.goal
            return {"created": {"id": g.id, "title": g.title, "metric": g.metric,
                                "target": g.target_value, "deadline": g.deadline}}
        return {"status": result.status, "question": result.question}
    if tool == "goals.read":
        goals = list((await db.execute(select(Goal).where(
            Goal.user_id == user.id, Goal.status == "active"
        ))).scalars())
        return {"goals": [{"id": g.id, "title": g.title, "metric": g.metric,
                           "target": g.target_value, "unit": g.unit,
                           "deadline": g.deadline, "progress": g.progress,
                           "constraints": g.constraints_json} for g in goals]}
    if tool == "goals.update":
        a = args
        goal = (await db.execute(select(Goal).where(
            Goal.id == a.goal_id, Goal.user_id == user.id
        ))).scalar_one_or_none()
        if goal is None:
            return {"error": "goal not found"}
        changes = {}
        for f in ("target_value", "deadline", "status", "progress"):
            v = getattr(a, f)
            if v is not None:
                setattr(goal, f, v)
                changes[f] = v
        if changes:
            await ledger.record(db, user.id, "goal_updated", actor_type="agent",
                                actor_id="orchestrator", entity_type="goal",
                                entity_id=goal.id, payload=changes)
            from backend.life_kernel.focus import rebuild_focus

            await rebuild_focus(db, user.id)
        return {"updated": changes, "goal_id": goal.id}
    if tool == "crew.delegate":
        result = await crew.delegate(db, user, args.role, args.task,
                                     orchestrator_run_id=orchestrator_run_id)
        return {"role": args.role, "result": result}
    if tool == "crew.status":
        runs = await crew.last_runs(db, user.id, limit=8)
        return {"recent_runs": [
            {"role": r.crew_role, "status": r.status, "task": r.task_summary,
             "finished_at": r.finished_at.isoformat() if r.finished_at else None}
            for r in runs
        ]}
    return {"error": f"unknown tool {tool}"}


def _memory_brief(memories: list) -> str:
    if not memories:
        return "none stored yet"
    lines = [f"- [{m.memory_type}/{m.provenance}] {m.key} = "
             f"{json.dumps((m.value_json or {}).get('value'))}" for m in memories[:30]]
    return "\n".join(lines)


async def handle_message(db: AsyncSession, user: User, session: AgentSession, text: str,
                         *, channel: str = "web", role: str = "orchestrator",
                         page_context: dict | None = None) -> str:
    """One bounded tool-loop turn → final reply. Stores chat messages.

    role="manager" runs the same deterministic loop with the Manager prompt,
    manager-only draft tools enabled, and page context injected (§12-§13)."""
    orchestrator_run_id = uuid.uuid4().hex
    db.add(ChatMessage(user_id=user.id, session_id=session.id, role="user",
                       content=text, channel=channel))
    await db.flush()

    context = await load_agent_context(db, user)
    memories = await memory.read(db, user.id)
    system_parts = [
        await crew.get_prompt(db, user.id, role),
        f"## Focus\n{context['focus_md']}",
        f"## World snapshot\n{json.dumps(context['world'], default=str)[:4000]}",
        f"## Stored memory\n{_memory_brief(memories)}",
    ]
    if page_context:
        system_parts.append(
            "## PAGE CONTEXT (what the user is currently looking at)\n"
            + json.dumps(page_context, default=str)[:1500])
    system_parts.append("Remember: reply with EXACTLY one JSON object per the output contract.")
    system = "\n\n".join(system_parts)

    history = list((await db.execute(
        select(ChatMessage).where(ChatMessage.session_id == session.id)
        .order_by(ChatMessage.created_at.desc()).limit(MAX_HISTORY)
    )).scalars())[::-1]
    messages = [{"role": "system", "content": system}] + [
        {"role": m.role, "content": m.content}
        for m in history if m.role in ("user", "assistant")
    ]

    reply_text: str | None = None
    for _step in range(MAX_TOOL_STEPS + 1):
        result = await registry.generate(
            db, user.id, messages, crew_role=role, purpose="chat",
            orchestrator_run_id=orchestrator_run_id, json_mode=True, max_tokens=900,
        )
        parsed = result.parse_json()
        if not isinstance(parsed, dict) or "action" not in parsed:
            # model ignored the contract — treat its text as the reply (§20 still holds:
            # nothing was mutated without a validated tool call)
            reply_text = result.text.strip() or "I couldn't process that — try rephrasing."
            break
        if parsed.get("action") == "reply":
            reply_text = str(parsed.get("text") or "").strip() or "(empty reply)"
            break
        if parsed.get("action") == "tool":
            tool = str(parsed.get("tool") or "")
            schema = TOOL_SCHEMAS.get(tool)
            if schema is None:
                tool_result = {"error": f"unknown tool: {tool}"}
            else:
                try:
                    args = schema.model_validate(parsed.get("args") or {})
                    tool_result = await _execute_tool(db, user, tool, args,
                                                      orchestrator_run_id, role=role)
                except ValidationError as e:
                    tool_result = {"error": f"invalid args: {e.errors()[:2]}"}
                except Exception as e:  # noqa: BLE001 - tool failures return to the model
                    logger.warning("tool %s failed: %s", tool, e)
                    tool_result = {"error": f"{type(e).__name__}"}
            messages.append({"role": "assistant", "content": json.dumps(parsed)})
            messages.append({"role": "user",
                             "content": f"TOOL RESULT for {tool}: "
                                        f"{json.dumps(tool_result, default=str)[:3000]}\n"
                                        "Continue per the output contract."})
            continue
        reply_text = "I couldn't process that — try rephrasing."
        break
    if reply_text is None:
        reply_text = "I hit my per-turn tool limit — here's where I got: the requested " \
                     "operations were executed; ask me for the details."

    db.add(ChatMessage(user_id=user.id, session_id=session.id, role="assistant",
                       content=reply_text, channel=channel,
                       metadata_json={"orchestrator_run_id": orchestrator_run_id}))
    await db.flush()
    return reply_text


async def runs_for(db: AsyncSession, user_id: str, orchestrator_run_id: str) -> list[CrewRun]:
    return list((await db.execute(select(CrewRun).where(
        CrewRun.user_id == user_id,
        CrewRun.orchestrator_run_id == orchestrator_run_id,
    ))).scalars())
