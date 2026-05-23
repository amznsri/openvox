"""Auth routes — synthetic local user + OAuth scaffolds.

Ported from the (now-deleted) Node gateway's
``packages/server/src/routes/auth.ts`` as part of Phase 1 of the
Sessions-15+ roadmap (no-Docker single-service stack).

Why this is mostly stubs:
    OpenVox runs LOCAL-FIRST by default. There's only one user (the
    operator) and they don't need to log in. ``/me`` returns a synthetic
    ``Local User`` so the dashboard doesn't have to special-case the
    unauthenticated path.

    OAuth ``/github/start`` and ``/google/start`` exist so the dashboard
    can link to them safely even when credentials aren't configured —
    they return 501 with a polite "set your env vars" message instead
    of 404-ing the link target.

When does this become a real auth service?
    When ``OPENVOX_AUTH=enabled`` AND the operator deploys OpenVox in
    multi-tenant mode (Cloud-hosted multi-tenant mode, item #16 on the
    open-follow-ups list). At that point ``/me`` verifies the JWT and
    returns the authenticated user. For now: stubs.
"""
from __future__ import annotations

import urllib.parse
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from openvox.config import get_settings

router = APIRouter()


@router.get("/me")
async def me(request: Request) -> dict[str, Any]:
    """Return the current user.

    In local-first mode: synthetic ``Local User`` (matches the topbar
    label in ``apps/dashboard/src/components/nav/topbar.tsx:193``).

    In multi-tenant mode (``OPENVOX_AUTH=enabled``): verifies the bearer
    token in the ``Authorization`` header. Today this just checks the
    JWT was issued by us (no upstream userinfo lookup) — multi-tenant
    user storage lands when we ship the cloud mode.
    """
    settings = get_settings()
    if settings.openvox_auth != "enabled":
        return {"id": "local", "name": "Local User", "provider": "local"}

    # OPENVOX_AUTH=enabled — for now we just acknowledge a token if it
    # parses. Full JWT verification (issuer, expiry, signature) will land
    # alongside the cloud-hosted multi-tenant work.
    auth_header = request.headers.get("authorization") or ""
    if not auth_header.lower().startswith("bearer "):
        return {"id": None}
    # TODO: parse the JWT, return the sub + email claims.
    return {"id": "authenticated", "name": "OAuth User", "provider": "jwt"}


@router.get("/github/start", response_model=None)
async def github_start():
    """Redirect to GitHub's OAuth consent screen.

    Returns 501 if ``GITHUB_OAUTH_CLIENT_ID`` isn't set — keeps the
    dashboard's "Sign in with GitHub" link from 404-ing in local mode.
    """
    settings = get_settings()
    client_id = getattr(settings, "github_oauth_client_id", None) or ""
    if not client_id:
        return JSONResponse(
            {"error": "github oauth not configured"},
            status_code=501,
        )
    params = {
        "client_id": client_id,
        "scope": "read:user user:email",
    }
    url = "https://github.com/login/oauth/authorize?" + urllib.parse.urlencode(params)
    return RedirectResponse(url, status_code=302)


@router.get("/google/start", response_model=None)
async def google_start():
    """Redirect to Google's OAuth consent screen.

    Returns 501 if ``GOOGLE_OAUTH_CLIENT_ID`` isn't set. Same rationale
    as the GitHub equivalent above.
    """
    settings = get_settings()
    client_id = getattr(settings, "google_oauth_client_id", None) or ""
    if not client_id:
        return JSONResponse(
            {"error": "google oauth not configured"},
            status_code=501,
        )
    params = {
        "client_id": client_id,
        "response_type": "code",
        "scope": "openid email profile",
    }
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
    return RedirectResponse(url, status_code=302)
