"""Grok/xAI -> X Intelligence: real, live X search through xAI's server-side
`x_search` tool on the Responses API. Guarded here:

  · extends the existing xAI provider/`generate_with_x_search` (built for
    Radar's Market Watches) rather than duplicating a parallel provider —
    the adapter (backend/providers/x_intelligence.py) is a thin normalization
    layer over it;
  · the USER's own connected xAI credential is used, tenant-scoped, never a
    Moseisley-owned key, never routed through the Factory allowance, and
    gated by the same FREE_ONLY/paid usage policy Radar's calls already are;
  · no `store_messages` is ever sent — xAI only persists conversation history
    when a caller opts in, so omitting it keeps calls off xAI's server-side
    storage by default;
  · handle/date inputs are validated and normalized, never passed through
    raw;
  · citations from xAI's own annotations become `sources` — never a
    fabricated post, handle, date, URL or quotation;
  · upstream failures map to clean, structured, actionable states, never a
    raw provider body or stack trace;
  · exact provider-reported cost (cost_in_usd_ticks) is captured when
    present, never estimated or invented;
  · retrieved X content is treated as untrusted DATA, never as instructions
    to the model, both in the system instruction sent to Grok and in the
    Manager/Orchestrator prompts that receive the tool's result.
"""
from __future__ import annotations

import httpx
import pytest

from backend.agents.orchestrator import MANAGER_ONLY_TOOLS, TOOL_SCHEMAS, XSearchArgs, _execute_tool
from backend.core.models import LlmUsage, User
from backend.providers import usage_policy
from backend.providers import x_intelligence as xi

XAI_KEY = "xai-secret-do-not-leak"


def _x_response(text: str = "People are discussing the new launch positively.", *,
                model: str = "grok-4", cost_ticks: int | None = 37756000,
                citations: list[dict] | None = None) -> httpx.Response:
    if citations is None:
        citations = [{"type": "url_citation", "url": "https://x.com/someuser/status/123",
                     "title": "someuser on X"}]
    usage: dict = {"input_tokens": 120, "output_tokens": 40}
    if cost_ticks is not None:
        usage["cost_in_usd_ticks"] = cost_ticks
    return httpx.Response(200, json={
        "id": "resp-x-1", "model": model,
        "output": [{"content": [{"type": "output_text", "text": text,
                                 "annotations": citations}]}],
        "usage": usage,
    })


async def _uid(client, auth) -> str:
    return (await client.get("/api/me", headers=auth)).json()["id"]


async def _connect_xai(client, headers, api_key: str = XAI_KEY) -> None:
    r = await client.post("/api/providers", json={"provider": "xai", "api_key": api_key},
                          headers=headers)
    assert r.status_code == 200, r.text


async def _allow_paid(client, headers) -> None:
    r = await client.put("/api/providers/policy", json={"policy": "paid_allowed"}, headers=headers)
    assert r.status_code == 200, r.text


def _patch_post(monkeypatch, handler):
    """POST to xAI's /v1/responses -> handler(url, json_body, headers) -> httpx.Response.
    Anything else raises (no accidental real network call)."""
    async def fake_post(self, url, **kwargs):
        if "api.x.ai" in str(url):
            return handler(str(url), kwargs.get("json") or {}, kwargs.get("headers") or {})
        raise AssertionError(f"unexpected host: {url}")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)


# ── 1/2. valid dispatch, the user's own credential ───────────────────────

async def test_valid_x_search_dispatch_uses_the_users_credential(
        client, auth, db_session, monkeypatch):
    await _connect_xai(client, auth)
    await _allow_paid(client, auth)
    seen = {}

    def handler(url, body, headers):
        seen["url"], seen["json"], seen["headers"] = url, body, headers
        return _x_response()

    _patch_post(monkeypatch, handler)
    result = await xi.search(db_session, await _uid(client, auth), "humanoid robots")
    assert seen["url"] == "https://api.x.ai/v1/responses"
    assert seen["headers"]["Authorization"] == f"Bearer {XAI_KEY}"
    assert seen["json"]["tools"] == [{"type": "x_search"}]
    assert result["provider"] == "xai"
    assert result["answer"] == "People are discussing the new launch positively."
    assert result["mode"] is None


# ── 3. missing connection ────────────────────────────────────────────────

async def test_missing_connection_is_provider_not_connected(client, auth, db_session):
    with pytest.raises(xi.ProviderNotConnected):
        await xi.search(db_session, await _uid(client, auth), "humanoid robots")


# ── 4. tenant isolation ──────────────────────────────────────────────────

async def test_tenant_isolation_each_users_key_stays_their_own(
        client, db_session, monkeypatch):
    from tests.conftest import auth_headers

    auth_a = await auth_headers(client, "x-tenant-a@example.com")
    auth_b = await auth_headers(client, "x-tenant-b@example.com")
    await _connect_xai(client, auth_a, "xai-tenant-a-key")
    await _allow_paid(client, auth_a)
    await _connect_xai(client, auth_b, "xai-tenant-b-key")
    await _allow_paid(client, auth_b)

    seen_keys = []

    def handler(url, body, headers):
        seen_keys.append(headers["Authorization"])
        return _x_response()

    _patch_post(monkeypatch, handler)
    await xi.search(db_session, await _uid(client, auth_a), "q")
    assert seen_keys == ["Bearer xai-tenant-a-key"]
    await xi.search(db_session, await _uid(client, auth_b), "q")
    assert seen_keys == ["Bearer xai-tenant-a-key", "Bearer xai-tenant-b-key"]


# ── 5/22. never Factory-funded ───────────────────────────────────────────

async def test_x_search_never_charged_to_factory(client, auth, db_session, monkeypatch):
    from sqlalchemy import select as sa_select

    await _connect_xai(client, auth)
    await _allow_paid(client, auth)
    _patch_post(monkeypatch, lambda url, body, headers: _x_response())
    await xi.search(db_session, await _uid(client, auth), "q")
    rows = (await db_session.execute(sa_select(LlmUsage))).scalars().all()
    assert len(rows) == 1
    assert rows[0].provider == "xai"


# ── 6/8. tool registration, broadly available (not manager-only) ────────

def test_tool_is_registered_and_broadly_available():
    assert TOOL_SCHEMAS["x.search"] is XSearchArgs
    assert "x.search" not in MANAGER_ONLY_TOOLS


async def test_execute_tool_reachable_from_orchestrator_role_not_just_manager(
        client, auth, db_session, monkeypatch):
    await _connect_xai(client, auth)
    await _allow_paid(client, auth)
    _patch_post(monkeypatch, lambda url, body, headers: _x_response())
    uid = await _uid(client, auth)
    user = await db_session.get(User, uid)
    args = XSearchArgs(query="humanoid robots")
    out = await _execute_tool(db_session, user, "x.search", args, "run-1", role="orchestrator")
    assert out["provider"] == "xai"
    assert "error" not in out


# ── 7. privacy: no store_messages ever sent ──────────────────────────────

async def test_no_store_messages_parameter_is_ever_sent(client, auth, db_session, monkeypatch):
    await _connect_xai(client, auth)
    await _allow_paid(client, auth)
    seen = {}

    def handler(url, body, headers):
        seen["json"] = body
        return _x_response()

    _patch_post(monkeypatch, handler)
    await xi.search(db_session, await _uid(client, auth), "q")
    assert "store_messages" not in seen["json"]
    assert "store" not in seen["json"]


# ── 10/11. handle filtering + malformed handle rejection ────────────────

async def test_handle_filtering_reaches_the_provider(client, auth, db_session, monkeypatch):
    await _connect_xai(client, auth)
    await _allow_paid(client, auth)
    seen = {}

    def handler(url, body, headers):
        seen["json"] = body
        return _x_response()

    _patch_post(monkeypatch, handler)
    result = await xi.search(db_session, await _uid(client, auth), "q",
                             handles=["@xai", "OpenAI"])
    assert seen["json"]["tools"][0]["allowed_x_handles"] == ["xai", "OpenAI"]
    assert result["handles"] == ["xai", "OpenAI"]


async def test_malformed_handle_is_rejected_not_forwarded(client, auth, db_session):
    with pytest.raises(xi.InvalidSearchRequest):
        await xi.search(db_session, await _uid(client, auth), "q",
                        handles=["not a handle!"])


async def test_too_many_handles_is_rejected(client, auth, db_session):
    with pytest.raises(xi.InvalidSearchRequest):
        await xi.search(db_session, await _uid(client, auth), "q",
                        handles=[f"user{i}" for i in range(21)])


# ── 12. date mapping ──────────────────────────────────────────────────────

async def test_date_range_maps_to_provider_parameters(client, auth, db_session, monkeypatch):
    await _connect_xai(client, auth)
    await _allow_paid(client, auth)
    seen = {}

    def handler(url, body, headers):
        seen["json"] = body
        return _x_response()

    _patch_post(monkeypatch, handler)
    result = await xi.search(db_session, await _uid(client, auth), "q",
                             date_from="2026-08-12", date_to="2026-08-19")
    assert seen["json"]["tools"][0]["from_date"] == "2026-08-12"
    assert seen["json"]["tools"][0]["to_date"] == "2026-08-19"
    assert result["date_from"] == "2026-08-12"
    assert result["date_to"] == "2026-08-19"


async def test_malformed_date_is_rejected(client, auth, db_session):
    with pytest.raises(xi.InvalidSearchRequest):
        await xi.search(db_session, await _uid(client, auth), "q", date_from="last week")


# ── 13. citations preserved, never fabricated ────────────────────────────

async def test_citations_become_normalized_sources(client, auth, db_session, monkeypatch):
    await _connect_xai(client, auth)
    await _allow_paid(client, auth)
    citations = [
        {"type": "url_citation", "url": "https://x.com/alice/status/1", "title": "alice on X"},
        {"type": "url_citation", "url": "https://x.com/bob/status/2", "title": "bob on X"},
    ]
    _patch_post(monkeypatch, lambda url, body, headers: _x_response(citations=citations))
    result = await xi.search(db_session, await _uid(client, auth), "q")
    assert result["sources"] == [
        {"url": "https://x.com/alice/status/1", "title": "alice on X", "source_type": "x"},
        {"url": "https://x.com/bob/status/2", "title": "bob on X", "source_type": "x"},
    ]


async def test_max_results_trims_sources_client_side_only(client, auth, db_session, monkeypatch):
    """max_results is NOT a real x_search parameter (§5) — it must never be
    sent to the provider, only used to trim what we return."""
    await _connect_xai(client, auth)
    await _allow_paid(client, auth)
    citations = [{"type": "url_citation", "url": f"https://x.com/u/status/{i}", "title": f"post {i}"}
                for i in range(5)]
    seen = {}

    def handler(url, body, headers):
        seen["json"] = body
        return _x_response(citations=citations)

    _patch_post(monkeypatch, handler)
    result = await xi.search(db_session, await _uid(client, auth), "q", max_results=2)
    assert len(result["sources"]) == 2
    assert "max_results" not in seen["json"]
    assert "max_search_results" not in seen["json"]


# ── 14/15/16/17/18/20. structured, honest error states ────────────────────

async def test_rate_limit_maps_to_rate_limited(client, auth, db_session, monkeypatch):
    await _connect_xai(client, auth)
    await _allow_paid(client, auth)
    _patch_post(monkeypatch, lambda url, body, headers: httpx.Response(429, text="slow down"))
    with pytest.raises(Exception) as exc:  # noqa: PT011 — asserting via error_detail below
        await xi.search(db_session, await _uid(client, auth), "q")
    assert xi.error_detail(exc.value)["state"] == "rate_limited"


async def test_quota_exhaustion_is_distinguished_from_rate_limit(
        client, auth, db_session, monkeypatch):
    await _connect_xai(client, auth)
    await _allow_paid(client, auth)
    _patch_post(monkeypatch,
               lambda url, body, headers: httpx.Response(429, text="monthly usage limit exceeded"))
    with pytest.raises(Exception) as exc:  # noqa: PT011
        await xi.search(db_session, await _uid(client, auth), "q")
    assert xi.error_detail(exc.value)["state"] == "quota_exhausted"


async def test_invalid_key_maps_to_provider_key_invalid(client, auth, db_session, monkeypatch):
    await _connect_xai(client, auth)
    await _allow_paid(client, auth)
    _patch_post(monkeypatch, lambda url, body, headers: httpx.Response(401))
    with pytest.raises(Exception) as exc:  # noqa: PT011
        await xi.search(db_session, await _uid(client, auth), "q")
    assert xi.error_detail(exc.value)["state"] == "provider_key_invalid"


async def test_provider_timeout_maps_to_provider_timeout(client, auth, db_session, monkeypatch):
    await _connect_xai(client, auth)
    await _allow_paid(client, auth)

    async def timing_out(self, url, **kwargs):
        raise httpx.TimeoutException("no response")

    monkeypatch.setattr(httpx.AsyncClient, "post", timing_out)
    with pytest.raises(httpx.TimeoutException) as exc:
        await xi.search(db_session, await _uid(client, auth), "q")
    assert xi.error_detail(exc.value)["state"] == "provider_timeout"


async def test_provider_unavailable_maps_from_5xx(client, auth, db_session, monkeypatch):
    await _connect_xai(client, auth)
    await _allow_paid(client, auth)
    _patch_post(monkeypatch, lambda url, body, headers: httpx.Response(503))
    with pytest.raises(Exception) as exc:  # noqa: PT011
        await xi.search(db_session, await _uid(client, auth), "q")
    assert xi.error_detail(exc.value)["state"] == "provider_unavailable"


async def test_capability_unavailable_when_model_lacks_tool_support(
        client, auth, db_session, monkeypatch):
    await _connect_xai(client, auth)
    await _allow_paid(client, auth)
    _patch_post(monkeypatch, lambda url, body, headers:
               httpx.Response(400, text="the requested tool is not supported for this model"))
    with pytest.raises(Exception) as exc:  # noqa: PT011
        await xi.search(db_session, await _uid(client, auth), "q")
    assert xi.error_detail(exc.value)["state"] == "capability_unavailable"


async def test_malformed_upstream_response_is_reported_not_crashed_on(
        client, auth, db_session, monkeypatch):
    await _connect_xai(client, auth)
    await _allow_paid(client, auth)
    _patch_post(monkeypatch, lambda url, body, headers: httpx.Response(200, text="not json"))
    with pytest.raises(Exception) as exc:  # noqa: PT011
        await xi.search(db_session, await _uid(client, auth), "q")
    detail = xi.error_detail(exc.value)
    assert detail["state"] in ("provider_unavailable", "error")


# ── 19. no results is a distinct, honest state ────────────────────────────

async def test_no_results_is_a_distinct_honest_state(client, auth, db_session, monkeypatch):
    await _connect_xai(client, auth)
    await _allow_paid(client, auth)
    _patch_post(monkeypatch, lambda url, body, headers: _x_response(text="", citations=[]))
    with pytest.raises(xi.NoResults) as exc:
        await xi.search(db_session, await _uid(client, auth), "q")
    assert xi.error_detail(exc.value)["state"] == "no_results"


# ── 21. exact provider cost extraction ────────────────────────────────────

async def test_exact_provider_cost_is_captured_from_cost_in_usd_ticks(
        client, auth, db_session, monkeypatch):
    from sqlalchemy import select as sa_select

    await _connect_xai(client, auth)
    await _allow_paid(client, auth)
    _patch_post(monkeypatch, lambda url, body, headers: _x_response(cost_ticks=37756000))
    await xi.search(db_session, await _uid(client, auth), "q")
    row = (await db_session.execute(sa_select(LlmUsage))).scalar_one()
    assert row.cost_source == "PROVIDER_REPORTED"
    assert row.provider_reported_cost == pytest.approx(0.0037756, abs=1e-7)


async def test_cost_stays_unknown_when_not_reported_never_estimated(
        client, auth, db_session, monkeypatch):
    from sqlalchemy import select as sa_select

    await _connect_xai(client, auth)
    await _allow_paid(client, auth)
    _patch_post(monkeypatch, lambda url, body, headers: _x_response(cost_ticks=None))
    await xi.search(db_session, await _uid(client, auth), "q")
    row = (await db_session.execute(sa_select(LlmUsage))).scalar_one()
    assert row.cost_source == "UNKNOWN"
    assert row.provider_reported_cost is None


# ── 23. secret non-leakage ────────────────────────────────────────────────

async def test_secret_never_appears_in_errors(client, auth, db_session, monkeypatch):
    await _connect_xai(client, auth, "super-secret-xai-key")
    await _allow_paid(client, auth)
    _patch_post(monkeypatch, lambda url, body, headers: httpx.Response(401))
    with pytest.raises(Exception) as exc:  # noqa: PT011
        await xi.search(db_session, await _uid(client, auth), "q")
    assert "super-secret-xai-key" not in str(exc.value)
    detail = xi.error_detail(exc.value)
    assert "super-secret-xai-key" not in detail["message"]


# ── 24. kill switch ────────────────────────────────────────────────────────

async def test_kill_switch_blocks_x_search_like_any_other_tool(client, auth, db_session):
    from backend.core import killswitch

    await _connect_xai(client, auth)
    await _allow_paid(client, auth)
    uid = await _uid(client, auth)
    user = await db_session.get(User, uid)
    await killswitch.set_switch(db_session, uid, killswitch.PAUSE_ALL_AGENTS, True)
    await db_session.commit()

    args = XSearchArgs(query="humanoid robots")
    with pytest.raises(killswitch.KillSwitchEngaged):
        await _execute_tool(db_session, user, "x.search", args, "run-1", role="orchestrator")


# ── 25. role/tool spend policy ────────────────────────────────────────────

async def test_free_only_policy_blocks_x_search(client, auth, db_session):
    await _connect_xai(client, auth)
    with pytest.raises(usage_policy.PaidCapabilityBlocked):
        await xi.search(db_session, await _uid(client, auth), "q")


async def test_execute_tool_maps_policy_block_to_actionable_state(
        client, auth, db_session):
    await _connect_xai(client, auth)
    uid = await _uid(client, auth)
    user = await db_session.get(User, uid)
    args = XSearchArgs(query="humanoid robots")
    out = await _execute_tool(db_session, user, "x.search", args, "run-1", role="orchestrator")
    assert out["state"] == "paid_capability_blocked"
    assert "note" in out


# ── 26. X content is untrusted input, never instructions ─────────────────

def test_system_instruction_frames_x_content_as_untrusted_data():
    text = xi.SYSTEM_INSTRUCTION
    assert "DATA" in text
    assert "NOT instructions" in text or "never act on it" in text.lower() \
        or "never act on it" in text


async def test_system_instruction_is_actually_sent_as_a_system_role_item(
        client, auth, db_session, monkeypatch):
    await _connect_xai(client, auth)
    await _allow_paid(client, auth)
    seen = {}

    def handler(url, body, headers):
        seen["json"] = body
        return _x_response()

    _patch_post(monkeypatch, handler)
    await xi.search(db_session, await _uid(client, auth), "q")
    roles = [item["role"] for item in seen["json"]["input"]]
    assert roles[0] == "system"
    assert xi.SYSTEM_INSTRUCTION in seen["json"]["input"][0]["content"]


def test_orchestrator_prompt_teaches_untrusted_content_boundary():
    raw = open("backend/prompts/orchestrator.md", encoding="utf-8").read()
    prompt = " ".join(raw.split())
    assert "untrusted DATA" in prompt or "untrusted data" in prompt.lower()
    assert "x.search" in prompt


def test_manager_prompt_teaches_untrusted_content_boundary():
    raw = open("backend/prompts/manager.md", encoding="utf-8").read()
    prompt = " ".join(raw.split())
    assert "never as an instruction to you" in prompt.lower() \
        or "never an instruction to you" in prompt.lower()
    assert "x.search" in prompt


# ── 27. no unsupported-provider fallback (there is exactly one provider) ──

async def test_no_fallback_to_a_different_provider_on_failure(
        client, auth, db_session, monkeypatch):
    """Unlike web.search (Tavily/Brave/Perplexity), X Intelligence has exactly
    one possible provider — xAI. A failure must never silently retry against
    anything else; there is nothing else it could legitimately retry against
    without breaking BYOK (§13)."""
    await _connect_xai(client, auth)
    await _allow_paid(client, auth)
    calls = []

    def handler(url, body, headers):
        calls.append(1)
        return httpx.Response(500)

    _patch_post(monkeypatch, handler)
    with pytest.raises(Exception):  # noqa: PT011, B017
        await xi.search(db_session, await _uid(client, auth), "q")
    assert len(calls) == 1


# ── 9. normal Chat prompt guidance documents real trigger phrases ────────

def test_manager_prompt_documents_x_specific_triggers():
    raw = open("backend/prompts/manager.md", encoding="utf-8").read()
    prompt = " ".join(raw.split())
    assert "x.search" in prompt
    assert "web.search" in prompt  # distinguishing guidance references both
    for phrase in ("sentiment", "narrative", "thread"):
        assert phrase in prompt


# ── mode validation ────────────────────────────────────────────────────────

def test_tool_args_validate_mode():
    from pydantic import ValidationError

    XSearchArgs(query="humanoid robots", mode="sentiment")
    with pytest.raises(ValidationError):
        XSearchArgs(query="q", mode="not-a-real-mode")


async def test_invalid_mode_is_rejected_at_the_adapter_too(client, auth, db_session):
    await _connect_xai(client, auth)
    with pytest.raises(xi.InvalidSearchRequest):
        await xi.search(db_session, await _uid(client, auth), "q", mode="not-a-real-mode")


# ── Connections UI already surfaces xAI's X Intelligence capability ──────

async def test_connections_definitions_already_surface_x_intelligence(client, auth):
    defs = (await client.get("/api/providers/definitions", headers=auth)).json()
    xai_def = next(d for d in defs if d["id"] == "xai")
    assert "x_search" in xai_def["capabilities"]
    assert xai_def["state"] == "not_connected"

    await _connect_xai(client, auth)
    defs = (await client.get("/api/providers/definitions", headers=auth)).json()
    xai_def = next(d for d in defs if d["id"] == "xai")
    assert xai_def["state"] == "connected"
