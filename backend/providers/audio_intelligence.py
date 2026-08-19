"""Groq -> Audio Intelligence: real Whisper transcription/translation of a
Moseilsey-uploaded file through Groq's OpenAI-compatible audio endpoints
(console.groq.com/docs/speech-to-text, verified 2026-08).

This is a thin adapter over `backend.providers.registry.transcribe_with_groq`/
`translate_with_groq` — the Groq provider, its credential resolution, kill
switch, budget gate, the FREE_ONLY/paid usage policy gate, and LlmUsage/cost
accounting all already exist there (extended directly from the same pattern
`generate_with_x_search` established for X Intelligence), not duplicated.
What this module adds is the shape a file-based Chat tool needs: sourcing
bytes from the EXISTING attachment pipeline (FileRef + owned storage —
backend/api/routes/files.py, unrelated to and untouched by the separate
ephemeral voice-note dictation path in backend/audio/stt.py /
backend/api/routes/audio.py, which stays exactly as it was), file type/size
validation, timestamp/segment/word normalization, and structured error
states.

BYOK only (§15): the user's OWN connected Groq credential, gated by the same
usage_policy paid-capability check X Intelligence already uses for xAI — this
NEVER routes through a Moseisley-owned key or the Factory allowance.

NO ARBITRARY URL FETCHING (§8): Groq's endpoints accept a `url` field as an
alternative to `file` — never used here. Only bytes already sitting in
Moseisley's OWN storage (a FileRef the tenant owns, read via the existing
StorageAdapter) are ever sent to Groq, and always as the `file` multipart
field. A file living in external BYOS storage is refused, same restriction
the existing /files/{id}/content download route already applies (§8/§20).
"""
from __future__ import annotations

import logging
import re

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.models import FileRef
from backend.providers import registry, usage_policy
from backend.providers.clients import ProviderError
from backend.storage.base import StorageError
from backend.storage.factory import get_owned_storage

logger = logging.getLogger("mychief.audio_intelligence")

# §5: an explicit, centralized default — never scattered across call sites.
# turbo = best price/performance for ordinary transcription (current Groq
# guidance); the accuracy-sensitive full model is opt-in, never the default.
DEFAULT_MODEL = "whisper-large-v3-turbo"
ACCURATE_MODEL = "whisper-large-v3"
ALLOWED_MODELS = (DEFAULT_MODEL, ACCURATE_MODEL)

# §2/§6: current Groq-supported container formats (console.groq.com/docs/
# speech-to-text, verified 2026-08) — the same list backend/api/routes/audio.py
# already uses for the unrelated voice-note path, kept in sync deliberately.
SUPPORTED_EXTENSIONS = frozenset({
    "flac", "mp3", "mp4", "mpeg", "mpga", "m4a", "ogg", "wav", "webm",
})

# §7: Moseisley's own upload ceiling (backend/api/routes/files.py
# MAX_INLINE_BYTES) is already the binding constraint for anything reachable
# by file_id — well under Groq's documented free-tier 25MB cap, so this is a
# defense-in-depth re-check, not a new limit invented for this feature.
MAX_AUDIO_BYTES = 10 * 1024 * 1024

_LANGUAGE_RE = re.compile(r"^[a-z]{2}$")


class ProviderNotConnected(Exception):
    """Structured, actionable state (§15) — never a generic error."""


class InvalidAudioRequest(Exception):
    pass


class AttachmentNotFound(Exception):
    pass


class UnsupportedFileType(Exception):
    pass


class FileTooLarge(Exception):
    pass


class EmptyTranscript(Exception):
    """Groq genuinely returned nothing — distinct from a failure (§19)."""


def _extension_of(ref: FileRef) -> str | None:
    name = (ref.title or ref.path or "").lower()
    if "." not in name:
        return None
    return name.rsplit(".", 1)[-1].split("?")[0]


def normalize_language(language: str | None) -> str | None:
    if language is None:
        return None
    value = language.strip().lower()
    if not _LANGUAGE_RE.match(value):
        raise InvalidAudioRequest("language must be an ISO-639-1 code (e.g. 'en', 'fr')")
    return value


def _validate_model(model: str | None) -> str:
    if model is None:
        return DEFAULT_MODEL
    if model not in ALLOWED_MODELS:
        raise InvalidAudioRequest(f"model must be one of {ALLOWED_MODELS}")
    return model


async def _require_groq_connected(db: AsyncSession, user_id: str) -> None:
    row = await registry.get_provider_row(db, user_id, "groq")
    if row is None or not row.enabled or not row.encrypted_secret:
        raise ProviderNotConnected()


async def _load_attachment(db: AsyncSession, user_id: str, file_id: str) -> tuple[bytes, str]:
    """Bytes + a safe filename for a tenant-owned FileRef — never another
    tenant's file (§20), never a BYOS reference (§8/§20: no path to an
    arbitrary external fetch), and validated for type/size before a single
    byte reaches Groq."""
    ref = (await db.execute(select(FileRef).where(
        FileRef.id == file_id, FileRef.user_id == user_id,
    ))).scalar_one_or_none()
    if ref is None:
        raise AttachmentNotFound("no such attachment")
    storage = get_owned_storage()
    if ref.storage_provider != storage.provider_name:
        raise InvalidAudioRequest(
            "this file lives in external storage — only files uploaded to Moseisley "
            "can be transcribed")
    ext = _extension_of(ref)
    if ext not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFileType(
            f"unsupported file type{f' ({ext})' if ext else ''} — supported: "
            + ", ".join(sorted(SUPPORTED_EXTENSIONS)))
    if ref.size_bytes is not None and ref.size_bytes > MAX_AUDIO_BYTES:
        raise FileTooLarge(
            f"file is {ref.size_bytes / (1024 * 1024):.1f}MB — "
            f"the limit is {MAX_AUDIO_BYTES // (1024 * 1024)}MB")
    try:
        data = await storage.read(ref.path)
    except StorageError as e:
        raise AttachmentNotFound(str(e)) from e
    if len(data) > MAX_AUDIO_BYTES:
        raise FileTooLarge(
            f"file is {len(data) / (1024 * 1024):.1f}MB — "
            f"the limit is {MAX_AUDIO_BYTES // (1024 * 1024)}MB")
    filename = (ref.title or ref.path.rsplit("/", 1)[-1] or f"audio.{ext}")
    return data, filename


def _normalize_segments(data: dict) -> list[dict] | None:
    segments = data.get("segments")
    if not isinstance(segments, list) or not segments:
        return None
    out = []
    for seg in segments:
        if not isinstance(seg, dict) or "start" not in seg or "end" not in seg:
            continue
        out.append({"start_seconds": seg.get("start"), "end_seconds": seg.get("end"),
                    "text": str(seg.get("text") or "").strip()})
    return out or None


def _normalize_words(data: dict) -> list[dict] | None:
    words = data.get("words")
    if not isinstance(words, list) or not words:
        return None
    out = []
    for w in words:
        if not isinstance(w, dict) or "start" not in w or "end" not in w:
            continue
        out.append({"word": str(w.get("word") or ""), "start_seconds": w.get("start"),
                    "end_seconds": w.get("end")})
    return out or None


def _duration_of(data: dict, segments: list[dict] | None) -> float | None:
    seconds = (data.get("usage") or {}).get("seconds")
    if isinstance(seconds, (int, float)):
        return float(seconds)
    if segments:
        last_end = segments[-1].get("end_seconds")
        if isinstance(last_end, (int, float)):
            return float(last_end)
    return None


async def transcribe(
    db: AsyncSession, user_id: str, file_id: str, *,
    language: str | None = None, prompt: str | None = None, model: str | None = None,
    timestamps: bool = True, word_timestamps: bool = False,
    orchestrator_run_id: str | None = None,
) -> dict:
    """file_id (a tenant-owned attachment) -> a normalized transcript, via the
    user's OWN connected Groq credential. `timestamps` requests segment-level
    granularity (the useful default, §9); `word_timestamps` additionally asks
    for word-level only when genuinely needed (extra latency). Never
    fabricates a timestamp, language or duration Groq did not actually
    return."""
    resolved_model = _validate_model(model)
    normalized_language = normalize_language(language)
    await _require_groq_connected(db, user_id)
    audio_bytes, filename = await _load_attachment(db, user_id, file_id)

    granularities: list[str] = []
    if timestamps or word_timestamps:
        granularities.append("segment")
    if word_timestamps:
        granularities.append("word")
    response_format = "verbose_json" if granularities else "json"

    data = await registry.transcribe_with_groq(
        db, user_id, audio_bytes, filename=filename, model=resolved_model,
        language=normalized_language, prompt=prompt,
        timestamp_granularities=granularities or None, response_format=response_format,
        run_id=orchestrator_run_id)

    text = str(data.get("text") or "").strip()
    segments = _normalize_segments(data) if timestamps or word_timestamps else None
    words = _normalize_words(data) if word_timestamps else None
    if not text and not segments:
        raise EmptyTranscript("no speech was detected in this file")

    return {
        "text": text,
        "language": data.get("language"),
        "duration": _duration_of(data, segments),
        "segments": segments,
        "words": words,
        "provider": "groq",
        "model": resolved_model,
        "translated": False,
    }


async def translate(
    db: AsyncSession, user_id: str, file_id: str, *,
    prompt: str | None = None, model: str | None = None,
    orchestrator_run_id: str | None = None,
) -> dict:
    """file_id -> an English translation of its spoken content (§10) — never
    confused with transcription. No `language`/timestamp granularity is ever
    sent; the endpoint does not accept them."""
    resolved_model = _validate_model(model)
    await _require_groq_connected(db, user_id)
    audio_bytes, filename = await _load_attachment(db, user_id, file_id)

    data = await registry.translate_with_groq(
        db, user_id, audio_bytes, filename=filename, model=resolved_model,
        prompt=prompt, run_id=orchestrator_run_id)

    text = str(data.get("text") or "").strip()
    if not text:
        raise EmptyTranscript("no speech was detected in this file")

    return {
        "text": text,
        "language": "en",
        "duration": _duration_of(data, None),
        "segments": None,
        "words": None,
        "provider": "groq",
        "model": resolved_model,
        "translated": True,
    }


def error_detail(exc: Exception) -> dict:
    """Map any exception transcribe()/translate() can raise into a clean,
    structured, user-facing detail — never a raw provider stack trace,
    multipart body or key (§19/§20)."""
    if isinstance(exc, (ProviderNotConnected, registry.NoProviderAvailable)):
        return {"state": "provider_not_connected",
                "message": "Connect Groq in Connections to transcribe audio."}
    if isinstance(exc, AttachmentNotFound):
        return {"state": "invalid_request",
                "message": "That file wasn't found — it may have been deleted, or it "
                           "belongs to someone else."}
    if isinstance(exc, UnsupportedFileType):
        return {"state": "invalid_file_type", "message": str(exc)}
    if isinstance(exc, FileTooLarge):
        return {"state": "file_too_large", "message": str(exc)}
    if isinstance(exc, InvalidAudioRequest):
        return {"state": "invalid_request", "message": str(exc)}
    if isinstance(exc, EmptyTranscript):
        return {"state": "empty_transcript", "message": str(exc)}
    if isinstance(exc, httpx.TimeoutException):
        return {"state": "provider_timeout", "message": "Groq timed out — try again shortly."}
    if isinstance(exc, usage_policy.PaidCapabilityBlocked):
        return {"state": "paid_capability_blocked", "message": str(exc)}
    if isinstance(exc, usage_policy.ApprovalRequired):
        return {"state": "approval_required", "message": str(exc)}
    if isinstance(exc, ProviderError):
        status = exc.status_code
        body = (getattr(exc, "body_text", "") or "").lower()
        headers = getattr(exc, "headers", None)
        if status in (401, 403):
            return {"state": "provider_key_invalid",
                    "message": "Groq rejected the connected key — reconnect it in Connections."}
        if status == 413:
            return {"state": "file_too_large", "message": "Groq rejected this file as too large."}
        if status == 429:
            remaining_requests = headers.get("x-ratelimit-remaining-requests") if headers else None
            if remaining_requests == "0":
                return {"state": "quota_exhausted", "message": "Groq's daily quota is exhausted."}
            return {"state": "rate_limited",
                    "message": "Groq's rate limit was reached — try again shortly."}
        if status == 404 and "model" in body:
            return {"state": "capability_unavailable",
                    "message": "The configured Groq model doesn't support audio."}
        if status in (400, 422):
            return {"state": "invalid_request", "message": "Groq rejected this request."}
        if status is not None and status >= 500:
            return {"state": "provider_unavailable",
                    "message": "Groq is temporarily unavailable — try again shortly."}
        return {"state": "transcription_failed", "message": "Groq could not transcribe this file."}
    return {"state": "error", "message": "Could not process this audio."}
