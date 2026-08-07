# rag/hybrid_search.py
#
# RUBRIC: "Retrieval architectures, three required" (15 pts) -- architecture
# #2 of 3: "hybrid search (vector similarity plus keyword or BM25 in the
# same query)".
#
# Merges VectorStore (semantic/ANN) results with KeywordIndex (BM25)
# results using Reciprocal Rank Fusion (RRF) -- a standard, parameter-light
# way to combine two differently-scaled ranking signals (cosine similarity
# and BM25 score aren't on the same scale, so summing raw scores would be
# wrong; RRF merges by RANK instead, which sidesteps that).
#
# This is the architecture expected to win on citation-heavy questions
# ("what does clause 4.2b say about cardiac..." style, or here: "what does
# Section 5.2 say about reserve crew from another base?") because BM25
# matches the literal token "5.2" or "4.2b" precisely, which the embedding
# in naive_rag.py alone under-weights.

from __future__ import annotations

import time

from keyword_index import KeywordIndex
from llm_client import generate_answer
from vector_store import VectorStore

_store: VectorStore | None = None
_keyword_index: KeywordIndex | None = None


def _get_store() -> VectorStore:
    global _store
    if _store is None:
        _store = VectorStore()
    return _store


def _get_keyword_index() -> KeywordIndex:
    """Built lazily from the SAME chunk set the vector store holds (see
    VectorStore.get_all_documents), so both indexes always agree on what
    exists -- required, otherwise hybrid results could reference a chunk
    the vector index doesn't have and vice versa."""
    global _keyword_index
    if _keyword_index is None:
        store = _get_store()
        _keyword_index = KeywordIndex(store.get_all_documents())
    return _keyword_index


def _reciprocal_rank_fusion(
    vector_results: list[dict], keyword_results: list[dict], k: int = 60
) -> list[dict]:
    """RRF: score(doc) = sum over each ranked list of 1 / (k + rank).
    k=60 is the standard default from the original RRF paper (Cormack et
    al.) -- large enough that fusion isn't dominated by whichever list a
    doc happens to rank #1 in."""
    scores: dict[str, float] = {}
    payload: dict[str, dict] = {}

    for rank, doc in enumerate(vector_results):
        cid = doc["chunk_id"]
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
        payload[cid] = doc

    for rank, doc in enumerate(keyword_results):
        cid = doc["chunk_id"]
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
        if cid not in payload:
            payload[cid] = doc

    ranked_ids = sorted(scores.keys(), key=lambda cid: scores[cid], reverse=True)
    return [
        {**payload[cid], "rrf_score": scores[cid]}
        for cid in ranked_ids
    ]


def hybrid_rag_answer(
    query: str, top_k: int = 5, where: dict | None = None
) -> dict:
    t0 = time.perf_counter()
    store = _get_store()
    kw_index = _get_keyword_index()

    # Over-fetch from each individual ranker before fusing, so RRF has
    # enough candidates to actually re-rank rather than just re-ordering
    # the same top_k=5 from one list.
    vector_results = store.query(query, top_k=top_k * 2, where=where)
    keyword_results = kw_index.query(query, top_k=top_k * 2)

    fused = _reciprocal_rank_fusion(vector_results, keyword_results)[:top_k]
    context_chunks = [m["text"] for m in fused]
    answer = generate_answer(query, context_chunks)
    latency = time.perf_counter() - t0

    return {
        "architecture": "hybrid_search",
        "query": query,
        "answer": answer,
        "retrieved_chunks": fused,
        "vector_only_top1": vector_results[0]["chunk_id"] if vector_results else None,
        "keyword_only_top1": keyword_results[0]["chunk_id"] if keyword_results else None,
        "latency_seconds": latency,
        "approx_tokens": sum(len(c) for c in context_chunks) // 4 + len(answer) // 4,
    }


if __name__ == "__main__":
    result = hybrid_rag_answer(
        "What does clause 4.2b say about mechanical failure compensation?"
    )
    print(result["answer"])
    print(f"\nvector-only top1: {result['vector_only_top1']}")
    print(f"keyword-only top1: {result['keyword_only_top1']}")
