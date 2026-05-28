"""FastAPI app factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from openvox.api.routes import (
    admin as admin_routes,
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
from openvox.api.routes.integrations import google as integrations_google
from openvox import __version__
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


async def _hydrate_secrets_into_env() -> None:
    """Copy keys from the encrypted secrets store into os.environ.

    Bridges Phase 3 (the first-run wizard at /api/v1/admin/setup/keys
    that writes provider keys into the SQLite-backed encrypted store)
    with the rest of the codebase (provider modules that still read
    `settings.<provider>_<key>_api_key`, which is a pydantic-settings
    field hydrated from env vars / .env at process start).

    Without this bridge, every key entered via the wizard is invisible
    to providers — the TTS / LLM / STT modules report "API key not set"
    even though the user just typed one in. Surfaced by an actual
    end-user smoke test on 2026-05-24 after the v0.1.7 release.

    Resolution order is env-first (so Docker / .env workflows are
    unaffected) then store. After injection we bust `get_settings`'s
    lru_cache so subsequent calls re-read os.environ and pick up the
    just-injected values.

    Keep the (provider, key_name, env_var) mapping in sync with the
    fields declared in `packages/core/openvox/config.py` and the keys
    the wizard accepts in `api/routes/admin.py`.
    """
    import os

    from openvox import secrets as secret_store
    from openvox.config import get_settings as _get_settings

    # (provider, key_name) → ENV_VAR_NAME. provider+key_name is what
    # the wizard uses; ENV_VAR_NAME is what Settings reads via
    # pydantic-settings (case-insensitive, but we use the canonical
    # form here for greppability).
    mapping = [
        # BytePlus — LLM + voice (TTS/STT) keys
        (("byteplus", "llm_api_key"), "BYTEPLUS_LLM_API_KEY"),
        (("byteplus", "voice_api_key"), "BYTEPLUS_VOICE_API_KEY"),
        (("byteplus", "rtc_app_id"), "BYTEPLUS_RTC_APP_ID"),
        (("byteplus", "rtc_app_key"), "BYTEPLUS_RTC_APP_KEY"),
        # OpenAI — single key serves LLM + TTS + S2S (Realtime)
        (("openai", "api_key"), "OPENAI_API_KEY"),
        # Anthropic / Gemini / DeepSeek — LLM
        (("anthropic", "api_key"), "ANTHROPIC_API_KEY"),
        (("gemini", "api_key"), "GEMINI_API_KEY"),
        (("deepseek", "api_key"), "DEEPSEEK_API_KEY"),
        # Voice providers
        (("elevenlabs", "api_key"), "ELEVENLABS_API_KEY"),
        (("cartesia", "api_key"), "CARTESIA_API_KEY"),
        (("deepgram", "api_key"), "DEEPGRAM_API_KEY"),
        (("assemblyai", "api_key"), "ASSEMBLYAI_API_KEY"),
        # Telephony / channels
        (("twilio", "account_sid"), "TWILIO_ACCOUNT_SID"),
        (("twilio", "auth_token"), "TWILIO_AUTH_TOKEN"),
        # Google OAuth client (Phase 1 Native Connect Gmail / Calendar /
        # Contacts). Without these the dashboard's Integrations tab
        # shows "Google OAuth client not configured" + a disabled
        # Connect Gmail button. The maintainer typically pastes these
        # via the dashboard Settings page (which writes to the
        # encrypted store); the hydration step here re-exports them
        # as env vars so `Settings.google_oauth_client_id/_secret`
        # picks them up after the lru_cache bust below.
        (("google", "oauth_client_id"), "GOOGLE_OAUTH_CLIENT_ID"),
        (("google", "oauth_client_secret"), "GOOGLE_OAUTH_CLIENT_SECRET"),
    ]

    hydrated: list[str] = []
    for (provider, key_name), env_var in mapping:
        if os.environ.get(env_var, "").strip():
            continue  # env wins; user explicitly set this
        try:
            stored = await secret_store.get_provider_key(provider, key_name)
        except Exception as e:
            # Don't let a missing table / decryption failure block startup.
            logger.warning("hydrate_secrets: %s.%s lookup failed: %s", provider, key_name, e)
            continue
        if stored:
            os.environ[env_var] = stored
            hydrated.append(env_var)

    if hydrated:
        # Critical: bust the settings cache so any subsequent
        # get_settings() call re-reads os.environ. Without this, the
        # already-cached Settings instance keeps the empty values it
        # was constructed with.
        _get_settings.cache_clear()
        # Phase 4: now visible in `openvox logs` because run.py
        # calls logging.basicConfig() before uvicorn boots, and
        # uvicorn's dictConfig has disable_existing_loggers=False.
        # The print() workaround that lived here in v0.1.6-v0.1.8 is
        # gone; logger.info() is the canonical path.
        logger.info("hydrated %d secrets from encrypted store: %s",
                    len(hydrated), ", ".join(hydrated))


@asynccontextmanager
async def _lifespan(app: FastAPI):
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    # CRITICAL ORDER: init_db → hydrate_secrets → register_builtins.
    # register_builtins() instantiates each provider, and the providers
    # cache settings.<provider>_api_key in their __init__ — so the env
    # MUST be hydrated before that runs. Otherwise the wizard-entered
    # keys are invisible to providers until the next process restart.
    # (This bit us in v0.1.7: wizard saved keys, daemon started,
    # /api/v1/playground/synthesize returned 400 "API_KEY not set".)
    await init_db()
    await _hydrate_secrets_into_env()
    register_builtins()
    get_skill_registry()  # discover
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
        # Single source of truth — `openvox/__init__.py:__version__`.
        # Previously this was hardcoded and drifted out of sync with
        # the package + pyproject.toml during the v0.2.6 → v0.2.10
        # release sweep (CLAUDE.md v0.2.11 release notes). Reading
        # from __version__ keeps it pinned to the same string that
        # /health reports + that pip resolves at install time.
        version=__version__,
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
    app.include_router(admin_routes.router, prefix="/api/v1/admin", tags=["admin"])
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
    # Per-provider OAuth integrations (Phase 1.2). The API router goes
    # under /api/v1/integrations/google/* for management; the callback
    # router is mounted at the root so Google can redirect to
    # http://localhost:<port>/oauth/google/callback (the URI we
    # registered with the Cloud Console).
    app.include_router(
        integrations_google.api_router,
        prefix="/api/v1/integrations/google",
        tags=["integrations"],
    )
    app.include_router(integrations_google.oauth_callback_router, tags=["integrations"])
    app.include_router(voice_ws.router)
    app.include_router(twilio_ws.router)

    # ── Optional static-dashboard serving ───────────────────────────
    # Phase 1 PR-3 scaffolding: when the dashboard has been built with
    # BUILD_OUTPUT=export (Next.js static export → `out/` directory),
    # FastAPI serves it at /dashboard/* on the same port as the API.
    # This is what enables the single-process CLI experience
    # (`openvox run` → one process, browser opens to localhost:8000/dashboard).
    #
    # Discovery order — first existing path wins:
    #   1. OPENVOX_DASHBOARD_PATH env var (explicit override)
    #   2. /app/dashboard_static/    (Docker/CLI install — bundled at build)
    #   3. ../apps/dashboard/out/    (repo-relative dev workflow)
    #
    # If none of the above exist, the mount is silently skipped —
    # browsers hitting /dashboard get 404, which is correct for Docker
    # mode (where the separate `openvox-dashboard` container handles it).
    #
    # Today this is no-op: the dashboard's static-export build pipeline
    # ships in a follow-up commit (the agents/[id] route needs to be
    # refactored to query params first — Next.js static export can't
    # handle dynamic path params for runtime-created IDs). The mount
    # itself is committed now so we don't have to revisit api/app.py
    # in that follow-up — just produce the out/ directory and it
    # gets served automatically.
    _maybe_mount_dashboard_static(app)

    return app


def _maybe_mount_dashboard_static(app: FastAPI) -> None:
    """Mount the built dashboard static files under /dashboard/* if available.

    See the comment block in create_app() above for the discovery
    rules + why this is a no-op today. Pulled into its own function so
    it can be unit-tested + so the mount logic doesn't clutter the
    main app factory.
    """
    import os
    from pathlib import Path

    from fastapi.staticfiles import StaticFiles

    explicit = os.environ.get("OPENVOX_DASHBOARD_PATH")
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    candidates.append(Path("/app/dashboard_static"))
    # Wheel-bundled — `pip install openvox-core` ships the dashboard at
    # openvox/_dashboard/ via [tool.hatch.build.targets.wheel.force-
    # include] in pyproject.toml. This is the path that makes
    # `pipx install openvox-core && openvox start && open
    # localhost:8000/dashboard` actually serve the UI.
    candidates.append(Path(__file__).resolve().parent.parent / "_dashboard")
    # Repo-relative — works in `openvox run` invoked from the repo root
    # (editable install / contributor workflow).
    candidates.append(Path(__file__).resolve().parent.parent.parent.parent.parent
                      / "apps" / "dashboard" / "out")

    for path in candidates:
        if path.is_dir() and (path / "index.html").exists():
            # Two separate mounts to get URL routing right with a
            # default-config Next.js static export:
            #
            # 1. /_next/* → out/_next/*
            #    Next.js's static export hard-codes asset URLs as
            #    `/_next/static/...` (root-relative). Without this
            #    mount, every CSS/JS file 404s and the dashboard
            #    renders as unstyled HTML.
            #
            # 2. /dashboard/* → out/dashboard/*
            #    The Next.js route `/dashboard` lives at
            #    out/dashboard/index.html, NOT out/index.html (which
            #    is the marketing landing page from app/page.tsx).
            #    Mounting at out/ directly would serve the landing
            #    page when the user hits /dashboard. Pointing the
            #    mount at out/dashboard/ instead serves the actual
            #    dashboard.
            nextjs_assets = path / "_next"
            if nextjs_assets.is_dir():
                app.mount(
                    "/_next",
                    StaticFiles(directory=str(nextjs_assets)),
                    name="dashboard-assets",
                )

            dashboard_pages = path / "dashboard"
            if dashboard_pages.is_dir():
                app.mount(
                    "/dashboard",
                    StaticFiles(directory=str(dashboard_pages), html=True),
                    name="dashboard",
                )

            # Landing page at /. Serves the marketing-style
            # apps/dashboard/src/app/page.tsx export so a user who
            # types `localhost:8000/` sees a real page (with the
            # "Open dashboard" button) instead of a JSON blob or 404.
            # Assets resolve via the /_next mount above; nav links
            # in the page (which point at /dashboard/...) resolve
            # via the /dashboard mount.
            landing = path / "index.html"
            if landing.exists():
                from fastapi.responses import FileResponse

                @app.get("/", include_in_schema=False)
                async def _root_landing() -> FileResponse:
                    return FileResponse(str(landing))

            logger.info("dashboard mounted: /dashboard/* + /_next/* from %s", path)
            return

    # Not found — that's fine in Docker mode where the separate
    # `openvox-dashboard` container serves the dashboard on its own port.
    logger.debug(
        "no dashboard static files found — /dashboard will 404. "
        "Either set OPENVOX_DASHBOARD_PATH or use Docker dashboard service."
    )
