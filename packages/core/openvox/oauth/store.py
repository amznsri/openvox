"""Encrypted OAuth-token store. Sibling of ``openvox/secrets.py``.

Splits cleanly along lifecycle:

  - ``secrets.py`` — long-lived provider API keys (BytePlus, OpenAI,
    etc.). Set once via the wizard, no automatic refresh, env-var
    fallback for Docker-style deployments.
  - this module — OAuth 2.0 tokens (Google, etc.). Short-lived access
    + long-lived refresh, rotates on every use, NO env-var fallback
    (the user MUST complete the OAuth browser dance once per
    integration).

The encryption layer is shared — we reuse the per-machine Fernet key
that ``secrets.py`` already manages at ``~/.openvox/secret.key``.
That means:

  - One key file protects everything sensitive on the machine.
  - Backing up `~/.openvox/` (including secret.key) backs up
    integrations too.
  - Wiping secret.key invalidates BOTH provider keys and OAuth
    tokens — treat it like an SSH host key.

Phase 1 of PLANNING_SESSION18.md introduces this for native Google
OAuth (Gmail + Calendar + Contacts) — replaces the MCP-server-based
flow's "create your own Google Cloud OAuth client, paste credentials
into the MCP tab" friction with a one-click "Connect Gmail" button.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from cryptography.fernet import InvalidToken
from sqlalchemy import select

from openvox.secrets import _fernet

logger = logging.getLogger(__name__)


@dataclass
class OAuthTokenBundle:
    """Decrypted view of an ``OAuthToken`` row.

    Returned by ``get_oauth_token()`` and similar — callers should
    treat these as short-lived secrets and not log/persist them.
    """

    provider: str
    user_email: str
    access_token: str
    refresh_token: str
    token_uri: str
    client_id: str
    scopes: list[str]
    expires_at: datetime  # timezone-aware

    @property
    def is_expired(self) -> bool:
        """True when the access_token is past its expiry.

        Callers should refresh before using when this is True.
        Builds in a 60-second clock-skew tolerance — if the token
        expires "within the next minute", treat as already expired
        so we don't race with the upstream API rejecting a token
        that's technically still valid for 50ms.
        """
        if self.expires_at.tzinfo is None:
            # Defensive — every code path that writes should produce
            # timezone-aware datetimes, but legacy rows might be naive.
            # Treat the naive datetime as UTC.
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            expires = self.expires_at
        else:
            now = datetime.now(timezone.utc)
            expires = self.expires_at
        return (expires - now).total_seconds() < 60


# ── Store API ──────────────────────────────────────────────────────


async def set_oauth_token(
    *,
    provider: str,
    user_email: str,
    access_token: str,
    refresh_token: str,
    token_uri: str,
    client_id: str,
    scopes: list[str],
    expires_at: datetime,
) -> None:
    """Encrypt + UPSERT an OAuth token bundle.

    All-or-nothing: pass every field or get a TypeError. Each call
    REPLACES the entire row for (provider, user_email) — that
    matches OAuth's "fresh handshake gives you fresh tokens" model.

    Empty `access_token` is a usage error (use ``delete_oauth_token``
    instead). Empty `refresh_token` means the provider didn't return
    one — Google's OAuth returns it only on first consent, so a
    re-consent without `prompt=consent` may give us no refresh. We
    enforce a refresh_token here because the only sane way to use
    OAuth long-term is to be able to refresh.
    """
    from openvox.db import db_session
    from openvox.db.models import OAuthToken

    provider = provider.strip().lower()
    user_email = user_email.strip().lower()
    if not provider or not user_email:
        raise ValueError("provider and user_email must be non-empty")
    if not access_token:
        raise ValueError("access_token must be non-empty (use delete_oauth_token to remove)")
    if not refresh_token:
        raise ValueError(
            "refresh_token must be non-empty. The OAuth provider didn't "
            "return one — try the consent flow again with prompt=consent."
        )

    fernet = _fernet()
    enc_access = fernet.encrypt(access_token.encode("utf-8")).decode("ascii")
    enc_refresh = fernet.encrypt(refresh_token.encode("utf-8")).decode("ascii")

    # Make sure expires_at is timezone-aware before persisting.
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    async with db_session() as s:
        existing = await s.get(OAuthToken, (provider, user_email))
        if existing is None:
            s.add(
                OAuthToken(
                    provider=provider,
                    user_email=user_email,
                    encrypted_access_token=enc_access,
                    encrypted_refresh_token=enc_refresh,
                    token_uri=token_uri,
                    client_id=client_id,
                    scopes=list(scopes),
                    expires_at=expires_at,
                )
            )
        else:
            existing.encrypted_access_token = enc_access
            existing.encrypted_refresh_token = enc_refresh
            existing.token_uri = token_uri
            existing.client_id = client_id
            existing.scopes = list(scopes)
            existing.expires_at = expires_at

    logger.info("oauth_token set: %s/%s scopes=%d", provider, user_email, len(scopes))


async def get_oauth_token(provider: str, user_email: str) -> OAuthTokenBundle | None:
    """Decrypt + return the stored token bundle, or None if absent.

    On decrypt failure (machine key rotated out from under us), logs
    an error and returns None — caller should treat that the same as
    "not connected" and prompt the user to re-authorize.
    """
    from openvox.db import db_session
    from openvox.db.models import OAuthToken

    provider = provider.strip().lower()
    user_email = user_email.strip().lower()

    async with db_session() as s:
        row = await s.get(OAuthToken, (provider, user_email))
        if row is None:
            return None
        try:
            fernet = _fernet()
            access = fernet.decrypt(row.encrypted_access_token.encode("ascii")).decode("utf-8")
            refresh = fernet.decrypt(row.encrypted_refresh_token.encode("ascii")).decode("utf-8")
        except InvalidToken:
            logger.error(
                "oauth_token %s/%s failed to decrypt — machine key likely rotated. "
                "User must re-authorize via the dashboard.",
                provider, user_email,
            )
            return None
        return OAuthTokenBundle(
            provider=row.provider,
            user_email=row.user_email,
            access_token=access,
            refresh_token=refresh,
            token_uri=row.token_uri,
            client_id=row.client_id,
            scopes=list(row.scopes or []),
            expires_at=row.expires_at,
        )


async def delete_oauth_token(provider: str, user_email: str) -> None:
    """Drop a stored integration. No-op if it doesn't exist."""
    from openvox.db import db_session
    from openvox.db.models import OAuthToken

    provider = provider.strip().lower()
    user_email = user_email.strip().lower()

    async with db_session() as s:
        row = await s.get(OAuthToken, (provider, user_email))
        if row is not None:
            await s.delete(row)
            logger.info("oauth_token deleted: %s/%s", provider, user_email)


async def list_oauth_integrations() -> list[dict[str, Any]]:
    """Return metadata for every connected integration.

    Used by the dashboard Integrations tab. DELIBERATELY omits the
    access_token + refresh_token — those should never leave the
    backend. Callers that need actual tokens use ``get_oauth_token``.
    """
    from openvox.db import db_session
    from openvox.db.models import OAuthToken

    async with db_session() as s:
        result = await s.execute(select(OAuthToken))
        return [
            {
                "provider": row.provider,
                "user_email": row.user_email,
                "scopes": list(row.scopes or []),
                "expires_at": row.expires_at.isoformat() if row.expires_at else None,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
            for row in result.scalars().all()
        ]
