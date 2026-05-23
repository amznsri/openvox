"""FastAPI app factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from openvox.api.routes import (
    agents,
    auth as auth_routes,
    documents as documents_routes,
    evals as evals_routes,
    health,
    jobs as jobs_routes,
    mcp as mcp_routes,
    playground,
    pricing as pricing_routes,
    providers as providers_routes,
    rtc,
    sessions,
    skills as skills_routes,
    storage as storage_routes,
    telephony,
    templates as templates_routes,
)
from openvox.api.ws import twilio_stream as twilio_ws
from openvox.api.ws import voice as voice_ws
from openvox.config import get_settings
from openvox.db import init_db
from openvox.providers.bootstrap import register_builtins
from openvox.scheduler import start_scheduler, stop_scheduler
from openvox.skills.registry import get_skill_registry
from openvox.skills.watcher import start_watcher, stop_watcher

logger = logging.getLogger(__name__)


async def _seed_builtin_personas() -> None:
    """Upsert the built-in synthetic personas on startup.

    We upsert by id so edits to the prompt in eval/personas.py take
    effect without manual intervention, but user-edited personas
    (different id) are left alone.
    """
    from openvox.db import db_session
    from openvox.db.models import Persona
    from openvox.eval.personas import BUILTIN_PERSONAS

    async with db_session() as s:
        for entry in BUILTIN_PERSONAS:
            existing = await s.get(Persona, entry["id"])
            if existing is None:
                p = Persona(
                    id=entry["id"],
                    name=entry["name"],
                    description=entry.get("description", ""),
                    system_prompt=entry["system_prompt"],
                    tags=entry.get("tags", []),
                    builtin=True,
                )
                s.add(p)
            else:
                # Refresh prompt + tags on every boot so prompt iterations stick.
                existing.name = entry["name"]
                existing.description = entry.get("description", "")
                existing.system_prompt = entry["system_prompt"]
                existing.tags = entry.get("tags", [])
                existing.builtin = True


@asynccontextmanager
async def _lifespan(app: FastAPI):
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    register_builtins()
    get_skill_registry()  # discover
    await init_db()
    await _seed_builtin_personas()
    await start_scheduler()
    await start_watcher()  # hot-reload skills dropped in ~/.openvox/skills/
    # Phase 2: start telegram polling tasks for any agent connected
    # in polling mode. Webhook-mode agents bootstrap themselves on the
    # next incoming Telegram POST — no startup action needed.
    from openvox.telephony.telegram_polling import start_all_pollers, stop_all_pollers
    await start_all_pollers()
    # WhatsApp Personal: reconnect bridge sessions for any agent whose
    # channels.whatsapp_personal.enabled == true. No-op if the bridge
    # container isn't running (opt-in via --profile whatsapp).
    from openvox.telephony.whatsapp_personal import (
        start_all_sessions as wpp_start_all,
        stop_all_sessions as wpp_stop_all,
    )
    await wpp_start_all()
    logger.info("OpenVox core started — auth=%s storage=%s", settings.openvox_auth, settings.storage_backend)
    yield
    await wpp_stop_all()
    await stop_all_pollers()
    await stop_watcher()
    await stop_scheduler()
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
    app.include_router(auth_routes.router, prefix="/api/v1/auth", tags=["auth"])
    app.include_router(agents.router, prefix="/api/v1/agents", tags=["agents"])
    app.include_router(documents_routes.router, prefix="/api/v1/agents", tags=["documents"])
    app.include_router(sessions.router, prefix="/api/v1/sessions", tags=["sessions"])
    app.include_router(providers_routes.router, prefix="/api/v1/providers", tags=["providers"])
    app.include_router(skills_routes.router, prefix="/api/v1/skills", tags=["skills"])
    app.include_router(templates_routes.router, prefix="/api/v1/templates", tags=["templates"])
    app.include_router(rtc.router, prefix="/api/v1/rtc", tags=["rtc"])
    app.include_router(jobs_routes.router, prefix="/api/v1/jobs", tags=["jobs"])
    app.include_router(mcp_routes.router, prefix="/api/v1/mcp", tags=["mcp"])
    app.include_router(telephony.router, prefix="/api/v1/telephony", tags=["telephony"])
    app.include_router(pricing_routes.router, prefix="/api/v1/pricing", tags=["pricing"])
    app.include_router(evals_routes.router, prefix="/api/v1/evals", tags=["evals"])
    app.include_router(playground.router, prefix="/api/v1/playground", tags=["playground"])
    app.include_router(storage_routes.router, prefix="/storage", tags=["storage"])
    app.include_router(voice_ws.router)
    app.include_router(twilio_ws.router)

    return app
