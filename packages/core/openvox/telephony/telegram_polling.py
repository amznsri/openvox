"""Telegram long-polling driver — webhook-free Telegram channel.

This is the polling-mode counterpart to the webhook handler in
``api/routes/telephony.py``. For each Telegram-enabled agent whose
config has ``mode == "polling"``, a background asyncio task
continuously calls Telegram's ``getUpdates`` endpoint to receive
new messages. Each update is dispatched through the SAME
``_handle_telegram_update`` function the webhook handler uses — only
the ingestion path differs.

Why this exists:
    Webhook mode requires a public HTTPS URL (ngrok / a real domain /
    cloud-hosted OpenVox). For a non-technical user setting up a
    personal voice agent on their laptop, that's a 5-step technical
    detour. Polling mode adds zero external infrastructure: the bot
    polls Telegram FROM the user's machine, so NAT / firewalls /
    no-public-IP setups all just work.

Polling vs webhook trade-offs:
    - Polling: no public URL needed. Slightly higher latency (~1s
      avg). Open outbound HTTPS connection per agent.
    - Webhook: instant delivery. Requires public HTTPS URL.

Default for new agents: polling. Webhook stays opt-in via
``channels.telegram.mode = "webhook"`` for production deployments.

Lifecycle:
    * ``start_all_pollers()`` — called from FastAPI's lifespan startup.
      Scans DB for any agent with ``channels.telegram.mode == "polling"``
      and launches a poller task per agent.
    * ``start_polling(agent_id, bot_token)`` — invoked by the connect
      route after a fresh polling-mode setup.
    * ``stop_polling(agent_id)`` — invoked by the disconnect route, AND
      on app shutdown.
    * Per-task error handling: any exception in the poll loop logs +
      backs off 5s, then retries forever. CancelledError exits cleanly.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from openvox.utils.http import make_async_client

logger = logging.getLogger(__name__)

# Registry: agent_id → asyncio.Task. One poll loop per Telegram-enabled
# agent. Multiple agents = multiple concurrent loops (each their own
# bot, each their own outbound HTTPS connection).
_polling_tasks: dict[str, asyncio.Task] = {}


# Long-poll timeout passed to Telegram. 30s means roughly 2 idle
# requests/minute per bot — well under Telegram's 30-req/sec rate
# limit. Lower values increase responsiveness slightly at the cost
# of more outbound connections.
_LONG_POLL_TIMEOUT_S = 30

# Back-off between transient errors. We keep retrying forever; this
# prevents a network blip from spinning the loop tightly.
_ERROR_BACKOFF_S = 5


async def get_updates(
    token: str, *, offset: int = 0, timeout: int = _LONG_POLL_TIMEOUT_S
) -> list[dict[str, Any]]:
    """Long-poll Telegram's ``getUpdates`` endpoint.

    Telegram holds the HTTP connection open up to ``timeout`` seconds
    waiting for a new update; if none arrives, returns an empty list.

    Args:
        token: bot token from BotFather.
        offset: highest ``update_id`` we've already seen + 1. Tells
            Telegram to skip updates we've processed (acknowledges
            them and they won't be re-delivered).
        timeout: long-poll timeout in seconds.

    Returns:
        Raw update objects (https://core.telegram.org/bots/api#update).

    Raises:
        RuntimeError: on Telegram-level API failure (e.g., revoked
            token, wrong update params).
        httpx.HTTPError: on network failure.
    """
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    params = {
        "offset": offset,
        "timeout": timeout,
        # Restrict to message updates — we don't process edits, channel
        # posts, inline queries, etc. yet. Reducing payload + latency.
        "allowed_updates": ["message"],
    }
    # HTTP timeout = long-poll timeout + a safety margin so we don't
    # cancel just before Telegram does.
    async with make_async_client(timeout=timeout + 10) as c:
        r = await c.get(url, params=params)
        r.raise_for_status()
        data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"getUpdates failed: {data.get('description')}")
    return data.get("result", [])


async def _polling_loop(agent_id: str, bot_token: str) -> None:
    """Forever-loop fetching + dispatching updates for one bot.

    Re-reads the agent's channel config on every batch in case the
    user has changed ``reply_mode`` or other fields via the dashboard.
    If the agent has been deleted, exits the loop cleanly.
    """
    # Lazy import to break a circular dependency at module load time
    # (telephony route imports this module; this module would import
    # the route function at top-level otherwise).
    from openvox.api.routes.telephony import _handle_telegram_update
    from openvox.db import db_session
    from openvox.db.models import Agent

    offset = 0
    logger.info("telegram polling started for agent=%s", agent_id)
    try:
        while True:
            try:
                updates = await get_updates(bot_token, offset=offset)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(
                    "telegram polling error for agent=%s: %s — backing off %ds",
                    agent_id, e, _ERROR_BACKOFF_S,
                )
                await asyncio.sleep(_ERROR_BACKOFF_S)
                continue

            for u in updates:
                # Advance offset BEFORE dispatching: if dispatch fails we
                # still won't re-receive this update. Telegram drops the
                # message from its queue once acked via offset.
                offset = u.get("update_id", offset) + 1

                # Re-read agent config in case it changed (e.g., reply_mode
                # toggled in the dashboard).
                async with db_session() as s:
                    a = await s.get(Agent, agent_id)
                    if a is None:
                        logger.info(
                            "agent %s deleted — stopping telegram polling",
                            agent_id,
                        )
                        return
                    cfg = dict((a.channels or {}).get("telegram") or {})

                # Dispatch via the SAME handler the webhook route uses.
                # asyncio.create_task so the next poll batch isn't blocked
                # by a slow LLM turn.
                asyncio.create_task(
                    _handle_telegram_update(agent_id, cfg, u)
                )
    except asyncio.CancelledError:
        logger.info("telegram polling cancelled for agent=%s", agent_id)
        raise


async def start_polling(agent_id: str, bot_token: str) -> None:
    """Start (or restart) the polling task for one agent.

    Idempotent: if a task is already running for this agent, it's
    cancelled first. Use after a fresh connect or after reply_mode /
    bot_token changes.
    """
    await stop_polling(agent_id)
    task = asyncio.create_task(_polling_loop(agent_id, bot_token))
    _polling_tasks[agent_id] = task


async def stop_polling(agent_id: str) -> None:
    """Stop the polling task for one agent. Idempotent."""
    task = _polling_tasks.pop(agent_id, None)
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception("telegram polling task for %s exited with error", agent_id)


async def start_all_pollers() -> None:
    """Scan DB for polling-mode agents at startup and launch their tasks.

    Called from the FastAPI lifespan handler. Bootstraps polling for
    every agent that was connected via polling mode before the last
    restart.
    """
    from sqlalchemy import select

    from openvox.db import db_session
    from openvox.db.models import Agent

    started = 0
    async with db_session() as s:
        agents = (await s.execute(select(Agent))).scalars().all()
        for a in agents:
            cfg = (a.channels or {}).get("telegram") or {}
            # Be EXPLICIT about mode here — we don't want to accidentally
            # start polling for existing webhook-mode agents (which lack
            # a "mode" field because the field didn't exist before).
            # Polling requires the user (or dashboard) to set mode="polling"
            # explicitly during connect.
            if cfg.get("mode") == "polling" and cfg.get("bot_token"):
                try:
                    await start_polling(a.id, cfg["bot_token"])
                    started += 1
                except Exception:
                    logger.exception(
                        "failed to start telegram polling for agent=%s", a.id
                    )
    if started:
        logger.info("telegram polling: started %d agent(s) at boot", started)


async def stop_all_pollers() -> None:
    """Gracefully cancel every polling task. Called at app shutdown."""
    agent_ids = list(_polling_tasks.keys())
    for aid in agent_ids:
        await stop_polling(aid)
    if agent_ids:
        logger.info("telegram polling: stopped %d agent(s)", len(agent_ids))
