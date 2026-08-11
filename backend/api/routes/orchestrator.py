from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.agents import crew
from backend.core.security import DB, CurrentUser
from backend.ledger import service as ledger
from backend.providers import factory_pool, registry
from backend.providers.catalog import ensure_catalog, list_catalog, refresh_catalog

router = APIRouter()


@router.get("/orchestrator")
async def get_orchestrator(user: CurrentUser, db: DB):
    cfg = await registry.get_orchestrator_config(db, user.id)
    connected = None
    if cfg.get("provider"):
        row = await registry.get_provider_row(db, user.id, cfg["provider"])
        connected = bool(row and row.enabled and (row.encrypted_secret or cfg["provider"] == "mock"))
    return {"provider": cfg.get("provider"), "model": cfg.get("model"),
            "configured": bool(cfg), "provider_connected": connected}


class OrchestratorConfigRequest(BaseModel):
    provider: str
    model: str


@router.put("/orchestrator")
async def set_orchestrator(body: OrchestratorConfigRequest, user: CurrentUser, db: DB):
    if body.provider not in registry.KNOWN_PROVIDERS:
        raise HTTPException(400, f"unknown provider: {body.provider}")
    row = await registry.get_provider_row(db, user.id, body.provider)
    if row is None or (not row.encrypted_secret and body.provider != "mock"):
        # Factory mode (trial or paid) routes openrouter through the platform
        # key — the BYOK connection gate does not apply there.
        factory_bypass = (
            body.provider == "openrouter"
            and factory_pool.factory_available()
            and await factory_pool.effective_ai_mode(db, user.id, user) == "factory"
            and await factory_pool.get_factory_tier(db, user) != factory_pool.TIER_EXPIRED
        )
        if not factory_bypass:
            raise HTTPException(400, "connect this provider (API key) before selecting it")
    # model must come from the catalog — identifiers are never invented (§12)
    await ensure_catalog(db, body.provider, row)
    catalog = await list_catalog(db, body.provider, chat_only=False)
    if body.model not in {m.model_id for m in catalog}:
        raise HTTPException(400, "model not found in this provider's catalog — refresh models")
    await registry.set_orchestrator_config(db, user.id, body.provider, body.model)
    await ledger.record(db, user.id, "orchestrator_model_changed", actor_type="user",
                        payload={"provider": body.provider, "model": body.model})
    await db.commit()
    return {"provider": body.provider, "model": body.model, "configured": True}


@router.post("/orchestrator/test")
async def test_orchestrator(user: CurrentUser, db: DB):
    try:
        result = await registry.generate(
            db, user.id, [{"role": "user", "content": "Reply with the single word: ready"}],
            crew_role="orchestrator", purpose="chat", max_tokens=10,
        )
        await db.commit()
        return {"ok": True, "model": result.model, "reply": result.text[:100]}
    except Exception as e:  # noqa: BLE001 - report health truthfully
        await db.commit()  # keep the failed-usage record
        return {"ok": False, "detail": type(e).__name__}


@router.get("/crew")
async def list_crew(user: CurrentUser, db: DB):
    orch = await registry.get_orchestrator_config(db, user.id)
    usage = await crew.role_usage_this_month(db, user.id)
    runs = await crew.last_runs(db, user.id, limit=50)
    last_by_role = {}
    for r in runs:
        last_by_role.setdefault(r.crew_role, r)
    out = []
    for role, (name, mission) in crew.ROLES.items():
        cfg = await crew.get_config(db, user.id, role)
        last = last_by_role.get(role)
        out.append({
            "role": role, "name": name, "mission": mission,
            "enabled": cfg.enabled if cfg else True,
            "model_policy": cfg.model_policy if cfg else "inherit",
            "provider": (cfg.provider if cfg and cfg.model_policy == "custom"
                         else orch.get("provider")),
            "model": (cfg.model if cfg and cfg.model_policy == "custom"
                      else orch.get("model")),
            "uses_default_prompt": cfg.uses_default_prompt if cfg else True,
            "prompt_version": cfg.prompt_version if cfg else 0,
            "runtime": "native",
            "last_run": {
                "status": last.status, "task": last.task_summary,
                "finished_at": last.finished_at,
            } if last else None,
            "usage_month": usage.get(role),
        })
    return {"orchestrator": orch, "crew": out}


class ModelPolicyRequest(BaseModel):
    model_policy: str  # inherit | custom
    provider: str | None = None
    model: str | None = None


@router.put("/crew/{role}/model-policy")
async def set_crew_model(role: str, body: ModelPolicyRequest, user: CurrentUser, db: DB):
    if role not in crew.ROLES or role == "orchestrator":
        raise HTTPException(404, "unknown crew role")
    if body.model_policy == "custom":
        if body.provider not in registry.KNOWN_PROVIDERS:
            raise HTTPException(400, "unknown provider")
        row = await registry.get_provider_row(db, user.id, body.provider)
        if row is None or (not row.encrypted_secret and body.provider != "mock"):
            raise HTTPException(400, "connect this provider before assigning it")
    try:
        cfg = await crew.set_model_policy(db, user.id, role, model_policy=body.model_policy,
                                          provider=body.provider, model=body.model)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    await db.commit()
    return {"role": role, "model_policy": cfg.model_policy,
            "provider": cfg.provider, "model": cfg.model}


@router.get("/crew/{role}/prompt")
async def get_role_prompt(role: str, user: CurrentUser, db: DB):
    if role not in crew.ROLES:
        raise HTTPException(404, "unknown crew role")
    cfg = await crew.get_config(db, user.id, role)
    return {
        "role": role,
        "prompt": await crew.get_prompt(db, user.id, role),
        "uses_default": cfg.uses_default_prompt if cfg else True,
        "default_prompt": crew.default_prompt(role),
    }


class PromptRequest(BaseModel):
    prompt: str | None = None  # None resets to default


@router.put("/crew/{role}/prompt")
async def set_role_prompt(role: str, body: PromptRequest, user: CurrentUser, db: DB):
    if role not in crew.ROLES:
        raise HTTPException(404, "unknown crew role")
    cfg = await crew.set_prompt(db, user.id, role, body.prompt)
    await db.commit()
    return {"role": role, "uses_default": cfg.uses_default_prompt,
            "version": cfg.prompt_version}


@router.get("/providers/{provider}/models")
async def provider_models(provider: str, user: CurrentUser, db: DB,
                          search: str | None = None):
    if provider not in registry.KNOWN_PROVIDERS:
        raise HTTPException(404, "unknown provider")
    row = await registry.get_provider_row(db, user.id, provider)
    await ensure_catalog(db, provider, row)
    await db.commit()
    rows = await list_catalog(db, provider, search=search)
    return [{"model_id": m.model_id, "display_name": m.display_name,
             "context_window": m.context_window, "source": m.source,
             "fetched_at": m.fetched_at} for m in rows]


@router.post("/providers/{provider}/models/refresh")
async def refresh_models(provider: str, user: CurrentUser, db: DB):
    if provider not in registry.KNOWN_PROVIDERS:
        raise HTTPException(404, "unknown provider")
    row = await registry.get_provider_row(db, user.id, provider)
    result = await refresh_catalog(db, provider, row)
    await db.commit()
    return result
