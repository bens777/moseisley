"""Voice input: transcribe a recording, return text, keep nothing.

The audio never touches disk or the database here. It arrives in memory, goes
to whichever STT provider the user's routing resolves, and the bytes are gone
when the request ends. Only the transcript is returned, and even that is not
stored — the client puts it in the composer and the user decides what to do.

Availability is a first-class answer rather than a failure: STT routing accepts
openai / custom / mock only and deliberately bypasses factory (ROOKIE) mode, so
plenty of users have no backend transcription. The UI asks before it records.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, File, HTTPException, UploadFile

from backend.audio import stt as stt_mod
from backend.core import killswitch
from backend.core.security import DB, CurrentUser
from backend.providers import registry

logger = logging.getLogger("mychief.audio")

router = APIRouter(prefix="/audio")

MAX_AUDIO_BYTES = 10 * 1024 * 1024        # ~2 minutes of opus, generously
# What OpenAI-compatible /audio/transcriptions accepts. The extension we forward
# is how the provider identifies the container, so it has to be a real one.
ALLOWED_SUFFIXES = {"webm", "ogg", "oga", "mp3", "mp4", "m4a", "mpga", "mpeg", "wav", "flac"}
DEFAULT_SUFFIX = "webm"                   # MediaRecorder's default in every browser we target

UNAVAILABLE_REASON = (
    "Voice input needs a provider that can transcribe: an OpenAI key (Expert mode) "
    "or a custom endpoint. Platform AI (Rookie) and OpenRouter (Dev) do not offer "
    "speech-to-text."
)


def _suffix(filename: str | None) -> str:
    if filename and "." in filename:
        candidate = filename.rsplit(".", 1)[-1].lower().split("?")[0]
        if candidate in ALLOWED_SUFFIXES:
            return candidate
    return DEFAULT_SUFFIX


@router.get("/availability")
async def availability(user: CurrentUser, db: DB):
    """Can this user transcribe server-side? Answered before anything records."""
    try:
        _client, row = await registry.resolve_client(db, user.id, "stt")
    except registry.NoProviderAvailable:
        return {"available": False, "reason": "no_stt_provider", "provider": None,
                "detail": UNAVAILABLE_REASON}
    except killswitch.KillSwitchEngaged:
        return {"available": False, "reason": "stopped", "provider": None,
                "detail": "Your systems are stopped. Release the emergency stop first."}
    return {"available": True, "reason": None, "provider": row.provider,
            "detail": "Audio is transcribed and discarded — nothing is stored."}


@router.post("/transcribe")
async def transcribe(user: CurrentUser, db: DB, file: UploadFile = File(...),
                     language: str | None = None):
    audio = await file.read()
    if not audio:
        raise HTTPException(422, "empty recording")
    if len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "recording too long — keep it under two minutes")

    try:
        provider = await stt_mod.resolve_stt(db, user.id)
    except registry.NoProviderAvailable as e:
        # machine-readable so the UI can explain rather than just fail
        raise HTTPException(status_code=503, detail={
            "reason": "no_stt_provider", "message": UNAVAILABLE_REASON}) from e
    except killswitch.KillSwitchEngaged as e:
        raise HTTPException(status_code=503, detail={
            "reason": "stopped", "message": "Your systems are stopped."}) from e

    try:
        text = await provider.transcribe(
            audio, filename=f"voice.{_suffix(file.filename)}", language=language)
    except stt_mod.SttError as e:
        logger.warning("stt failed for user %s: %s", user.id, e)
        raise HTTPException(status_code=502, detail={
            "reason": "provider_failed", "message": "Transcription failed. Try again, "
                                                    "or type it instead."}) from e
    finally:
        del audio          # the bytes are not kept, logged, or written anywhere

    return {"text": (text or "").strip()}
