from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from backend.core.models import ProviderConnection
from backend.core.security import DB, CurrentUser
from backend.ledger import service as ledger
from backend.providers import registry, usage_policy

router = APIRouter(prefix="/providers")

KNOWN_PROVIDERS = registry.KNOWN_PROVIDERS

# Supported provider definitions — the UI must never invent or hide this list
# (owner directive third pass §1). Order is the display order.
#
# A provider is described by what it can DO, not one hardcoded UI bucket —
# most providers here can serve more than one role. "capabilities" drives
# where/how the frontend surfaces a provider:
#   "llm"           — can be picked as the AI Engine / orchestrator brain.
#                      Every provider here has it; that's what an LLM
#                      provider IS.
#   other values     — a specialized capability (youtube, x_search, documents,
#                      ocr, transcription, vision, ...) that additionally
#                      earns the provider a row in Intelligence Sources, using
#                      the SAME connected credential (one ProviderConnection
#                      row — no duplicate secret storage per role).
# "free_tier_note" is short, human copy about that provider's own free tier;
# None means "no free tier — usage is billed by the provider" and the UI must
# say so plainly, never imply a free option that doesn't exist. Independent
# of usage_policy.py: a free QUOTA on the provider's own account is not the
# same thing as Moseisley's permission to spend.
PROVIDER_DEFINITIONS = [
    {"id": "anthropic", "label": "Anthropic", "needs_key": True,
     "capabilities": ["llm"], "purpose": None, "free_tier_note": None},
    {"id": "openai", "label": "OpenAI", "needs_key": True,
     "capabilities": ["llm"], "purpose": None, "free_tier_note": None},
    {"id": "gemini", "label": "Google Gemini", "needs_key": True,
     "capabilities": ["llm", "youtube", "vision"], "purpose": "YouTube intelligence",
     "free_tier_note": "Free tier available"},
    {"id": "xai", "label": "xAI", "needs_key": True,
     "capabilities": ["llm", "x_search"], "purpose": "X & social intelligence",
     "free_tier_note": None},
    {"id": "mistral", "label": "Mistral", "needs_key": True,
     "capabilities": ["llm", "documents", "ocr"], "purpose": "Documents & OCR",
     "free_tier_note": "Free tier available"},
    {"id": "groq", "label": "Groq", "needs_key": True,
     "capabilities": ["llm", "transcription"], "purpose": "Fast AI & transcription",
     "free_tier_note": "Free tier available"},
    {"id": "deepseek", "label": "DeepSeek", "needs_key": True,
     "capabilities": ["llm"], "purpose": None, "free_tier_note": None},
    {"id": "openrouter", "label": "OpenRouter", "needs_key": True,
     "capabilities": ["llm"], "purpose": None, "free_tier_note": None},
    {"id": "custom", "label": "Custom (OpenAI-compatible)", "needs_key": True,
     "capabilities": ["llm"], "purpose": None, "free_tier_note": None},
    {"id": "mock", "label": "Mock (offline demo)", "needs_key": False,
     "capabilities": ["llm"], "purpose": None, "free_tier_note": None},
]


@router.get("/definitions")
async def provider_definitions(user: CurrentUser, db: DB):
    """All providers the backend supports, with this user's connection state.
    Never empty; never dependent on the user having configured a key."""
    rows = (
        await db.execute(select(ProviderConnection).where(ProviderConnection.user_id == user.id))
    ).scalars()
    by_provider = {r.provider: r for r in rows}
    out = []
    for d in PROVIDER_DEFINITIONS:
        row = by_provider.get(d["id"])
        if row is None:
            state = "not_connected"
        elif not row.enabled:
            state = "disabled"
        else:
            state = "connected"
        out.append({**d, "state": state,
                    "display_hint": row.display_hint if row else None})
    return out


def _serialize(row: ProviderConnection) -> dict:
    cfg = dict(row.configuration_json or {})
    cfg.pop("responses", None)
    return {
        "id": row.id,
        "provider": row.provider,
        "enabled": row.enabled,
        "display_hint": row.display_hint,  # masked; the secret is never returned (§29, §38)
        "configuration": cfg,
        "has_secret": bool(row.encrypted_secret),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


class SaveProviderRequest(BaseModel):
    provider: str
    api_key: str | None = None
    configuration: dict | None = None


@router.get("")
async def list_providers(user: CurrentUser, db: DB):
    # LLM connections only — search keys (brave/tavily/perplexity) live on their own
    # /websearch endpoints and must not surface in the AI provider list
    from backend.websearch.service import SEARCH_PROVIDERS

    rows = (
        await db.execute(select(ProviderConnection).where(
            ProviderConnection.user_id == user.id,
            ProviderConnection.provider.notin_(SEARCH_PROVIDERS)))
    ).scalars()
    return [_serialize(r) for r in rows]


@router.post("")
async def save_provider(body: SaveProviderRequest, user: CurrentUser, db: DB):
    if body.provider not in KNOWN_PROVIDERS:
        raise HTTPException(400, f"unknown provider: {body.provider}")
    # BYOK is a subscriber feature on hosted deployments; free on self-host.
    # Enforced here, not just in the UI. Existing rows are never touched.
    # Carve-out: OpenRouter is always connectable because DEV mode runs on the
    # user's own key — routing still restricts them to ":free" models until
    # they subscribe, so this connects nothing they cannot already use.
    if body.provider != "openrouter" and not await registry.byok_allowed(db, user.id):
        raise HTTPException(402, registry.BYOK_REQUIRES_SUBSCRIPTION)
    row = await registry.save_provider(db, user.id, body.provider, body.api_key, body.configuration)
    await ledger.record(
        db, user.id, "provider_connected", actor_type="user",
        entity_type="provider", entity_id=row.id, payload={"provider": body.provider},
    )
    await db.commit()
    return _serialize(row)


class ToggleRequest(BaseModel):
    enabled: bool


@router.post("/{provider}/toggle")
async def toggle_provider(provider: str, body: ToggleRequest, user: CurrentUser, db: DB):
    row = await registry.get_provider_row(db, user.id, provider)
    if row is None:
        raise HTTPException(404, "provider not configured")
    # re-enabling a key is BYOK too; disabling one is always allowed.
    # OpenRouter carve-out mirrors the connect gate above (DEV mode).
    if (body.enabled and provider != "openrouter"
            and not await registry.byok_allowed(db, user.id)):
        raise HTTPException(402, registry.BYOK_REQUIRES_SUBSCRIPTION)
    row.enabled = body.enabled
    await ledger.record(
        db, user.id, "provider_enabled" if body.enabled else "provider_disabled",
        actor_type="user", entity_type="provider", entity_id=row.id, payload={"provider": provider},
    )
    await db.commit()
    return _serialize(row)


@router.delete("/{provider}")
async def delete_provider(provider: str, user: CurrentUser, db: DB):
    row = await registry.get_provider_row(db, user.id, provider)
    if row is None:
        raise HTTPException(404, "provider not configured")
    await db.delete(row)
    await ledger.record(
        db, user.id, "provider_removed", actor_type="user",
        entity_type="provider", payload={"provider": provider},
    )
    await db.commit()
    return {"ok": True}


@router.post("/{provider}/test")
async def test_provider(provider: str, user: CurrentUser, db: DB):
    row = await registry.get_provider_row(db, user.id, provider)
    if row is None:
        raise HTTPException(404, "provider not configured")
    if not row.enabled:
        return {"ok": False, "detail": "provider disabled"}
    try:
        client, _ = await registry.resolve_client(db, user.id, "classification")
        ok = await client.health_check()
        return {"ok": ok}
    except Exception as e:  # noqa: BLE001 - report health, never raise secrets
        return {"ok": False, "detail": type(e).__name__}


# ---------------------------------------------------------------------------
# Provider spend policy — server-enforced, not just a UI toggle; gates any
# call known to cost money, LLM or not. See backend/providers/usage_policy.py
# for the enforcement side. Only USER_FACING_POLICIES are offered as a choice
# (ask_before_spending is real and enforced but not yet backed by a complete
# approval UI).
# ---------------------------------------------------------------------------

@router.get("/policy")
async def get_usage_policy(user: CurrentUser):
    return {"policy": usage_policy.get_policy(user),
            "options": list(usage_policy.USER_FACING_POLICIES)}


class SetPolicyRequest(BaseModel):
    policy: str


@router.put("/policy")
async def set_usage_policy(body: SetPolicyRequest, user: CurrentUser, db: DB):
    if body.policy not in usage_policy.USER_FACING_POLICIES:
        raise HTTPException(400, f"unknown policy: {body.policy}")
    usage_policy.set_policy(user, body.policy)
    await db.commit()
    return {"policy": body.policy}
