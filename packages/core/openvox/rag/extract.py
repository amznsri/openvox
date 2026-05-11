"""Text extraction for the Document Q&A agent.

Supported file types:
  - PDF (text-based)        — `pypdf` page-by-page extraction
  - Plain text / markdown    — read as UTF-8
  - Images (jpg, png, webp)  — no extraction; the chunk's `text` field
                                holds a `data:` URI so the LLM can see it
                                via its vision/OCR capability.

Anything else is silently skipped (no exception) so users can drop a
mixed folder without worrying about errors.
"""

from __future__ import annotations

import base64
import io
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


_PDF_MIMES = {"application/pdf"}
_IMAGE_MIMES = {"image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif"}
_TEXT_MIMES = {"text/plain", "text/markdown", "text/x-markdown", "text/csv", "application/json"}


@dataclass
class ExtractedChunk:
    text: str
    page: int = 0
    kind: str = "text"  # "text" | "image"


def _detect_kind(mime: str, name: str) -> str:
    m = (mime or "").lower()
    n = (name or "").lower()
    if m in _PDF_MIMES or n.endswith(".pdf"):
        return "pdf"
    if m in _IMAGE_MIMES or any(n.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif")):
        return "image"
    if m in _TEXT_MIMES or any(n.endswith(ext) for ext in (".txt", ".md", ".json", ".csv")):
        return "text"
    # Default to text — try to decode UTF-8.
    return "text"


def extract_text(data: bytes, mime_type: str, filename: str) -> tuple[list[ExtractedChunk], int]:
    """Extract retrievable chunks from a file.

    Returns `(chunks, page_count)`. The chunks are *raw* paragraphs/pages
    — caller is responsible for further sub-chunking before embedding.
    """
    kind = _detect_kind(mime_type, filename)

    if kind == "pdf":
        return _extract_pdf(data)
    if kind == "image":
        return _extract_image(data, mime_type or "image/png"), 1
    # plain text / fallback
    try:
        text = data.decode("utf-8", errors="replace")
    except Exception:  # pragma: no cover
        text = ""
    return [ExtractedChunk(text=text, page=0, kind="text")], 1


def _extract_pdf(data: bytes) -> tuple[list[ExtractedChunk], int]:
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise RuntimeError("pypdf is required for PDF extraction") from e

    reader = PdfReader(io.BytesIO(data))
    chunks: list[ExtractedChunk] = []
    for i, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception as e:
            logger.warning("pdf page %d extract failed: %s", i, e)
            text = ""
        if text.strip():
            chunks.append(ExtractedChunk(text=text, page=i + 1, kind="text"))
    return chunks, len(reader.pages)


def _extract_image(data: bytes, mime: str) -> list[ExtractedChunk]:
    """For images we don't OCR up-front. We stash a `data:` URI as the
    chunk's "text" so the vision-capable LLM can see it at query time."""
    encoded = base64.b64encode(data).decode("ascii")
    data_url = f"data:{mime};base64,{encoded}"
    return [ExtractedChunk(text=data_url, page=1, kind="image")]


# ── Sub-chunking ────────────────────────────────────────────────────


def split_text(text: str, *, target_size: int = 800, overlap: int = 100) -> list[str]:
    """Split a long string into roughly target-size chunks with a small
    overlap, preferring paragraph and sentence boundaries.

    Designed for retrieval — we want chunks small enough that one chunk's
    embedding stays focused, but large enough that retrieved context has
    real semantic content.
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= target_size:
        return [text]

    # Split first on double-newlines (paragraphs), then on sentences.
    paras = [p for p in text.split("\n\n") if p.strip()]
    if not paras:
        paras = [text]

    chunks: list[str] = []
    buf = ""
    for p in paras:
        if len(buf) + len(p) + 2 <= target_size:
            buf = (buf + "\n\n" + p).strip()
            continue
        if buf:
            chunks.append(buf)
        # Long paragraph: hard-split.
        if len(p) > target_size:
            for i in range(0, len(p), target_size - overlap):
                chunks.append(p[i : i + target_size])
            buf = ""
        else:
            buf = p
    if buf:
        chunks.append(buf)
    return chunks
