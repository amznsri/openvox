"""Pure-Python BM25 retrieval — used as a fallback when cloud embeddings
are unavailable.

Surprisingly competitive with vector search for keyword-style Q&A on
technical documents. Zero dependencies, runs in milliseconds for the
scale of a single user's local KB.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass


_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+", re.UNICODE)
# A small English stop-word list. Keeping this tiny on purpose — anything
# longer hurts technical-doc retrieval (e.g. "a", "in", "I" carry signal
# in some legal/technical contexts).
_STOP = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "at",
    "for", "is", "are", "was", "were", "be", "been", "being",
    "this", "that", "these", "those", "it", "its", "with", "as",
    "by", "from", "have", "has", "had", "do", "does", "did",
}


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "") if t.lower() not in _STOP]


@dataclass
class _Doc:
    idx: int
    tokens: list[str]
    counter: Counter
    length: int


def score(query: str, docs: list[str], *, k1: float = 1.5, b: float = 0.75) -> list[float]:
    """BM25 scores for `query` across `docs` (one score per doc).

    `k1` controls term-frequency saturation; `b` controls length normalisation.
    Defaults are the classic Robertson values that work well across corpora.
    """
    q_tokens = [t for t in tokenize(query) if t]
    if not q_tokens or not docs:
        return [0.0] * len(docs)

    parsed: list[_Doc] = []
    for i, d in enumerate(docs):
        toks = tokenize(d)
        parsed.append(_Doc(idx=i, tokens=toks, counter=Counter(toks), length=len(toks)))

    n = len(parsed)
    avgdl = sum(p.length for p in parsed) / max(1, n)

    # Document frequency per query term.
    df: Counter[str] = Counter()
    for p in parsed:
        for term in set(q_tokens):
            if term in p.counter:
                df[term] += 1

    scores = [0.0] * n
    for p in parsed:
        if p.length == 0:
            continue
        s = 0.0
        for term in q_tokens:
            f = p.counter.get(term, 0)
            if f == 0:
                continue
            idf = math.log(((n - df[term] + 0.5) / (df[term] + 0.5)) + 1.0)
            denom = f + k1 * (1.0 - b + b * (p.length / avgdl))
            s += idf * (f * (k1 + 1.0)) / denom
        scores[p.idx] = s
    return scores
