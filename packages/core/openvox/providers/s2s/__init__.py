"""Speech-to-Speech (S2S) provider adapters.

Each module wraps a bidirectional voice model (OpenAI Realtime,
Gemini Live, etc.) and exposes it via the ``S2SProvider`` interface
defined in ``openvox.providers.base``.

Today: only ``openai_realtime`` ships. Gemini Live and Anthropic's
upcoming S2S API are likely future additions following the same
pattern — adapter translates from vendor event protocol down to
the canonical ``S2SEvent`` shape.
"""

from openvox.providers.s2s.openai_realtime import OpenAIRealtimeProvider

__all__ = ["OpenAIRealtimeProvider"]
