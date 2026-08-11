"""Embedding provider abstraction (architecture update).

Embeddings are OPTIONAL and generated only on explicit request — never
indiscriminately. Provider/model are stored alongside generated vectors so stored
documents are never permanently coupled to one vendor.
"""
from __future__ import annotations

import hashlib

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.crypto import decrypt_secret
from backend.providers import registry
from backend.providers.clients import ProviderError


class EmbeddingProvider:
    provider_name = "base"
    model = ""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class OpenAICompatibleEmbeddings(EmbeddingProvider):
    provider_name = "openai_compatible"

    def __init__(self, api_key: str, base_url: str = "https://api.openai.com/v1",
                 model: str = "text-embedding-3-small"):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self.base_url}/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "input": texts},
            )
        if resp.status_code != 200:
            raise ProviderError(f"embedding provider returned {resp.status_code}")
        data = resp.json()
        return [item["embedding"] for item in data["data"]]


class MockEmbeddings(EmbeddingProvider):
    """Deterministic 8-dim embeddings for tests/offline use."""

    provider_name = "mock"
    model = "mock-embed-1"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            h = hashlib.sha256(t.encode()).digest()
            out.append([b / 255.0 for b in h[:8]])
        return out


async def resolve_embeddings(db: AsyncSession, user_id: str) -> EmbeddingProvider:
    _, row = await registry.resolve_client(db, user_id, "classification")
    cfg = row.configuration_json or {}
    if row.provider == "mock":
        return MockEmbeddings()
    secret = decrypt_secret(row.encrypted_secret) if row.encrypted_secret else ""
    base_url = cfg.get("base_url") or ("https://api.openai.com/v1" if row.provider == "openai" else None)
    if not base_url:
        raise ProviderError(f"provider {row.provider} has no embeddings endpoint")
    return OpenAICompatibleEmbeddings(secret, base_url,
                                      cfg.get("embedding_model", "text-embedding-3-small"))
