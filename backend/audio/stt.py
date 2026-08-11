"""Speech-to-text provider abstraction (§23).

Providers: OpenAI-compatible transcription API, optional local Whisper
(self-hosted, if the `whisper` package is installed), deterministic mock for tests.
Business logic never depends on an exact model version.
"""
from __future__ import annotations

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.crypto import decrypt_secret
from backend.providers import registry


class SttError(Exception):
    pass


class SpeechToTextProvider:
    async def transcribe(self, audio_bytes: bytes, *, filename: str = "audio.ogg", language: str | None = None) -> str:
        raise NotImplementedError


class OpenAICompatibleStt(SpeechToTextProvider):
    def __init__(self, api_key: str, base_url: str = "https://api.openai.com/v1", model: str = "whisper-1"):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model

    async def transcribe(self, audio_bytes, *, filename="audio.ogg", language=None) -> str:
        data = {"model": self.model}
        if language:
            data["language"] = language
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{self.base_url}/audio/transcriptions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                data=data,
                files={"file": (filename, audio_bytes)},
            )
        if resp.status_code != 200:
            raise SttError(f"stt provider returned {resp.status_code}")
        return resp.json().get("text", "")


class LocalWhisperStt(SpeechToTextProvider):
    """Optional local Whisper for self-hosted deployments. Requires `pip install openai-whisper`."""

    def __init__(self, model_name: str = "base"):
        self.model_name = model_name
        self._model = None

    async def transcribe(self, audio_bytes, *, filename="audio.ogg", language=None) -> str:
        import asyncio
        import os
        import tempfile

        try:
            import whisper  # type: ignore
        except ImportError as e:  # pragma: no cover
            raise SttError("local whisper not installed (pip install openai-whisper)") from e
        if self._model is None:
            self._model = whisper.load_model(self.model_name)
        fd, path = tempfile.mkstemp(suffix=os.path.splitext(filename)[1] or ".ogg")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(audio_bytes)
            result = await asyncio.to_thread(self._model.transcribe, path, language=language)
            return result.get("text", "").strip()
        finally:
            if os.path.exists(path):
                os.unlink(path)


class MockStt(SpeechToTextProvider):
    def __init__(self, transcript: str = ""):
        self.transcript = transcript

    async def transcribe(self, audio_bytes, *, filename="audio.ogg", language=None) -> str:
        return self.transcript or audio_bytes.decode("utf-8", errors="ignore")


async def resolve_stt(db: AsyncSession, user_id: str) -> SpeechToTextProvider:
    """Resolve the STT provider through the registry routing for purpose 'stt'."""
    _, row = await registry.resolve_client(db, user_id, "stt")
    cfg = row.configuration_json or {}
    if row.provider == "mock":
        return MockStt(cfg.get("transcript", ""))
    if cfg.get("stt_engine") == "local_whisper":
        return LocalWhisperStt(cfg.get("stt_model", "base"))
    secret = decrypt_secret(row.encrypted_secret) if row.encrypted_secret else ""
    base_url = cfg.get("base_url") or ("https://api.openai.com/v1" if row.provider == "openai" else None)
    if not base_url:
        raise SttError(f"provider {row.provider} has no STT endpoint configured")
    return OpenAICompatibleStt(secret, base_url, cfg.get("stt_model", "whisper-1"))
