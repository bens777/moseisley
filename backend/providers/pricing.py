"""Versioned model pricing + deterministic cost estimation (owner directive §31-34).

Snapshots come from official provider documentation (source recorded); historical
snapshots are never rewritten. When no reliable pricing exists the cost is UNKNOWN/NULL —
never guessed. OpenRouter cost is provider-reported per request and takes precedence.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.models import ModelPricingSnapshot

# Seed pricing per official provider pricing pages, checked 2026-08 (USD per 1M tokens).
# Effective dates are the seed date; refresh via new snapshots, never by editing rows.
SEED_PRICING: list[dict] = [
    {"provider": "anthropic", "model_id": "claude-sonnet-5", "input": 3.0, "cached": 0.30, "output": 15.0},
    {"provider": "anthropic", "model_id": "claude-haiku-4-5-20251001", "input": 1.0, "cached": 0.10, "output": 5.0},
    {"provider": "openai", "model_id": "gpt-4.1", "input": 2.0, "cached": 0.50, "output": 8.0},
    {"provider": "openai", "model_id": "gpt-4.1-mini", "input": 0.40, "cached": 0.10, "output": 1.60},
    {"provider": "deepseek", "model_id": "deepseek-chat", "input": 0.27, "cached": 0.07, "output": 1.10},
    {"provider": "mistral", "model_id": "mistral-large-latest", "input": 2.0, "cached": None, "output": 6.0},
    {"provider": "gemini", "model_id": "gemini-2.5-flash", "input": 0.30, "cached": 0.075, "output": 2.50},
    {"provider": "mock", "model_id": "mock-1", "input": 0.0, "cached": 0.0, "output": 0.0},
]

_SOURCE = "official provider pricing pages, 2026-08"


async def seed_pricing(db: AsyncSession) -> int:
    """Idempotently insert seed snapshots (skips models that already have one)."""
    created = 0
    for entry in SEED_PRICING:
        exists = (await db.execute(
            select(ModelPricingSnapshot).where(
                ModelPricingSnapshot.provider == entry["provider"],
                ModelPricingSnapshot.model_id == entry["model_id"],
            ).limit(1)
        )).scalar_one_or_none()
        if exists:
            continue
        db.add(ModelPricingSnapshot(
            provider=entry["provider"], model_id=entry["model_id"], currency="USD",
            input_per_million=entry["input"], cached_input_per_million=entry["cached"],
            output_per_million=entry["output"],
            source_type="official_docs", source_reference=_SOURCE,
        ))
        created += 1
    await db.flush()
    return created


async def current_snapshot(db: AsyncSession, provider: str,
                           model_id: str) -> ModelPricingSnapshot | None:
    """Latest snapshot effective now for (provider, model)."""
    now = datetime.now(UTC)
    rows = list((await db.execute(
        select(ModelPricingSnapshot).where(
            ModelPricingSnapshot.provider == provider,
            ModelPricingSnapshot.model_id == model_id,
        ).order_by(ModelPricingSnapshot.effective_from.desc())
    )).scalars())
    for row in rows:
        eff_to = row.effective_to
        if eff_to is not None and (eff_to.replace(tzinfo=UTC) if eff_to.tzinfo is None else eff_to) < now:
            continue
        return row
    return None


def estimate_cost(snapshot: ModelPricingSnapshot, *, input_tokens: int | None,
                  cached_input_tokens: int | None, output_tokens: int | None) -> float | None:
    """Deterministic generic estimate. Returns None when token data is insufficient.
    Provider-specific extras (cache writes, tools, audio) are intentionally NOT guessed."""
    if input_tokens is None and output_tokens is None:
        return None
    cached = cached_input_tokens or 0
    non_cached_input = max((input_tokens or 0) - cached, 0)
    cost = 0.0
    if snapshot.input_per_million is not None:
        cost += non_cached_input * snapshot.input_per_million / 1_000_000
    if cached and snapshot.cached_input_per_million is not None:
        cost += cached * snapshot.cached_input_per_million / 1_000_000
    elif cached and snapshot.input_per_million is not None:
        cost += cached * snapshot.input_per_million / 1_000_000
    if snapshot.output_per_million is not None:
        cost += (output_tokens or 0) * snapshot.output_per_million / 1_000_000
    return round(cost, 8)
