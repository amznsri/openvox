"""BytePlus RAG Cloud (Knowledge Base) client.

Endpoint:  https://api-knowledgebase.mlp.cn-hongkong.bytepluses.com
Auth:      HMAC-SHA256 over canonical request (Volcengine-style SigV4).
Service:   `air`     Region: `cn-hongkong`

Reference docs (provided by the user):
  - Signature_authentication_and_examples
  - collection_create / collection_info / collection_update
  - collection_service_chat / Multi-turn / Multimodal_QA_sample
  - add_new / doc_info

Status: this client implements the request-signing flow and the most
useful chat endpoint (`POST /api/knowledge/collection/service_chat`).
Other actions (collection management, doc upload) follow the same
signing pattern — drop new methods on `BytePlusRAGClient` as you need
them.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import logging
from typing import Any
from urllib.parse import quote, urlparse

from openvox.config import get_settings
from openvox.utils.http import make_async_client

logger = logging.getLogger(__name__)


_SIGNED_HEADERS = ["host", "x-content-sha256", "x-date"]


def _hmac(key: bytes, data: str) -> bytes:
    return hmac.new(key, data.encode("utf-8"), hashlib.sha256).digest()


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _signing_key(secret: str, date_short: str, region: str, service: str) -> bytes:
    k = _hmac(secret.encode("utf-8"), date_short)
    k = _hmac(k, region)
    k = _hmac(k, service)
    return _hmac(k, "request")


def _canonical_query(query: str) -> str:
    if not query:
        return ""
    pairs = []
    for piece in query.split("&"):
        if not piece:
            continue
        if "=" in piece:
            k, v = piece.split("=", 1)
        else:
            k, v = piece, ""
        pairs.append((quote(k, safe="-_.~"), quote(v, safe="-_.~")))
    pairs.sort()
    return "&".join(f"{k}={v}" for k, v in pairs)


def _sign_request(
    *,
    method: str,
    url: str,
    body: bytes,
    access_key: str,
    secret_key: str,
    region: str,
    service: str,
) -> dict[str, str]:
    """Produce the headers (Host, X-Date, X-Content-Sha256, Authorization)
    required for a signed BytePlus / Volcengine API request."""
    parsed = urlparse(url)
    host = parsed.netloc
    path = parsed.path or "/"
    canonical_query = _canonical_query(parsed.query or "")

    now = _dt.datetime.now(_dt.timezone.utc)
    x_date = now.strftime("%Y%m%dT%H%M%SZ")
    short_date = now.strftime("%Y%m%d")
    payload_hash = _sha256_hex(body)

    canonical_headers = (
        f"host:{host}\n"
        f"x-content-sha256:{payload_hash}\n"
        f"x-date:{x_date}\n"
    )
    signed_headers = ";".join(_SIGNED_HEADERS)

    canonical_request = "\n".join(
        [
            method.upper(),
            path,
            canonical_query,
            canonical_headers,
            signed_headers,
            payload_hash,
        ]
    )

    credential_scope = f"{short_date}/{region}/{service}/request"
    string_to_sign = "\n".join(
        [
            "HMAC-SHA256",
            x_date,
            credential_scope,
            _sha256_hex(canonical_request.encode("utf-8")),
        ]
    )

    signing_key = _signing_key(secret_key, short_date, region, service)
    signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    authorization = (
        "HMAC-SHA256 "
        f"Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, "
        f"Signature={signature}"
    )

    return {
        "Host": host,
        "X-Date": x_date,
        "X-Content-Sha256": payload_hash,
        "Authorization": authorization,
        "Content-Type": "application/json",
    }


class BytePlusRAGClient:
    """Thin client for the most common RAG Cloud actions."""

    def __init__(self) -> None:
        s = get_settings()
        self._ak = s.byteplus_rag_access_key
        self._sk = s.byteplus_rag_secret_key
        self._endpoint = s.byteplus_rag_endpoint.rstrip("/")
        self._region = s.byteplus_rag_region
        self._service = s.byteplus_rag_service
        self._collection = s.byteplus_rag_collection

    @property
    def is_configured(self) -> bool:
        return bool(self._ak and self._sk and self._collection)

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        import json

        url = f"{self._endpoint}{path}"
        body_bytes = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        headers = _sign_request(
            method="POST",
            url=url,
            body=body_bytes,
            access_key=self._ak,
            secret_key=self._sk,
            region=self._region,
            service=self._service,
        )
        async with make_async_client(timeout=60.0, headers=headers) as c:
            r = await c.post(url, content=body_bytes)
            r.raise_for_status()
            return r.json()

    # ── Public API ────────────────────────────────────────────────

    async def chat(
        self,
        question: str,
        *,
        history: list[dict[str, str]] | None = None,
        top_k: int = 5,
    ) -> dict[str, Any]:
        """Multi-turn knowledge-base chat.

        See: https://docs.byteplus.com/en/docs/RAG_Cloud/collection_service_chat
        """
        if not self.is_configured:
            raise RuntimeError(
                "BytePlus RAG Cloud not configured. Set BYTEPLUS_RAG_ACCESS_KEY, "
                "BYTEPLUS_RAG_SECRET_KEY, and BYTEPLUS_RAG_COLLECTION in .env."
            )
        body: dict[str, Any] = {
            "name": self._collection,
            "query": question,
            "messages": history or [],
            "rerank_switch": True,
            "limit": top_k,
        }
        return await self._post("/api/knowledge/collection/service_chat", body)

    async def collection_info(self) -> dict[str, Any]:
        """Look up details of the configured collection."""
        return await self._post("/api/knowledge/collection/info", {"name": self._collection})


def get_rag_client() -> BytePlusRAGClient | None:
    """Return a configured client, or None if RAG Cloud isn't set up."""
    c = BytePlusRAGClient()
    return c if c.is_configured else None
