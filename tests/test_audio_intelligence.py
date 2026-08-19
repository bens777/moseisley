"""Groq -> Audio Intelligence: real Whisper transcription/translation of a
Moseisley-uploaded file. Guarded here:

  · extends the existing Groq provider/`registry` (the same pattern
    `generate_with_x_search` established for X Intelligence) rather than a
    parallel provider — `transcribe_with_groq`/`translate_with_groq` share
    Groq's credential resolution, kill switch, budget gate and paid-usage
    policy;
  · sources bytes from the EXISTING attachment pipeline (FileRef + owned
    storage) — never an arbitrary URL, never a BYOS reference;
  · the separate, pre-existing ephemeral voice-note dictation path
    (backend/audio/stt.py, backend/api/routes/audio.py) is untouched except
    for gaining Groq as an optional provider behind the SAME abstraction
    (tests/test_audio_stt.py covers that regression directly);
  · file type/size validation happens before a single byte reaches Groq;
  · timestamps/segments/words are normalized and never fabricated;
  · transcript content is treated as untrusted DATA, never instructions;
  · upstream failures map to clean, structured, actionable states.
"""
from __future__ import annotations

import base64

import httpx
import pytest

from backend.agents.orchestrator import (
    MANAGER_ONLY_TOOLS,
    TOOL_SCHEMAS,
    AudioTranscribeArgs,
    AudioTranslateArgs,
    _execute_tool,
)
from backend.core.models import LlmUsage, User
from backend.providers import audio_intelligence as ai
from backend.providers import usage_policy

GROQ_KEY = "groq-secret-do-not-leak"
AUDIO_BYTES = b"pretend this is an mp3 file"


def _verbose_json_body(text: str = "Let's start with the Q3 numbers.", *,
                       model: str = "whisper-large-v3-turbo",
                       segments: list[dict] | None = None,
                       language: str | None = "en", cost: float | None = 0.000508,
                       seconds: float | None = 9.2) -> httpx.Response:
    if segments is None:
        segments = [{"id": 0, "start": 0.0, "end": 4.6, "text": "Let's start"},
                    {"id": 1, "start": 4.6, "end": 9.2, "text": "with the Q3 numbers."}]
    usage: dict = {}
    if seconds is not None:
        usage["seconds"] = seconds
    if cost is not None:
        usage["cost"] = cost
    body: dict = {"text": text, "segments": segments, "task": "transcribe"}
    if language is not None:
        body["language"] = language
    if usage:
        body["usage"] = usage
    return httpx.Response(200, json=body)


async def _uid(client, auth) -> str:
    return (await client.get("/api/me", headers=auth)).json()["id"]


async def _connect_groq(client, headers, api_key: str = GROQ_KEY) -> None:
    r = await client.post("/api/providers", json={"provider": "groq", "api_key": api_key},
                          headers=headers)
    assert r.status_code == 200, r.text


async def _allow_paid(client, headers) -> None:
    r = await client.put("/api/providers/policy", json={"policy": "paid_allowed"}, headers=headers)
    assert r.status_code == 200, r.text


async def _upload(client, headers, *, name: str = "meeting.mp3", content: bytes = AUDIO_BYTES,
                  mime_type: str = "audio/mpeg") -> str:
    r = await client.post("/api/files/upload", headers=headers, json={
        "path": f"audio/{name}", "content_base64": base64.b64encode(content).decode(),
        "title": name, "mime_type": mime_type,
    })
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _patch_post(monkeypatch, handler):
    """POST to Groq's audio endpoints -> handler(url, data, files, headers) ->
    httpx.Response. The test client's own ASGI calls pass through untouched.
    `data` arrives as a list[tuple[str,str]] (repeated form fields, e.g.
    timestamp_granularities[]) — normalized here to a plain dict (last value
    wins) for the common single-value assertions; tests that need every
    repeated value use `_patch_post_raw` instead."""
    original_post = httpx.AsyncClient.post

    async def fake_post(self, url, **kwargs):
        if "api.groq.com" not in str(url):
            return await original_post(self, url, **kwargs)
        raw = kwargs.get("data") or []
        data = dict(raw) if isinstance(raw, list) else dict(raw)
        return handler(str(url), data, kwargs.get("files") or {}, kwargs.get("headers") or {})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)


def _patch_post_raw(monkeypatch, handler):
    """Like _patch_post, but hands the handler the RAW list[tuple[str,str]]
    form fields (repeated keys preserved) instead of a collapsed dict."""
    original_post = httpx.AsyncClient.post

    async def fake_post(self, url, **kwargs):
        if "api.groq.com" not in str(url):
            return await original_post(self, url, **kwargs)
        return handler(str(url), kwargs.get("data") or [], kwargs.get("files") or {},
                       kwargs.get("headers") or {})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)


# ── 1/2. valid dispatch, the user's own credential ───────────────────────

async def test_valid_audio_dispatch_uses_the_users_credential(client, auth, db_session, monkeypatch):
    await _connect_groq(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    seen = {}

    def handler(url, data, files, headers):
        seen["url"], seen["data"], seen["files"], seen["headers"] = url, data, files, headers
        return _verbose_json_body()

    _patch_post(monkeypatch, handler)
    result = await ai.transcribe(db_session, await _uid(client, auth), file_id)
    assert seen["url"] == "https://api.groq.com/openai/v1/audio/transcriptions"
    assert seen["headers"]["Authorization"] == f"Bearer {GROQ_KEY}"
    assert seen["files"]["file"][0] == "meeting.mp3"
    assert result["text"] == "Let's start with the Q3 numbers."
    assert result["provider"] == "groq"


# ── 3. missing connection ────────────────────────────────────────────────

async def test_missing_connection_is_provider_not_connected(client, auth, db_session):
    file_id = await _upload(client, auth)
    with pytest.raises(ai.ProviderNotConnected):
        await ai.transcribe(db_session, await _uid(client, auth), file_id)


# ── 4. tenant credential isolation ───────────────────────────────────────

async def test_tenant_credential_isolation(client, db_session, monkeypatch):
    from tests.conftest import auth_headers

    auth_a = await auth_headers(client, "audio-tenant-a@example.com")
    auth_b = await auth_headers(client, "audio-tenant-b@example.com")
    await _connect_groq(client, auth_a, "groq-tenant-a-key")
    await _allow_paid(client, auth_a)
    await _connect_groq(client, auth_b, "groq-tenant-b-key")
    await _allow_paid(client, auth_b)
    file_a = await _upload(client, auth_a)
    file_b = await _upload(client, auth_b)

    seen_keys = []

    def handler(url, data, files, headers):
        seen_keys.append(headers["Authorization"])
        return _verbose_json_body()

    _patch_post(monkeypatch, handler)
    await ai.transcribe(db_session, await _uid(client, auth_a), file_a)
    assert seen_keys == ["Bearer groq-tenant-a-key"]
    await ai.transcribe(db_session, await _uid(client, auth_b), file_b)
    assert seen_keys == ["Bearer groq-tenant-a-key", "Bearer groq-tenant-b-key"]


# ── 5. attachment tenant isolation ───────────────────────────────────────

async def test_attachment_tenant_isolation(client, db_session, monkeypatch):
    from tests.conftest import auth_headers

    auth_a = await auth_headers(client, "audio-file-a@example.com")
    auth_b = await auth_headers(client, "audio-file-b@example.com")
    await _connect_groq(client, auth_b, "groq-b-key")
    await _allow_paid(client, auth_b)
    file_a = await _upload(client, auth_a)  # belongs to tenant A

    _patch_post(monkeypatch, lambda url, data, files, headers: _verbose_json_body())
    with pytest.raises(ai.AttachmentNotFound):
        await ai.transcribe(db_session, await _uid(client, auth_b), file_a)


# ── 6/7/8. file type/size validation ─────────────────────────────────────

async def test_supported_file_is_accepted(client, auth, db_session, monkeypatch):
    await _connect_groq(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth, name="clip.wav", mime_type="audio/wav")
    _patch_post(monkeypatch, lambda url, data, files, headers: _verbose_json_body())
    result = await ai.transcribe(db_session, await _uid(client, auth), file_id)
    assert result["text"]


async def test_unsupported_file_type_is_rejected(client, auth, db_session):
    await _connect_groq(client, auth)
    file_id = await _upload(client, auth, name="notes.txt", mime_type="text/plain")
    with pytest.raises(ai.UnsupportedFileType):
        await ai.transcribe(db_session, await _uid(client, auth), file_id)


async def test_oversized_file_is_rejected(client, auth, db_session):
    """Moseisley's own upload endpoint already caps inline uploads at 10MB
    (backend/api/routes/files.py MAX_INLINE_BYTES) — the SAME ceiling as
    ai.MAX_AUDIO_BYTES, so a real upload can never exceed it. This test
    covers the defense-in-depth re-check directly: a FileRef whose recorded
    size_bytes exceeds the limit is refused from its metadata alone, before
    a single byte is read from storage."""
    from backend.core.models import FileRef
    from backend.storage.factory import get_owned_storage

    await _connect_groq(client, auth)
    storage = get_owned_storage()
    uid = await _uid(client, auth)
    await storage.write(f"users/{uid}/audio/huge.mp3", AUDIO_BYTES)
    ref = FileRef(user_id=uid, storage_provider=storage.provider_name,
                  path=f"users/{uid}/audio/huge.mp3", title="huge.mp3",
                  mime_type="audio/mpeg", size_bytes=ai.MAX_AUDIO_BYTES + 1)
    db_session.add(ref)
    await db_session.commit()

    with pytest.raises(ai.FileTooLarge):
        await ai.transcribe(db_session, uid, ref.id)


# ── 9/10. model policy ────────────────────────────────────────────────────

async def test_default_model_is_turbo(client, auth, db_session, monkeypatch):
    await _connect_groq(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    seen = {}

    def handler(url, data, files, headers):
        seen["model"] = data["model"]
        return _verbose_json_body(model="whisper-large-v3-turbo")

    _patch_post(monkeypatch, handler)
    result = await ai.transcribe(db_session, await _uid(client, auth), file_id)
    assert seen["model"] == "whisper-large-v3-turbo"
    assert result["model"] == "whisper-large-v3-turbo"


async def test_accuracy_model_is_selectable(client, auth, db_session, monkeypatch):
    await _connect_groq(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    seen = {}

    def handler(url, data, files, headers):
        seen["model"] = data["model"]
        return _verbose_json_body(model="whisper-large-v3")

    _patch_post(monkeypatch, handler)
    result = await ai.transcribe(db_session, await _uid(client, auth), file_id,
                                 model="whisper-large-v3")
    assert seen["model"] == "whisper-large-v3"
    assert result["model"] == "whisper-large-v3"


def test_unknown_model_is_rejected():
    with pytest.raises(ai.InvalidAudioRequest):
        ai._validate_model("whisper-tiny")


# ── 11/12/13. normalization ───────────────────────────────────────────────

async def test_transcription_text_is_normalized(client, auth, db_session, monkeypatch):
    await _connect_groq(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    _patch_post(monkeypatch, lambda url, data, files, headers:
               _verbose_json_body(text="  padded text  \n"))
    result = await ai.transcribe(db_session, await _uid(client, auth), file_id)
    assert result["text"] == "padded text"


async def test_segment_timestamps_are_normalized(client, auth, db_session, monkeypatch):
    await _connect_groq(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    _patch_post(monkeypatch, lambda url, data, files, headers: _verbose_json_body())
    result = await ai.transcribe(db_session, await _uid(client, auth), file_id)
    assert result["segments"] == [
        {"start_seconds": 0.0, "end_seconds": 4.6, "text": "Let's start"},
        {"start_seconds": 4.6, "end_seconds": 9.2, "text": "with the Q3 numbers."},
    ]
    assert result["duration"] == 9.2


async def test_word_timestamps_only_when_requested(client, auth, db_session, monkeypatch):
    await _connect_groq(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    seen = {}

    def handler(url, data, files, headers):
        seen["granularities"] = [v for k, v in data if k == "timestamp_granularities[]"]
        return httpx.Response(200, json={
            "text": "hello world", "language": "en",
            "segments": [{"start": 0.0, "end": 1.0, "text": "hello world"}],
            "words": [{"word": "hello", "start": 0.0, "end": 0.4},
                     {"word": "world", "start": 0.5, "end": 1.0}],
        })

    _patch_post_raw(monkeypatch, handler)
    result = await ai.transcribe(db_session, await _uid(client, auth), file_id,
                                 word_timestamps=True)
    assert seen["granularities"] == ["segment", "word"]
    assert result["words"] == [
        {"word": "hello", "start_seconds": 0.0, "end_seconds": 0.4},
        {"word": "world", "start_seconds": 0.5, "end_seconds": 1.0},
    ]

    file_id2 = await _upload(client, auth, name="other.mp3")
    _patch_post(monkeypatch, lambda url, data, files, headers: _verbose_json_body())
    result2 = await ai.transcribe(db_session, await _uid(client, auth), file_id2)
    assert result2["words"] is None


# ── 14/15. translation + language hint ────────────────────────────────────

async def test_translation_path_is_distinct_from_transcription(
        client, auth, db_session, monkeypatch):
    await _connect_groq(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth, name="reunion.mp3")
    seen = {}

    def handler(url, data, files, headers):
        seen["url"] = url
        seen["has_language"] = "language" in data
        seen["has_granularities"] = "timestamp_granularities[]" in data
        return httpx.Response(200, json={"text": "This is the translated meeting."})

    _patch_post(monkeypatch, handler)
    result = await ai.translate(db_session, await _uid(client, auth), file_id)
    assert seen["url"] == "https://api.groq.com/openai/v1/audio/translations"
    assert seen["has_language"] is False
    assert seen["has_granularities"] is False
    assert result["translated"] is True
    assert result["language"] == "en"
    assert result["text"] == "This is the translated meeting."


async def test_language_hint_is_mapped_to_the_provider(client, auth, db_session, monkeypatch):
    await _connect_groq(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    seen = {}

    def handler(url, data, files, headers):
        seen["language"] = data.get("language")
        return _verbose_json_body(language="fr")

    _patch_post(monkeypatch, handler)
    result = await ai.transcribe(db_session, await _uid(client, auth), file_id, language="fr")
    assert seen["language"] == "fr"
    assert result["language"] == "fr"


async def test_malformed_language_hint_is_rejected(client, auth, db_session):
    await _connect_groq(client, auth)
    file_id = await _upload(client, auth)
    with pytest.raises(ai.InvalidAudioRequest):
        await ai.transcribe(db_session, await _uid(client, auth), file_id, language="french")


# ── 16/17/18/19/20/21/22. structured, honest error states ─────────────────

async def test_rate_limit_maps_to_rate_limited(client, auth, db_session, monkeypatch):
    await _connect_groq(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    _patch_post(monkeypatch, lambda url, data, files, headers:
               httpx.Response(429, headers={"x-ratelimit-remaining-requests": "42"}))
    with pytest.raises(Exception) as exc:  # noqa: PT011
        await ai.transcribe(db_session, await _uid(client, auth), file_id)
    assert ai.error_detail(exc.value)["state"] == "rate_limited"


async def test_quota_exhaustion_uses_the_ratelimit_header(client, auth, db_session, monkeypatch):
    await _connect_groq(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    _patch_post(monkeypatch, lambda url, data, files, headers:
               httpx.Response(429, headers={"x-ratelimit-remaining-requests": "0"}))
    with pytest.raises(Exception) as exc:  # noqa: PT011
        await ai.transcribe(db_session, await _uid(client, auth), file_id)
    assert ai.error_detail(exc.value)["state"] == "quota_exhausted"


async def test_invalid_key_maps_to_provider_key_invalid(client, auth, db_session, monkeypatch):
    await _connect_groq(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    _patch_post(monkeypatch, lambda url, data, files, headers: httpx.Response(401))
    with pytest.raises(Exception) as exc:  # noqa: PT011
        await ai.transcribe(db_session, await _uid(client, auth), file_id)
    assert ai.error_detail(exc.value)["state"] == "provider_key_invalid"


async def test_provider_timeout_maps_to_provider_timeout(client, auth, db_session, monkeypatch):
    await _connect_groq(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    original_post = httpx.AsyncClient.post

    async def timing_out(self, url, **kwargs):
        if "api.groq.com" not in str(url):
            return await original_post(self, url, **kwargs)
        raise httpx.TimeoutException("no response")

    monkeypatch.setattr(httpx.AsyncClient, "post", timing_out)
    with pytest.raises(httpx.TimeoutException) as exc:
        await ai.transcribe(db_session, await _uid(client, auth), file_id)
    assert ai.error_detail(exc.value)["state"] == "provider_timeout"


async def test_provider_unavailable_from_5xx(client, auth, db_session, monkeypatch):
    await _connect_groq(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    _patch_post(monkeypatch, lambda url, data, files, headers: httpx.Response(503))
    with pytest.raises(Exception) as exc:  # noqa: PT011
        await ai.transcribe(db_session, await _uid(client, auth), file_id)
    assert ai.error_detail(exc.value)["state"] == "provider_unavailable"


async def test_empty_transcript_is_a_distinct_honest_state(client, auth, db_session, monkeypatch):
    await _connect_groq(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    _patch_post(monkeypatch, lambda url, data, files, headers:
               httpx.Response(200, json={"text": "", "segments": []}))
    with pytest.raises(ai.EmptyTranscript) as exc:
        await ai.transcribe(db_session, await _uid(client, auth), file_id)
    assert ai.error_detail(exc.value)["state"] == "empty_transcript"


async def test_malformed_upstream_response_is_reported_not_crashed_on(
        client, auth, db_session, monkeypatch):
    await _connect_groq(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    _patch_post(monkeypatch, lambda url, data, files, headers: httpx.Response(200, text="not json"))
    with pytest.raises(Exception) as exc:  # noqa: PT011
        await ai.transcribe(db_session, await _uid(client, auth), file_id)
    assert ai.error_detail(exc.value)["state"] == "transcription_failed"


# ── 23/24. no Factory fallback, BYOK usage accounting ─────────────────────

async def test_no_factory_fallback_and_usage_is_recorded(client, auth, db_session, monkeypatch):
    from sqlalchemy import select as sa_select

    await _connect_groq(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    _patch_post(monkeypatch, lambda url, data, files, headers: _verbose_json_body())
    await ai.transcribe(db_session, await _uid(client, auth), file_id)
    rows = (await db_session.execute(sa_select(LlmUsage))).scalars().all()
    assert len(rows) == 1
    assert rows[0].provider == "groq"
    assert rows[0].purpose == "stt"
    assert rows[0].cost_source == "PROVIDER_REPORTED"
    assert rows[0].provider_reported_cost == pytest.approx(0.000508, abs=1e-9)


async def test_cost_stays_unknown_when_not_reported(client, auth, db_session, monkeypatch):
    from sqlalchemy import select as sa_select

    await _connect_groq(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    _patch_post(monkeypatch, lambda url, data, files, headers: _verbose_json_body(cost=None))
    await ai.transcribe(db_session, await _uid(client, auth), file_id)
    row = (await db_session.execute(sa_select(LlmUsage))).scalar_one()
    assert row.cost_source == "UNKNOWN"
    assert row.provider_reported_cost is None


# ── 25/26. secret + private-path non-leakage ───────────────────────────────

async def test_secret_never_appears_in_errors(client, auth, db_session, monkeypatch):
    await _connect_groq(client, auth, "super-secret-groq-key")
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    _patch_post(monkeypatch, lambda url, data, files, headers: httpx.Response(401))
    with pytest.raises(Exception) as exc:  # noqa: PT011
        await ai.transcribe(db_session, await _uid(client, auth), file_id)
    assert "super-secret-groq-key" not in str(exc.value)
    detail = ai.error_detail(exc.value)
    assert "super-secret-groq-key" not in detail["message"]


async def test_attachment_error_never_leaks_the_storage_path(client, auth, db_session):
    await _connect_groq(client, auth)
    with pytest.raises(ai.AttachmentNotFound) as exc:
        await ai.transcribe(db_session, await _uid(client, auth), "not-a-real-file-id")
    detail = ai.error_detail(exc.value)
    assert "users/" not in detail["message"]
    assert "/" not in detail["message"]


# ── 27/28/29. tool registration, kill switch, role policy ─────────────────

def test_tools_are_registered_and_broadly_available():
    assert TOOL_SCHEMAS["audio.transcribe"] is AudioTranscribeArgs
    assert TOOL_SCHEMAS["audio.translate"] is AudioTranslateArgs
    assert "audio.transcribe" not in MANAGER_ONLY_TOOLS
    assert "audio.translate" not in MANAGER_ONLY_TOOLS


async def test_execute_tool_reachable_from_orchestrator_role(
        client, auth, db_session, monkeypatch):
    await _connect_groq(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    _patch_post(monkeypatch, lambda url, data, files, headers: _verbose_json_body())
    uid = await _uid(client, auth)
    user = await db_session.get(User, uid)
    args = AudioTranscribeArgs(file_id=file_id)
    out = await _execute_tool(db_session, user, "audio.transcribe", args, "run-1",
                              role="orchestrator")
    assert out["provider"] == "groq"
    assert "error" not in out


async def test_kill_switch_blocks_audio_tools_like_any_other(client, auth, db_session):
    from backend.core import killswitch

    await _connect_groq(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    uid = await _uid(client, auth)
    user = await db_session.get(User, uid)
    await killswitch.set_switch(db_session, uid, killswitch.PAUSE_ALL_AGENTS, True)
    await db_session.commit()

    args = AudioTranscribeArgs(file_id=file_id)
    with pytest.raises(killswitch.KillSwitchEngaged):
        await _execute_tool(db_session, user, "audio.transcribe", args, "run-1",
                            role="orchestrator")


async def test_free_only_policy_blocks_audio_transcription(client, auth, db_session):
    await _connect_groq(client, auth)
    file_id = await _upload(client, auth)
    with pytest.raises(usage_policy.PaidCapabilityBlocked):
        await ai.transcribe(db_session, await _uid(client, auth), file_id)


async def test_execute_tool_maps_policy_block_to_actionable_state(client, auth, db_session):
    await _connect_groq(client, auth)
    file_id = await _upload(client, auth)
    uid = await _uid(client, auth)
    user = await db_session.get(User, uid)
    args = AudioTranscribeArgs(file_id=file_id)
    out = await _execute_tool(db_session, user, "audio.transcribe", args, "run-1",
                              role="orchestrator")
    assert out["state"] == "paid_capability_blocked"
    assert "note" in out


# ── 30. normal chat prompt guidance ────────────────────────────────────────

def test_prompts_document_the_audio_tools_and_triggers():
    manager = " ".join(open("backend/prompts/manager.md", encoding="utf-8").read().split())
    orchestrator = " ".join(open("backend/prompts/orchestrator.md", encoding="utf-8").read().split())
    for prompt in (manager, orchestrator):
        assert "audio.transcribe" in prompt
        assert "audio.translate" in prompt
    assert "file_id" in manager
    assert "transcribe this" in manager.lower() or "summarize this meeting" in manager.lower()


# ── 31. untrusted transcript instruction boundary ─────────────────────────

def test_orchestrator_prompt_treats_transcripts_as_untrusted():
    raw = " ".join(open("backend/prompts/orchestrator.md", encoding="utf-8").read().split())
    assert "audio transcripts" in raw or "phrase found inside a transcript" in raw.lower()


def test_manager_prompt_treats_transcripts_as_untrusted():
    raw = " ".join(open("backend/prompts/manager.md", encoding="utf-8").read().split()).lower()
    assert "not something moseisley was told to do" in raw or "instruction to you" in raw


async def test_execute_tool_result_carries_the_untrusted_content_note(
        client, auth, db_session, monkeypatch):
    await _connect_groq(client, auth)
    await _allow_paid(client, auth)
    file_id = await _upload(client, auth)
    _patch_post(monkeypatch, lambda url, data, files, headers: _verbose_json_body())
    uid = await _uid(client, auth)
    user = await db_session.get(User, uid)
    args = AudioTranscribeArgs(file_id=file_id)
    out = await _execute_tool(db_session, user, "audio.transcribe", args, "run-1",
                              role="orchestrator")
    assert "not instructions to you" in out["note"]


# ── 32. existing voice-input path stays untouched ──────────────────────────

def test_the_ephemeral_voice_path_still_never_touches_storage_or_filerefs():
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    for path in (repo / "backend" / "audio").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "backend.storage" not in source, path.name
        assert "FileRef" not in source, path.name


def test_audio_intelligence_is_a_separate_module_from_the_voice_path():
    """Two distinct capabilities behind one Groq credential (§1/§3) — the
    ephemeral dictation path and file-based Audio Intelligence never share
    an implementation module."""
    import backend.audio.stt as stt_mod

    assert ai.__name__ != stt_mod.__name__
    assert not hasattr(stt_mod, "transcribe_with_groq")
