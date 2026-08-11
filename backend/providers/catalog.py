"""Server-side model discovery (owner directive §11-13).

Live provider APIs are the primary source of truth, cached in the model_catalog table.
A SMALL curated fallback exists for offline/dev use and is clearly marked source=fallback.
Model identifiers are never invented: they come from live discovery or the curated list
(sourced from official docs at implementation time, 2026-08).
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.crypto import decrypt_secret
from backend.core.models import ModelCatalogEntry, ProviderConnection

logger = logging.getLogger("mychief.catalog")

CATALOG_TTL_HOURS = 24

# Curated fallback (source: official provider documentation, checked 2026-08).
# Live discovery always wins; these unblock offline/dev use only.
FALLBACK_CATALOG: dict[str, list[dict]] = {
    "anthropic": [
        {"model_id": "claude-fable-5", "display_name": "Claude Fable 5"},
        {"model_id": "claude-opus-5", "display_name": "Claude Opus 5"},
        {"model_id": "claude-sonnet-5", "display_name": "Claude Sonnet 5"},
        {"model_id": "claude-haiku-4-5-20251001", "display_name": "Claude Haiku 4.5"},
    ],
    "openai": [
        {"model_id": "gpt-4.1", "display_name": "GPT-4.1"},
        {"model_id": "gpt-4.1-mini", "display_name": "GPT-4.1 mini"},
        {"model_id": "o3-mini", "display_name": "o3-mini"},
    ],
    "gemini": [
        {"model_id": "gemini-2.5-pro", "display_name": "Gemini 2.5 Pro"},
        {"model_id": "gemini-2.5-flash", "display_name": "Gemini 2.5 Flash"},
    ],
    "xai": [
        {"model_id": "grok-4", "display_name": "Grok 4"},
        {"model_id": "grok-3-mini", "display_name": "Grok 3 mini"},
    ],
    "mistral": [
        {"model_id": "mistral-large-latest", "display_name": "Mistral Large"},
        {"model_id": "mistral-small-latest", "display_name": "Mistral Small"},
    ],
    "deepseek": [
        {"model_id": "deepseek-chat", "display_name": "DeepSeek Chat (V3)"},
        {"model_id": "deepseek-reasoner", "display_name": "DeepSeek Reasoner (R1)"},
    ],
    "openrouter": [
        {"model_id": "anthropic/claude-sonnet-5", "display_name": "Claude Sonnet 5 (OpenRouter)"},
        {"model_id": "openai/gpt-4.1-mini", "display_name": "GPT-4.1 mini (OpenRouter)"},
    ],
    "mock": [{"model_id": "mock-1", "display_name": "Mock (offline testing)"}],
}

# Exclude non-chat models from reasoning selectors (§12).
_EXCLUDE_PATTERNS = re.compile(
    r"embed|embedding|whisper|tts|audio|dall-e|image|moderation|vision-only|"
    r"transcribe|speech|aqa|imagen|veo|-ocr|guard",
    re.IGNORECASE,
)


def _chat_capable(model_id: str) -> bool:
    return not _EXCLUDE_PATTERNS.search(model_id)


async def _fetch_openai_style(base_url: str, api_key: str,
                              header: str = "Authorization") -> list[dict]:
    headers = {header: f"Bearer {api_key}"} if header == "Authorization" else {header: api_key}
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(f"{base_url}/models", headers=headers)
    if resp.status_code != 200:
        raise RuntimeError(f"model list returned {resp.status_code}")
    return [{"model_id": m["id"], "display_name": m.get("name") or m["id"],
             "context_window": m.get("context_length"),
             "pricing": m.get("pricing") or {}}
            for m in resp.json().get("data", [])]


async def _fetch_anthropic(api_key: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            "https://api.anthropic.com/v1/models?limit=100",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
        )
    if resp.status_code != 200:
        raise RuntimeError(f"model list returned {resp.status_code}")
    return [{"model_id": m["id"], "display_name": m.get("display_name") or m["id"]}
            for m in resp.json().get("data", [])]


async def _fetch_gemini(api_key: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            "https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
            headers={"x-goog-api-key": api_key},
        )
    if resp.status_code != 200:
        raise RuntimeError(f"model list returned {resp.status_code}")
    out = []
    for m in resp.json().get("models", []):
        if "generateContent" not in (m.get("supportedGenerationMethods") or []):
            continue
        out.append({
            "model_id": m["name"].removeprefix("models/"),
            "display_name": m.get("displayName") or m["name"],
            "context_window": m.get("inputTokenLimit"),
        })
    return out


async def fetch_live_models(provider: str, api_key: str, base_url: str | None = None) -> list[dict]:
    if provider == "anthropic":
        return await _fetch_anthropic(api_key)
    if provider == "gemini":
        return await _fetch_gemini(api_key)
    if provider == "openrouter":
        return await _fetch_openai_style("https://openrouter.ai/api/v1", api_key)
    if provider == "openai":
        return await _fetch_openai_style("https://api.openai.com/v1", api_key)
    if provider == "xai":
        return await _fetch_openai_style("https://api.x.ai/v1", api_key)
    if provider == "mistral":
        return await _fetch_openai_style("https://api.mistral.ai/v1", api_key)
    if provider == "deepseek":
        return await _fetch_openai_style("https://api.deepseek.com", api_key)
    if provider == "custom" and base_url:
        return await _fetch_openai_style(base_url, api_key)
    raise RuntimeError(f"no discovery for provider {provider}")


async def refresh_catalog(db: AsyncSession, provider: str,
                          connection: ProviderConnection | None) -> dict:
    """Refresh a provider's model catalog: live discovery first, fallback otherwise."""
    models: list[dict] = []
    source = "fallback"
    error: str | None = None
    if connection is not None and connection.encrypted_secret and provider != "mock":
        try:
            key = decrypt_secret(connection.encrypted_secret)
            base_url = (connection.configuration_json or {}).get("base_url")
            models = await fetch_live_models(provider, key, base_url)
            source = "live"
        except Exception as e:  # noqa: BLE001 - discovery degrades to fallback
            error = f"{type(e).__name__}"
            logger.warning("model discovery failed for %s: %s", provider, e)
    if not models:
        models = [dict(m) for m in FALLBACK_CATALOG.get(provider, [])]

    now = datetime.now(UTC)
    existing = {
        e.model_id: e for e in (await db.execute(
            select(ModelCatalogEntry).where(ModelCatalogEntry.provider == provider)
        )).scalars()
    }
    seen = set()
    for m in models:
        model_id = m["model_id"]
        seen.add(model_id)
        row = existing.get(model_id)
        if row is None:
            row = ModelCatalogEntry(provider=provider, model_id=model_id,
                                    display_name=m.get("display_name") or model_id)
            db.add(row)
        row.display_name = m.get("display_name") or model_id
        row.context_window = m.get("context_window")
        row.pricing_json = m.get("pricing") or {}
        row.capabilities_json = {"chat": _chat_capable(model_id)}
        row.available = True
        row.source = source
        row.fetched_at = now
    for model_id, row in existing.items():
        if model_id not in seen and source == "live":
            row.available = False
    await db.flush()
    return {"provider": provider, "source": source, "count": len(seen), "error": error}


async def list_catalog(db: AsyncSession, provider: str, *, chat_only: bool = True,
                       search: str | None = None, limit: int = 500) -> list[ModelCatalogEntry]:
    q = select(ModelCatalogEntry).where(ModelCatalogEntry.provider == provider,
                                        ModelCatalogEntry.available.is_(True))
    rows = list((await db.execute(q.order_by(ModelCatalogEntry.model_id))).scalars())
    if chat_only:
        rows = [r for r in rows if (r.capabilities_json or {}).get("chat", True)]
    if search:
        needle = search.lower()
        rows = [r for r in rows if needle in r.model_id.lower() or needle in r.display_name.lower()]
    return rows[:limit]


async def ensure_catalog(db: AsyncSession, provider: str,
                         connection: ProviderConnection | None) -> None:
    """Refresh if the cached catalog is empty or stale."""
    newest = (await db.execute(
        select(ModelCatalogEntry.fetched_at).where(ModelCatalogEntry.provider == provider)
        .order_by(ModelCatalogEntry.fetched_at.desc()).limit(1)
    )).scalar_one_or_none()
    if newest is not None:
        age = datetime.now(UTC) - (newest.replace(tzinfo=UTC) if newest.tzinfo is None else newest)
        if age < timedelta(hours=CATALOG_TTL_HOURS):
            return
    await refresh_catalog(db, provider, connection)
