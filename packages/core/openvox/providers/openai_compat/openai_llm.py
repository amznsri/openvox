"""OpenAI chat completions provider."""

from __future__ import annotations

from openvox.config import get_settings
from openvox.providers.base import ProviderCapability
from openvox.providers.openai_compat._openai_base import OpenAICompatLLM


class OpenAILLM(OpenAICompatLLM):
    id = "openai"
    display_name = "OpenAI (GPT-4 / GPT-4o)"
    capabilities = {ProviderCapability.STREAMING, ProviderCapability.TOOL_CALLING, ProviderCapability.VISION}
    default_model = "gpt-4o-mini"
    base_url = "https://api.openai.com/v1/chat/completions"

    def __init__(self) -> None:
        super().__init__()
        self.api_key = get_settings().openai_api_key
