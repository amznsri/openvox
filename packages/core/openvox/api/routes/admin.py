"""Admin endpoints — backing API for the dashboard's first-run wizard.

Phase 3 of `docs/PLANNING_SESSION15.md`. Three endpoints:

- ``GET  /api/v1/admin/setup/status``   — what's configured + is setup complete?
- ``POST /api/v1/admin/setup/keys``     — save provider keys (encrypted-at-rest)
- ``DELETE /api/v1/admin/setup/keys``   — remove a stored key

The dashboard's wizard pages (``apps/dashboard/src/app/dashboard/setup/*``)
poll status on mount, present the form when ``complete == false``, and
POST the user's pasted keys here. The encrypted-store layer
(``openvox/secrets.py``) handles persistence; this module is just the
HTTP shell.

Security notes
==============
- Endpoints don't require auth today because OpenVox runs local-first;
  the operator and the admin are the same person. When
  ``OPENVOX_AUTH=enabled`` (multi-tenant cloud), these must be gated
  behind admin-only authorization — see the TODO at the top of
  ``check_admin()`` below.
- POSTed values flow into Fernet-encrypted storage. Never logged or
  returned in any response.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from openvox import secrets as secret_store

logger = logging.getLogger(__name__)

router = APIRouter()


def check_admin() -> None:
    """Authorization placeholder for the multi-tenant future.

    Today: no-op. OpenVox runs local-first; operator == admin.

    When ``OPENVOX_AUTH=enabled`` ships (cloud mode), this should
    inspect the request's bearer token and 403 if the caller isn't
    an admin. Keep all admin endpoints calling this so the eventual
    gate has a single edit point.
    """
    # TODO: when OPENVOX_AUTH lands, verify admin role from JWT.
    return None


class SetupKeysRequest(BaseModel):
    """Save one or more provider keys at once.

    Example body:
        {
          "provider": "byteplus",
          "keys": {
            "llm_api_key": "01a2b3c4...",
            "voice_api_key": "01a2b3c4..."
          }
        }

    An empty string for a key value deletes the stored key (caller
    can use this to "forget" a key without a separate DELETE call).
    """

    provider: str = Field(..., min_length=1, max_length=64)
    keys: dict[str, str] = Field(default_factory=dict)


@router.get("/setup/status")
async def setup_status() -> dict[str, Any]:
    """Tell the dashboard whether the first-run wizard should run.

    Used by ``apps/dashboard/src/app/dashboard/layout.tsx`` (or
    equivalent) on mount: if ``complete == false`` and the user
    isn't already on /dashboard/setup, redirect them there.
    """
    check_admin()
    return await secret_store.setup_complete()


@router.post("/setup/keys")
async def setup_keys(req: SetupKeysRequest) -> dict[str, Any]:
    """Persist a batch of provider keys.

    Returns the updated setup-status so the wizard can refresh its
    "what's configured" panel without a second round-trip.
    """
    check_admin()

    provider = req.provider.strip().lower()
    if not provider:
        raise HTTPException(400, "provider is required")
    if not req.keys:
        raise HTTPException(400, "keys map must be non-empty")

    saved: list[str] = []
    deleted: list[str] = []
    for key_name, value in req.keys.items():
        try:
            await secret_store.set_provider_key(provider, key_name, value)
        except ValueError as e:
            raise HTTPException(400, f"invalid key '{key_name}': {e}") from e
        (deleted if value == "" else saved).append(key_name)

    logger.info(
        "admin/setup/keys: provider=%s saved=%s deleted=%s",
        provider, saved, deleted,
    )

    # Make the just-saved keys take effect in THIS running process so the
    # user doesn't have to restart the daemon before they "take". We
    # re-run the same env-hydration bridge the daemon runs at startup:
    # it exports newly-stored keys into os.environ and busts the
    # get_settings() lru_cache so the next read sees them.
    #
    # The concrete win: the Integrations "Connect Gmail" button is gated
    # on `get_settings().google_oauth_client_id` (via the /status route).
    # Before this, saving the OAuth client left that setting empty until
    # a restart, so the button stayed disabled and the flow looked
    # broken. Re-hydrating here flips it live immediately.
    #
    # Caveat: LLM/TTS/STT provider INSTANCES are built once at startup
    # and cache their key, so their is_available() badge still needs a
    # restart to turn green. But setup-status (store-backed) and the
    # Google OAuth flow (settings read per-call) go live now. Best-effort
    # — a failure here never blocks the save (restart is the fallback).
    try:
        from openvox.api.app import _hydrate_secrets_into_env

        await _hydrate_secrets_into_env()
    except Exception as e:  # noqa: BLE001 — never fail the save on this
        logger.warning(
            "setup_keys: live re-hydration failed (restart will still apply keys): %s",
            e,
        )

    return {
        "ok": True,
        "saved": saved,
        "deleted": deleted,
        "status": await secret_store.setup_complete(),
    }


class DeleteKeyRequest(BaseModel):
    provider: str = Field(..., min_length=1, max_length=64)
    key_name: str = Field(..., min_length=1, max_length=64)


@router.delete("/setup/keys")
async def delete_key(req: DeleteKeyRequest) -> dict[str, Any]:
    """Remove a single stored key — env-var fallback resumes."""
    check_admin()
    await secret_store.delete_provider_key(req.provider, req.key_name)
    return {"ok": True, "status": await secret_store.setup_complete()}
