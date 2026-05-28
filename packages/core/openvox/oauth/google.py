"""Google OAuth 2.0 helpers — Desktop App / installed-app flow with PKCE.

This module wraps the protocol-level mechanics of OAuth 2.0 against
Google's endpoints. The HTTP-route layer (``api/routes/integrations/
google.py``) calls into these helpers; the token-store layer
(``oauth.store``) persists what comes back.

Flow shape (per `Google OAuth 2.0 for Mobile and Desktop apps
<https://developers.google.com/identity/protocols/oauth2/native-app>`_):

  1. **Start.** ``start_auth_flow(scopes)`` returns the URL the user
     opens in a browser. We generate a fresh PKCE code_verifier +
     CSRF state token; the verifier is stashed in the in-process
     ``_pending_states`` dict keyed by state so we can look it up on
     the callback.

  2. **User consents.** Browser hits Google → user authorises →
     Google redirects back to
     ``http://localhost:<core_port>/oauth/google/callback?code=…&state=…``.

  3. **Exchange.** ``exchange_code(state, code)`` POSTs to
     ``oauth2.googleapis.com/token`` with the matching code_verifier;
     Google returns ``access_token`` + ``refresh_token`` + ``expires_in``.

  4. **Identify.** ``fetch_userinfo(access_token)`` resolves the
     Google account's email so we can key tokens by ``(google, email)``
     in the store. This is what lets a user connect both personal +
     work Gmail and have them coexist.

  5. **Persist.** Caller writes the bundle via ``set_oauth_token(…)``.

The Desktop App flow uses PKCE rather than a confidential client
secret, but Google still requires the client_secret in the token
exchange request (it's a public secret that ships embedded in the
desktop app binary — they call this out in their own docs). For
OpenVox the secret lives in ``settings.google_oauth_client_secret``;
the user pastes both Client ID + Client Secret into the wizard from
the Cloud Console output. Backwards-compatible: if no secret is
configured we attempt PKCE-only, which Google rejects for the
"Desktop app" client type but accepts for the "Web application"
type — useful escape hatch for advanced users.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets as _secrets
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from openvox.config import get_settings
from openvox.utils.http import make_async_client

logger = logging.getLogger(__name__)


# ── Constants ──────────────────────────────────────────────────────

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"
REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"

# Default scopes covering Phase 1 + Phase 2 use cases:
#   - openid email profile           → userinfo (resolves user_email)
#   - gmail.modify                   → list/read/send via the native skills
#   - calendar                       → full calendar access
#   - contacts.readonly              → People API name → email resolution
#                                       (Phase 2; lets Executive Assistant
#                                       answer "schedule with John Doe"
#                                       even with no prior Gmail history)
#
# Phase 1 users who connected before this list expanded keep working
# (their stored bundle still has the old scopes; their access_token
# refreshes cleanly). Calls to People API skills will return a
# "scope not granted, please reconnect" error in that case — the
# dashboard's Integrations card surfaces this via the per-scope badges.
DEFAULT_SCOPES = [
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/contacts.readonly",
]

# How long an unconsented authorization URL stays valid in our memory.
# After this, the state lookup fails and the user has to restart the
# flow. 10 minutes is generous — most users complete in seconds.
PENDING_STATE_TTL = 600  # seconds


# ── Pending-state cache ────────────────────────────────────────────

# Map of state-token → (code_verifier, expires_at_unix). In-process
# memory is fine because:
#   - OpenVox runs single-process by default (`openvox run`),
#   - the round-trip is seconds (user clicks → callback hits),
#   - we don't ever need to recover this across restarts (a server
#     restart mid-OAuth just means the user retries).
#
# If we ever go multi-process / multi-machine, swap this for the
# DB (encrypted column) or Redis with a TTL. For now the dict +
# manual eviction in start/exchange is sufficient.
_pending_states: dict[str, tuple[str, float]] = {}


def _evict_expired_states() -> None:
    """Drop any pending state past its TTL — keeps memory bounded."""
    now = time.time()
    expired = [s for s, (_, exp) in _pending_states.items() if exp < now]
    for s in expired:
        _pending_states.pop(s, None)


# ── PKCE helpers ───────────────────────────────────────────────────


def _gen_code_verifier() -> str:
    """Generate a PKCE code_verifier per RFC 7636 §4.1.

    43-128 chars from the unreserved URL set. 64 hex chars (256 bits
    of entropy) is comfortably in range and trivially URL-safe.
    """
    return _secrets.token_urlsafe(48)  # ~64 chars, all unreserved


def _code_challenge_from_verifier(verifier: str) -> str:
    """SHA256 + base64url, RFC 7636 §4.2 — the S256 challenge method.

    Google insists on S256 for installed apps (the older `plain`
    method is rejected).
    """
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _gen_state() -> str:
    """CSRF state token. 32 bytes of entropy is overkill but cheap."""
    return _secrets.token_urlsafe(32)


# ── Result types ───────────────────────────────────────────────────


@dataclass
class AuthFlowStart:
    """What ``start_auth_flow`` hands back to the caller.

    The route handler builds a 302 redirect to ``authorization_url``;
    we keep ``state`` for client-side logging / debugging but the
    caller doesn't need to do anything with it (the callback receives
    it back from Google and we look it up here).
    """

    authorization_url: str
    state: str


@dataclass
class TokenExchangeResult:
    """Output of ``exchange_code`` — everything ``set_oauth_token`` needs.

    The route handler calls ``oauth.store.set_oauth_token(…)`` with
    these fields and the (provider="google", user_email=email).
    """

    access_token: str
    refresh_token: str
    expires_at: datetime  # timezone-aware UTC
    scopes: list[str]
    user_email: str
    token_uri: str
    client_id: str


# ── Public API ─────────────────────────────────────────────────────


def get_redirect_uri() -> str:
    """The redirect URI Google will send the browser back to.

    Must match EXACTLY what the maintainer entered in the Cloud
    Console — protocol, host, port, path all character-for-character.
    For the Desktop App flow we use the loopback IP pattern with the
    configured core_port (default 8000).
    """
    settings = get_settings()
    return f"http://localhost:{settings.core_port}/oauth/google/callback"


def start_auth_flow(scopes: list[str] | None = None) -> AuthFlowStart:
    """Build the Google consent-screen URL.

    Generates a fresh PKCE code_verifier and CSRF state. The verifier
    is cached by state for the ``exchange_code`` lookup later.

    Caller (route handler) issues a 302 redirect to the returned URL.
    """
    settings = get_settings()
    client_id = (settings.google_oauth_client_id or "").strip()
    if not client_id:
        raise RuntimeError(
            "GOOGLE_OAUTH_CLIENT_ID is not set. Configure your OAuth client "
            "in the Cloud Console + paste the Client ID into the Settings → "
            "Integrations page (or set the env var)."
        )

    use_scopes = scopes or DEFAULT_SCOPES
    verifier = _gen_code_verifier()
    challenge = _code_challenge_from_verifier(verifier)
    state = _gen_state()

    _evict_expired_states()
    _pending_states[state] = (verifier, time.time() + PENDING_STATE_TTL)

    params = {
        "client_id": client_id,
        "redirect_uri": get_redirect_uri(),
        "response_type": "code",
        "scope": " ".join(use_scopes),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        # `access_type=offline` is what gets us a refresh_token. Without
        # it Google only returns the short-lived access_token, which is
        # useless past the 1-hour expiry — see test_empty_refresh_token_
        # rejected in test_oauth_store.py for why we enforce this.
        "access_type": "offline",
        # `prompt=consent` forces the consent screen even on re-auth,
        # which is the only reliable way to receive a fresh refresh_token
        # if the user previously consented and we lost their tokens.
        "prompt": "consent",
        # `include_granted_scopes` tells Google to merge new scopes with
        # any previously-granted ones for incremental authorisation.
        # Useful for Phase 2 when we add contacts.readonly without
        # losing the Gmail/Calendar grants.
        "include_granted_scopes": "true",
    }
    url = AUTH_ENDPOINT + "?" + urllib.parse.urlencode(params)
    logger.info("google oauth: built authorization URL state=%s scopes=%d", state, len(use_scopes))
    return AuthFlowStart(authorization_url=url, state=state)


async def exchange_code(state: str, code: str) -> TokenExchangeResult:
    """Trade the ``code`` from the callback for an access + refresh token.

    Validates the state token (CSRF guard) and pulls the matching
    code_verifier out of the pending-state cache. POSTs to Google's
    token endpoint, then fetches userinfo so we know which Google
    account just authorised.

    Raises ``ValueError`` on bad state, ``RuntimeError`` on protocol
    errors from Google.
    """
    settings = get_settings()
    client_id = (settings.google_oauth_client_id or "").strip()
    client_secret = (settings.google_oauth_client_secret or "").strip()
    if not client_id:
        raise RuntimeError("GOOGLE_OAUTH_CLIENT_ID is not set")

    _evict_expired_states()
    entry = _pending_states.pop(state, None)
    if entry is None:
        raise ValueError(
            "Unknown or expired OAuth state — start the flow again from "
            "the Integrations page."
        )
    verifier, _ = entry

    data = {
        "client_id": client_id,
        "code": code,
        "code_verifier": verifier,
        "grant_type": "authorization_code",
        "redirect_uri": get_redirect_uri(),
    }
    if client_secret:
        # Desktop App clients still require the client_secret per
        # Google's own docs — see the module docstring. The Web
        # Application flow accepts PKCE-only; for that case we just
        # omit the secret.
        data["client_secret"] = client_secret

    async with make_async_client(timeout=15.0) as client:
        resp = await client.post(TOKEN_ENDPOINT, data=data)
        if resp.status_code != 200:
            # Google returns JSON error bodies — surface the message.
            try:
                detail = resp.json()
            except Exception:
                detail = {"raw": resp.text[:500]}
            raise RuntimeError(
                f"Google token exchange failed (HTTP {resp.status_code}): {detail}"
            )
        token_resp = resp.json()

    access_token = token_resp.get("access_token") or ""
    refresh_token = token_resp.get("refresh_token") or ""
    expires_in = int(token_resp.get("expires_in") or 0)
    scopes_str = token_resp.get("scope") or ""
    granted_scopes = scopes_str.split() if scopes_str else []
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    if not access_token:
        raise RuntimeError("Google token response missing access_token")
    if not refresh_token:
        # This is the failure mode the planning doc warns about: if a
        # user already consented previously and our prompt=consent was
        # somehow ignored, Google omits the refresh_token. Treat as
        # fatal — the token store rejects empty refresh tokens anyway,
        # and a non-refreshable token is useless past expiry.
        raise RuntimeError(
            "Google did not return a refresh_token. This usually means "
            "you previously consented to this app — visit "
            "https://myaccount.google.com/permissions to remove OpenVox, "
            "then try the connect flow again."
        )

    # Resolve the user's email — this becomes the second half of the
    # token store's composite PK.
    email = await fetch_userinfo_email(access_token)
    logger.info("google oauth: exchanged code for tokens — user=%s scopes=%d",
                email, len(granted_scopes))

    return TokenExchangeResult(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
        scopes=granted_scopes,
        user_email=email,
        token_uri=TOKEN_ENDPOINT,
        client_id=client_id,
    )


async def fetch_userinfo_email(access_token: str) -> str:
    """GET ``/v1/userinfo`` → email.

    Pulled out as its own helper so callers (or future tests) can
    stub it without re-mocking the token exchange.
    """
    async with make_async_client(timeout=10.0) as client:
        resp = await client.get(
            USERINFO_ENDPOINT,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Google userinfo failed (HTTP {resp.status_code}): {resp.text[:200]}"
            )
        body = resp.json()
    email = (body.get("email") or "").strip().lower()
    if not email:
        raise RuntimeError("Google userinfo response missing email")
    return email


async def refresh_access_token(refresh_token: str) -> dict[str, Any]:
    """Trade a refresh_token for a fresh access_token.

    Returns the parsed token endpoint response. Caller updates the
    store via ``set_oauth_token(…)`` with the new access_token + new
    expires_at; the refresh_token usually carries over unchanged but
    Google occasionally rotates it (Google's docs say refresh tokens
    *can* be invalidated; we always re-persist the refresh value
    returned by this call — falling back to the input value if
    Google didn't return one).
    """
    settings = get_settings()
    client_id = (settings.google_oauth_client_id or "").strip()
    client_secret = (settings.google_oauth_client_secret or "").strip()
    if not client_id:
        raise RuntimeError("GOOGLE_OAUTH_CLIENT_ID is not set")

    data = {
        "client_id": client_id,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    if client_secret:
        data["client_secret"] = client_secret

    async with make_async_client(timeout=15.0) as client:
        resp = await client.post(TOKEN_ENDPOINT, data=data)
        if resp.status_code != 200:
            try:
                detail = resp.json()
            except Exception:
                detail = {"raw": resp.text[:500]}
            raise RuntimeError(
                f"Google token refresh failed (HTTP {resp.status_code}): {detail}"
            )
        return resp.json()


async def ensure_fresh_access_token(user_email: str) -> str:
    """Return a valid access_token for ``(google, user_email)``.

    Looks up the stored bundle. If it's expired (per ``is_expired``'s
    60-second clock-skew window), runs the refresh flow and writes
    the new bundle back to the store. Returns the access_token the
    caller should put in the ``Authorization: Bearer …`` header.

    Raises ``LookupError`` if no integration is connected for the
    user, ``RuntimeError`` on protocol errors (which the skill layer
    should surface to the user as "your Google integration is broken,
    please reconnect").

    This is the helper every native Gmail / Calendar skill calls
    before making an upstream API request — single place to put the
    refresh logic so we never accidentally ship a stale token.
    """
    from openvox.oauth.store import get_oauth_token, set_oauth_token

    bundle = await get_oauth_token("google", user_email)
    if bundle is None:
        raise LookupError(
            f"No Google integration connected for {user_email}. "
            "Visit the dashboard's Integrations tab to connect."
        )
    if not bundle.is_expired:
        return bundle.access_token

    # Stale → refresh. Google occasionally rotates the refresh_token
    # itself in the response; if absent we re-persist the existing
    # value so the bundle stays consistent.
    logger.info("google access_token expired for %s — refreshing", user_email)
    refresh_resp = await refresh_access_token(bundle.refresh_token)
    new_access = refresh_resp.get("access_token") or ""
    new_refresh = refresh_resp.get("refresh_token") or bundle.refresh_token
    expires_in = int(refresh_resp.get("expires_in") or 0)
    if not new_access:
        raise RuntimeError(
            f"Google refresh for {user_email} returned no access_token: {refresh_resp}"
        )
    new_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    # Re-persist. set_oauth_token validates non-empty + re-encrypts.
    await set_oauth_token(
        provider="google",
        user_email=user_email,
        access_token=new_access,
        refresh_token=new_refresh,
        token_uri=bundle.token_uri,
        client_id=bundle.client_id,
        scopes=bundle.scopes,
        expires_at=new_expires_at,
    )
    return new_access


async def revoke_token(token: str) -> bool:
    """Tell Google to forget about this token.

    Best-effort — returns True on success, False on any failure (and
    logs). We never let revoke failures block the local-side
    ``delete_oauth_token`` call, because the user's intent ("stop
    using my Google account here") is satisfied as long as we drop
    our copy. Google's side cleans up its own grants eventually.
    """
    try:
        async with make_async_client(timeout=10.0) as client:
            resp = await client.post(
                REVOKE_ENDPOINT,
                data={"token": token},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            ok = resp.status_code == 200
            if not ok:
                logger.warning(
                    "google revoke returned HTTP %s: %s",
                    resp.status_code, resp.text[:200],
                )
            return ok
    except Exception as e:
        logger.warning("google revoke raised: %s", e)
        return False
