"""FastAPI application factory."""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.core import killswitch
from backend.core.config import get_settings
from backend.core.logging import setup_logging
from backend.providers.registry import LlmBudgetExceeded, NoProviderAvailable

logger = logging.getLogger("mychief.api")


def create_app() -> FastAPI:
    setup_logging()
    settings = get_settings()
    app = FastAPI(title="Moseisley.sh API", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(killswitch.KillSwitchEngaged)
    async def kill_switch_handler(request: Request, exc: killswitch.KillSwitchEngaged):
        return JSONResponse(status_code=423, content={"detail": str(exc), "switch": exc.switch})

    @app.exception_handler(NoProviderAvailable)
    async def no_provider_handler(request: Request, exc: NoProviderAvailable):
        return JSONResponse(status_code=424, content={"detail": str(exc)})

    @app.exception_handler(LlmBudgetExceeded)
    async def llm_budget_handler(request: Request, exc: LlmBudgetExceeded):
        return JSONResponse(status_code=429, content={"detail": str(exc)})

    @app.on_event("startup")
    async def seed_reference_data():
        from backend.core.db import get_sessionmaker
        from backend.providers.pricing import seed_pricing

        try:
            async with get_sessionmaker()() as db:
                await seed_pricing(db)
                await db.commit()
        except Exception:
            logger.warning("pricing seed skipped (db not ready)")

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "service": "mychief-api", "version": "0.1.0"}

    from backend.api.routes import (
        activity,
        agents,
        auditor,
        auth,
        autopilot,
        billing,
        chat,
        decisions,
        dev,
        documents,
        experiments,
        files,
        friends,
        goals,
        instructions,
        integrations,
        manager,
        market,
        memory,
        metrics,
        orchestrator,
        projects,
        providers,
        search,
        telegram,
        today,
        treasury,
        usage,
        xray,
    )
    from backend.api.routes import settings as settings_routes

    for r in (
        auth.router, providers.router, settings_routes.router, activity.router,
        documents.router, goals.router, chat.router, decisions.router, telegram.router,
        integrations.router, xray.router, autopilot.router, today.router, market.router,
        agents.router, experiments.router, treasury.router, auditor.router,
        files.router, search.router, orchestrator.router, memory.router,
        usage.router, billing.router, projects.router, metrics.router,
        instructions.router, manager.router, dev.router,
        friends.router, friends.public_router,
    ):
        app.include_router(r, prefix="/api")

    return app


app = create_app()
