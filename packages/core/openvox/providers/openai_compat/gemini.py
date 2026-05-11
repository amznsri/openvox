"""Google Gemini — uses the OpenAI-compatible endpoint Google ships at
`generativelanguage.googleapis.com/v1beta/openai/`. This is officially
supported and lets us reuse the OpenAI streaming logic."""

from __future__ import annotations

from openvox.config import get_settings
from openvox.providers.base import ProviderCapability
from openvox.providers.openai_compat._openai_base import OpenAICompatLLM


class GeminiLLM(OpenAICompatLLM):
    id = "gemini"
    display_name = "Google Gemini"
    capabilities = {ProviderCapability.STREAMING, ProviderCapability.TOOL_CALLING, ProviderCapability.VISION}
    default_model = "gemini-2.0-flash"
    base_url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"

    def __init__(self) -> None:
        super().__init__()
        self.api_key = get_settings().gemini_api_key
