"""Persist and retrieve document chunks with embeddings.

The store is just SQLAlchemy + NumPy. We brute-force cosine similarity
across an agent's chunks at query time. For a few thousand chunks this
is a sub-millisecond operation in NumPy.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
from sqlalchemy import delete, select

from openvox.db import db_session
from openvox.db.models import Document, DocumentChunk
from openvox.rag.bm25 import score as bm25_scores
from openvox.rag.embeddings import embed_texts
from openvox.rag.extract import extract_text, split_text

logger = logging.getLogger(__name__)


@dataclass
class Retrieved:
    document_id: str
    document_name: str
    chunk_index: int
    page: int
    kind: str
    text: str
    score: float


async def index_document(
    *,
    agent_id: str,
    document_id: str,
    data: bytes,
    mime_type: str,
    filename: str,
) -> dict[str, Any]:
    """Extract → chunk → embed → write rows. Updates the Document with
    `indexed=True`, `chunk_count`, `page_count`, or `error` on failure."""
    raw_chunks, page_count = extract_text(data, mime_type, filename)

    # Sub-split text chunks; image chunks pass through unchanged.
    flat: list[tuple[str, int, str]] = []  # (text, page, kind)
    for c in raw_chunks:
        if c.kind == "text":
            for piece in split_text(c.text):
                flat.append((piece, c.page, "text"))
        else:
            flat.append((c.text, c.page, c.kind))

    if not flat:
        async with db_session() as s:
            doc = await s.get(Document, document_id)
            if doc is not None:
                doc.indexed = True
                doc.chunk_count = 0
                doc.page_count = page_count
        return {"chunk_count": 0, "page_count": page_count}

    # Try cloud embeddings; if they're unavailable (404, missing model,
    # rate-limit, etc.) fall back to keyword-only retrieval. The chunks
    # are still stored — query() switches to BM25 when embeddings are
    # empty, so the user can ask questions either way.
    to_embed = [
        (t if k == "text" else f"image attachment {filename} (page {p})")
        for (t, p, k) in flat
    ]
    vectors: list[list[float]] = []
    embed_warning = ""
    try:
        vectors = await embed_texts(to_embed)
    except Exception as e:
        logger.warning("cloud embeddings unavailable, falling back to BM25: %s", e)
        embed_warning = f"embeddings unavailable ({e}); using keyword retrieval"
        vectors = [[] for _ in to_embed]

    async with db_session() as s:
        # Replace any existing chunks for the document (idempotent re-index).
        await s.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document_id))
        for i, ((text, page, kind), vec) in enumerate(zip(flat, vectors)):
            s.add(
                DocumentChunk(
                    document_id=document_id,
                    agent_id=agent_id,
                    chunk_index=i,
                    page=page,
                    kind=kind,
                    text=text,
                    embedding=list(vec),
                )
            )
        doc = await s.get(Document, document_id)
        if doc is not None:
            doc.indexed = True
            doc.chunk_count = len(flat)
            doc.page_count = page_count
            # Persist the warning so the dashboard can show "indexed
            # without embeddings" instead of a hard failure.
            doc.error = embed_warning
    return {
        "chunk_count": len(flat),
        "page_count": page_count,
        "embeddings": "cloud" if not embed_warning else "bm25_fallback",
        "warning": embed_warning,
    }


async def query(
    *,
    agent_id: str,
    question: str,
    top_k: int = 5,
    document_id: str | None = None,
) -> list[Retrieved]:
    """Return the top-k most relevant chunks for an agent.

    Uses cosine similarity over cloud embeddings when the chunks were
    indexed with them; otherwise falls back to BM25 keyword retrieval.
    Both paths return the same shape so callers don't have to care.
    """
    if not question.strip():
        return []

    async with db_session() as s:
        stmt = (
            select(DocumentChunk, Document.name)
            .join(Document, DocumentChunk.document_id == Document.id)
            .where(DocumentChunk.agent_id == agent_id)
        )
        if document_id:
            stmt = stmt.where(DocumentChunk.document_id == document_id)
        rows = (await s.execute(stmt)).all()

    if not rows:
        return []

    has_vectors = any(chunk.embedding for chunk, _ in rows)

    if has_vectors:
        try:
            qvec = (await embed_texts([question]))[0]
        except Exception as e:
            logger.warning("query embedding failed, switching to BM25: %s", e)
            qvec = []
    else:
        qvec = []

    if qvec:
        q = np.asarray(qvec, dtype=np.float32)
        qn = float(np.linalg.norm(q)) or 1.0
        scored: list[tuple[float, DocumentChunk, str]] = []
        for chunk, doc_name in rows:
            emb = chunk.embedding
            if not emb:
                # Mixed corpus (some chunks lack embeddings): rank these
                # via BM25 against the same question — combined later.
                continue
            v = np.asarray(emb, dtype=np.float32)
            vn = float(np.linalg.norm(v)) or 1.0
            scored.append((float(np.dot(q, v) / (qn * vn)), chunk, doc_name))
    else:
        # BM25 fallback over chunk text.
        texts = [chunk.text if chunk.kind == "text" else doc_name for chunk, doc_name in rows]
        bm = bm25_scores(question, texts)
        # BM25 produces unbounded positive scores; normalise to [0, 1] for
        # cleaner UX. Empty/no-match → 0.
        max_s = max(bm) if bm else 0.0
        norm = (lambda s: s / max_s) if max_s > 0 else (lambda s: 0.0)
        scored = [(norm(bm[i]), rows[i][0], rows[i][1]) for i in range(len(rows))]

    scored.sort(key=lambda x: x[0], reverse=True)
    out: list[Retrieved] = []
    for score, chunk, doc_name in scored[:top_k]:
        if score <= 0:
            continue
        out.append(
            Retrieved(
                document_id=chunk.document_id,
                document_name=doc_name,
                chunk_index=chunk.chunk_index,
                page=chunk.page,
                kind=chunk.kind,
                text=chunk.text,
                score=score,
            )
        )
    return out
