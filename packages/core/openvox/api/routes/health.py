from fastapi import APIRouter

from openvox import __version__

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "version": __version__}


# Note: `/` is intentionally NOT registered here. When a bundled
# dashboard is found, `_maybe_mount_dashboard_static` in api/app.py
# serves the static landing page (apps/dashboard's out/index.html)
# at `/`. When no dashboard is bundled (raw API-only install), `/`
# returns FastAPI's default 404 — the user knows to hit `/health`
# or `/docs` directly.
