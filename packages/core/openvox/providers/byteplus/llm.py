"""BytePlus Ark — Seed-2.0 / Doubao chat completions.

Ark exposes an OpenAI-compatible REST API. We target it directly with
httpx so we don't pull in the full openai SDK for a single endpoint.

Endpoints:
    Intl.  https://ark.ap-southeast.bytepluses.com/api/v3/chat/completions
    China  https://ark.cn-beijing.volces.com/api/v3/chat/completions

Auth:
    Authorization: Bearer <BYTEPLUS_LLM_API_KEY>

Body (subset, OpenAI-compatible):
    {
      "model": "doubao-seed-1.6-250615" | "ep-xxxx" (deployed endpoint id),
      "messages": [{"role": "user", "content": "..."}],
      "stream": true,
      "temperature": 0.7,
      "max_tokens": 2048,
      "tools": [...]            # OpenAI tool-calling shape
    }

Streaming response is SSE: lines of the form `data: {...json...}` ending
with `data: [DONE]`. Each chunk has `choices[0].delta.content`.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from openvox.config import get_settings
from openvox.utils.http import make_async_client
from openvox.providers.base import (
    LLMConfig,
    LLMMessage,
    LLMProvider,
    LLMResponseChunk,
    ProviderCapability,
)

logger = logging.getLogger(__name__)


class BytePlusLLM(LLMProvider):
    id = "byteplus"
    display_name = "BytePlus Seed (Ark)"
    capabilities = {
        ProviderCapability.STREAMING,
        ProviderCapability.TOOL_CALLING,
        ProviderCapability.VISION,
    }

    def __init__(self) -> None:
        s = get_settings()
        self._api_key = s.byteplus_llm_api_key
        self._endpoint = s.byteplus_llm_endpoint
        # If a deployed inference endpoint id (ep-xxx) is supplied, we
        # prefer it; otherwise we use the model id from config.
        self._endpoint_id = s.byteplus_llm_endpoint_id
        self._default_model = s.byteplus_llm_model
        self._client: httpx.AsyncClient | None = None

    def is_available(self) -> bool:
        return bool(self._api_key)

    async def warmup(self) -> None:
        if self._client is None:
            self._client = make_async_client(
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
            )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _model_id(self, requested: str) -> str:
        # Endpoint id wins, then explicit `requested`, then default.
        if self._endpoint_id:
            return self._endpoint_id
        return requested or self._default_model

    async def chat_stream(
        self, messages: list[LLMMessage], config: LLMConfig
    ) -> AsyncIterator[LLMResponseChunk]:
        if not self.is_available():
            raise RuntimeError("BYTEPLUS_LLM_API_KEY is not set")
        await self.warmup()
        assert self._client is not None

        body: dict[str, Any] = {
            "model": self._model_id(config.model),
            "messages": [self._serialize_msg(m) for m in messages],
            "stream": config.stream,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
        }
        if config.tools:
            body["tools"] = config.tools
        if config.response_format:
            body["response_format"] = config.response_format

        if not config.stream:
            r = await self._client.post(self._endpoint, json=body)
            r.raise_for_status()
            data = r.json()
            choice = data["choices"][0]
            yield LLMResponseChunk(
                delta=choice["message"].get("content") or "",
                finish_reason=choice.get("finish_reason"),
                tool_calls=choice["message"].get("tool_calls"),
                raw=data,
            )
            return

        async with self._client.stream("POST", self._endpoint, json=body) as resp:
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
                    logger.debug("bad sse line: %s", line[:200])
                    continue
                choice = (obj.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}
                yield LLMResponseChunk(
                    delta=delta.get("content") or "",
                    finish_reason=choice.get("finish_reason"),
                    tool_calls=delta.get("tool_calls"),
                    raw=obj,
                )

    @staticmethod
    def _serialize_msg(m: LLMMessage) -> dict[str, Any]:
        d: dict[str, Any] = {"role": m.role, "content": m.content}
        if m.tool_call_id:
            d["tool_call_id"] = m.tool_call_id
        if m.name:
            d["name"] = m.name
        if m.tool_calls:
            d["tool_calls"] = m.tool_calls
        return d
