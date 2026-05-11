"""Provider catalogue + availability checks."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from openvox.providers import ProviderType, get_registry

router = APIRouter()


@router.get("")
async def list_providers(type: str | None = None) -> list[dict[str, Any]]:
    return get_registry().list(ProviderType(type) if type else None)


@router.get("/voices")
async def list_voices() -> dict[str, list[dict[str, str]]]:
    """Catalogue of voices we ship with for each TTS provider.

    These are static for now — production would call each provider's
    `voices` endpoint.
    """
    return {
        # Seed-Speech 2.0 — full catalog at:
        #   https://docs.byteplus.com/en/docs/byteplusvoice/voicelist
        # Each voice must be activated on your BytePlus account before use.
        "byteplus": [
            {"id": "en_male_tim_uranus_bigtts", "name": "Tim (en, male)"},
            {"id": "en_female_skye_emo_v2_mars_bigtts", "name": "Skye — expressive (en, female)"},
            {"id": "en_male_adam_mars_bigtts", "name": "Adam (en, male)"},
            {"id": "en_female_anna_mars_bigtts", "name": "Anna (en, female)"},
            {"id": "zh_female_cancan_mars_bigtts", "name": "Cancan (zh-CN, female)"},
            {"id": "zh_female_vv_uranus_bigtts", "name": "VV (zh-CN, female)"},
            {"id": "ja_female_hina_mars_bigtts", "name": "Hina (ja-JP, female)"},
        ],
        "elevenlabs": [
            {"id": "21m00Tcm4TlvDq8ikWAM", "name": "Rachel"},
            {"id": "AZnzlk1XvdvUeBnXmlld", "name": "Domi"},
            {"id": "EXAVITQu4vr4xnSDxMaL", "name": "Bella"},
        ],
        "openai": [
            {"id": "alloy", "name": "Alloy"},
            {"id": "echo", "name": "Echo"},
            {"id": "fable", "name": "Fable"},
            {"id": "nova", "name": "Nova"},
            {"id": "shimmer", "name": "Shimmer"},
        ],
        "cartesia": [],
    }
