"""Voice input: the transcription route, and what it refuses to do.

STT routing accepts openai / custom / mock only and deliberately never reaches
factory (platform) mode, so most users have NO backend transcription. That is a
first-class answer here — the UI asks before it records — not an error the user
discovers after speaking for a minute.

Nothing in this path stores audio.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from backend.audio import stt as stt_mod
from backend.core.config import get_settings
from backend.providers import registry

REPO = Path(__file__).resolve().parents[1]
WEB = REPO / "apps" / "web"

AUDIO = b"pretend this is opus"


async def _mock_stt(client, auth, transcript: str = "buy nvidia at the open"):
    """The mock provider doubles as the STT provider: its configuration carries
    the transcript it will return."""
    resp = await client.post("/api/providers", headers=auth, json={
        "provider": "mock", "api_key": "mock",
        "configuration": {"transcript": transcript}})
    assert resp.status_code == 200, resp.text


def _upload(content: bytes = AUDIO, name: str = "voice.webm"):
    return {"file": (name, content, "audio/webm")}


# ── availability is answered before anything records ────────────────

async def test_a_user_with_no_stt_provider_is_told_so(client, auth):
    body = (await client.get("/api/audio/availability", headers=auth)).json()
    assert body["available"] is False
    assert body["reason"] == "no_stt_provider"
    assert "Expert mode" in body["detail"]
    assert "Rookie" in body["detail"] and "Dev" in body["detail"]


async def test_a_user_with_a_usable_provider_is_told_that_too(client, auth):
    await _mock_stt(client, auth)
    body = (await client.get("/api/audio/availability", headers=auth)).json()
    assert body["available"] is True and body["provider"] == "mock"
    assert "discarded" in body["detail"]


async def test_availability_needs_a_login(client):
    assert (await client.get("/api/audio/availability")).status_code == 401
    assert (await client.post("/api/audio/transcribe")).status_code == 401


async def test_openrouter_alone_does_not_unlock_voice(client, auth):
    """DEV mode runs on OpenRouter, which is not in the STT routing — so a DEV
    user has no backend transcription, and the endpoint says so plainly."""
    await client.post("/api/providers", headers=auth, json={
        "provider": "openrouter", "api_key": "sk-or-test"})
    body = (await client.get("/api/audio/availability", headers=auth)).json()
    assert body["available"] is False and body["reason"] == "no_stt_provider"


def test_the_stt_routing_deliberately_excludes_platform_ai():
    assert registry.DEFAULT_ROUTING["stt"] == ["openai", "custom", "mock"]
    assert "openrouter" not in registry.DEFAULT_ROUTING["stt"]


# ── transcription ───────────────────────────────────────────────────

async def test_a_recording_comes_back_as_text(client, auth):
    await _mock_stt(client, auth, "watch nvidia for a breakout")
    resp = await client.post("/api/audio/transcribe", headers=auth, files=_upload())
    assert resp.status_code == 200
    assert resp.json() == {"text": "watch nvidia for a breakout"}


async def test_an_unavailable_user_gets_a_machine_readable_reason(client, auth):
    resp = await client.post("/api/audio/transcribe", headers=auth, files=_upload())
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert detail["reason"] == "no_stt_provider"
    assert "Expert mode" in detail["message"]


async def test_an_empty_recording_is_refused(client, auth):
    await _mock_stt(client, auth)
    resp = await client.post("/api/audio/transcribe", headers=auth,
                             files={"file": ("voice.webm", b"", "audio/webm")})
    assert resp.status_code == 422


async def test_an_oversized_recording_is_refused(client, auth):
    await _mock_stt(client, auth)
    from backend.api.routes.audio import MAX_AUDIO_BYTES

    resp = await client.post("/api/audio/transcribe", headers=auth,
                             files=_upload(b"x" * (MAX_AUDIO_BYTES + 1)))
    assert resp.status_code == 413
    assert "two minutes" in resp.json()["detail"]


async def test_a_provider_failure_is_reported_not_crashed(client, auth, monkeypatch):
    await _mock_stt(client, auth)

    class Broken:
        async def transcribe(self, *_a, **_k):
            raise stt_mod.SttError("upstream 500")

    async def resolve(*_a, **_k):
        return Broken()

    monkeypatch.setattr(stt_mod, "resolve_stt", resolve)
    resp = await client.post("/api/audio/transcribe", headers=auth, files=_upload())
    assert resp.status_code == 502
    assert resp.json()["detail"]["reason"] == "provider_failed"


async def test_a_recording_from_a_stopped_system_is_refused(client, auth):
    await _mock_stt(client, auth)
    await client.post("/api/settings/emergency-stop", headers=auth, json={"on": True})

    body = (await client.get("/api/audio/availability", headers=auth)).json()
    assert body["available"] is False and body["reason"] == "stopped"
    resp = await client.post("/api/audio/transcribe", headers=auth, files=_upload())
    assert resp.status_code == 503 and resp.json()["detail"]["reason"] == "stopped"


@pytest.mark.parametrize("name,expected", [
    ("voice.webm", "webm"), ("clip.ogg", "ogg"), ("note.mp3", "mp3"),
    ("thing.exe", "webm"), ("noextension", "webm"), ("../../etc/passwd", "webm"),
])
def test_the_container_extension_is_taken_from_an_allowlist(name, expected):
    from backend.api.routes.audio import _suffix

    assert _suffix(name) == expected


async def test_transcription_is_not_persisted_anywhere(client, auth, db_session):
    """The transcript is returned to the composer and forgotten — sending it is
    the user's decision, made after they have edited it."""
    from sqlalchemy import select

    from backend.core.models import ChatMessage, Document

    await _mock_stt(client, auth, "this must not be stored")
    await client.post("/api/audio/transcribe", headers=auth, files=_upload())

    messages = (await db_session.execute(select(ChatMessage))).scalars().all()
    assert messages == []
    docs = (await db_session.execute(select(Document))).scalars().all()
    assert not any("must not be stored" in (d.content_md or "") for d in docs)


# ── the audio itself is never kept ──────────────────────────────────

def test_the_route_writes_no_audio_to_disk_or_database():
    source = (REPO / "backend" / "api" / "routes" / "audio.py").read_text(encoding="utf-8")
    for token in ("open(", "db.add", "storage", "Path(", "tempfile", "aiofiles"):
        assert token not in source, token


def test_the_openai_path_streams_bytes_without_touching_disk():
    source = inspect.getsource(stt_mod.OpenAICompatibleStt.transcribe)
    assert "files=" in source
    for token in ("open(", "tempfile", "write("):
        assert token not in source, token


def test_local_whisper_always_deletes_its_temp_file():
    """The one path that must touch disk cleans up in a finally, unconditionally."""
    source = inspect.getsource(stt_mod.LocalWhisperStt.transcribe)
    assert "tempfile.mkstemp" in source
    assert "finally:" in source
    tail = source.split("finally:")[1]
    assert "os.unlink(path)" in tail


def test_the_storage_layer_is_never_imported_by_the_audio_stack():
    for path in ((REPO / "backend" / "audio").glob("*.py")):
        source = path.read_text(encoding="utf-8")
        assert "backend.storage" not in source, path.name
        assert "FileRef" not in source, path.name


# ── the client side ─────────────────────────────────────────────────

def test_the_mic_is_in_both_composers():
    for component in ("manager.tsx", "orchestrator.tsx"):
        source = (WEB / "components" / component).read_text(encoding="utf-8")
        assert "<VoiceInput" in source, component
        assert "onTranscript" in source, component


def test_a_transcript_lands_in_the_box_and_is_never_auto_sent():
    for component in ("manager.tsx", "orchestrator.tsx"):
        source = (WEB / "components" / component).read_text(encoding="utf-8")
        handler = source.split("onTranscript")[1].split("}} />")[0]
        assert "send(" not in handler, component
        assert "selectionEnd" in handler, component      # caret goes to the end


def test_the_button_refuses_to_record_what_cannot_be_transcribed():
    source = (WEB / "components" / "voice-input.tsx").read_text(encoding="utf-8")
    assert 'const mode: Mode = backendReady ? "backend" : browserReady ? "browser" : "none"' in source
    assert "disabled={disabled || unavailable" in source
    assert "Voice input needs an OpenAI key (Expert mode) or a supported browser." in source


def test_the_recorder_has_a_ceiling_an_escape_and_a_privacy_note():
    source = (WEB / "components" / "voice-input.tsx").read_text(encoding="utf-8")
    assert "const MAX_SECONDS = 120" in source
    assert 'e.key === "Escape"' in source
    assert "transcribed and then discarded" in source
    assert "MediaRecorder" in source and "audio/webm" in source
    # the mic is released on every exit path
    assert source.count("releaseMic()") >= 4


def test_a_blocked_microphone_explains_the_browser_permission():
    source = (WEB / "components" / "voice-input.tsx").read_text(encoding="utf-8")
    assert "Microphone blocked" in source
    assert "browser settings" in source


def test_no_audio_library_was_added():
    package = (WEB / "package.json").read_text(encoding="utf-8")
    for library in ("recorder", "wavesurfer", "hark", "opus", "vosk"):
        assert library not in package.lower(), library
    settings = get_settings()
    assert settings is not None      # config untouched by this feature
