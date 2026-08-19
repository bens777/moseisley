"""Gemini -> YouTube Intelligence: BYOK-only video analysis via Gemini's
native YouTube video input. Guarded here:
  · robust URL validation (exact-host allowlist, not substring matching) —
    normal forms accepted, disguised/malformed ones rejected;
  · the USER's own connected Gemini credential is used, never a shared/
    Factory one, and never leaks into the response or logs;
  · tenant isolation — one user's Gemini key is never reachable from another
    user's call;
  · upstream Gemini failures (429, 400, 401/403, 5xx) map to clean,
    structured states, never a raw provider error;
  · the tool is registered for the orchestrator/manager tool loop and
    respects the same kill switch every other tool does.
"""
from __future__ import annotations

import logging

import httpx
import pytest
from sqlalchemy import select

from backend.agents.orchestrator import MANAGER_ONLY_TOOLS, TOOL_SCHEMAS, AnalyzeYoutubeArgs, _execute_tool
from backend.core.models import LlmUsage, User
from backend.providers import youtube_intelligence as yti

GEMINI_KEY_A = "gemini-key-tenant-a-do-not-leak"
GEMINI_KEY_B = "gemini-key-tenant-b-do-not-leak"


def _gemini_response(text: str = "This video explains how to bake bread.", *,
                     model: str = "gemini-2.5-flash") -> httpx.Response:
    return httpx.Response(200, json={
        "candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}],
        "modelVersion": model,
        "responseId": "resp-1",
        "usageMetadata": {"promptTokenCount": 1200, "candidatesTokenCount": 80,
                          "totalTokenCount": 1280},
    })


async def _uid(client, auth) -> str:
    return (await client.get("/api/me", headers=auth)).json()["id"]


async def _connect_gemini(client, auth, api_key: str) -> None:
    r = await client.post("/api/providers", headers=auth,
                          json={"provider": "gemini", "api_key": api_key})
    assert r.status_code == 200, r.text


def _patch_gemini_post(monkeypatch, handler):
    """POST to Gemini's generateContent -> handler(url, json, headers) ->
    httpx.Response. Anything else raises (no accidental real network call)."""
    async def fake_post(self, url, **kwargs):
        if "generativelanguage.googleapis.com" in str(url):
            return handler(str(url), kwargs.get("json") or {}, kwargs.get("headers") or {})
        raise AssertionError(f"unexpected host: {url}")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)


# ── URL validation (§5) ─────────────────────────────────────────────────

@pytest.mark.parametrize("url,expected_id", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ?t=42", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL123", "dQw4w9WgXcQ"),
])
def test_accepted_youtube_url_forms(url, expected_id):
    canonical = yti.canonical_youtube_url(url)
    assert canonical == f"https://www.youtube.com/watch?v={expected_id}"


@pytest.mark.parametrize("url", [
    "https://vimeo.com/12345678",
    "https://youtube.com.evil.example/watch?v=dQw4w9WgXcQ",  # disguised host
    "https://evil-youtube.com/watch?v=dQw4w9WgXcQ",
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "file:///etc/passwd",
    "http://youtube.com/watch?v=dQw4w9WgXcQ",  # not https
    "https://youtube.com/watch?v=short",       # id too short
    "https://youtube.com/watch",                # no id at all
    "not a url",
    "",
    "https://127.0.0.1/watch?v=dQw4w9WgXcQ",
    "https://youtube.com@evil.example/watch?v=dQw4w9WgXcQ",  # userinfo trick
])
def test_rejected_urls(url):
    with pytest.raises(yti.YoutubeUrlInvalid):
        yti.canonical_youtube_url(url)


# ── dispatch + response shape (§7/§9) ───────────────────────────────────

async def test_connected_valid_url_dispatches_correctly(client, auth, db_session, monkeypatch):
    await _connect_gemini(client, auth, GEMINI_KEY_A)
    uid = await _uid(client, auth)

    seen = {}

    def handler(url, payload, headers):
        seen["url"] = url
        seen["payload"] = payload
        seen["headers"] = headers
        return _gemini_response("The video demonstrates a sourdough technique.")

    _patch_gemini_post(monkeypatch, handler)
    out = await yti.analyze(db_session, uid, "https://youtu.be/dQw4w9WgXcQ",
                            "What technique is shown?")
    await db_session.commit()

    assert out["source_type"] == "youtube"
    assert out["provider"] == "gemini"
    assert out["source_url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert out["model"] == "gemini-2.5-flash"
    assert "sourdough" in out["text"]

    # the actual request sent to Gemini
    parts = seen["payload"]["contents"][0]["parts"]
    assert parts[0]["file_data"]["file_uri"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert parts[1]["text"] == "What technique is shown?"
    assert seen["headers"]["x-goog-api-key"] == GEMINI_KEY_A
    assert "systemInstruction" in seen["payload"]

    # usage recorded, correctly tagged (§15 — see the no-Factory-fallback test below)
    usage = (await db_session.execute(
        select(LlmUsage).where(LlmUsage.user_id == uid)
    )).scalar_one()
    assert usage.provider == "gemini"
    assert usage.purpose == "youtube_intelligence"
    assert usage.total_tokens == 1280


async def test_timeline_mode_requests_json_and_parses_structured_output(client, auth, db_session, monkeypatch):
    await _connect_gemini(client, auth, GEMINI_KEY_A)
    uid = await _uid(client, auth)

    timeline_json = (
        '[{"timestamp": "00:00:15", "title": "Intro", "description": "Host introduces the topic"},'
        ' {"timestamp": "00:03:42", "title": "Pricing", "description": "Pricing is discussed"}]'
    )

    def handler(url, payload, headers):
        assert payload["generationConfig"]["responseMimeType"] == "application/json"
        return _gemini_response(timeline_json)

    _patch_gemini_post(monkeypatch, handler)
    out = await yti.analyze(db_session, uid, "https://youtu.be/dQw4w9WgXcQ",
                            "", analysis_mode="timeline")
    assert out["timeline"] == [
        {"timestamp": "00:00:15", "title": "Intro", "description": "Host introduces the topic"},
        {"timestamp": "00:03:42", "title": "Pricing", "description": "Pricing is discussed"},
    ]


async def test_timeline_mode_never_fabricates_on_unparseable_output(client, auth, db_session, monkeypatch):
    await _connect_gemini(client, auth, GEMINI_KEY_A)
    uid = await _uid(client, auth)
    _patch_gemini_post(monkeypatch, lambda u, p, h: _gemini_response("not valid json at all"))
    out = await yti.analyze(db_session, uid, "https://youtu.be/dQw4w9WgXcQ",
                            "", analysis_mode="timeline")
    assert "timeline" not in out  # never a fabricated/guessed structure
    assert out["text"] == "not valid json at all"


# ── provider not connected (§6) ─────────────────────────────────────────

async def test_gemini_not_connected_returns_provider_not_connected(client, auth, db_session):
    uid = await _uid(client, auth)
    with pytest.raises(yti.ProviderNotConnected):
        await yti.analyze(db_session, uid, "https://youtu.be/dQw4w9WgXcQ", "summarize")


async def test_tool_execution_surfaces_provider_not_connected_cleanly(client, auth, db_session):
    uid = (await client.get("/api/me", headers=auth)).json()["id"]
    user = await db_session.get(User, uid)
    args = AnalyzeYoutubeArgs(url="https://youtu.be/dQw4w9WgXcQ", instruction="summarize")
    out = await _execute_tool(db_session, user, "youtube.analyze", args, "run-1")
    assert out["state"] == "provider_not_connected"
    assert "Connections" in out["message"]


# ── error mapping (§13) ──────────────────────────────────────────────────

async def test_rate_limit_maps_to_rate_limited_state(client, auth, db_session, monkeypatch):
    await _connect_gemini(client, auth, GEMINI_KEY_A)
    uid = (await client.get("/api/me", headers=auth)).json()["id"]
    user = await db_session.get(User, uid)
    _patch_gemini_post(monkeypatch, lambda u, p, h: httpx.Response(429, json={"error": "rate limited"}))
    args = AnalyzeYoutubeArgs(url="https://youtu.be/dQw4w9WgXcQ", instruction="summarize")
    out = await _execute_tool(db_session, user, "youtube.analyze", args, "run-1")
    assert out["state"] == "rate_limited"


async def test_inaccessible_video_maps_to_video_unavailable_state(client, auth, db_session, monkeypatch):
    """Gemini's actual behavior for a private/unlisted/otherwise-inaccessible
    video is a non-200 (commonly 400) — never fabricated as a real answer."""
    await _connect_gemini(client, auth, GEMINI_KEY_A)
    uid = (await client.get("/api/me", headers=auth)).json()["id"]
    user = await db_session.get(User, uid)
    _patch_gemini_post(monkeypatch, lambda u, p, h: httpx.Response(
        400, json={"error": {"message": "Unable to process input"}}))
    args = AnalyzeYoutubeArgs(url="https://youtu.be/dQw4w9WgXcQ", instruction="summarize")
    out = await _execute_tool(db_session, user, "youtube.analyze", args, "run-1")
    assert out["state"] == "video_unavailable"
    assert "public YouTube videos" in out["message"]


async def test_invalid_key_maps_to_provider_key_invalid_state(client, auth, db_session, monkeypatch):
    await _connect_gemini(client, auth, GEMINI_KEY_A)
    uid = (await client.get("/api/me", headers=auth)).json()["id"]
    user = await db_session.get(User, uid)
    _patch_gemini_post(monkeypatch, lambda u, p, h: httpx.Response(401, json={"error": "bad key"}))
    args = AnalyzeYoutubeArgs(url="https://youtu.be/dQw4w9WgXcQ", instruction="summarize")
    out = await _execute_tool(db_session, user, "youtube.analyze", args, "run-1")
    assert out["state"] == "provider_key_invalid"


async def test_malformed_url_never_reaches_gemini(client, auth, db_session, monkeypatch):
    await _connect_gemini(client, auth, GEMINI_KEY_A)
    uid = (await client.get("/api/me", headers=auth)).json()["id"]
    user = await db_session.get(User, uid)

    async def fail_post(self, url, **kwargs):
        raise AssertionError("must never dispatch a request for an invalid URL")

    monkeypatch.setattr(httpx.AsyncClient, "post", fail_post)
    args = AnalyzeYoutubeArgs(url="https://vimeo.com/12345", instruction="summarize")
    out = await _execute_tool(db_session, user, "youtube.analyze", args, "run-1")
    assert out["state"] == "invalid_url"


# ── tenant isolation + credential safety (§7/§8/§14) ────────────────────

async def test_tenant_credential_isolation(client, db_session, monkeypatch):
    from tests.conftest import auth_headers

    auth_a = await auth_headers(client, "yti-tenant-a@example.com")
    auth_b = await auth_headers(client, "yti-tenant-b@example.com")
    await _connect_gemini(client, auth_a, GEMINI_KEY_A)
    # tenant B has NO Gemini connection at all

    uid_a = await _uid(client, auth_a)
    uid_b = await _uid(client, auth_b)

    seen_keys = []

    def handler(url, payload, headers):
        seen_keys.append(headers["x-goog-api-key"])
        return _gemini_response()

    _patch_gemini_post(monkeypatch, handler)

    # A's own call uses A's own key.
    await yti.analyze(db_session, uid_a, "https://youtu.be/dQw4w9WgXcQ", "summarize")
    assert seen_keys == [GEMINI_KEY_A]

    # B has no connection — must fail closed, never silently borrow A's key.
    with pytest.raises(yti.ProviderNotConnected):
        await yti.analyze(db_session, uid_b, "https://youtu.be/dQw4w9WgXcQ", "summarize")
    assert seen_keys == [GEMINI_KEY_A]  # no second (borrowed-key) call was ever made


async def test_two_tenants_each_use_their_own_key(client, db_session, monkeypatch):
    from tests.conftest import auth_headers

    auth_a = await auth_headers(client, "yti-tenant-c@example.com")
    auth_b = await auth_headers(client, "yti-tenant-d@example.com")
    await _connect_gemini(client, auth_a, GEMINI_KEY_A)
    await _connect_gemini(client, auth_b, GEMINI_KEY_B)
    uid_a = await _uid(client, auth_a)
    uid_b = await _uid(client, auth_b)

    seen_keys = []
    _patch_gemini_post(monkeypatch, lambda u, p, h: (seen_keys.append(h["x-goog-api-key"]),
                                                      _gemini_response())[1])

    await yti.analyze(db_session, uid_a, "https://youtu.be/dQw4w9WgXcQ", "summarize")
    await yti.analyze(db_session, uid_b, "https://youtu.be/dQw4w9WgXcQ", "summarize")
    assert seen_keys == [GEMINI_KEY_A, GEMINI_KEY_B]


async def test_credential_never_appears_in_response_or_logs(client, auth, db_session, monkeypatch, caplog):
    await _connect_gemini(client, auth, GEMINI_KEY_A)
    uid = await _uid(client, auth)
    _patch_gemini_post(monkeypatch, lambda u, p, h: httpx.Response(500, json={"error": "boom"}))

    from backend.providers.clients import ProviderError

    with caplog.at_level(logging.INFO, logger="mychief.youtube_intelligence"):
        with pytest.raises(ProviderError):
            await yti.analyze(db_session, uid, "https://youtu.be/dQw4w9WgXcQ", "summarize")
    assert GEMINI_KEY_A not in caplog.text


# ── no Factory/Moseisley credential fallback (§15) ───────────────────────

async def test_never_falls_back_to_factory_credential(client, auth, db_session, monkeypatch):
    """A Community/free user with NO subscription and NO Factory eligibility
    can still use their OWN connected Gemini key — this capability is BYOK
    only and has nothing to do with Factory admission at all."""
    from backend.core.config import get_settings

    monkeypatch.setattr(get_settings(), "factory_openrouter_api_key", "should-never-be-used")
    await _connect_gemini(client, auth, GEMINI_KEY_A)
    uid = await _uid(client, auth)

    def handler(url, payload, headers):
        assert headers["x-goog-api-key"] == GEMINI_KEY_A
        return _gemini_response()

    _patch_gemini_post(monkeypatch, handler)
    await yti.analyze(db_session, uid, "https://youtu.be/dQw4w9WgXcQ", "summarize")
    await db_session.commit()

    row = (await db_session.execute(
        select(LlmUsage).where(LlmUsage.user_id == uid)
    )).scalar_one()
    assert row.provider == "gemini"


# ── tool registration + kill switch (§10/§12/§13) ────────────────────────

def test_tool_is_registered_and_broadly_available():
    assert "youtube.analyze" in TOOL_SCHEMAS
    assert TOOL_SCHEMAS["youtube.analyze"] is AnalyzeYoutubeArgs
    # not hardcoded to a single agent (§10) — available to any role, same as
    # memory.*/goals.*/crew.*, not gated behind MANAGER_ONLY_TOOLS
    assert "youtube.analyze" not in MANAGER_ONLY_TOOLS


def test_analysis_mode_is_validated():
    from pydantic import ValidationError

    AnalyzeYoutubeArgs(url="https://youtu.be/dQw4w9WgXcQ", instruction="x", analysis_mode="timeline")
    with pytest.raises(ValidationError):
        AnalyzeYoutubeArgs(url="https://youtu.be/dQw4w9WgXcQ", instruction="x",
                          analysis_mode="not-a-real-mode")


async def test_kill_switch_blocks_the_tool_like_any_other(client, auth, db_session):
    from backend.core import killswitch

    await _connect_gemini(client, auth, GEMINI_KEY_A)
    uid = await _uid(client, auth)
    user = await db_session.get(User, uid)
    await killswitch.set_switch(db_session, uid, killswitch.PAUSE_ALL_AGENTS, True)
    await db_session.commit()

    args = AnalyzeYoutubeArgs(url="https://youtu.be/dQw4w9WgXcQ", instruction="summarize")
    with pytest.raises(killswitch.KillSwitchEngaged):
        await _execute_tool(db_session, user, "youtube.analyze", args, "run-1")


def test_prompts_document_the_tool():
    from pathlib import Path

    prompts_dir = Path(__file__).resolve().parents[1] / "backend" / "prompts"
    manager_md = (prompts_dir / "manager.md").read_text(encoding="utf-8")
    orchestrator_md = (prompts_dir / "orchestrator.md").read_text(encoding="utf-8")
    assert "youtube.analyze" in manager_md
    assert "youtube.analyze" in orchestrator_md
    assert "public YouTube" in manager_md or "public" in manager_md
