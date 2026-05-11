"""Anthropic Messages API — translated to LLM provider interface."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from openvox.config import get_settings
from openvox.providers.base import (
    LLMConfig,
    LLMMessage,
    LLMProvider,
    LLMResponseChunk,
    ProviderCapability,
)


class AnthropicLLM(LLMProvider):
    id = "anthropic"
    display_name = "Anthropic Claude"
    capabilities = {ProviderCapability.STREAMING, ProviderCapability.TOOL_CALLING, ProviderCapability.VISION}

    def __init__(self) -> None:
        s = get_settings()
        self._api_key = s.anthropic_api_key
        self._client: httpx.AsyncClient | None = None
        self._url = "https://api.anthropic.com/v1/messages"

    def is_available(self) -> bool:
        return bool(self._api_key)

    async def warmup(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(60.0, connect=5.0),
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
            )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def chat_stream(
        self, messages: list[LLMMessage], config: LLMConfig
    ) -> AsyncIterator[LLMResponseChunk]:
        if not self.is_available():
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        await self.warmup()
        assert self._client is not None

        # Anthropic separates `system` from `messages`.
        system_chunks = [m.content for m in messages if m.role == "system"]
        msgs = [
            {"role": "user" if m.role == "user" else "assistant", "content": m.content}
            for m in messages
            if m.role in ("user", "assistant")
        ]
        body: dict[str, Any] = {
            "model": config.model or "claude-3-5-sonnet-20241022",
            "max_tokens": config.max_tokens,
            "temperature": config.temperature,
            "messages": msgs,
            "stream": config.stream,
        }
        if system_chunks:
            body["system"] = "\n".join(system_chunks)

        if not config.stream:
            r = await self._client.post(self._url, json=body)
            r.raise_for_status()
            data = r.json()
            content = "".join(b.get("text", "") for b in data.get("content", []))
            yield LLMResponseChunk(delta=content, finish_reason=data.get("stop_reason"), raw=data)
            return

        async with self._client.stream("POST", self._url, json=body) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                payload = line.removeprefix("data:").strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    obj = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") == "content_block_delta":
                    delta = obj.get("delta", {}).get("text") or ""
                    if delta:
                        yield LLMResponseChunk(delta=delta, raw=obj)
                elif obj.get("type") == "message_stop":
                    return
