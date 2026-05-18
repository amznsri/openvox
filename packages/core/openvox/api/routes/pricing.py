"""Pricing routes — rate card + per-session estimate + "what-if" comparison.

Three endpoints:
  GET  /api/v1/pricing/rates           — current rate card (after env overrides)
  POST /api/v1/pricing/estimate        — cost breakdown for one synthetic call
  GET  /api/v1/pricing/sessions/{id}   — actual telemetry cost for a real session
                                          + "what if I switched providers?" matrix

The "what-if" matrix is the differentiator: given an actual session's
duration / tokens / TTS chars, recompute total cost for every plausible
provider combo so the dashboard can render a "you could save $X by
switching from OpenAI to BytePlus" recommendation.
"""

from __future__ import annotations

from itertools import product
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from openvox.db import db_session
from openvox.db.models import Agent, Session as DBSession
from openvox.pricing import estimate_session_cost, load_rates

router = APIRouter()


# ── Rate card ───────────────────────────────────────────────────────


@router.get("/rates")
async def get_rates() -> dict[str, Any]:
    """Return all known provider rates as a flat dict. The dashboard
    pricing card uses this for the "rate card" expander + edit hint."""
    rates = load_rates()
    return {
        "providers": {
            pid: {
                "stt_usd_per_minute": r.stt_usd_per_minute,
                "stt_usd_per_1m_chars": r.stt_usd_per_1m_chars,
                "llm_usd_per_1m_input": r.llm_usd_per_1m_input,
                "llm_usd_per_1m_output": r.llm_usd_per_1m_output,
                "tts_usd_per_1k_chars": r.tts_usd_per_1k_chars,
                # Auditability surface — dashboard renders these so
                # users can click through to the live pricing page and
                # see when a rate was last verified.
                "model_name": r.model_name,
                "source_url": r.source_url,
                "verified_at": r.verified_at,
                "notes": r.notes,
            }
            for pid, r in rates.items()
        },
        "override_via": "OPENVOX_RATES_FILE=/path/to/rates.json",
    }


# ── Estimate from a synthetic input ────────────────────────────────


class EstimateRequest(BaseModel):
    duration_ms: int
    tokens_in: int = 0
    tokens_out: int = 0
    tts_chars: int = 0
    stt_provider: str = "byteplus"
    llm_provider: str = "byteplus"
    tts_provider: str = "byteplus"


@router.post("/estimate")
async def estimate(req: EstimateRequest) -> dict[str, Any]:
    return estimate_session_cost(
        duration_ms=req.duration_ms,
        tokens_in=req.tokens_in,
        tokens_out=req.tokens_out,
        tts_chars=req.tts_chars,
        stt_provider=req.stt_provider,
        llm_provider=req.llm_provider,
        tts_provider=req.tts_provider,
    )


# ── Real session cost + what-if matrix ─────────────────────────────


@router.get("/sessions/{session_id}")
async def session_pricing(session_id: str) -> dict[str, Any]:
    """Compute actual cost for a recorded session + alternatives.

    The "what-if" matrix swaps STT × LLM × TTS providers and recomputes
    cost for each plausible combo, so the dashboard can show
    "this call cost $0.061; switching TTS to Cartesia would save $0.018".
    """
    async with db_session() as s:
        sess = await s.get(DBSession, session_id)
        if sess is None:
            raise HTTPException(404, "session not found")
        agent = await s.get(Agent, sess.agent_id)
        if agent is None:
            raise HTTPException(404, "agent not found")
        stt_now = agent.stt_provider
        llm_now = agent.llm_provider
        tts_now = agent.tts_provider
        duration_ms = sess.duration_ms or 0
        # If the orchestrator never wrote real counters (older sessions
        # from before this column existed) we still want a sensible
        # estimate. Assume 80 tokens per minute of conversation as a
        # rough placeholder rather than $0.
        tokens_in = sess.llm_tokens_in or max(0, duration_ms // 60_000 * 60)
        tokens_out = sess.llm_tokens_out or max(0, duration_ms // 60_000 * 120)
        tts_chars = sess.tts_chars or max(0, duration_ms // 60_000 * 600)

    actual = estimate_session_cost(
        duration_ms=duration_ms,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        tts_chars=tts_chars,
        stt_provider=stt_now,
        llm_provider=llm_now,
        tts_provider=tts_now,
    )

    rates = load_rates()
    stt_options = [pid for pid, r in rates.items() if r.stt_usd_per_minute > 0]
    llm_options = [pid for pid, r in rates.items() if r.llm_usd_per_1m_input > 0]
    tts_options = [pid for pid, r in rates.items() if r.tts_usd_per_1k_chars > 0]

    # Cap the matrix to keep payload reasonable (5×5×3 = 75 max).
    alternatives: list[dict[str, Any]] = []
    for s_pid, l_pid, t_pid in product(stt_options[:5], llm_options[:5], tts_options[:5]):
        if s_pid == stt_now and l_pid == llm_now and t_pid == tts_now:
            continue  # skip the current combo
        alt = estimate_session_cost(
            duration_ms=duration_ms,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            tts_chars=tts_chars,
            stt_provider=s_pid,
            llm_provider=l_pid,
            tts_provider=t_pid,
        )
        alternatives.append({
            "combo": {"stt": s_pid, "llm": l_pid, "tts": t_pid},
            "total_usd": alt["total_usd"],
            "delta_usd": round(alt["total_usd"] - actual["total_usd"], 6),
        })
    # Sort cheapest first.
    alternatives.sort(key=lambda x: x["total_usd"])
    cheapest = alternatives[0] if alternatives else None
    savings_vs_cheapest = (
        round(actual["total_usd"] - cheapest["total_usd"], 6) if cheapest else 0
    )

    return {
        "session_id": session_id,
        "duration_ms": duration_ms,
        "telemetry": {
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "tts_chars": tts_chars,
            "estimated_from_duration": (
                (sess.llm_tokens_in or 0) == 0  # noqa: F821
            ),
        },
        "actual": actual,
        "alternatives": alternatives[:20],  # top 20 cheapest
        "cheapest": cheapest,
        "savings_vs_cheapest_usd": savings_vs_cheapest,
    }
