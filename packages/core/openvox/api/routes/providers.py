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
async def list_voices() -> dict[str, Any]:
    """Catalogue of voices we ship with for each TTS provider.

    BytePlus catalogue is the **full TTS 2.0 voice list** mirrored
    from `providers/byteplus/voices.py` (which itself is sourced
    from the BytePlus docs page on the date stamped there). Every
    `id` in the BytePlus list is a real Seed-Speech 2.0 voice — the
    dashboard can safely render this as a dropdown.

    Caveats:
      • Each voice must still be **activated** on the user's BytePlus
        account in the console before use. We can't probe activation
        without burning API quota; expose the catalogue + a "Test
        voice" button so users can validate per-voice on demand.
      • OpenAI / ElevenLabs / Cartesia entries are short curated
        samples — not the full provider catalogue.
    """
    from openvox.providers.byteplus.voices import VOICES, VOICES_REFRESHED_AT

    return {
        "byteplus": [v.to_dict() for v in VOICES],
        "byteplus_refreshed_at": VOICES_REFRESHED_AT,
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
