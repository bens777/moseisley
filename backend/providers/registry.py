"""ProviderRegistry + ModelRouter (§29-32).

Critical invariants enforced here, deterministically:
- a disabled provider is never called (§30);
- the DISABLE_LLM kill switch blocks all LLM calls (§82);
- daily/monthly LLM budgets are enforced in code (§32);
- no other subsystem decrypts provider credentials.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core import killswitch
from backend.core.config import get_settings
from backend.core.crypto import decrypt_secret, encrypt_secret, mask_secret
from backend.core.models import Budget, LlmUsage, ProviderConnection, User
from backend.providers import factory_pool
from backend.providers.clients import (
    AnthropicClient,
    BaseLlmClient,
    GeminiClient,
    LlmResult,
    MockLlmClient,
    OpenAICompatibleClient,
    ProviderError,
)

PURPOSES = ["strategy", "market", "goal_compilation", "audit", "classification", "chat", "stt", "tts"]

# Purpose -> ordered provider preference (first enabled+configured wins). User-overridable
# via SystemSetting key "model_routing".
_CHAIN = ["anthropic", "openai", "gemini", "xai", "mistral", "deepseek", "openrouter", "custom", "mock"]

DEFAULT_ROUTING: dict[str, list[str]] = {
    "strategy": _CHAIN,
    "market": ["xai", *[p for p in _CHAIN if p != "xai"]],
    "goal_compilation": ["openai", *[p for p in _CHAIN if p != "openai"]],
    "audit": _CHAIN,
    "classification": ["openai", *[p for p in _CHAIN if p != "openai"]],
    "chat": _CHAIN,
    "stt": ["openai", "custom", "mock"],
    "tts": ["openai", "custom", "mock"],
}

_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "xai": "https://api.x.ai/v1",
    "mistral": "https://api.mistral.ai/v1",
    "deepseek": "https://api.deepseek.com",
    "openrouter": "https://openrouter.ai/api/v1",
}
_DEFAULT_MODELS = {
    "openai": "gpt-4.1-mini",
    "xai": "grok-3-mini",
    "anthropic": "claude-sonnet-5",
    "gemini": "gemini-2.5-flash",
    "mistral": "mistral-small-latest",
    "deepseek": "deepseek-chat",
    "openrouter": "anthropic/claude-sonnet-5",
    "custom": "",
    "mock": "mock-1",
}

KNOWN_PROVIDERS = set(_CHAIN)


class ProviderDisabled(Exception):
    pass


class NoProviderAvailable(Exception):
    pass


class LlmBudgetExceeded(Exception):
    pass


# Factory-mode errors subclass LlmBudgetExceeded so the existing app-level
# handler surfaces them as HTTP 429 with the message below. The bracketed
# prefix is the machine-readable error code clients can match on.

class FactoryTrialExpired(LlmBudgetExceeded):
    def __init__(self) -> None:
        super().__init__(
            "[factory_trial_expired] The free factory trial has ended. Refuel monthly "
            "(Basic $9 / Pro $19), bring your own API keys, or plug in your own Ollama "
            "— Settings → AI mode.")


class FactoryFuelExhausted(LlmBudgetExceeded):
    def __init__(self, tier: str) -> None:
        upgrade = " Upgrade for a bigger tank, or" if tier == factory_pool.TIER_TRIAL else ""
        super().__init__(
            "[factory_fuel_exhausted] Your crew needs more fuel — today's factory "
            f"requests are used up. Resets tomorrow.{upgrade} Use your own keys anytime.")


class DevKeyMissing(NoProviderAvailable):
    """DEV mode without the user's own OpenRouter key. Subclasses
    NoProviderAvailable so the existing app handler answers 424."""

    def __init__(self) -> None:
        super().__init__(
            "[dev_key_missing] DEV mode runs on your own OpenRouter key. Add one in "
            "Connections → OpenRouter (free models only, billed to nobody), or switch "
            "back to ROOKIE for platform AI.")


class FactoryServiceUnavailable(LlmBudgetExceeded):
    def __init__(self) -> None:
        super().__init__(
            "[factory_service_unavailable] The Cantina generator is recharging. "
            "Try again soon, or plug in your own keys.")


# Retryable factory statuses: rate limit, credits (may clear on the fallback
# route), transient server errors, and OpenRouter's 400/404 model-not-found.
_FACTORY_RETRYABLE = {400, 402, 404, 429, 500, 502, 503, 529}


class _FactoryRow:
    """Stand-in for ProviderConnection on the factory path — generate() and
    pricing only ever read `.provider` from the resolved row."""

    provider = factory_pool.FACTORY_USAGE_PROVIDER


async def save_provider(
    db: AsyncSession, user_id: str, provider: str, secret: str | None, configuration: dict | None = None
) -> ProviderConnection:
    row = (
        await db.execute(
            select(ProviderConnection).where(
                ProviderConnection.user_id == user_id, ProviderConnection.provider == provider
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = ProviderConnection(user_id=user_id, provider=provider)
        db.add(row)
    if secret:
        row.encrypted_secret = encrypt_secret(secret)
        row.display_hint = mask_secret(secret)
    if configuration is not None:
        row.configuration_json = configuration
    row.enabled = True
    await db.flush()
    return row


async def get_provider_row(db: AsyncSession, user_id: str, provider: str) -> ProviderConnection | None:
    return (
        await db.execute(
            select(ProviderConnection).where(
                ProviderConnection.user_id == user_id, ProviderConnection.provider == provider
            )
        )
    ).scalar_one_or_none()


def _build_client(row: ProviderConnection) -> BaseLlmClient:
    cfg = row.configuration_json or {}
    secret = decrypt_secret(row.encrypted_secret) if row.encrypted_secret else ""
    model = cfg.get("default_model") or _DEFAULT_MODELS.get(row.provider, "")
    if row.provider == "anthropic":
        return AnthropicClient(secret, cfg.get("base_url"), model or "claude-sonnet-5")
    if row.provider == "gemini":
        return GeminiClient(secret, cfg.get("base_url"), model or "gemini-2.5-flash")
    if row.provider == "mock":
        return MockLlmClient(scripted=cfg.get("responses") or {})
    base_url = cfg.get("base_url") or _BASE_URLS.get(row.provider)
    if row.provider == "custom" and not base_url:
        raise ProviderError("custom provider requires base_url")
    return OpenAICompatibleClient(secret, base_url, model or "gpt-4.1-mini")


async def _routing_for(db: AsyncSession, user_id: str, purpose: str) -> list[str]:
    override = await killswitch.get_setting(db, user_id, "model_routing")
    if override and purpose in override:
        return list(override[purpose])
    return DEFAULT_ROUTING.get(purpose, DEFAULT_ROUTING["chat"])


async def resolve_client(db: AsyncSession, user_id: str, purpose: str) -> tuple[BaseLlmClient, ProviderConnection]:
    """Resolve the first enabled configured provider for a purpose. Deterministic."""
    if purpose not in PURPOSES:
        raise ValueError(f"unknown purpose: {purpose}")
    await killswitch.require_operational(db, user_id, killswitch.DISABLE_LLM)
    for provider in await _routing_for(db, user_id, purpose):
        row = await get_provider_row(db, user_id, provider)
        if row is None or not row.enabled:
            continue
        if not row.encrypted_secret and provider != "mock":
            continue
        return _build_client(row), row
    raise NoProviderAvailable(f"no enabled provider for purpose '{purpose}'")


async def _llm_spend_since(db: AsyncSession, user_id: str, since: datetime) -> int:
    total = (
        await db.execute(
            select(func.coalesce(func.sum(LlmUsage.estimated_cost_millicents), 0)).where(
                LlmUsage.user_id == user_id, LlmUsage.created_at >= since
            )
        )
    ).scalar_one()
    return int(total)


async def check_llm_budget(db: AsyncSession, user_id: str) -> None:
    budget = (
        await db.execute(select(Budget).where(Budget.user_id == user_id, Budget.scope == "llm"))
    ).scalar_one_or_none()
    if budget is None:
        return
    now = datetime.now(UTC)
    if budget.daily_limit_cents is not None:
        spent = await _llm_spend_since(db, user_id, now - timedelta(days=1))
        if spent >= budget.daily_limit_cents * 1000:
            raise LlmBudgetExceeded("daily LLM budget exceeded")
    if budget.monthly_limit_cents is not None:
        spent = await _llm_spend_since(db, user_id, now - timedelta(days=30))
        if spent >= budget.monthly_limit_cents * 1000:
            raise LlmBudgetExceeded("monthly LLM budget exceeded")


# ---------------------------------------------------------------------------
# Central generation path (owner directive §27): EVERY LLM call goes through here.
# ---------------------------------------------------------------------------

async def get_orchestrator_config(db: AsyncSession, user_id: str) -> dict:
    cfg = await killswitch.get_setting(db, user_id, "orchestrator_config")
    return cfg if cfg.get("provider") else {}


async def set_orchestrator_config(db: AsyncSession, user_id: str, provider: str, model: str) -> None:
    await killswitch.set_setting(db, user_id, "orchestrator_config",
                                 {"provider": provider, "model": model})


BYOK_REQUIRES_SUBSCRIPTION = (
    "[byok_requires_subscription] Your own API keys (and self-hosted Ollama) are "
    "part of a Moseisley subscription — Basic $9 or Pro $19 a month. During the "
    "free trial your crew runs on platform AI. Self-hosting Moseisley yourself "
    "keeps every key unlocked, free."
)


async def byok_allowed(db: AsyncSession, user_id: str) -> bool:
    """May this user connect their own provider keys?

    Self-host (Stripe not configured) → always yes, zero gating. On a hosted
    deployment BYOK is a subscriber feature: trial and expired users run on
    platform AI until they subscribe. Follows the entitlements pattern —
    server-synced Stripe state only, never browser-supplied.
    """
    from backend.billing import entitlements

    if not entitlements.billing_enforced():
        return True
    return await entitlements.user_plan(db, user_id) in ("basic", "pro")


async def _dev_candidates(db: AsyncSession, user_id: str, purpose: str,
                          json_mode: bool) -> list[str]:
    """Ordered ":free" models for a DEV-mode call. A model the user selected
    themselves is honoured only when it is free; anything else is silently
    coerced to the dev pool — dev mode never spends money on their key."""
    pool = list(factory_pool.DEV_MODELS[factory_pool.bucket_for(purpose, json_mode)])
    orch = await get_orchestrator_config(db, user_id)
    chosen = orch.get("model") or ""
    if orch.get("provider") == "openrouter" and chosen.endswith(factory_pool.FREE_SUFFIX):
        return [chosen, *[m for m in pool if m != chosen]]
    return pool


async def _resolve_for_role(
    db: AsyncSession, user_id: str, crew_role: str | None,
    *, purpose: str = "chat", json_mode: bool = False,
) -> tuple[BaseLlmClient, ProviderConnection | _FactoryRow, str | None, list[str] | None]:
    """Resolution order: DEV mode (absolute) → crew custom config → factory
    (when the effective ai_mode is factory and the platform key exists) →
    orchestrator config → purpose routing. The 4th element is the ordered
    fallback candidate list (None where a single model is used)."""
    from backend.core.models import CrewConfig

    async def _from(provider: str, model: str | None):
        row = await get_provider_row(db, user_id, provider)
        if row is None or not row.enabled:
            return None
        if not row.encrypted_secret and provider != "mock":
            return None
        return _build_client(row), row, model, None

    # DEV mode: the user's OWN OpenRouter key, ":free" models only. This wins
    # over every other config — "every LLM call" in dev mode is free-tier, so a
    # crew role pinned to another provider cannot leak paid usage here.
    if await factory_pool.effective_ai_mode(db, user_id) == factory_pool.MODE_DEV:
        row = await get_provider_row(db, user_id, "openrouter")
        if row is None or not row.enabled or not row.encrypted_secret:
            raise DevKeyMissing()
        candidates = await _dev_candidates(db, user_id, purpose, json_mode)
        return _build_client(row), row, candidates[0], candidates

    if crew_role and crew_role != "orchestrator":
        cc = (await db.execute(select(CrewConfig).where(
            CrewConfig.user_id == user_id, CrewConfig.role == crew_role
        ))).scalar_one_or_none()
        if cc is not None and cc.model_policy == "custom" and cc.provider:
            resolved = await _from(cc.provider, cc.model)
            if resolved:
                return resolved
    # Factory routing when the user is in factory mode — or when they are not
    # entitled to BYOK (hosted trial/expired). A pre-existing "custom" setting
    # is honoured again the moment they subscribe; nothing is rewritten.
    if factory_pool.factory_available() and (
            await factory_pool.effective_ai_mode(db, user_id) == "factory"
            or not await byok_allowed(db, user_id)):
        candidates = factory_pool.FACTORY_MODELS[factory_pool.bucket_for(purpose, json_mode)]
        client = OpenAICompatibleClient(
            get_settings().factory_openrouter_api_key or "",
            _BASE_URLS["openrouter"], candidates[0])
        return client, _FactoryRow(), candidates[0], candidates
    orch = await get_orchestrator_config(db, user_id)
    if orch.get("provider"):
        resolved = await _from(orch["provider"], orch.get("model"))
        if resolved:
            return resolved
    client, row = await resolve_client(db, user_id, "chat")
    return client, row, None, None


def _user_day_start_utc(tz_name: str) -> datetime:
    """Start of the user's current local day, in UTC — same timezone convention
    as the scheduler's next_local_time()."""
    try:
        tz = ZoneInfo(tz_name or "UTC")
    except Exception:
        tz = ZoneInfo("UTC")
    local_now = datetime.now(tz)
    return local_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


async def factory_fuel_used_today(db: AsyncSession, user: User) -> int:
    """Factory requests today (user-local day). Factory usage rows carry the
    dedicated provider marker, so the count never mixes with BYOK traffic."""
    since = _user_day_start_utc(user.timezone)
    return (await db.execute(
        select(func.count()).select_from(LlmUsage).where(
            LlmUsage.user_id == user.id,
            LlmUsage.provider == factory_pool.FACTORY_USAGE_PROVIDER,
            LlmUsage.created_at >= since,
        )
    )).scalar_one()


async def _check_factory_admission(db: AsyncSession, user_id: str) -> None:
    """Admission for a factory call. Consumption order: the tier's included
    daily allowance first, then purchased fuel from The Bar (which never
    expires and works for every tier, including an ended trial). Errors are
    raised only when both are gone. Lazily stamps the trial start.
    """
    user = await db.get(User, user_id)
    if user is None:  # defensive — generate() is always called for a real user
        raise NoProviderAvailable("unknown user")
    tier = await factory_pool.get_factory_tier(db, user)
    if tier == factory_pool.TIER_EXPIRED:
        # no included allowance once the trial is over — purchased fuel only
        if factory_pool.get_fuel_balance(user) <= 0:
            raise FactoryTrialExpired()
        factory_pool.consume_fuel(user)
        return
    if tier == factory_pool.TIER_TRIAL:
        factory_pool.ensure_trial_started(user)
    cap = await factory_pool.daily_cap_for_user(db, user, tier)
    if await factory_fuel_used_today(db, user) >= cap:
        if factory_pool.get_fuel_balance(user) <= 0:
            raise FactoryFuelExhausted(tier)
        factory_pool.consume_fuel(user)


async def _factory_complete(
    client: BaseLlmClient, candidates: list[str], messages: list[dict],
    *, max_tokens: int, temperature: float, json_mode: bool, platform: bool = True,
) -> LlmResult:
    """Try the bucket's models in order (max 2 attempts). Retryable provider
    errors move to the fallback; a final 402 (prepaid credits exhausted) maps
    to the calm FactoryServiceUnavailable — never a raw crash (§5).

    platform=False is DEV mode (the user's own key): the same fallback walk,
    but provider errors surface as themselves — a platform "generator is
    recharging" message would be a lie about someone else's account."""
    attempts = candidates[:2]
    for idx, candidate in enumerate(attempts):
        try:
            result = await client.complete(
                messages, model=candidate, max_tokens=max_tokens,
                temperature=temperature, json_mode=json_mode)
            if not result.model:
                result.model = candidate
            return result
        except ProviderError as e:
            if idx + 1 < len(attempts) and e.status_code in _FACTORY_RETRYABLE:
                continue
            if platform and e.status_code == 402:
                raise FactoryServiceUnavailable() from e
            raise
    raise FactoryServiceUnavailable()  # unreachable with a non-empty bucket


async def generate(
    db: AsyncSession,
    user_id: str,
    messages: list[dict],
    *,
    crew_role: str | None = None,
    purpose: str = "chat",
    run_id: str | None = None,
    orchestrator_run_id: str | None = None,
    project_id: str | None = None,
    max_tokens: int = 1024,
    temperature: float = 0.3,
    json_mode: bool = False,
) -> LlmResult:
    """The single instrumented entry point for every LLM generation.

    Order: emergency stop → LLM switch → budget → resolve provider/model →
    execute → normalize usage → cost (PROVIDER_REPORTED > ESTIMATED > UNKNOWN) →
    persist usage → return.
    """
    from backend.core.models import LlmUsage
    from backend.providers import pricing as pricing_mod

    await killswitch.require_operational(db, user_id, killswitch.DISABLE_LLM)
    await check_llm_budget(db, user_id)

    client, row, model_override, factory_candidates = await _resolve_for_role(
        db, user_id, crew_role, purpose=purpose, json_mode=json_mode)
    # Platform-funded (ROOKIE) calls only: tier, trial and fuel admission. DEV
    # traffic runs on the user's own key — no caps, and its usage rows carry
    # provider "openrouter", so factory fuel counting never sees it.
    is_platform = row.provider == factory_pool.FACTORY_USAGE_PROVIDER
    if is_platform:
        await _check_factory_admission(db, user_id)
    requested_model = model_override or client.default_model
    usage = LlmUsage(
        user_id=user_id, provider=row.provider, model=requested_model,
        requested_model=requested_model, purpose=purpose, crew_role=crew_role,
        run_id=run_id, orchestrator_run_id=orchestrator_run_id, project_id=project_id,
        status="failed",
    )
    try:
        if factory_candidates is not None:
            result = await _factory_complete(
                client, factory_candidates, messages, max_tokens=max_tokens,
                temperature=temperature, json_mode=json_mode, platform=is_platform)
        else:
            result = await client.complete(
                messages, model=model_override, max_tokens=max_tokens,
                temperature=temperature, json_mode=json_mode,
            )
    except Exception:
        db.add(usage)  # §37: record the failed attempt (no usage metadata assumed)
        usage.finished_at = datetime.now(UTC)
        await db.flush()
        raise

    usage.status = "success"
    usage.model = result.model or requested_model
    usage.provider_request_id = result.provider_request_id
    usage.input_tokens = result.input_tokens
    usage.cached_input_tokens = result.cached_input_tokens
    usage.output_tokens = result.output_tokens
    usage.reasoning_tokens = result.reasoning_tokens
    usage.total_tokens = result.total_tokens
    usage.finished_at = datetime.now(UTC)

    if result.provider_cost is not None:
        usage.provider_reported_cost = result.provider_cost
        usage.cost_source = "PROVIDER_REPORTED"
    else:
        snapshot = await pricing_mod.current_snapshot(db, row.provider, usage.model)
        est = None
        if snapshot is not None:
            est = pricing_mod.estimate_cost(
                snapshot, input_tokens=result.input_tokens,
                cached_input_tokens=result.cached_input_tokens,
                output_tokens=result.output_tokens,
            )
        if est is not None:
            usage.estimated_cost = est
            usage.cost_source = "ESTIMATED"
            usage.pricing_snapshot_id = snapshot.id
            usage.estimated_cost_millicents = int(est * 100 * 1000)  # legacy budget column
        else:
            usage.cost_source = "UNKNOWN"
    db.add(usage)
    await db.flush()
    return result


async def generate_with_x_search(
    db: AsyncSession,
    user_id: str,
    prompt: str,
    *,
    allowed_x_handles: list[str] | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    crew_role: str = "radar",
    run_id: str | None = None,
    project_id: str | None = None,
    max_tokens: int = 1600,
) -> dict:
    """X live search through the CURRENT xAI Agent Tools API (2026): POST
    /v1/responses with a server-side `x_search` tool. The old `search_parameters`
    Live Search API was retired 2026-01-12 and returns 410 — do not use it.

    Same instrumentation contract as generate(): emergency stop → LLM switch →
    budget → execute → persist usage. Requires the user's connected, enabled
    xAI provider; with the mock provider connected, returns a scripted result
    (offline tests / demos) clearly marked mock=True.
    """
    import httpx as _httpx

    from backend.core.models import LlmUsage

    await killswitch.require_operational(db, user_id, killswitch.DISABLE_LLM)
    await check_llm_budget(db, user_id)

    xai_row = await get_provider_row(db, user_id, "xai")
    if xai_row is None or not xai_row.enabled or not xai_row.encrypted_secret:
        mock_row = await get_provider_row(db, user_id, "mock")
        if mock_row is not None and mock_row.enabled:
            result = await generate(db, user_id,
                                    [{"role": "user", "content": prompt}],
                                    crew_role=crew_role, purpose="market",
                                    run_id=run_id, project_id=project_id,
                                    max_tokens=max_tokens, json_mode=True)
            return {"text": result.text, "citations": [], "model": result.model,
                    "mock": True}
        raise NoProviderAvailable(
            "X search needs a connected, enabled xAI provider (Connections → xAI)")

    api_key = decrypt_secret(xai_row.encrypted_secret)
    model = (xai_row.configuration_json or {}).get("default_model") or _DEFAULT_MODELS["xai"]
    tool: dict = {"type": "x_search"}
    if allowed_x_handles:
        tool["allowed_x_handles"] = [h.lstrip("@") for h in allowed_x_handles][:20]
    if from_date:
        tool["from_date"] = from_date
    if to_date:
        tool["to_date"] = to_date

    usage = LlmUsage(user_id=user_id, provider="xai", model=model, requested_model=model,
                     purpose="market", crew_role=crew_role, run_id=run_id,
                     project_id=project_id, status="failed")
    try:
        async with _httpx.AsyncClient(timeout=120.0) as http:
            resp = await http.post(
                f"{_BASE_URLS['xai']}/responses",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": model, "input": [{"role": "user", "content": prompt}],
                      "tools": [tool], "max_output_tokens": max_tokens},
            )
        if resp.status_code >= 300:
            raise ProviderError(f"xai responses API returned {resp.status_code}")
        data = resp.json()
    except Exception:
        db.add(usage)
        usage.finished_at = datetime.now(UTC)
        await db.flush()
        raise

    # output text: concatenate output_text blocks from message items
    text_parts: list[str] = []
    citations: list[str] = list(data.get("citations") or [])
    for item in data.get("output") or []:
        for block in item.get("content") or []:
            if block.get("type") == "output_text":
                text_parts.append(block.get("text", ""))
                for ann in block.get("annotations") or []:
                    if ann.get("type") == "url_citation" and ann.get("url"):
                        citations.append(ann["url"])
    u = data.get("usage") or {}
    usage.status = "success"
    usage.model = data.get("model") or model
    usage.provider_request_id = data.get("id")
    usage.input_tokens = u.get("input_tokens")
    usage.output_tokens = u.get("output_tokens")
    usage.reasoning_tokens = u.get("reasoning_tokens")
    usage.total_tokens = u.get("total_tokens")
    usage.finished_at = datetime.now(UTC)
    usage.cost_source = "UNKNOWN"  # responses+tools pricing not estimated — never invented
    db.add(usage)
    await db.flush()
    seen: set[str] = set()
    unique_citations = [c for c in citations if not (c in seen or seen.add(c))]
    return {"text": "\n".join(text_parts), "citations": unique_citations,
            "model": usage.model, "mock": False}


async def complete(
    db: AsyncSession,
    user_id: str,
    purpose: str,
    messages: list[dict],
    *,
    max_tokens: int = 1024,
    temperature: float = 0.3,
    json_mode: bool = False,
    run_id: str | None = None,
) -> LlmResult:
    """Legacy wrapper — routes existing subsystems through the central generate() path,
    mapping purpose → crew role for correct model resolution and usage attribution."""
    role_map = {
        "strategy": "strategist", "market": "radar", "audit": "auditor",
        "goal_compilation": "goal_compiler", "chat": None,
    }
    return await generate(
        db, user_id, messages, crew_role=role_map.get(purpose), purpose=purpose,
        run_id=run_id, max_tokens=max_tokens, temperature=temperature, json_mode=json_mode,
    )
