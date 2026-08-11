from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from backend.core.models import ProviderConnection
from backend.core.security import DB, CurrentUser
from backend.ledger import service as ledger
from backend.providers import registry

router = APIRouter(prefix="/providers")

KNOWN_PROVIDERS = registry.KNOWN_PROVIDERS

# Supported provider definitions — the UI must never invent or hide this list
# (owner directive third pass §1). Order is the display order.
PROVIDER_DEFINITIONS = [
    {"id": "anthropic", "label": "Anthropic", "needs_key": True},
    {"id": "openai", "label": "OpenAI", "needs_key": True},
    {"id": "gemini", "label": "Google Gemini", "needs_key": True},
    {"id": "xai", "label": "xAI", "needs_key": True},
    {"id": "mistral", "label": "Mistral", "needs_key": True},
    {"id": "deepseek", "label": "DeepSeek", "needs_key": True},
    {"id": "openrouter", "label": "OpenRouter", "needs_key": True},
    {"id": "custom", "label": "Custom (OpenAI-compatible)", "needs_key": True},
    {"id": "mock", "label": "Mock (offline demo)", "needs_key": False},
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
    rows = (
        await db.execute(select(ProviderConnection).where(ProviderConnection.user_id == user.id))
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
