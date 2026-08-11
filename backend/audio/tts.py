"""Text-to-speech provider abstraction (§24). Modes: off | voice_only | text_and_voice."""
from __future__ import annotations

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.crypto import decrypt_secret
from backend.providers import registry

TTS_MODES = ("off", "voice_only", "text_and_voice")


class TtsError(Exception):
    pass


class TextToSpeechProvider:
    async def synthesize(self, text: str, *, voice: str | None = None) -> bytes:
        raise NotImplementedError


class OpenAICompatibleTts(TextToSpeechProvider):
    def __init__(self, api_key: str, base_url: str = "https://api.openai.com/v1",
                 model: str = "gpt-4o-mini-tts", default_voice: str = "alloy"):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.default_voice = default_voice

    async def synthesize(self, text, *, voice=None) -> bytes:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{self.base_url}/audio/speech",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "input": text[:4000],
                      "voice": voice or self.default_voice, "response_format": "opus"},
            )
        if resp.status_code != 200:
            raise TtsError(f"tts provider returned {resp.status_code}")
        return resp.content


class MockTts(TextToSpeechProvider):
    async def synthesize(self, text, *, voice=None) -> bytes:
        return b"OGG-MOCK:" + text.encode("utf-8")[:200]


async def resolve_tts(db: AsyncSession, user_id: str) -> TextToSpeechProvider:
    _, row = await registry.resolve_client(db, user_id, "tts")
    cfg = row.configuration_json or {}
    if row.provider == "mock":
        return MockTts()
    secret = decrypt_secret(row.encrypted_secret) if row.encrypted_secret else ""
    base_url = cfg.get("base_url") or ("https://api.openai.com/v1" if row.provider == "openai" else None)
    if not base_url:
        raise TtsError(f"provider {row.provider} has no TTS endpoint configured")
    return OpenAICompatibleTts(secret, base_url, cfg.get("tts_model", "gpt-4o-mini-tts"),
                               cfg.get("tts_voice", "alloy"))
