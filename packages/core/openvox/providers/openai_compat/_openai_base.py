"""Shared OpenAI-compatible streaming client logic."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from openvox.providers.base import (
    LLMConfig,
    LLMMessage,
    LLMProvider,
    LLMResponseChunk,
)

logger = logging.getLogger(__name__)


class OpenAICompatLLM(LLMProvider):
    """Stream chat completions from any OpenAI-compatible endpoint."""

    base_url: str = ""
    api_key: str = ""
    default_model: str = ""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    def is_available(self) -> bool:
        return bool(self.api_key)

    async def warmup(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(60.0, connect=5.0),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
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
            raise RuntimeError(f"{self.__class__.__name__}: api_key not set")
        await self.warmup()
        assert self._client is not None

        body: dict[str, Any] = {
            "model": config.model or self.default_model,
            "messages": [self._serialize(m) for m in messages],
            "stream": config.stream,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
        }
        if config.tools:
            body["tools"] = config.tools
        # Same trick as the BytePlus path: opt in to the terminal usage
        # frame on streaming so the orchestrator can record real token
        # counts. Providers that don't support this flag will simply
        # ignore it (OpenAI, DeepSeek, Gemini-OpenAI-compat all do).
        if config.stream:
            body["stream_options"] = {"include_usage": True}

        if not config.stream:
            r = await self._client.post(self.base_url, json=body)
            r.raise_for_status()
            data = r.json()
            ch = data["choices"][0]
            yield LLMResponseChunk(
                delta=ch["message"].get("content") or "",
                finish_reason=ch.get("finish_reason"),
                tool_calls=ch["message"].get("tool_calls"),
                usage=data.get("usage") or None,
                raw=data,
            )
            return

        async with self._client.stream("POST", self.base_url, json=body) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                payload = line.removeprefix("data:").strip()
                if payload == "[DONE]":
                    return
                try:
                    obj = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                # Final usage chunk has empty choices[] but populated usage.
                choices = obj.get("choices") or []
                ch = choices[0] if choices else {}
                delta = ch.get("delta") or {}
                yield LLMResponseChunk(
                    delta=delta.get("content") or "",
                    finish_reason=ch.get("finish_reason"),
                    tool_calls=delta.get("tool_calls"),
                    usage=obj.get("usage") or None,
                    raw=obj,
                )

    @staticmethod
    def _serialize(m: LLMMessage) -> dict[str, Any]:
        d: dict[str, Any] = {"role": m.role, "content": m.content}
        if m.tool_call_id:
            d["tool_call_id"] = m.tool_call_id
        if m.name:
            d["name"] = m.name
        if m.tool_calls:
            d["tool_calls"] = m.tool_calls
        return d
