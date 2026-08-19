"""Web-search connections (BYOK): the user's own Brave or Perplexity key.

Deliberately its own endpoints, not part of /providers: search keys are not AI
brains — they never appear in the LLM provider list, never open the OpenRouter
gate, never flip a user into EXPERT, and are NOT behind the BYOK subscription
gate. Brave's free tier is the zero-cost path for everyone, so gating it
behind Basic/Pro would defeat the point.

Storage is the same provider_connections table with the same encryption as
LLM keys; configuration_json.kind = "search" marks the row and the provider
names are the discriminator the LLM-side scans filter on.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from backend.core.models import ProviderConnection
from backend.core.security import DB, CurrentUser
from backend.ledger import service as ledger
from backend.providers import registry
from backend.websearch.service import SEARCH_PROVIDERS

router = APIRouter(prefix="/websearch")

LABELS = {"brave": "Brave Search", "perplexity": "Perplexity", "tavily": "Tavily"}


class SaveSearchKeyRequest(BaseModel):
    provider: str
    api_key: str


async def _rows(db, user_id: str) -> dict[str, ProviderConnection]:
    return {r.provider: r for r in (await db.execute(
        select(ProviderConnection).where(
            ProviderConnection.user_id == user_id,
            ProviderConnection.provider.in_(SEARCH_PROVIDERS)))).scalars()}


@router.get("")
async def list_search_providers(user: CurrentUser, db: DB):
    rows = await _rows(db, user.id)
    return [{"provider": p, "label": LABELS[p],
             "connected": bool(r is not None and r.enabled and r.encrypted_secret),
             "display_hint": r.display_hint if r else None}
            for p in SEARCH_PROVIDERS for r in [rows.get(p)]]


@router.post("")
async def save_search_key(body: SaveSearchKeyRequest, user: CurrentUser, db: DB):
    provider = body.provider.strip().lower()
    key = body.api_key.strip()
    if provider not in SEARCH_PROVIDERS:
        raise HTTPException(400, f"unknown search provider: {provider}")
    if not key:
        raise HTTPException(400, "paste a key first")
    # same storage path as LLM keys: encryption, masking, one row per provider
    row = await registry.save_provider(db, user.id, provider, key, {"kind": "search"})
    await ledger.record(db, user.id, "provider_connected", actor_type="user",
                        entity_type="provider", entity_id=row.id,
                        payload={"provider": provider, "kind": "search"})
    await db.commit()
    return {"ok": True, "provider": provider, "display_hint": row.display_hint}


@router.post("/{provider}/test")
async def test_search_provider(provider: str, user: CurrentUser, db: DB):
    if provider not in SEARCH_PROVIDERS:
        raise HTTPException(400, f"unknown search provider: {provider}")
    row = (await _rows(db, user.id)).get(provider)
    if row is None or not row.encrypted_secret:
        raise HTTPException(404, "provider not configured")
    if not row.enabled:
        return {"ok": False, "detail": "provider disabled"}
    from backend.core.crypto import decrypt_secret
    from backend.websearch.service import test_provider as _test

    ok = await _test(provider, decrypt_secret(row.encrypted_secret))
    return {"ok": ok}


@router.delete("/{provider}")
async def remove_search_key(provider: str, user: CurrentUser, db: DB):
    if provider not in SEARCH_PROVIDERS:
        raise HTTPException(400, f"unknown search provider: {provider}")
    row = (await _rows(db, user.id)).get(provider)
    if row is not None:
        await db.delete(row)
        await ledger.record(db, user.id, "provider_removed", actor_type="user",
                            entity_type="provider", entity_id=row.id,
                            payload={"provider": provider, "kind": "search"})
    await db.commit()
    return {"removed": row is not None}
