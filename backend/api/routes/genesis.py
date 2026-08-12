"""Crew Genesis — AI-assembled crew setup, run after registration and
re-runnable from the Crew page.

Two endpoints, both building on what already exists:
  · propose  → registry.generate() (works on ROOKIE) returns strict JSON,
    pydantic-validated, one retry, then a deterministic fallback template.
  · apply    → enables the kept roles in crew_configs and creates one native
    AgentConfig per role (avatar in configuration_json, exactly like the
    create-agent wizard), then posts a real Manager message into the existing
    manager session so the welcome lives in the conversation history.

Pro-gated roles are filtered out of the apply batch unless the user is
entitled, so the flow can never 402 half-way through.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ValidationError

from backend.agents import crew
from backend.agents import manager as manager_svc
from backend.api.routes.agents import CREW_AVATARS, ROLE_FEATURES
from backend.billing import entitlements
from backend.core.models import AgentConfig, ChatMessage
from backend.core.security import DB, CurrentUser
from backend.ledger import service as ledger
from backend.providers import registry

router = APIRouter(prefix="/crew/genesis")
logger = logging.getLogger("mychief.genesis")

GENESIS_DONE_KEY = "crew_genesis_done"      # settings_json flag: never auto-run again
GENESIS_SKIPPED_KEY = "crew_genesis_skipped"

MAX_PROPOSALS = 5
MIN_PROPOSALS = 2

# Sensible default face per role — the model may override with any shipped id.
ROLE_AVATARS = {
    "orchestrator": "crew-orchestrator.webp", "strategist": "crew-strategist.webp",
    "radar": "crew-radar.webp", "xray": "crew-xray.webp",
    "challenger": "crew-challenger.webp", "auditor": "crew-auditor.webp",
    "follow_up": "crew-followup.webp", "dev": "crew-dev.webp",
    "commitment_tracker": "crew-treasury.webp", "inbox_triage": "crew-bartender.webp",
    "goal_compiler": "crew-orchestrator.webp", "manager": "crew-bartender.webp",
}

# Used when the model returns nothing usable twice. Ungated roles only, so it
# always applies cleanly for a trial user.
FALLBACK_ROLES = ["follow_up", "inbox_triage", "commitment_tracker"]

PROPOSAL_PROMPT = """\
You are assembling a small AI crew for a solo founder on Moseisley.

THE USER'S INTENT:
{intent}

AVAILABLE ROLES (choose only from these ids):
{roles}

Return between {min_n} and {max_n} crew members that genuinely serve that intent.
Prefer fewer, well-chosen members over a full roster. Never invent a role id.

OUTPUT CONTRACT — reply with EXACTLY one JSON object and nothing else:
{{"crew": [{{"role": "<role id>", "name": "<short display name>",
            "avatar": "<one of: {avatars}>", "rationale": "<one line, why this
            member, in plain words>"}}]}}
"""


class ProposalMember(BaseModel):
    role: str
    name: str = Field(min_length=1, max_length=80)
    avatar: str
    rationale: str = Field(default="", max_length=200)


class Proposal(BaseModel):
    crew: list[ProposalMember]


class ProposeRequest(BaseModel):
    intent: str = Field(default="", max_length=2000)


class ApplyMember(BaseModel):
    role: str
    name: str = Field(min_length=1, max_length=80)
    avatar: str | None = None


class ApplyRequest(BaseModel):
    crew: list[ApplyMember] = []
    remove_roles: list[str] = []     # re-run: explicitly unselected roles
    skip: bool = False


def _clean(member: ProposalMember) -> ProposalMember | None:
    """Drop anything the model invented; snap the avatar to a shipped id."""
    if member.role not in crew.ROLES:
        return None
    if member.avatar not in CREW_AVATARS:
        member.avatar = ROLE_AVATARS.get(member.role, "crew-orchestrator.webp")
    return member


async def _gated_roles(db, user) -> set[str]:
    """Roles this user may not create yet (hosted plans only)."""
    if not entitlements.billing_enforced():
        return set()
    plan = await entitlements.user_plan(db, user.id)
    return {r for r, feature in ROLE_FEATURES.items()
            if not entitlements.plan_allows(plan, feature)}


@router.post("/propose")
async def propose(body: ProposeRequest, user: CurrentUser, db: DB):
    """Ask the platform's own AI for a crew. Never fails the request: an
    unusable answer twice over falls back to a deterministic template."""
    roles_block = "\n".join(f"- {r}: {name} — {mission}"
                            for r, (name, mission) in crew.ROLES.items()
                            if r not in ("orchestrator", "manager"))
    prompt = PROPOSAL_PROMPT.format(
        intent=body.intent.strip() or "(not stated — suggest a sensible starter crew)",
        roles=roles_block, min_n=MIN_PROPOSALS, max_n=MAX_PROPOSALS,
        avatars=", ".join(sorted(CREW_AVATARS)),
    )

    members: list[ProposalMember] = []
    source = "llm"
    for attempt in (1, 2):                     # one retry on invalid JSON
        try:
            result = await registry.generate(
                db, user.id, [{"role": "user", "content": prompt}],
                purpose="classification", json_mode=True, max_tokens=700,
            )
            parsed = Proposal.model_validate(result.parse_json() or {})
            members = [m for m in (_clean(x) for x in parsed.crew) if m]
            if members:
                break
        except (ValidationError, ValueError, TypeError) as e:
            logger.info("genesis proposal attempt %s unusable: %s", attempt, type(e).__name__)
        except Exception as e:                 # noqa: BLE001 — provider trouble
            logger.warning("genesis proposal attempt %s failed: %s", attempt, type(e).__name__)
            break

    if not members:                            # deterministic, always applicable
        source = "fallback"
        members = [ProposalMember(role=r, name=crew.ROLES[r][0],
                                  avatar=ROLE_AVATARS.get(r, "crew-orchestrator.webp"),
                                  rationale=crew.ROLES[r][1]) for r in FALLBACK_ROLES]

    members = members[:MAX_PROPOSALS]
    gated = await _gated_roles(db, user)
    await db.commit()                          # persist the usage row from generate()
    return {
        "source": source,
        "crew": [{**m.model_dump(),
                  "gated": m.role in gated,
                  "mission": crew.ROLES[m.role][1]} for m in members],
        "gated_roles": sorted(gated),
    }


@router.post("/apply")
async def apply(body: ApplyRequest, user: CurrentUser, db: DB):
    """Enable the kept roles and give each one a native agent with its face."""
    if body.skip:
        user.settings_json = {**(user.settings_json or {}),
                              GENESIS_SKIPPED_KEY: True, GENESIS_DONE_KEY: True}
        await db.commit()
        return {"skipped": True, "created": [], "enabled": [], "removed": []}

    gated = await _gated_roles(db, user)
    existing = {a.configuration_json.get("role"): a
                for a in await _user_agents(db, user.id)
                if (a.configuration_json or {}).get("role")}

    created, enabled, blocked = [], [], []
    for member in body.crew:
        if member.role not in crew.ROLES:
            raise HTTPException(400, f"unknown crew role: {member.role}")
        if member.role in gated:               # never 402 mid-flow: skip + report
            blocked.append(member.role)
            continue
        await crew.set_model_policy(db, user.id, member.role, model_policy="inherit")
        cfg = await crew.get_config(db, user.id, member.role)
        if cfg is not None:
            cfg.enabled = True
        enabled.append(member.role)

        if member.role in existing:            # re-run: keep what is already there
            continue
        avatar = member.avatar if member.avatar in CREW_AVATARS else \
            ROLE_AVATARS.get(member.role, "crew-orchestrator.webp")
        agent = AgentConfig(
            user_id=user.id, adapter_type="native", display_name=member.name.strip(),
            configuration_json={"role": member.role, "avatar": avatar},
            health_status="ok",
        )
        db.add(agent)
        created.append({"role": member.role, "name": agent.display_name, "avatar": avatar})

    removed = []
    for role in body.remove_roles:              # explicit unselection only
        cfg = await crew.get_config(db, user.id, role)
        if cfg is not None:
            cfg.enabled = False
        agent = existing.get(role)
        if agent is not None:
            await db.delete(agent)
        removed.append(role)

    user.settings_json = {**(user.settings_json or {}), GENESIS_DONE_KEY: True}
    await db.flush()
    await _post_welcome(db, user, created or [{"name": crew.ROLES[r][0]} for r in enabled])
    await ledger.record(db, user.id, "agent_switched", actor_type="user",
                        payload={"event": "crew_genesis", "enabled": enabled,
                                 "removed": removed, "blocked": blocked})
    await db.commit()
    return {"skipped": False, "created": created, "enabled": enabled,
            "removed": removed, "blocked": blocked}


async def _user_agents(db, user_id: str) -> list[AgentConfig]:
    from sqlalchemy import select

    return list((await db.execute(select(AgentConfig).where(
        AgentConfig.user_id == user_id))).scalars())


async def _post_welcome(db, user, created: list[dict]) -> None:
    """A real message in the real Manager session, so it is there in history."""
    names = ", ".join(c["name"] for c in created) or "your starter crew"
    session = await manager_svc.get_session(db, user)
    db.add(ChatMessage(
        user_id=user.id, session_id=session.id, role="assistant", channel="web",
        content=(f"Welcome to the Cantina. I've assembled your crew: {names}. "
                 "They're on the clock — say “brief me” anytime and I'll tell you "
                 "what they're doing."),
    ))
    await db.flush()


@router.get("/state")
async def state(user: CurrentUser, db: DB):
    """Has this user been through Genesis, and what do they have already?"""
    agents = await _user_agents(db, user.id)
    settings = user.settings_json or {}
    return {
        "done": bool(settings.get(GENESIS_DONE_KEY)),
        "skipped": bool(settings.get(GENESIS_SKIPPED_KEY)),
        "agents": [{"id": a.id, "name": a.display_name,
                    "role": (a.configuration_json or {}).get("role"),
                    "avatar": (a.configuration_json or {}).get("avatar")}
                   for a in agents],
        "gated_roles": sorted(await _gated_roles(db, user)),
        "roles": [{"role": r, "name": n, "mission": m,
                   "avatar": ROLE_AVATARS.get(r, "crew-orchestrator.webp")}
                  for r, (n, m) in crew.ROLES.items()],
    }
