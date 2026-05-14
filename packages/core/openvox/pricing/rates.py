"""Provider rate cards + per-session cost calculator.

Why this lives in a separate module:
    Voice agents have an unusual cost structure — three or four metered
    components per minute (STT, LLM input, LLM output, TTS, optionally
    telephony) all priced in different units (per second, per 1M tokens,
    per 1k characters). A dashboard "this session cost $0.06" number
    means stitching all of that together. Doing it inline in routes is
    a recipe for inconsistency, so we centralise here.

Rates as of 2026-05-14 — most providers update prices quarterly. If a
rate moves, edit the dict below OR set `OPENVOX_RATES_FILE=/path/to/yaml`
to override entirely (useful for negotiated enterprise discounts).

Pricing units in the dict:
    stt_usd_per_minute       — straight per-minute rate for streaming STT.
    llm_usd_per_1m_input     — input-token cost; multiply by tokens_in / 1e6.
    llm_usd_per_1m_output    — output-token cost; multiply by tokens_out / 1e6.
    tts_usd_per_1k_chars     — TTS cost; multiply by chars / 1000.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ProviderRates:
    """Pricing for one provider id. Missing fields → treated as $0."""

    stt_usd_per_minute: float = 0.0
    llm_usd_per_1m_input: float = 0.0
    llm_usd_per_1m_output: float = 0.0
    tts_usd_per_1k_chars: float = 0.0
    notes: str = ""


# Rate card. Numbers reflect public pricing as of 2026-05-14. Where a
# provider publishes ranges (input/output tier), we pick the cheapest
# tier most agents actually hit. Override via OPENVOX_RATES_FILE.
DEFAULT_RATES: dict[str, ProviderRates] = {
    # LLM rates
    "byteplus":  ProviderRates(llm_usd_per_1m_input=0.20,  llm_usd_per_1m_output=0.60,
                                stt_usd_per_minute=0.006,   tts_usd_per_1k_chars=0.012,
                                notes="BytePlus Seed-2.0 Pro tier; Asia-region prices."),
    "openai":    ProviderRates(llm_usd_per_1m_input=2.50,  llm_usd_per_1m_output=10.00,
                                tts_usd_per_1k_chars=0.015,
                                notes="gpt-4o; openai TTS-1 standard."),
    "anthropic": ProviderRates(llm_usd_per_1m_input=3.00,  llm_usd_per_1m_output=15.00,
                                notes="claude-3.5-sonnet."),
    "gemini":    ProviderRates(llm_usd_per_1m_input=1.25,  llm_usd_per_1m_output=5.00,
                                notes="gemini-1.5-pro standard tier."),
    "deepseek":  ProviderRates(llm_usd_per_1m_input=0.14,  llm_usd_per_1m_output=0.28,
                                notes="deepseek-chat."),
    # STT-only providers
    "deepgram":   ProviderRates(stt_usd_per_minute=0.0043,
                                notes="Nova-2 streaming."),
    "assemblyai": ProviderRates(stt_usd_per_minute=0.0065,
                                notes="real-time tier."),
    "whisper":    ProviderRates(stt_usd_per_minute=0.006,
                                notes="OpenAI Whisper hosted."),
    # TTS-only providers
    "elevenlabs": ProviderRates(tts_usd_per_1k_chars=0.165,
                                notes="Multilingual v2 voices, Starter plan."),
    "cartesia":   ProviderRates(tts_usd_per_1k_chars=0.045,
                                notes="Sonic-2 standard."),
}


def load_rates() -> dict[str, ProviderRates]:
    """Return DEFAULT_RATES merged with OPENVOX_RATES_FILE overrides.

    Override file is JSON:
        {
          "byteplus": {"llm_usd_per_1m_input": 0.15},
          "openai":   {"stt_usd_per_minute": 0.005}
        }
    Unknown keys in the override are *added* — handy for ad-hoc
    providers (e.g. a self-hosted LLM with $0 cost).
    """
    override_path = os.environ.get("OPENVOX_RATES_FILE", "")
    if not override_path:
        return dict(DEFAULT_RATES)
    try:
        data = json.loads(Path(override_path).read_text())
    except Exception as e:
        logger.warning("could not read %s: %s — using defaults", override_path, e)
        return dict(DEFAULT_RATES)

    merged = {pid: ProviderRates(**rates.__dict__) for pid, rates in DEFAULT_RATES.items()}
    for pid, fields in data.items():
        if not isinstance(fields, dict):
            continue
        existing = merged.get(pid) or ProviderRates()
        for k, v in fields.items():
            if hasattr(existing, k):
                setattr(existing, k, v)
        merged[pid] = existing
    return merged


def estimate_session_cost(
    *,
    duration_ms: int,
    tokens_in: int,
    tokens_out: int,
    tts_chars: int,
    stt_provider: str,
    llm_provider: str,
    tts_provider: str,
    rates: dict[str, ProviderRates] | None = None,
) -> dict:
    """Return a per-component cost breakdown.

    Returns:
        {
          "total_usd": 0.0612,
          "components": {
            "stt": 0.0021,
            "llm_input": 0.0040,
            "llm_output": 0.0312,
            "tts": 0.0239,
          },
          "rate_card": "byteplus / byteplus / byteplus",
          "warnings": ["no rate for provider 'foo' — assumed $0"],
        }
    """
    rt = rates or load_rates()
    warnings: list[str] = []

    def _r(pid: str) -> ProviderRates:
        r = rt.get(pid)
        if r is None:
            warnings.append(f"no rate for provider '{pid}' — assumed $0")
            return ProviderRates()
        return r

    stt = _r(stt_provider)
    llm = _r(llm_provider)
    tts = _r(tts_provider)

    minutes = max(0.0, duration_ms / 60_000.0)
    cost_stt = stt.stt_usd_per_minute * minutes
    cost_in = llm.llm_usd_per_1m_input * (tokens_in / 1_000_000.0)
    cost_out = llm.llm_usd_per_1m_output * (tokens_out / 1_000_000.0)
    cost_tts = tts.tts_usd_per_1k_chars * (tts_chars / 1000.0)

    return {
        "total_usd": round(cost_stt + cost_in + cost_out + cost_tts, 6),
        "components": {
            "stt": round(cost_stt, 6),
            "llm_input": round(cost_in, 6),
            "llm_output": round(cost_out, 6),
            "tts": round(cost_tts, 6),
        },
        "rate_card": f"{stt_provider} / {llm_provider} / {tts_provider}",
        "warnings": warnings,
    }
