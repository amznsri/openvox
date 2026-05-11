"""FastAPI app factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from openvox.api.routes import (
    agents,
    documents as documents_routes,
    health,
    playground,
    providers as providers_routes,
    rtc,
    sessions,
    skills as skills_routes,
    storage as storage_routes,
    telephony,
    templates as templates_routes,
)
from openvox.api.ws import voice as voice_ws
from openvox.config import get_settings
from openvox.db import init_db
from openvox.providers.bootstrap import register_builtins
from openvox.skills.registry import get_skill_registry

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    register_builtins()
    get_skill_registry()  # discover
    await init_db()
    logger.info("OpenVox core started — auth=%s storage=%s", settings.openvox_auth, settings.storage_backend)
    yield
    logger.info("OpenVox core shutting down")


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())

    app = FastAPI(
        title="OpenVox Core",
        description="Voice agent pipeline (STT + LLM + TTS + RTC + telephony).",
        version="0.1.0",
        lifespan=_lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # local-first — accept dashboard from any local origin
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(agents.router, prefix="/api/v1/agents", tags=["agents"])
    app.include_router(documents_routes.router, prefix="/api/v1/agents", tags=["documents"])
    app.include_router(sessions.router, prefix="/api/v1/sessions", tags=["sessions"])
    app.include_router(providers_routes.router, prefix="/api/v1/providers", tags=["providers"])
    app.include_router(skills_routes.router, prefix="/api/v1/skills", tags=["skills"])
    app.include_router(templates_routes.router, prefix="/api/v1/templates", tags=["templates"])
    app.include_router(rtc.router, prefix="/api/v1/rtc", tags=["rtc"])
    app.include_router(telephony.router, prefix="/api/v1/telephony", tags=["telephony"])
    app.include_router(playground.router, prefix="/api/v1/playground", tags=["playground"])
    app.include_router(storage_routes.router, prefix="/storage", tags=["storage"])
    app.include_router(voice_ws.router)

    return app
