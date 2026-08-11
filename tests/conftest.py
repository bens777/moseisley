from __future__ import annotations

import os

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Test environment must be configured before backend imports.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("APP_SECRET", "test-secret-key-32-bytes-long-ok!")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("TELEGRAM_MODE", "disabled")
os.environ.setdefault("EMAIL_PROVIDER", "memory")
os.environ.setdefault(
    "STORAGE_LOCAL_PATH",
    "/tmp/claude-1002/-home-mychief-mychief/03cfc465-68d1-4302-9d31-f76ea75c05d0"
    "/scratchpad/test-storage",
)

# Money-touching platform credentials are pinned OFF (hard override, not
# setdefault: a deployment .env would otherwise supply the real values). Without
# this, a test with no connected provider routes through factory mode and bills
# the operator's live OpenRouter key with real prompts. Tests that need these
# monkeypatch them explicitly.
os.environ["FACTORY_OPENROUTER_API_KEY"] = ""
os.environ["STRIPE_API_KEY"] = ""
os.environ["STRIPE_PRICE_ID_ALE"] = ""
os.environ["STRIPE_PRICE_ID_PUNCH"] = ""
os.environ["STRIPE_PRICE_ID_ROUND"] = ""
os.environ["STRIPE_PORTAL_RETURN_URL"] = ""

from backend.core.config import get_settings  # noqa: E402
from backend.core.db import get_engine, get_sessionmaker, reset_engine  # noqa: E402
from backend.core.email import MemoryEmailProvider, reset_email_provider  # noqa: E402
from backend.core.models import Base  # noqa: E402

get_settings.cache_clear()

# Stripe price ids must be genuinely UNSET (None), not "" — plan derivation
# distinguishes "no basic price configured" from "empty". An env override can
# only express "", so pin them on the cached settings object instead; a
# deployment .env would otherwise leak real price ids into plan assertions.
_settings = get_settings()
_settings.stripe_price_id_basic = None
_settings.stripe_price_id_pro = None
_settings.stripe_price_id = None

TEST_PASSWORD = "correct-horse-battery"


@pytest_asyncio.fixture()
async def db_setup():
    import shutil

    from backend.core.auth import reset_rate_limiter
    from backend.storage.factory import reset_owned_storage

    reset_engine()
    reset_email_provider()
    reset_rate_limiter()
    reset_owned_storage()
    MemoryEmailProvider.sent.clear()
    shutil.rmtree(os.environ["STORAGE_LOCAL_PATH"], ignore_errors=True)
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    from backend.providers.pricing import seed_pricing

    async with get_sessionmaker()() as session:
        await seed_pricing(session)
        await session.commit()
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
    reset_engine()


@pytest_asyncio.fixture()
async def client(db_setup):
    from backend.api.app import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture()
async def db_session(db_setup):
    async with get_sessionmaker()() as session:
        yield session


async def auth_headers(client: AsyncClient, email: str = "founder@example.com") -> dict:
    """Register (idempotent) + login through the real self-hosted auth flow."""
    await client.post("/api/auth/register",
                      json={"email": email, "password": TEST_PASSWORD})
    resp = await client.post("/api/auth/login",
                             data={"username": email, "password": TEST_PASSWORD})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest_asyncio.fixture()
async def auth(client):
    return await auth_headers(client)


async def setup_mock_provider(client: AsyncClient, headers: dict, responses: dict | None = None):
    resp = await client.post(
        "/api/providers",
        json={"provider": "mock", "api_key": "mock", "configuration": {"responses": responses or {}}},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()
