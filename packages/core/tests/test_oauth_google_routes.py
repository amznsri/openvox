"""Route-level tests for the Google integration endpoints.

Exercises the FastAPI app surface end-to-end: builds the app, fires
real HTTP requests via ``httpx.AsyncClient(transport=ASGITransport)``,
mocks only Google's endpoints (via respx). Coverage:

  - GET /api/v1/integrations/google/start    — 302 to Google, 501 unconfigured
  - GET /api/v1/integrations/google/status   — lists connected accounts
  - GET /oauth/google/callback                — exchange + persist + redirect
  - DELETE /api/v1/integrations/google/{email}/disconnect — revoke + delete

The token store + Phase 1.3's encryption layer participate live — we
write a token via the callback, then assert the status endpoint sees
it (omitting secret values, per the store's security property).
"""

from __future__ import annotations

import urllib.parse

import httpx
import pytest


async def _build_app():
    """Construct the FastAPI app from scratch.

    Done inside each test so the test's monkeypatched env vars take
    effect (the app's lifespan reads settings at startup).
    """
    from openvox.api.app import create_app

    return create_app()


async def _httpx_client(app):
    """Async client wired to an in-process ASGI transport.

    ``follow_redirects=False`` is critical — we WANT to assert the
    302 to Google rather than have httpx silently chase it out to
    the real internet.
    """
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    )


# ── /start ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_redirects_to_google_when_configured(
    isolated_db, monkeypatch
):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "test-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "test-sec")
    from openvox.config import get_settings

    get_settings.cache_clear()

    app = await _build_app()
    async with await _httpx_client(app) as client:
        resp = await client.get("/api/v1/integrations/google/start")

    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    qs = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(location).query))
    assert qs["client_id"] == "test-id"
    # Phase 1's default scopes must include Gmail + Calendar.
    assert "gmail.modify" in qs["scope"]
    assert "calendar" in qs["scope"]


@pytest.mark.asyncio
async def test_start_returns_501_when_not_configured(isolated_db, monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "")
    from openvox.config import get_settings

    get_settings.cache_clear()

    app = await _build_app()
    async with await _httpx_client(app) as client:
        resp = await client.get("/api/v1/integrations/google/start")

    assert resp.status_code == 501
    body = resp.json()
    assert body["error"] == "google_oauth_not_configured"
    # Message mentions where the user can fix it.
    assert "GOOGLE_OAUTH_CLIENT_ID" in body["message"]


# ── /status ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_returns_empty_when_no_integrations(isolated_db, monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    from openvox.config import get_settings

    get_settings.cache_clear()

    app = await _build_app()
    async with await _httpx_client(app) as client:
        resp = await client.get("/api/v1/integrations/google/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is True
    assert body["accounts"] == []


@pytest.mark.asyncio
async def test_status_lists_connected_accounts(isolated_db, monkeypatch):
    """After a /callback success, /status reflects the row."""
    from datetime import datetime, timedelta, timezone

    from openvox.oauth import set_oauth_token

    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    from openvox.config import get_settings

    get_settings.cache_clear()

    await set_oauth_token(
        provider="google",
        user_email="bob@example.com",
        access_token="atok",
        refresh_token="rtok",
        token_uri="https://t/",
        client_id="cid",
        scopes=["openid", "email"],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )

    app = await _build_app()
    async with await _httpx_client(app) as client:
        resp = await client.get("/api/v1/integrations/google/status")

    body = resp.json()
    assert len(body["accounts"]) == 1
    row = body["accounts"][0]
    assert row["provider"] == "google"
    assert row["user_email"] == "bob@example.com"
    assert row["scopes"] == ["openid", "email"]
    # Security property re-asserted at the route boundary.
    assert "access_token" not in row
    assert "refresh_token" not in row
    assert "atok" not in str(row)


# ── /oauth/google/callback ────────────────────────────────────────


@pytest.mark.asyncio
async def test_callback_success_persists_and_redirects(
    isolated_db, monkeypatch
):
    """Code+state in → tokens persisted → 302 to dashboard."""
    import respx

    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "csec")
    from openvox.config import get_settings

    get_settings.cache_clear()

    # Seed the pending state so exchange_code finds a verifier.
    from openvox.oauth import google as g

    g._pending_states["xyz"] = ("verifier", float("inf"))

    with respx.mock(assert_all_called=True) as router:
        router.post("https://oauth2.googleapis.com/token").mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "ya29.A",
                    "refresh_token": "1//R",
                    "expires_in": 3600,
                    "scope": "openid email https://www.googleapis.com/auth/calendar",
                    "token_type": "Bearer",
                },
            )
        )
        router.get(
            "https://openidconnect.googleapis.com/v1/userinfo"
        ).mock(
            return_value=httpx.Response(
                200, json={"email": "carol@example.com"}
            )
        )

        app = await _build_app()
        async with await _httpx_client(app) as client:
            resp = await client.get(
                "/oauth/google/callback",
                params={"code": "the-code", "state": "xyz"},
            )

    assert resp.status_code == 302
    loc = resp.headers["location"]
    assert "google=success" in loc
    assert "carol%40example.com" in loc or "carol@example.com" in loc

    # And the row landed in the store.
    from openvox.oauth import get_oauth_token

    bundle = await get_oauth_token("google", "carol@example.com")
    assert bundle is not None
    assert bundle.access_token == "ya29.A"
    assert bundle.refresh_token == "1//R"


@pytest.mark.asyncio
async def test_callback_with_google_error_returns_html(isolated_db, monkeypatch):
    """User clicked Cancel on Google's consent — show a friendly page."""
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    from openvox.config import get_settings

    get_settings.cache_clear()

    app = await _build_app()
    async with await _httpx_client(app) as client:
        resp = await client.get(
            "/oauth/google/callback",
            params={"error": "access_denied", "error_description": "User cancelled"},
        )

    assert resp.status_code == 400
    assert "text/html" in resp.headers["content-type"]
    assert "access_denied" in resp.text


@pytest.mark.asyncio
async def test_callback_bad_state_returns_html_error(isolated_db, monkeypatch):
    """Tampered or stale state → CSRF guard fires."""
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "csec")
    from openvox.config import get_settings

    get_settings.cache_clear()

    app = await _build_app()
    async with await _httpx_client(app) as client:
        resp = await client.get(
            "/oauth/google/callback",
            params={"code": "c", "state": "never-issued"},
        )

    assert resp.status_code == 400
    assert "state mismatch" in resp.text.lower()


@pytest.mark.asyncio
async def test_callback_missing_params_returns_html_error(
    isolated_db, monkeypatch
):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    from openvox.config import get_settings

    get_settings.cache_clear()

    app = await _build_app()
    async with await _httpx_client(app) as client:
        resp = await client.get("/oauth/google/callback")  # no code, no state

    assert resp.status_code == 400
    assert "malformed callback" in resp.text.lower()


# ── /disconnect ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_disconnect_revokes_and_deletes(isolated_db, monkeypatch):
    """Happy path — token row exists, revoke succeeds, row gone."""
    import respx
    from datetime import datetime, timedelta, timezone

    from openvox.oauth import get_oauth_token, set_oauth_token

    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    from openvox.config import get_settings

    get_settings.cache_clear()

    await set_oauth_token(
        provider="google",
        user_email="dora@example.com",
        access_token="atok-to-revoke",
        refresh_token="rtok",
        token_uri="https://t/",
        client_id="cid",
        scopes=["openid"],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )

    with respx.mock(assert_all_called=True) as router:
        revoke = router.post("https://oauth2.googleapis.com/revoke").mock(
            return_value=httpx.Response(200)
        )

        app = await _build_app()
        async with await _httpx_client(app) as client:
            resp = await client.delete(
                "/api/v1/integrations/google/dora@example.com/disconnect"
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["revoke_succeeded"] is True
    assert revoke.called

    # Local row is gone.
    assert await get_oauth_token("google", "dora@example.com") is None


@pytest.mark.asyncio
async def test_disconnect_unknown_email_still_succeeds(
    isolated_db, monkeypatch
):
    """No local row → no revoke attempt, but DELETE still 200s.

    Mirrors the store's idempotent delete — calling disconnect on
    something that was never connected is a no-op, not an error.
    """
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    from openvox.config import get_settings

    get_settings.cache_clear()

    app = await _build_app()
    async with await _httpx_client(app) as client:
        resp = await client.delete(
            "/api/v1/integrations/google/nobody@example.com/disconnect"
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    # No revoke attempted because no token to revoke.
    assert body["revoke_succeeded"] is None
