"""Round-trip + edge-case tests for openvox.oauth.store.

Mirrors the test pattern in test_secrets.py — every test uses the
``isolated_db`` fixture so each gets its own SQLite + machine key.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


# ── Round-trip ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_then_get_roundtrip(isolated_db):
    """Store a token bundle, fetch it back, fields match."""
    from openvox.oauth import get_oauth_token, set_oauth_token

    expiry = datetime.now(timezone.utc) + timedelta(hours=1)
    await set_oauth_token(
        provider="google",
        user_email="alice@example.com",
        access_token="ya29.access-tok",
        refresh_token="1//refresh-tok",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="123.apps.googleusercontent.com",
        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        expires_at=expiry,
    )
    bundle = await get_oauth_token("google", "alice@example.com")
    assert bundle is not None
    assert bundle.provider == "google"
    assert bundle.user_email == "alice@example.com"
    assert bundle.access_token == "ya29.access-tok"
    assert bundle.refresh_token == "1//refresh-tok"
    assert bundle.token_uri == "https://oauth2.googleapis.com/token"
    assert bundle.client_id == "123.apps.googleusercontent.com"
    assert bundle.scopes == ["https://www.googleapis.com/auth/gmail.readonly"]


@pytest.mark.asyncio
async def test_get_missing_returns_none(isolated_db):
    from openvox.oauth import get_oauth_token

    assert await get_oauth_token("google", "nobody@example.com") is None


@pytest.mark.asyncio
async def test_upsert_replaces_existing(isolated_db):
    """A second set() for the same (provider, user) overwrites."""
    from openvox.oauth import get_oauth_token, set_oauth_token

    expiry = datetime.now(timezone.utc) + timedelta(hours=1)
    for value in ("first-access", "second-access"):
        await set_oauth_token(
            provider="google",
            user_email="alice@example.com",
            access_token=value,
            refresh_token="rtok",
            token_uri="https://t/",
            client_id="cid",
            scopes=[],
            expires_at=expiry,
        )
    bundle = await get_oauth_token("google", "alice@example.com")
    assert bundle is not None
    assert bundle.access_token == "second-access"


@pytest.mark.asyncio
async def test_multiple_accounts_for_one_provider(isolated_db):
    """Composite PK allows multiple Google accounts to coexist."""
    from openvox.oauth import get_oauth_token, set_oauth_token

    expiry = datetime.now(timezone.utc) + timedelta(hours=1)
    for email in ("personal@example.com", "work@example.com"):
        await set_oauth_token(
            provider="google",
            user_email=email,
            access_token=f"access-{email}",
            refresh_token="rtok",
            token_uri="https://t/",
            client_id="cid",
            scopes=[],
            expires_at=expiry,
        )
    personal = await get_oauth_token("google", "personal@example.com")
    work = await get_oauth_token("google", "work@example.com")
    assert personal is not None and personal.access_token == "access-personal@example.com"
    assert work is not None and work.access_token == "access-work@example.com"


@pytest.mark.asyncio
async def test_delete(isolated_db):
    from openvox.oauth import (
        delete_oauth_token,
        get_oauth_token,
        set_oauth_token,
    )

    expiry = datetime.now(timezone.utc) + timedelta(hours=1)
    await set_oauth_token(
        provider="google",
        user_email="alice@example.com",
        access_token="atok",
        refresh_token="rtok",
        token_uri="https://t/",
        client_id="cid",
        scopes=[],
        expires_at=expiry,
    )
    await delete_oauth_token("google", "alice@example.com")
    assert await get_oauth_token("google", "alice@example.com") is None


@pytest.mark.asyncio
async def test_delete_idempotent(isolated_db):
    from openvox.oauth import delete_oauth_token

    # No row exists — should not raise.
    await delete_oauth_token("google", "nobody@example.com")


# ── Listing ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_integrations_omits_token_values(isolated_db):
    """The listing API exposes metadata but never the secret values."""
    from openvox.oauth import list_oauth_integrations, set_oauth_token

    expiry = datetime.now(timezone.utc) + timedelta(hours=1)
    await set_oauth_token(
        provider="google",
        user_email="alice@example.com",
        access_token="sensitive-access",
        refresh_token="sensitive-refresh",
        token_uri="https://t/",
        client_id="cid",
        scopes=["scope-a", "scope-b"],
        expires_at=expiry,
    )

    out = await list_oauth_integrations()
    assert len(out) == 1
    row = out[0]
    assert row["provider"] == "google"
    assert row["user_email"] == "alice@example.com"
    assert row["scopes"] == ["scope-a", "scope-b"]
    # Critical security property: the listing must NOT leak token
    # material out of the backend.
    assert "access_token" not in row
    assert "refresh_token" not in row
    assert "sensitive-access" not in str(row)
    assert "sensitive-refresh" not in str(row)


@pytest.mark.asyncio
async def test_list_integrations_empty(isolated_db):
    from openvox.oauth import list_oauth_integrations

    assert await list_oauth_integrations() == []


# ── Validation ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_access_token_rejected(isolated_db):
    from openvox.oauth import set_oauth_token

    with pytest.raises(ValueError, match="access_token"):
        await set_oauth_token(
            provider="google",
            user_email="alice@example.com",
            access_token="",
            refresh_token="rtok",
            token_uri="https://t/",
            client_id="cid",
            scopes=[],
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )


@pytest.mark.asyncio
async def test_empty_refresh_token_rejected(isolated_db):
    """Refresh-token must be present — without one we can't refresh
    later, and a non-refreshable token is useless past expiry."""
    from openvox.oauth import set_oauth_token

    with pytest.raises(ValueError, match="refresh_token"):
        await set_oauth_token(
            provider="google",
            user_email="alice@example.com",
            access_token="atok",
            refresh_token="",
            token_uri="https://t/",
            client_id="cid",
            scopes=[],
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )


@pytest.mark.asyncio
async def test_provider_case_normalised(isolated_db):
    """`Google` and `google` resolve to the same row."""
    from openvox.oauth import get_oauth_token, set_oauth_token

    expiry = datetime.now(timezone.utc) + timedelta(hours=1)
    await set_oauth_token(
        provider="GOOGLE",
        user_email="Alice@Example.com",
        access_token="atok",
        refresh_token="rtok",
        token_uri="https://t/",
        client_id="cid",
        scopes=[],
        expires_at=expiry,
    )
    bundle = await get_oauth_token("google", "alice@example.com")
    assert bundle is not None
    assert bundle.access_token == "atok"


# ── Expiry detection ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_is_expired_when_past_expiry(isolated_db):
    from openvox.oauth import get_oauth_token, set_oauth_token

    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    await set_oauth_token(
        provider="google",
        user_email="alice@example.com",
        access_token="atok",
        refresh_token="rtok",
        token_uri="https://t/",
        client_id="cid",
        scopes=[],
        expires_at=past,
    )
    bundle = await get_oauth_token("google", "alice@example.com")
    assert bundle is not None
    assert bundle.is_expired is True


@pytest.mark.asyncio
async def test_is_expired_false_when_fresh(isolated_db):
    from openvox.oauth import get_oauth_token, set_oauth_token

    fresh = datetime.now(timezone.utc) + timedelta(hours=1)
    await set_oauth_token(
        provider="google",
        user_email="alice@example.com",
        access_token="atok",
        refresh_token="rtok",
        token_uri="https://t/",
        client_id="cid",
        scopes=[],
        expires_at=fresh,
    )
    bundle = await get_oauth_token("google", "alice@example.com")
    assert bundle is not None
    assert bundle.is_expired is False


@pytest.mark.asyncio
async def test_is_expired_within_skew_window_treated_as_expired(isolated_db):
    """We treat "expires in <60s" as already expired to avoid the
    race where we send a token the upstream API rejects."""
    from openvox.oauth import get_oauth_token, set_oauth_token

    soon = datetime.now(timezone.utc) + timedelta(seconds=30)
    await set_oauth_token(
        provider="google",
        user_email="alice@example.com",
        access_token="atok",
        refresh_token="rtok",
        token_uri="https://t/",
        client_id="cid",
        scopes=[],
        expires_at=soon,
    )
    bundle = await get_oauth_token("google", "alice@example.com")
    assert bundle is not None
    assert bundle.is_expired is True
