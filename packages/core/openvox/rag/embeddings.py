"""BytePlus Ark embeddings client.

Endpoint: `POST {ark_base}/api/v3/embeddings` — OpenAI-compatible.
Model: `BYTEPLUS_EMBEDDING_MODEL` (defaults to a Doubao text-embedding model).

Request:
    {"model": "<model-id>", "input": ["chunk 1", "chunk 2"]}

Response:
    {"data": [{"embedding": [0.01, ...]}, ...]}
"""

from __future__ import annotations

from openvox.config import get_settings
from openvox.utils.http import make_async_client


def _embeddings_endpoint() -> str:
    s = get_settings()
    base = s.byteplus_llm_endpoint.rsplit("/api/", 1)[0]
    return f"{base}/api/v3/embeddings"


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Return one embedding vector per input text. Empty inputs map to zero
    vectors so the caller doesn't have to special-case them."""
    if not texts:
        return []
    s = get_settings()
    if not s.byteplus_llm_api_key:
        raise RuntimeError(
            "BYTEPLUS_LLM_API_KEY is required for document embeddings. "
            "Add a BytePlus LLM API key via the dashboard setup wizard "
            "(the /dashboard/setup page), or set "
            "BYTEPLUS_LLM_API_KEY in your .env file. Either way, "
            "restart the core service after configuring."
        )

    # The Ark embeddings endpoint accepts batched input. Strip empty strings
    # to a placeholder space — Ark rejects pure-empty entries.
    safe = [t if t.strip() else " " for t in texts]
    body = {"model": s.byteplus_embedding_model, "input": safe}

    headers = {
        "Authorization": f"Bearer {s.byteplus_llm_api_key}",
        "Content-Type": "application/json",
    }
    async with make_async_client(timeout=60.0, headers=headers) as c:
        r = await c.post(_embeddings_endpoint(), json=body)
        r.raise_for_status()
        data = r.json()

    return [list(item["embedding"]) for item in data.get("data", [])]


async def embed_text(text: str) -> list[float]:
    out = await embed_texts([text])
    return out[0] if out else []
