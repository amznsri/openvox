"""WhatsApp Personal — Python orchestrator for the Node bridge.

OpenVox's "WhatsApp Personal" channel uses the unofficial WhatsApp Web
protocol via whatsapp-web.js. Unlike the WhatsApp Business API (which
is webhook-based), Personal mode:

  * Does NOT need a public URL — the connection is outbound from the
    user's machine to Meta's servers.
  * Authenticates by scanning a QR code with the user's phone, exactly
    like web.whatsapp.com.
  * Has a real, documented account-ban risk. Meta does not sanction
    this protocol; accounts using it can be banned without warning.

This module is the Python side of the bridge. It speaks HTTP to the
Node service in ``packages/whatsapp_personal_bridge`` which holds the
actual whatsapp-web.js Client instances. The bridge multiplexes
multiple agents — one ``Client`` per agent_id, all in one Node
process.

Lifecycle:
  * ``connect(agent_id)``       — POSTs the bridge to spin up a client
  * ``status(agent_id)``        — GETs current state (qr | ready | etc.)
  * ``send_text(agent_id, to, body)`` — POSTs bridge to send
  * ``disconnect(agent_id)``    — DELETEs bridge session (wipes auth)
  * ``start_all_sessions()``    — bootstrap at app startup for agents
                                   that have whatsapp_personal enabled
                                   in their channels config
  * ``stop_all_sessions()``     — graceful shutdown
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def _bridge_base_url() -> str:
    """Resolve the bridge's HTTP root.

    In Docker mode (the only mode we ship today), the bridge service is
    reachable at ``http://whatsapp-bridge:4100``. In CLI mode (Phase 4
    future work) this becomes ``http://localhost:4100`` and the bridge
    runs as a Node subprocess we manage from Python.

    Override via ``OPENVOX_WHATSAPP_BRIDGE_URL`` for tests / non-default
    hostnames.
    """
    return os.environ.get(
        "OPENVOX_WHATSAPP_BRIDGE_URL", "http://whatsapp-bridge:4100"
    ).rstrip("/")


# Reasonable per-request timeout. The bridge replies fast on /start
# (initialization is async — actual QR / ready events lag behind the
# HTTP response). /status polling is even faster.
_HTTP_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


async def _http() -> httpx.AsyncClient:
    """Single-purpose helper so we apply consistent timeouts everywhere."""
    return httpx.AsyncClient(timeout=_HTTP_TIMEOUT, base_url=_bridge_base_url())


async def is_bridge_reachable() -> bool:
    """True iff the bridge container is up and responding to /health.

    Used by the dashboard to surface a friendly "bridge not running —
    run ``docker compose --profile whatsapp up`` first" hint instead of
    a generic 502.
    """
    try:
        async with await _http() as c:
            r = await c.get("/health")
            return r.status_code == 200
    except Exception as e:
        logger.debug("whatsapp bridge unreachable: %s", e)
        return False


async def connect(agent_id: str) -> dict[str, Any]:
    """Spin up (or restart) the whatsapp client for this agent.

    The HTTP response is immediate — the bridge starts the Puppeteer/
    chromium boot in the background and the QR / ready events come
    later via subsequent ``status()`` polls.
    """
    async with await _http() as c:
        r = await c.post(f"/sessions/{agent_id}/start")
        r.raise_for_status()
        return r.json()


async def status(agent_id: str) -> dict[str, Any]:
    """Current state of one agent's whatsapp client.

    Returns one of:
      {status: "not_started"}                          — never connected (or bridge restarted)
      {status: "initializing"}                         — Puppeteer booting
      {status: "qr", qr: "data:image/png;base64,..."}  — waiting for phone scan
      {status: "authenticated"}                         — transient between qr-scan and ready
      {status: "ready", info: {wid, pushname, ...}}    — connected, can send/receive
      {status: "disconnected", last_error: "..."}      — user logged out / phone unreachable
      {status: "error", last_error: "..."}             — auth failure or bridge crash

    Never raises for protocol-level failures; callers see the state in
    the response. Raises only for true HTTP / network errors.
    """
    async with await _http() as c:
        r = await c.get(f"/sessions/{agent_id}/status")
        r.raise_for_status()
        return r.json()


async def send_text(agent_id: str, to: str, body: str) -> None:
    """Send a text message via the agent's whatsapp session.

    Args:
        agent_id: which agent's whatsapp client to send through.
        to: WhatsApp chat-id, e.g., ``"15551234567@c.us"``. Inbound
            messages always include this format in their ``from`` field.
        body: the message text.

    Raises:
        RuntimeError: if the session isn't in ``ready`` state.
    """
    async with await _http() as c:
        r = await c.post(
            f"/sessions/{agent_id}/send",
            json={"to": to, "body": body},
        )
        if r.status_code == 503:
            payload = r.json()
            raise RuntimeError(
                f"whatsapp session for agent={agent_id} not ready: "
                f"{payload.get('status')} — {payload.get('error')}"
            )
        r.raise_for_status()


async def disconnect(agent_id: str) -> None:
    """Tear down the agent's whatsapp client AND wipe persisted auth.

    Wiping auth means the next connect requires a fresh QR scan. That's
    the right behaviour for a "disconnect" UX — if the user just wanted
    to pause, we'd add a separate ``pause`` endpoint. Today we treat
    disconnect == full logout.
    """
    async with await _http() as c:
        r = await c.delete(f"/sessions/{agent_id}")
        # 404 from bridge is fine (no such session); other 4xx/5xx are
        # genuine failures we want to surface.
        if r.status_code not in (200, 404):
            r.raise_for_status()


# ── Lifecycle helpers called from openvox/api/app.py lifespan ─────────


async def start_all_sessions() -> None:
    """Bootstrap whatsapp clients for any agent flagged enabled in DB.

    Called from the FastAPI lifespan startup hook. Skips silently if the
    bridge isn't reachable (the operator hasn't run ``--profile whatsapp
    up``) — those agents will just show ``not_started`` in the dashboard
    until the operator brings the bridge online.
    """
    from sqlalchemy import select

    from openvox.db import db_session
    from openvox.db.models import Agent

    if not await is_bridge_reachable():
        logger.info(
            "whatsapp bridge not reachable — skipping auto-start "
            "(run `docker compose --profile whatsapp up` to enable)"
        )
        return

    started = 0
    async with db_session() as s:
        agents = (await s.execute(select(Agent))).scalars().all()
        for a in agents:
            cfg = (a.channels or {}).get("whatsapp_personal") or {}
            if cfg.get("enabled"):
                try:
                    await connect(a.id)
                    started += 1
                except Exception:
                    logger.exception(
                        "failed to start whatsapp_personal for agent=%s", a.id
                    )
    if started:
        logger.info("whatsapp_personal: started %d agent(s) at boot", started)


async def stop_all_sessions() -> None:
    """No-op today.

    The bridge handles its own graceful shutdown via SIGTERM (sent by
    Docker on stop). We don't need to call /sessions/<id> DELETE here
    because we want sessions to survive a core restart — calling DELETE
    here would wipe LocalAuth and force the user to re-scan on every
    core reboot.

    Kept as a symmetric counterpart to start_all_sessions() so future
    cleanup work (e.g., flushing telemetry) has an obvious place to
    hook in.
    """
    return None
