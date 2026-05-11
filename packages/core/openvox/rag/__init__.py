"""Light-weight retrieval-augmented-generation toolkit.

Three small pieces:
  - `embeddings.py` — thin client for the BytePlus Ark embeddings endpoint
    (`/api/v3/embeddings`, OpenAI-compatible).
  - `extract.py` — pulls text out of PDF / docx / txt / md files; for
    images we pass the bytes straight to the vision-capable LLM.
  - `store.py` — chunks text, embeds it, persists chunks + embeddings in
    the OpenVox SQL DB, and runs cosine-similarity retrieval at query time.

Why no chromadb / weaviate / pgvector?
    Local-first installs typically have a few dozen documents at most.
    NumPy cosine search over a few thousand chunks runs in milliseconds.
    Skipping the vector-DB dependency keeps the install lean and the data
    self-contained in a single SQLite or Postgres instance.
"""

from openvox.rag.embeddings import embed_texts
from openvox.rag.extract import extract_text
from openvox.rag.store import index_document, query

__all__ = ["embed_texts", "extract_text", "index_document", "query"]
