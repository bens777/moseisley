"""Factory mode: platform-provided AI (one OpenRouter key, prepaid credits).

Tier model: TRIAL (first `factory_trial_days` after signup) → PAID (active
Basic/Pro subscription) → EXPIRED (platform AI cut; subscribe, BYOK, or
self-hosted Ollama). Zero operational maintenance by design: the model list
below is a static code constant — no remote discovery, no cache, no admin UI.
The platform key is read from Settings only, never stored in the DB and never
returned by any API.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.billing import stripe_billing
from backend.core.config import get_settings
from backend.core.models import ProviderConnection, User

# ---------------------------------------------------------------------------
# THE ONLY BLOCK AN OPERATOR EVER EDITS.
# Ordered candidates per bucket: first id is tried, the second is the fallback
# on retryable provider errors. Ids verified against openrouter.ai/api/v1/models
# (2026-08). Same models for trial and paid — tiers differ only by daily cap.
# ---------------------------------------------------------------------------
FACTORY_MODELS: dict[str, list[str]] = {
    "DEFAULT_FAST": ["deepseek/deepseek-chat", "qwen/qwen3.7-flash"],
    "DEFAULT_REASONING": ["deepseek/deepseek-chat", "google/gemini-2.5-flash"],
    "DEFAULT_TOOLS": ["deepseek/deepseek-chat", "google/gemini-2.5-flash"],
}

# ---------------------------------------------------------------------------
# DEV mode pool — the user's OWN OpenRouter key, ":free" models only. Their
# key, their quota, zero platform cost. Same bucket shape as FACTORY_MODELS;
# ids verified against openrouter.ai/api/v1/models (2026-08, all tool-capable;
# the TOOLS bucket only lists models that also support response_format).
# Every id here MUST end in ":free" — routing enforces it.
# ---------------------------------------------------------------------------
DEV_MODELS: dict[str, list[str]] = {
    "DEFAULT_FAST": ["nvidia/nemotron-3.5-lightning:free",
                     "nvidia/nemotron-3-nano-30b-a3b:free"],
    "DEFAULT_REASONING": ["nvidia/nemotron-3-ultra-550b-a55b:free",
                          "nvidia/nemotron-3-super-120b-a12b:free"],
    "DEFAULT_TOOLS": ["google/gemma-4-31b-it:free",
                      "nvidia/nemotron-3-super-120b-a12b:free"],
}

FREE_SUFFIX = ":free"

DEFAULT_FAST = "DEFAULT_FAST"
DEFAULT_REASONING = "DEFAULT_REASONING"
DEFAULT_TOOLS = "DEFAULT_TOOLS"

# Marker used on llm_usage rows for platform-funded calls. Distinct from
# "openrouter" so factory traffic never mixes with a user's own OpenRouter
# usage — this makes the daily-cap query exact and usage views honest.
FACTORY_USAGE_PROVIDER = "factory"

MODE_ROOKIE = "factory"  # user-facing: ROOKIE. Internal value unchanged.
MODE_DEV = "dev"         # user-facing: DEV — own OpenRouter key, free models.
MODE_EXPERT = "custom"   # user-facing: EXPERT. Internal value unchanged.
AI_MODES = (MODE_ROOKIE, MODE_DEV, MODE_EXPERT)

TIER_TRIAL = "trial"
TIER_PAID = "paid"
TIER_EXPIRED = "expired"

TRIAL_STARTED_KEY = "trial_started_at"


def factory_available() -> bool:
    """Factory mode exists only when the operator configured the platform key."""
    return bool(get_settings().factory_openrouter_api_key)


def bucket_for(purpose: str, json_mode: bool) -> str:
    """Purpose → model bucket. stt/tts/embeddings never reach factory routing
    (they resolve through resolve_client(), which is untouched)."""
    if json_mode:
        return DEFAULT_TOOLS
    if purpose in ("strategy", "audit"):
        return DEFAULT_REASONING
    return DEFAULT_FAST


async def has_provider_connections(db: AsyncSession, user_id: str) -> bool:
    count = (await db.execute(
        select(func.count()).select_from(ProviderConnection).where(
            ProviderConnection.user_id == user_id)
    )).scalar_one()
    return count > 0


async def effective_ai_mode(db: AsyncSession, user_id: str, user: User | None = None) -> str:
    """settings_json["ai_mode"] if set ("factory"|"dev"|"custom"); else factory
    for users with zero ProviderConnection rows, custom otherwise. Single
    helper, used everywhere."""
    if user is None:
        user = await db.get(User, user_id)
    explicit = ((user.settings_json or {}).get("ai_mode")) if user else None
    if explicit in AI_MODES:
        return explicit
    return MODE_EXPERT if await has_provider_connections(db, user_id) else MODE_ROOKIE


async def dev_key_connected(db: AsyncSession, user_id: str) -> bool:
    """DEV mode needs the user's own enabled OpenRouter connection with a key."""
    row = (await db.execute(
        select(ProviderConnection).where(ProviderConnection.user_id == user_id,
                                         ProviderConnection.provider == "openrouter")
    )).scalar_one_or_none()
    return bool(row and row.enabled and row.encrypted_secret)


def _parse_trial_start(user: User) -> datetime | None:
    raw = (user.settings_json or {}).get(TRIAL_STARTED_KEY)
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def ensure_trial_started(user: User) -> datetime:
    """Lazily stamp the trial start on the user's FIRST factory LLM call."""
    started = _parse_trial_start(user)
    if started is None:
        started = datetime.now(UTC)
        user.settings_json = {**(user.settings_json or {}),
                              TRIAL_STARTED_KEY: started.isoformat()}
    return started


async def get_factory_tier(db: AsyncSession, user: User) -> str:
    """"paid" (active Basic/Pro) > "trial" (within the window, or not started
    yet) > "expired". Read-only with respect to billing state."""
    state = await stripe_billing.get_state(db, user.id)
    if stripe_billing.plan_for_state(state) in ("basic", "pro"):
        return TIER_PAID
    started = _parse_trial_start(user)
    if started is None:
        return TIER_TRIAL  # trial not consumed yet — starts on first factory call
    days = get_settings().factory_trial_days
    return TIER_TRIAL if datetime.now(UTC) < started + timedelta(days=days) else TIER_EXPIRED


def trial_days_left(user: User) -> int:
    """Whole days of trial remaining (full window if not started yet)."""
    days = get_settings().factory_trial_days
    started = _parse_trial_start(user)
    if started is None:
        return days
    remaining = (started + timedelta(days=days)) - datetime.now(UTC)
    return max(0, -(-int(remaining.total_seconds()) // 86400))  # ceil to whole days


def daily_cap_for_plan(tier: str, plan: str | None = None) -> int:
    """Per-day request allowance. Paid users get their plan's tank — Pro's is
    bigger, which is part of what Pro sells. Any plan string other than "pro"
    on a paid tier falls back to the Basic cap (least privilege)."""
    s = get_settings()
    if tier != TIER_PAID:
        return s.factory_trial_daily_requests
    return s.factory_pro_daily_requests if plan == "pro" else s.factory_basic_daily_requests


async def daily_cap_for_user(db: AsyncSession, user: User, tier: str) -> int:
    """Cap for this user, resolving their Stripe-synced plan when it matters."""
    if tier != TIER_PAID:
        return daily_cap_for_plan(tier)
    plan = stripe_billing.plan_for_state(await stripe_billing.get_state(db, user.id))
    return daily_cap_for_plan(tier, plan)


# ── purchased fuel (The Bar) ────────────────────────────────────────
# One-time drink purchases top up a balance of extra factory requests. The
# balance NEVER expires — there is deliberately no expiry logic anywhere. It
# is spent only after the daily cap is reached, and only by factory calls.

FUEL_BALANCE_KEY = "fuel_balance"
GIFT_PENDING_KEY = "bar_gift_pending"


def get_fuel_balance(user: User) -> int:
    raw = (user.settings_json or {}).get(FUEL_BALANCE_KEY)
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def _set_fuel_balance(user: User, value: int) -> int:
    # reassign: JSON columns only detect whole-value changes
    user.settings_json = {**(user.settings_json or {}), FUEL_BALANCE_KEY: max(0, value)}
    return max(0, value)


def credit_fuel(user: User, amount: int) -> int:
    """Add purchased/gifted fuel. Returns the new balance."""
    return _set_fuel_balance(user, get_fuel_balance(user) + max(0, int(amount)))


def consume_fuel(user: User) -> int:
    """Spend one purchased request. Returns the remaining balance."""
    return _set_fuel_balance(user, get_fuel_balance(user) - 1)


def mark_gift_received(user: User, *, from_name: str, fuel: int) -> None:
    """Flag a received round so the recipient gets a dismissible banner."""
    user.settings_json = {**(user.settings_json or {}),
                          GIFT_PENDING_KEY: {"from": from_name, "fuel": int(fuel)}}
