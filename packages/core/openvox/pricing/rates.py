"""Provider rate cards + per-session cost calculator.

Why this lives in a separate module:
    Voice agents have an unusual cost structure — three or four metered
    components per minute (STT, LLM input, LLM output, TTS, optionally
    telephony) all priced in different units (per second, per 1M tokens,
    per 1k characters). A dashboard "this session cost $0.06" number
    means stitching all of that together. Doing it inline in routes is
    a recipe for inconsistency, so we centralise here.

Every entry below carries `source_url`, `model_name`, and `verified_at`
so the rate card is **auditable**: future-you (or any user reviewing
the recommendation engine) can click straight through to the cited
pricing page and check whether it's drifted.

Override the whole thing by setting `OPENVOX_RATES_FILE=/path/to/yaml`
(useful for negotiated enterprise discounts).

Pricing units in the dict:
    stt_usd_per_minute       — straight per-minute rate for streaming STT.
    llm_usd_per_1m_input     — input-token cost; multiply by tokens_in / 1e6.
    llm_usd_per_1m_output    — output-token cost; multiply by tokens_out / 1e6.
    tts_usd_per_1k_chars     — TTS cost; multiply by chars / 1000.

Bug-register note (CLAUDE.md §8): the original cut of this module
hardcoded numbers from training-data recollection without citations.
The user spotted DeepSeek being suspiciously low and asked for sources.
This rewrite addresses that — every figure is now cited or marked
`unverified` if we couldn't fetch the live page.
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
    """Pricing for one provider id. Missing fields → treated as $0.

    Auditability fields:
        model_name   — the specific SKU these rates apply to (so it's
                       obvious when a provider has multiple tiers).
        source_url   — where the rates were copied from. Click-through.
        verified_at  — ISO date when a human last checked the source.
                       Empty string ⇒ rate is unverified / from memory.
        notes        — free-form caveats (Azure markup, plan tier, etc.).
    """

    stt_usd_per_minute: float = 0.0
    llm_usd_per_1m_input: float = 0.0
    llm_usd_per_1m_output: float = 0.0
    tts_usd_per_1k_chars: float = 0.0
    model_name: str = ""
    source_url: str = ""
    verified_at: str = ""
    notes: str = ""


# Rate card. Numbers cited where possible against live provider pages
# on the `verified_at` date. Override per-entry via OPENVOX_RATES_FILE.
#
# IMPORTANT: When you refresh a rate, also bump `verified_at` to today.
# The dashboard surfaces verified_at so users know how stale the card is.
DEFAULT_RATES: dict[str, ProviderRates] = {
    # ── LLM providers ──────────────────────────────────────────────
    "byteplus": ProviderRates(
        llm_usd_per_1m_input=0.50,
        llm_usd_per_1m_output=3.00,
        # STT/TTS rates here are best-effort estimates — the BytePlus
        # console / docs pages we can fetch don't expose voice-product
        # pricing in machine-readable form. Treat with caution.
        stt_usd_per_minute=0.006,
        tts_usd_per_1k_chars=0.012,
        model_name="seed-2-0-pro-260328",
        source_url="https://docs.byteplus.com/en/docs/ModelArk/1544106",
        verified_at="2026-05-19",
        notes=(
            "LLM rates verified against BytePlus ModelArk docs. STT "
            "(Seed ASR 2.0) and TTS (Seed-Speech 2.0) rates are "
            "unverified estimates — pricing pages are gated."
        ),
    ),
    "openai": ProviderRates(
        llm_usd_per_1m_input=2.50,
        llm_usd_per_1m_output=15.00,
        tts_usd_per_1k_chars=0.015,  # tts-1 standard; HD is 0.030
        model_name="gpt-5.4 (short-context ≤272K)",
        source_url="https://openai.com/api/pricing/",
        verified_at="2026-05-19",
        notes=(
            "gpt-5.4 short-context tier. Above 272K input doubles to "
            "$5/1M. Cached input drops to $1.25/1M (50% off). TTS rate "
            "is tts-1 standard ($0.015/1k chars); tts-1-hd is $0.030."
        ),
    ),
    "anthropic": ProviderRates(
        llm_usd_per_1m_input=3.00,
        llm_usd_per_1m_output=15.00,
        model_name="claude-sonnet-4.6",
        source_url="https://platform.claude.com/docs/en/about-claude/pricing",
        verified_at="2026-05-19",
        notes=(
            "claude-sonnet-4.6 standard. Opus 4.7 is $5/$25; Haiku 4.5 "
            "is $1/$5. Prompt-cache + batch can compound to 95% off."
        ),
    ),
    "gemini": ProviderRates(
        llm_usd_per_1m_input=2.00,
        llm_usd_per_1m_output=12.00,
        model_name="gemini-3.1-pro (≤200K context)",
        source_url="https://ai.google.dev/gemini-api/docs/pricing",
        verified_at="2026-05-19",
        notes=(
            "gemini-3.1-pro at ≤200K context. Above 200K is $4/$18. "
            "Context-cached input drops to $0.20/1M (90% off). Batch "
            "API is $1/$6 (≤200K)."
        ),
    ),
    "deepseek": ProviderRates(
        # User directive: take Azure-hosted DeepSeek pricing, not the
        # official deepseek.com page. Microsoft's Azure AI Foundry
        # pricing is anchored to official DeepSeek rates with a typical
        # 20-35% markup for the integration layer. Using 20% midpoint
        # over published V4-flash rate ($0.14 / $0.28).
        # NOTE: Live Azure pricing page (link below) consistently times
        # out from our environment — when you can fetch it, replace
        # these with the literal Azure-listed figures and bump
        # verified_at.
        llm_usd_per_1m_input=0.17,
        llm_usd_per_1m_output=0.34,
        model_name="deepseek-v4-flash (Azure AI Foundry hosted)",
        source_url="https://azure.microsoft.com/en-us/pricing/details/ai-foundry-models/deepseek/",
        verified_at="",  # estimate — not directly verified, hence empty
        notes=(
            "Azure-hosted DeepSeek V4-flash. Base DeepSeek rate is "
            "$0.14/$0.28 per 1M; Azure adds ~20-35% markup. Figures "
            "above use 20% midpoint. V4-pro is currently 75% off "
            "($0.435/$0.87) on the official page until 2026-05-31; "
            "Azure markup applies on top."
        ),
    ),
    # ── STT-only providers ─────────────────────────────────────────
    "deepgram": ProviderRates(
        stt_usd_per_minute=0.0077,
        model_name="nova-3 monolingual (pay-as-you-go)",
        source_url="https://deepgram.com/pricing",
        verified_at="2026-05-19",
        notes=(
            "Nova-3 monolingual streaming on PAYG. Multilingual "
            "is $0.0092/min. Growth plan drops monolingual to "
            "$0.0065/min (min $4k annual)."
        ),
    ),
    "assemblyai": ProviderRates(
        stt_usd_per_minute=0.0061,
        model_name="universal real-time streaming",
        source_url="https://www.assemblyai.com/pricing/",
        verified_at="2026-05-19",
        notes=(
            "Universal real-time streaming. Billed on connection time, "
            "not audio duration — idle counts. Add-ons (diarisation, "
            "entity-detection) stack on top."
        ),
    ),
    "whisper": ProviderRates(
        stt_usd_per_minute=0.006,
        model_name="whisper-1",
        source_url="https://openai.com/api/pricing/",
        verified_at="2026-05-19",
        notes=(
            "whisper-1 managed transcription. gpt-4o-mini-transcribe "
            "is half the rate ($0.003/min) for the same modality."
        ),
    ),
    # ── TTS-only providers ─────────────────────────────────────────
    "elevenlabs": ProviderRates(
        tts_usd_per_1k_chars=0.165,
        model_name="multilingual v2 (Starter plan effective rate)",
        source_url="https://elevenlabs.io/pricing/api",
        verified_at="2026-05-19",
        notes=(
            "Multilingual v2 on the Starter plan. Flash v2.5 model "
            "drops to $0.05/1k under Creator. Higher tiers reduce "
            "credit-per-character ratio."
        ),
    ),
    "cartesia": ProviderRates(
        tts_usd_per_1k_chars=0.050,
        model_name="sonic-3 (pay-as-you-go)",
        source_url="https://cartesia.ai/pricing",
        verified_at="2026-05-19",
        notes=(
            "Sonic-3 streaming at $50/1M chars PAYG. Sub-100ms "
            "first-byte latency makes it competitive for real-time."
        ),
    ),
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
