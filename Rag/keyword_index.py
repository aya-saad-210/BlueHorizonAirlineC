# rag/keyword_index.py
#
# Keyword scorer used by hybrid_search.py. This is the piece that lets
# hybrid search win on citation-heavy questions ("what does 4.2b say?"),
# because an exact token like "4.2b" matches BM25's term-frequency scoring
# precisely, whereas it barely moves a bag-of-words/semantic embedding.
#
# Uses rank_bm25 (Okapi BM25), the reference implementation named directly
# in the lab's resource list.

from __future__ import annotations

import re

from rank_bm25 import BM25Okapi

TOKEN_RE = re.compile(r"[a-z0-9]+(?:\.[a-z0-9]+)*")  # keeps "4.2b" as one token


def _tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


class KeywordIndex:
    def __init__(self, documents: list[dict]):
        """documents: list of {"chunk_id", "text", "metadata"} -- the same
        shape VectorStore.get_all_documents() returns, so both indexes are
        always built over the identical chunk set."""
        self._documents = documents
        self._tokenized_corpus = [_tokenize(d["text"]) for d in documents]
        self._bm25 = BM25Okapi(self._tokenized_corpus)

    def query(self, query_text: str, top_k: int = 5) -> list[dict]:
        tokens = _tokenize(query_text)
        scores = self._bm25.get_scores(tokens)
        ranked = sorted(
            zip(self._documents, scores), key=lambda pair: pair[1], reverse=True
        )[:top_k]
        return [
            {
                "chunk_id": doc["chunk_id"],
                "text": doc["text"],
                "metadata": doc["metadata"],
                "bm25_score": float(score),
            }
            for doc, score in ranked
            if score > 0
        ]
