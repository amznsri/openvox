"""Unit tests for ``openvox.oauth.google`` — PKCE helpers + protocol mechanics.

These cover the bits that don't talk to Google over the wire:

  - PKCE verifier + S256 challenge generation
  - URL building (consent screen URL has every required param)
  - State CSRF round-trip (start → exchange validates state)
  - TTL eviction of stale pending states
  - Token exchange (respx-mocked, asserts request shape + persistence)
  - Refresh / revoke (respx-mocked, asserts request shape)

The route layer is tested separately in ``test_oauth_google_routes.py``.
"""

from __future__ import annotations

import hashlib
import urllib.parse

import httpx
import pytest


# ── PKCE primitives ───────────────────────────────────────────────


def test_code_verifier_is_url_safe_and_long(tmp_openvox_home):
    """RFC 7636 says verifiers are 43-128 chars from the unreserved set."""
    from openvox.oauth.google import _gen_code_verifier

    v = _gen_code_verifier()
    assert 43 <= len(v) <= 128
    # All chars URL-safe — no %-encoding required.
    assert urllib.parse.quote(v, safe="-._~") == v


def test_code_challenge_matches_sha256_of_verifier(tmp_openvox_home):
    """S256 challenge = base64url(sha256(verifier)), no padding."""
    import base64

    from openvox.oauth.google import _code_challenge_from_verifier

    verifier = "test-verifier-1234"
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    assert _code_challenge_from_verifier(verifier) == expected


def test_two_verifiers_are_different(tmp_openvox_home):
    """Every call to ``_gen_code_verifier`` must produce a fresh value."""
    from openvox.oauth.google import _gen_code_verifier

    seen = {_gen_code_verifier() for _ in range(50)}
    assert len(seen) == 50  # no duplicates in 50 draws


# ── Auth-URL building ─────────────────────────────────────────────


def test_start_auth_flow_requires_client_id(tmp_openvox_home, monkeypatch):
    """No GOOGLE_OAUTH_CLIENT_ID configured → RuntimeError."""
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "")
    from openvox.config import get_settings

    get_settings.cache_clear()

    from openvox.oauth.google import start_auth_flow

    with pytest.raises(RuntimeError, match="GOOGLE_OAUTH_CLIENT_ID"):
        start_auth_flow()


def test_start_auth_flow_builds_correct_url(tmp_openvox_home, monkeypatch):
    """The consent URL has every required query parameter."""
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("CORE_PORT", "8000")
    from openvox.config import get_settings

    get_settings.cache_clear()

    from openvox.oauth.google import AUTH_ENDPOINT, start_auth_flow

    flow = start_auth_flow()
    assert flow.authorization_url.startswith(AUTH_ENDPOINT + "?")
    parsed = urllib.parse.urlparse(flow.authorization_url)
    qs = dict(urllib.parse.parse_qsl(parsed.query))

    assert qs["client_id"] == "test-client-id"
    assert qs["response_type"] == "code"
    assert qs["redirect_uri"] == "http://localhost:8000/oauth/google/callback"
    assert qs["code_challenge_method"] == "S256"
    assert qs["access_type"] == "offline"
    assert qs["prompt"] == "consent"
    # Scopes are space-separated string. Phase 1 + Phase 2 scopes all
    # need to be in the default auth URL so a fresh connect gives the
    # LLM access to Gmail, Calendar, AND People API in one consent.
    assert "gmail.modify" in qs["scope"]
    assert "calendar" in qs["scope"]
    assert "openid" in qs["scope"]
    # Phase 2 — People API for `resolve_contact`. include_granted_scopes
    # ensures existing Phase 1 connections merge this on re-auth.
    assert "contacts.readonly" in qs["scope"]
    # State must be present and non-empty (CSRF guard).
    assert flow.state
    assert qs["state"] == flow.state


def test_start_auth_flow_accepts_custom_scopes(tmp_openvox_home, monkeypatch):
    """Caller can override the default scope list."""
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "test-client-id")
    from openvox.config import get_settings

    get_settings.cache_clear()

    from openvox.oauth.google import start_auth_flow

    flow = start_auth_flow(scopes=["openid", "email"])
    parsed = urllib.parse.urlparse(flow.authorization_url)
    qs = dict(urllib.parse.parse_qsl(parsed.query))
    assert qs["scope"] == "openid email"


# ── State round-trip ──────────────────────────────────────────────


def test_pending_state_stored_after_start(tmp_openvox_home, monkeypatch):
    """``start_auth_flow`` puts the verifier in ``_pending_states``."""
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    from openvox.config import get_settings

    get_settings.cache_clear()

    from openvox.oauth import google as g

    flow = g.start_auth_flow()
    assert flow.state in g._pending_states
    verifier, expires_at = g._pending_states[flow.state]
    assert verifier  # non-empty
    assert expires_at > 0


@pytest.mark.asyncio
async def test_exchange_code_unknown_state_raises(tmp_openvox_home, monkeypatch):
    """Bad state → ValueError, no HTTP call attempted."""
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    from openvox.config import get_settings

    get_settings.cache_clear()

    from openvox.oauth.google import exchange_code

    with pytest.raises(ValueError, match="Unknown or expired"):
        await exchange_code(state="never-issued", code="abc")


def test_expired_state_is_evicted(tmp_openvox_home, monkeypatch):
    """``_evict_expired_states`` drops entries past their TTL."""
    import time as _time

    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    from openvox.config import get_settings

    get_settings.cache_clear()

    from openvox.oauth import google as g

    g._pending_states.clear()
    g._pending_states["stale"] = ("verifier", _time.time() - 1)
    g._pending_states["fresh"] = ("verifier", _time.time() + 600)
    g._evict_expired_states()
    assert "stale" not in g._pending_states
    assert "fresh" in g._pending_states


# ── Token exchange (mocked HTTP) ──────────────────────────────────


@pytest.mark.asyncio
async def test_exchange_code_persists_tokens(tmp_openvox_home, monkeypatch):
    """Happy path — code → tokens → returned bundle.

    Uses respx to intercept the POST to ``oauth2.googleapis.com/token``
    and the GET to ``openidconnect.googleapis.com/v1/userinfo``.
    Asserts the request shape (client_id, code_verifier, grant_type)
    AND the parsed result.
    """
    import respx

    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "client-abc")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "secret-xyz")
    from openvox.config import get_settings

    get_settings.cache_clear()

    from openvox.oauth import google as g

    # Seed a known state so we don't have to call start_auth_flow first.
    g._pending_states["S1"] = ("my-verifier", float("inf"))

    with respx.mock(assert_all_called=True) as router:
        token_route = router.post("https://oauth2.googleapis.com/token").mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "ya29.access",
                    "refresh_token": "1//refresh",
                    "expires_in": 3600,
                    "scope": "openid email https://www.googleapis.com/auth/gmail.modify",
                    "token_type": "Bearer",
                },
            )
        )
        userinfo_route = router.get(
            "https://openidconnect.googleapis.com/v1/userinfo"
        ).mock(
            return_value=httpx.Response(
                200,
                json={"email": "alice@example.com", "sub": "12345"},
            )
        )

        result = await g.exchange_code(state="S1", code="auth-code-here")

    # Token exchange request was made with the right body.
    assert token_route.called
    body = dict(urllib.parse.parse_qsl(token_route.calls.last.request.content.decode()))
    assert body["client_id"] == "client-abc"
    assert body["client_secret"] == "secret-xyz"
    assert body["code"] == "auth-code-here"
    assert body["code_verifier"] == "my-verifier"
    assert body["grant_type"] == "authorization_code"

    # Userinfo request had the Bearer header.
    assert userinfo_route.called
    auth_header = userinfo_route.calls.last.request.headers.get("authorization", "")
    assert auth_header == "Bearer ya29.access"

    # Result.
    assert result.access_token == "ya29.access"
    assert result.refresh_token == "1//refresh"
    assert result.user_email == "alice@example.com"
    assert "gmail.modify" in " ".join(result.scopes)
    assert result.client_id == "client-abc"


@pytest.mark.asyncio
async def test_exchange_code_handles_google_error(tmp_openvox_home, monkeypatch):
    """4xx from Google's token endpoint → RuntimeError with body."""
    import respx

    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "csec")
    from openvox.config import get_settings

    get_settings.cache_clear()

    from openvox.oauth import google as g

    g._pending_states["S2"] = ("v", float("inf"))
    with respx.mock(assert_all_called=False) as router:
        router.post("https://oauth2.googleapis.com/token").mock(
            return_value=httpx.Response(
                400,
                json={"error": "invalid_grant", "error_description": "bad code"},
            )
        )
        with pytest.raises(RuntimeError, match="HTTP 400"):
            await g.exchange_code(state="S2", code="bad")


@pytest.mark.asyncio
async def test_exchange_code_missing_refresh_token_raises(
    tmp_openvox_home, monkeypatch
):
    """Google returned access_token but no refresh — fatal.

    This is the failure mode the planning doc warns about: it usually
    means the user already consented to this app and prompt=consent
    was ignored. We refuse to persist a non-refreshable bundle.
    """
    import respx

    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "csec")
    from openvox.config import get_settings

    get_settings.cache_clear()

    from openvox.oauth import google as g

    g._pending_states["S3"] = ("v", float("inf"))
    with respx.mock(assert_all_called=False) as router:
        router.post("https://oauth2.googleapis.com/token").mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "ya29.access",
                    "expires_in": 3600,
                    "scope": "openid",
                    "token_type": "Bearer",
                    # NO refresh_token — Google sometimes omits it.
                },
            )
        )
        with pytest.raises(RuntimeError, match="refresh_token"):
            await g.exchange_code(state="S3", code="c")


# ── Refresh ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_access_token_calls_token_endpoint(
    tmp_openvox_home, monkeypatch
):
    import respx

    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "csec")
    from openvox.config import get_settings

    get_settings.cache_clear()

    from openvox.oauth.google import refresh_access_token

    with respx.mock(assert_all_called=True) as router:
        route = router.post("https://oauth2.googleapis.com/token").mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "ya29.new-access",
                    "expires_in": 3600,
                    "scope": "openid email",
                    "token_type": "Bearer",
                },
            )
        )
        result = await refresh_access_token("rtok-here")

    body = dict(urllib.parse.parse_qsl(route.calls.last.request.content.decode()))
    assert body["grant_type"] == "refresh_token"
    assert body["refresh_token"] == "rtok-here"
    assert body["client_id"] == "cid"
    assert result["access_token"] == "ya29.new-access"


# ── Revoke ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_revoke_token_success(tmp_openvox_home, monkeypatch):
    import respx

    from openvox.oauth.google import revoke_token

    with respx.mock(assert_all_called=True) as router:
        router.post("https://oauth2.googleapis.com/revoke").mock(
            return_value=httpx.Response(200)
        )
        assert await revoke_token("some-token") is True


@pytest.mark.asyncio
async def test_revoke_token_returns_false_on_error(tmp_openvox_home, monkeypatch):
    """Revoke failures must NOT raise — local-side drop is the truth."""
    import respx

    from openvox.oauth.google import revoke_token

    with respx.mock(assert_all_called=False) as router:
        router.post("https://oauth2.googleapis.com/revoke").mock(
            return_value=httpx.Response(400, json={"error": "invalid_token"})
        )
        assert await revoke_token("expired") is False


@pytest.mark.asyncio
async def test_revoke_token_swallows_network_errors(tmp_openvox_home, monkeypatch):
    """No respx mock → outbound call fails → False (not crash)."""
    import respx

    from openvox.oauth.google import revoke_token

    # respx with no routes raises by default; set passthrough off and
    # let httpx fail to connect to a guaranteed-bad URL by pointing
    # REVOKE_ENDPOINT at it. Simpler approach: stub with an exception.
    with respx.mock(assert_all_called=False) as router:
        router.post("https://oauth2.googleapis.com/revoke").mock(
            side_effect=httpx.ConnectError("simulated network error")
        )
        assert await revoke_token("any") is False
