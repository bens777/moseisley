"""Web search as a user connection (BYOK): Brave or Perplexity, the user's key.

Guarded here:
  · the key is stored like every other key (encrypted, masked) but a search
    connection is NOT an AI brain — it never appears in the LLM provider list
    and never opens the OpenRouter gate;
  · web.search resolves the USER'S provider and sends the USER'S key —
    Perplexity preferred over Brave when both are connected;
  · Perplexity's citations become the source URLs findings require; Brave's
    result links likewise (sourceless facts stay rejected, unchanged rule —
    see test_project_flow.test_unsourced_benchmark_findings_are_rejected);
  · NO search provider is a designed state, not an error: the tool returns the
    exact connect-or-paste message and the flow continues.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from backend.agents import actions, orchestrator
from backend.core.config import get_settings
from backend.websearch import service as websearch
from tests.conftest import setup_mock_provider

WEB = Path(__file__).resolve().parents[1] / "apps" / "web"

BRAVE_BODY = {"web": {"results": [
    {"title": "Toulouse commerce report", "url": "https://example.com/market",
     "description": "Local merchant market size is <b>€2B</b>"},
    {"title": "Competitor list", "url": "https://example.com/competitors",
     "description": "The main players are A and B"},
]}}

PERPLEXITY_BODY = {
    "choices": [{"message": {"content": "The Toulouse market is about €2B [1]."}}],
    "search_results": [
        {"title": "Toulouse commerce report", "url": "https://example.com/report",
         "date": "2025-11-02"},
    ],
    "citations": ["https://example.com/report"],
}


async def _uid(client, auth) -> str:
    return (await client.get("/api/me", headers=auth)).json()["id"]


# ── the connection itself ───────────────────────────────────────────

async def test_search_key_is_a_connection_but_not_a_brain(client, auth):
    r = await client.post("/api/websearch", headers=auth,
                          json={"provider": "brave", "api_key": "brave-key-123"})
    assert r.status_code == 200 and r.json()["ok"] is True

    listed = (await client.get("/api/websearch", headers=auth)).json()
    brave = next(x for x in listed if x["provider"] == "brave")
    assert brave["connected"] is True
    assert brave["display_hint"] and "brave-key-123" not in str(listed)

    # NOT in the LLM provider list — a search key is eyes, not a brain. (The
    # OpenRouter onboarding gate this also guards against in the full product
    # ships with the OAuth onboarding feature, deferred separately — see
    # docs/community-export.md follow-up notes.)
    llm = (await client.get("/api/providers", headers=auth)).json()
    assert all(p["provider"] not in ("brave", "perplexity") for p in llm)


async def test_search_key_can_be_disconnected(client, auth):
    await client.post("/api/websearch", headers=auth,
                      json={"provider": "perplexity", "api_key": "pplx-1"})
    assert (await client.delete("/api/websearch/perplexity", headers=auth)
            ).json()["removed"] is True
    listed = (await client.get("/api/websearch", headers=auth)).json()
    assert all(not x["connected"] for x in listed)


async def test_unknown_search_provider_is_refused(client, auth):
    r = await client.post("/api/websearch", headers=auth,
                          json={"provider": "google", "api_key": "x"})
    assert r.status_code == 400


def test_the_platform_search_key_is_gone_from_config():
    assert not hasattr(get_settings(), "brave_search_api_key")


# ── resolution: the user's key, the user's provider ─────────────────

async def test_search_uses_the_users_brave_key(client, auth, db_session, monkeypatch):
    await client.post("/api/websearch", headers=auth,
                      json={"provider": "brave", "api_key": "brave-key-123"})
    seen: list[dict] = []

    async def fake_get(url, *, params, headers):
        seen.append({"url": url, "headers": headers, "params": params})
        return httpx.Response(200, json=BRAVE_BODY)

    monkeypatch.setattr(websearch, "_http_get", fake_get)
    r = await websearch.search(db_session, await _uid(client, auth),
                               "toulouse merchants", count=5)
    assert seen[0]["headers"]["X-Subscription-Token"] == "brave-key-123"
    assert r.provider == "brave" and r.answer is None
    assert r.results[0].url == "https://example.com/market"
    assert "€2B" in r.results[0].snippet and "<b>" not in r.results[0].snippet


async def test_search_uses_perplexity_and_its_citations(client, auth, db_session,
                                                        monkeypatch):
    await client.post("/api/websearch", headers=auth,
                      json={"provider": "perplexity", "api_key": "pplx-key-9"})
    seen: list[dict] = []

    async def fake_post(url, *, json, headers):
        seen.append({"url": url, "headers": headers, "json": json})
        return httpx.Response(200, json=PERPLEXITY_BODY)

    monkeypatch.setattr(websearch, "_http_post", fake_post)
    r = await websearch.search(db_session, await _uid(client, auth), "toulouse")
    assert seen[0]["url"] == "https://api.perplexity.ai/chat/completions"
    assert seen[0]["headers"]["Authorization"] == "Bearer pplx-key-9"
    assert r.provider == "perplexity"
    # the citations are the sources a BenchmarkFinding can carry
    assert r.results[0].url == "https://example.com/report"
    assert r.answer and "€2B" in r.answer


async def test_perplexity_wins_when_both_are_connected(client, auth, db_session):
    await client.post("/api/websearch", headers=auth,
                      json={"provider": "brave", "api_key": "b"})
    await client.post("/api/websearch", headers=auth,
                      json={"provider": "perplexity", "api_key": "p"})
    assert await websearch.connected_provider(
        db_session, await _uid(client, auth)) == "perplexity"


TAVILY_BODY = {"results": [
    {"title": "Toulouse commerce report", "url": "https://example.com/tavily-report",
     "content": "Local merchant market size is about €2B"},
]}


async def test_tavily_is_a_connection_but_not_a_brain(client, auth):
    r = await client.post("/api/websearch", headers=auth,
                          json={"provider": "tavily", "api_key": "tvly-secret-123"})
    assert r.status_code == 200 and r.json()["ok"] is True

    listed = (await client.get("/api/websearch", headers=auth)).json()
    tavily = next(x for x in listed if x["provider"] == "tavily")
    assert tavily["connected"] is True

    llm_providers = (await client.get("/api/providers", headers=auth)).json()
    assert all(p["provider"] != "tavily" for p in llm_providers)
    assert "tvly-secret-123" not in listed.__str__()


async def test_search_uses_the_users_tavily_key(client, auth, db_session, monkeypatch):
    await client.post("/api/websearch", headers=auth,
                      json={"provider": "tavily", "api_key": "tvly-secret-123"})
    seen: list[dict] = []

    async def fake_post(url, *, json, headers):
        seen.append({"url": url, "headers": headers, "json": json})
        return httpx.Response(200, json=TAVILY_BODY)

    monkeypatch.setattr(websearch, "_http_post", fake_post)
    r = await websearch.search(db_session, await _uid(client, auth), "toulouse merchants")
    assert seen[0]["url"] == "https://api.tavily.com/search"
    assert seen[0]["headers"]["Authorization"] == "Bearer tvly-secret-123"
    assert seen[0]["json"]["query"] == "toulouse merchants"
    assert r.provider == "tavily"
    assert r.results[0].url == "https://example.com/tavily-report"
    assert "€2B" in r.results[0].snippet


async def test_tavily_preference_between_perplexity_and_brave(client, auth, db_session):
    """perplexity > tavily > brave: paid/highest-quality first, then the two
    free options."""
    await client.post("/api/websearch", headers=auth,
                      json={"provider": "brave", "api_key": "b"})
    await client.post("/api/websearch", headers=auth,
                      json={"provider": "tavily", "api_key": "t"})
    assert await websearch.connected_provider(
        db_session, await _uid(client, auth)) == "tavily"

    await client.post("/api/websearch", headers=auth,
                      json={"provider": "perplexity", "api_key": "p"})
    assert await websearch.connected_provider(
        db_session, await _uid(client, auth)) == "perplexity"


async def test_tavily_rejected_key_is_an_honest_failure(client, auth, db_session, monkeypatch):
    await client.post("/api/websearch", headers=auth,
                      json={"provider": "tavily", "api_key": "expired"})

    async def fake_post(url, *, json, headers):
        return httpx.Response(401)

    monkeypatch.setattr(websearch, "_http_post", fake_post)
    with pytest.raises(websearch.WebSearchUnavailable):
        await websearch.search(db_session, await _uid(client, auth), "q")


async def test_websearch_test_endpoint_reports_real_health(client, auth, monkeypatch):
    await client.post("/api/websearch", headers=auth,
                      json={"provider": "tavily", "api_key": "tvly-secret-123"})

    async def fake_post(url, *, json, headers):
        return httpx.Response(200, json=TAVILY_BODY)

    monkeypatch.setattr(websearch, "_http_post", fake_post)
    ok = await client.post("/api/websearch/tavily/test", headers=auth)
    assert ok.json() == {"ok": True}


async def test_websearch_test_endpoint_reports_failure_not_fake_success(client, auth, monkeypatch):
    await client.post("/api/websearch", headers=auth,
                      json={"provider": "tavily", "api_key": "bad-key"})

    async def fake_post(url, *, json, headers):
        return httpx.Response(401)

    monkeypatch.setattr(websearch, "_http_post", fake_post)
    failed = await client.post("/api/websearch/tavily/test", headers=auth)
    assert failed.json() == {"ok": False}


async def test_websearch_test_endpoint_requires_a_configured_provider(client, auth):
    resp = await client.post("/api/websearch/tavily/test", headers=auth)
    assert resp.status_code == 404


async def test_citations_only_response_still_yields_sources(client, auth, db_session,
                                                            monkeypatch):
    await client.post("/api/websearch", headers=auth,
                      json={"provider": "perplexity", "api_key": "p"})
    body = {"choices": [{"message": {"content": "answer [1]"}}],
            "citations": ["https://example.com/only-citation"]}

    async def fake_post(url, *, json, headers):
        return httpx.Response(200, json=body)

    monkeypatch.setattr(websearch, "_http_post", fake_post)
    r = await websearch.search(db_session, await _uid(client, auth), "q")
    assert r.results[0].url == "https://example.com/only-citation"


async def test_rejected_key_is_an_honest_failure(client, auth, db_session, monkeypatch):
    await client.post("/api/websearch", headers=auth,
                      json={"provider": "brave", "api_key": "expired"})

    async def fake_get(url, *, params, headers):
        return httpx.Response(401)

    monkeypatch.setattr(websearch, "_http_get", fake_get)
    with pytest.raises(websearch.WebSearchUnavailable):
        await websearch.search(db_session, await _uid(client, auth), "q")


async def test_no_provider_raises_the_designed_state(client, auth, db_session):
    with pytest.raises(websearch.NoSearchProvider):
        await websearch.search(db_session, await _uid(client, auth), "anything")


# ── graceful absence in the flow: connect one, or paste sources ─────

def test_absent_message_is_the_exact_copy_and_whitelisted():
    assert orchestrator.SEARCH_ABSENT_MESSAGE == (
        "I can research the market context automatically if you connect a search "
        "provider (Brave is free) — [connect one](action:connections). Or paste "
        "your own sources and references here and I'll build the benchmark from those."
    )
    assert actions.sanitize(orchestrator.SEARCH_ABSENT_MESSAGE) \
        == orchestrator.SEARCH_ABSENT_MESSAGE
    assert actions.found_in(orchestrator.SEARCH_ABSENT_MESSAGE) == ["connections"]


async def test_no_provider_reaches_the_user_as_connect_or_paste_not_an_error(
        client, auth):
    """The tool result carries `say` and no error; the Manager relays it and
    the flow continues."""
    await setup_mock_provider(client, auth, responses={
        "research the benchmark": json.dumps(
            {"action": "tool", "tool": "web.search", "args": {"query": "toulouse"}}),
        "no_search_provider": json.dumps(
            {"action": "reply", "text": orchestrator.SEARCH_ABSENT_MESSAGE}),
    })
    r = (await client.post("/api/manager/message", headers=auth,
                           json={"text": "ok, research the benchmark"})).json()
    assert "connect a search provider" in r["reply"]
    assert "[connect one](action:connections)" in r["reply"]
    # the whitelisted action survives into the stored history
    stored = (await client.get("/api/manager/messages", headers=auth)).json()[-1]
    assert "(action:connections)" in stored["content"]


def test_prompt_and_page_teach_the_new_reality():
    raw = open("backend/prompts/manager.md", encoding="utf-8").read()
    prompt = " ".join(raw.split())            # wrap-tolerant: phrases, not layout
    assert "no_search_provider" in prompt
    assert "USER'S OWN connected Web Intelligence source" in prompt
    assert "the flow NEVER stalls on this" in prompt

    page = (WEB / "app" / "connections" / "page.tsx").read_text(encoding="utf-8")
    assert "Web Search" in page
    assert '"https://brave.com/search/api/"' in page
    assert 'target="_blank"' in page
    assert "2000 free searches/month" in page
    assert "paid, higher quality" in page
