"""Document management for the Document Q&A agent.

Endpoints:
    POST   /api/v1/agents/{agent_id}/documents      multipart upload
    GET    /api/v1/agents/{agent_id}/documents      list
    DELETE /api/v1/agents/{agent_id}/documents/{id} remove (file + chunks)

Uploads are stored via the configured storage backend (local FS by
default). Indexing runs in the background — list endpoint surfaces an
`indexed` flag so the dashboard can poll until it flips to true.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from sqlalchemy import select

from openvox.db import db_session
from openvox.db.models import Agent, Document, DocumentChunk
from openvox.rag import index_document
from openvox.storage import get_storage

logger = logging.getLogger(__name__)
router = APIRouter()


def _doc_to_dict(d: Document) -> dict[str, Any]:
    return {
        "id": d.id,
        "agent_id": d.agent_id,
        "name": d.name,
        "mime_type": d.mime_type,
        "size_bytes": d.size_bytes,
        "page_count": d.page_count,
        "chunk_count": d.chunk_count,
        "indexed": d.indexed,
        "error": d.error,
        "created_at": d.created_at.isoformat() if d.created_at else "",
    }


@router.get("/{agent_id}/documents")
async def list_documents(agent_id: str) -> list[dict[str, Any]]:
    async with db_session() as s:
        rows = (
            await s.execute(
                select(Document).where(Document.agent_id == agent_id).order_by(Document.created_at.desc())
            )
        ).scalars().all()
        return [_doc_to_dict(d) for d in rows]


@router.post("/{agent_id}/documents", status_code=201)
async def upload_document(
    agent_id: str,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    async with db_session() as s:
        agent = await s.get(Agent, agent_id)
        if agent is None:
            raise HTTPException(404, "agent not found")

    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")

    storage = get_storage()
    safe_name = (file.filename or "upload").replace("/", "_").replace("..", "")
    key = f"agents/{agent_id}/docs/{safe_name}"
    storage_url = await storage.upload(key, data, content_type=file.content_type or "application/octet-stream")

    async with db_session() as s:
        doc = Document(
            agent_id=agent_id,
            name=safe_name,
            mime_type=file.content_type or "",
            size_bytes=len(data),
            storage_url=storage_url,
            indexed=False,
        )
        s.add(doc)
        await s.flush()
        doc_id = doc.id

    # Kick off indexing in the background — caller gets the document id
    # immediately and can poll the list endpoint for `indexed=true`.
    asyncio.create_task(_index_safely(agent_id, doc_id, data, file.content_type or "", safe_name))

    async with db_session() as s:
        doc = await s.get(Document, doc_id)
        return _doc_to_dict(doc)  # type: ignore[arg-type]


async def _index_safely(agent_id: str, document_id: str, data: bytes, mime: str, name: str) -> None:
    try:
        await index_document(
            agent_id=agent_id,
            document_id=document_id,
            data=data,
            mime_type=mime,
            filename=name,
        )
    except Exception as e:
        logger.exception("document indexing failed")
        async with db_session() as s:
            doc = await s.get(Document, document_id)
            if doc is not None:
                doc.indexed = False
                doc.error = str(e)


@router.delete("/{agent_id}/documents/{document_id}", status_code=204)
async def delete_document(agent_id: str, document_id: str) -> None:
    async with db_session() as s:
        doc = await s.get(Document, document_id)
        if doc is None or doc.agent_id != agent_id:
            raise HTTPException(404, "document not found")
        # Remove chunks first.
        from sqlalchemy import delete as sqla_delete

        await s.execute(sqla_delete(DocumentChunk).where(DocumentChunk.document_id == document_id))
        await s.delete(doc)
    # Best-effort: drop the file from storage too.
    try:
        storage = get_storage()
        key = doc.storage_url.split("//", 1)[-1].split("/", 1)[-1] if "//" in doc.storage_url else f"agents/{agent_id}/docs/{doc.name}"
        await storage.delete(key)
    except Exception as e:  # pragma: no cover
        logger.warning("storage delete failed: %s", e)
