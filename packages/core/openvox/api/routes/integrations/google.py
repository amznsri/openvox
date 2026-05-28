"""Google OAuth integration routes.

Two routers exported:

  - ``api_router`` — mounted under ``/api/v1/integrations/google``,
    provides ``/start``, ``/status``, ``/{email}/disconnect``.
  - ``oauth_callback_router`` — mounted at root so Google can hit
    ``http://localhost:<core_port>/oauth/google/callback`` (the
    redirect URI registered in the Cloud Console — protocol/host/
    port/path must match exactly).

Flow narrative:

  1. Dashboard's "Connect Gmail" button hits GET ``/start``. We build
     the consent URL and 302 the browser to Google.

  2. User consents. Google redirects the browser back to
     ``/oauth/google/callback?code=…&state=…``. We exchange the code,
     persist tokens via ``oauth.store.set_oauth_token``, then redirect
     the browser to the dashboard's integrations tab with a success
     query param so the UI can refresh + show the new row.

  3. To disconnect: dashboard DELETEs
     ``/api/v1/integrations/google/<email>/disconnect``. We call
     Google's revoke endpoint (best-effort) and drop the local row.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from openvox.config import get_settings
from openvox.oauth import google as google_oauth
from openvox.oauth.store import (
    delete_oauth_token,
    get_oauth_token,
    list_oauth_integrations,
    set_oauth_token,
)

logger = logging.getLogger(__name__)

api_router = APIRouter()
oauth_callback_router = APIRouter()


# ── Helpers ────────────────────────────────────────────────────────


def _dashboard_redirect(success: bool, message: str | None = None,
                        email: str | None = None) -> RedirectResponse:
    """Send the browser back to the dashboard with a status flag.

    The dashboard's Integrations tab reads ``?google=success`` or
    ``?google=error&google_msg=…`` from the URL and renders a toast
    + refreshes the list.
    """
    settings = get_settings()
    base = f"http://localhost:{settings.core_port}/dashboard/integrations/"
    params = []
    if success:
        params.append("google=success")
        if email:
            import urllib.parse
            params.append("google_email=" + urllib.parse.quote(email))
    else:
        params.append("google=error")
        if message:
            import urllib.parse
            params.append("google_msg=" + urllib.parse.quote(message))
    return RedirectResponse(f"{base}?{'&'.join(params)}", status_code=302)


def _callback_html_fallback(body_html: str, status: int = 200) -> HTMLResponse:
    """Return a minimal HTML page for the callback path.

    Used when we want to surface a message before/instead of
    redirecting (e.g. config error, exchange failed). Keeps the page
    self-contained so it works even when the dashboard isn't served
    on the same port.
    """
    html = f"""<!doctype html>
<html><head><title>OpenVox — Google OAuth</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; margin: 4em auto;
          max-width: 560px; padding: 0 1em; color: #1a1a2e; }}
  h1 {{ font-size: 1.4em; margin-bottom: 0.5em; }}
  .box {{ padding: 1.5em; border-radius: 0.5em; background: #f6f6fa;
          border: 1px solid #ddd; }}
  a {{ color: #5b21b6; }}
  code {{ background: #fff; padding: 0.1em 0.4em; border-radius: 0.2em; }}
</style></head>
<body>{body_html}</body></html>"""
    return HTMLResponse(html, status_code=status)


# ── /api/v1/integrations/google ────────────────────────────────────


@api_router.get("/start", response_model=None)
async def start():
    """Begin the OAuth flow — 302 the browser to Google's consent screen.

    Returns 501 (with a friendly body) if the maintainer hasn't
    configured ``GOOGLE_OAUTH_CLIENT_ID``. We deliberately don't
    surface 400 here — the user can't fix this themselves, it's a
    deployment-time concern.
    """
    settings = get_settings()
    if not (settings.google_oauth_client_id or "").strip():
        return JSONResponse(
            {
                "error": "google_oauth_not_configured",
                "message": (
                    "Set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET "
                    "in your environment (or via the Settings → Integrations "
                    "page in the dashboard). See docs/integrations/google.md."
                ),
            },
            status_code=501,
        )
    try:
        flow = google_oauth.start_auth_flow()
    except RuntimeError as e:
        return JSONResponse(
            {"error": "google_oauth_misconfigured", "message": str(e)},
            status_code=501,
        )
    return RedirectResponse(flow.authorization_url, status_code=302)


@api_router.get("/status")
async def status() -> dict[str, Any]:
    """List Google integrations currently connected on this machine.

    Returns metadata only — never the encrypted token material itself.
    The dashboard renders the result as one row per connected account
    on the Integrations tab.
    """
    all_integrations = await list_oauth_integrations()
    google_only = [row for row in all_integrations if row.get("provider") == "google"]
    return {
        "configured": bool(
            (get_settings().google_oauth_client_id or "").strip()
        ),
        "accounts": google_only,
    }


@api_router.delete("/{email}/disconnect", response_model=None)
async def disconnect(email: str) -> dict[str, Any]:
    """Drop the integration for ``email``.

    Tries to revoke the access_token on Google's side (best-effort —
    a 4xx from Google doesn't block the local drop, because the
    user's intent "stop using this Google account here" is satisfied
    by removing our local copy). Returns the revocation status so
    the dashboard can mention it if Google rejected the revoke
    (most commonly because the token expired naturally first).
    """
    bundle = await get_oauth_token("google", email)
    revoke_ok: bool | None = None
    if bundle is not None:
        revoke_ok = await google_oauth.revoke_token(bundle.access_token)
    await delete_oauth_token("google", email)
    logger.info("google integration disconnected: %s (revoke=%s)", email, revoke_ok)
    return {"ok": True, "email": email, "revoke_succeeded": revoke_ok}


# ── /oauth/google/callback (root-mounted) ──────────────────────────


@oauth_callback_router.get("/oauth/google/callback", response_model=None)
async def google_callback(request: Request):
    """Receive Google's redirect — exchange the code + persist tokens.

    On success: 302 to the dashboard with ``?google=success``.
    On failure: HTML page explaining what went wrong (we deliberately
    don't dump the user back to the dashboard for failures because
    the error message is more diagnosable on a dedicated page).
    """
    qs = dict(request.query_params)
    if "error" in qs:
        # Google itself returned an error — user cancelled the
        # consent screen, or the OAuth client is misconfigured.
        err = qs.get("error", "unknown")
        desc = qs.get("error_description", "")
        return _callback_html_fallback(
            f"<h1>Google consent declined</h1>"
            f"<div class='box'><p>Google returned <code>{err}</code>"
            f"{f': {desc}' if desc else ''}.</p>"
            f"<p>You can <a href='/dashboard/integrations/'>"
            f"return to the dashboard</a> and try again.</p></div>",
            status=400,
        )
    state = qs.get("state") or ""
    code = qs.get("code") or ""
    if not state or not code:
        return _callback_html_fallback(
            "<h1>Malformed callback</h1>"
            "<div class='box'><p>Google's redirect didn't include the "
            "expected <code>code</code> and <code>state</code> parameters. "
            "Start the connect flow again from the dashboard.</p></div>",
            status=400,
        )
    try:
        result = await google_oauth.exchange_code(state, code)
    except ValueError as e:
        # Bad state — CSRF guard tripped, or the verifier expired.
        return _callback_html_fallback(
            f"<h1>OAuth state mismatch</h1>"
            f"<div class='box'><p>{e}</p></div>",
            status=400,
        )
    except RuntimeError as e:
        # Google rejected the exchange — protocol-level failure.
        logger.exception("google oauth exchange failed")
        return _callback_html_fallback(
            f"<h1>Google rejected the token exchange</h1>"
            f"<div class='box'><p><code>{e}</code></p>"
            f"<p>Common causes: wrong Client Secret, redirect URI mismatch "
            f"between OpenVox and the Cloud Console, or app verification "
            f"required for the requested scopes.</p></div>",
            status=502,
        )

    # Persist via the Phase 1.3 store. set_oauth_token already
    # validates non-empty access/refresh tokens, normalises case on
    # provider + email, and encrypts both tokens at rest.
    await set_oauth_token(
        provider="google",
        user_email=result.user_email,
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        token_uri=result.token_uri,
        client_id=result.client_id,
        scopes=result.scopes,
        expires_at=result.expires_at,
    )
    logger.info("google integration connected: %s scopes=%d",
                result.user_email, len(result.scopes))
    return _dashboard_redirect(success=True, email=result.user_email)
