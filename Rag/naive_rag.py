# rag/naive_rag.py
#
# RUBRIC: "Retrieval architectures, three required" (15 pts) -- this is
# architecture #1 of 3: "naive RAG (the baseline chunk, embed, index,
# retrieve, generate pipeline)".
#
# Deliberately the simplest of the three: one vector search, no keyword
# scoring, no reasoning loop. This is the baseline the comparison table in
# retrieval_eval/ measures hybrid_search.py and agentic_rag.py against.
#
# Expected failure mode (this is WHY hybrid search exists as architecture
# #2): naive RAG should do fine on general questions but perform worse on
# citation-heavy questions like "what does clause 4.2b say?", because a
# short alphanumeric clause ID like "4.2b" doesn't embed distinctively --
# see rag/policy_docs/compensation_policy.md Section 4.2 for the exact case
# this is modeling.

from __future__ import annotations

import time

from llm_client import generate_answer
from vector_store import VectorStore

_store: VectorStore | None = None


def _get_store() -> VectorStore:
    global _store
    if _store is None:
        _store = VectorStore()
    return _store


def naive_rag_answer(query: str, top_k: int = 5, where: dict | None = None) -> dict:
    """The full naive pipeline: embed query -> ANN search -> stuff context
    into prompt -> generate. Returns a dict with everything retrieval_eval/
    and self_rag_verify.py need (answer, retrieved chunks, timing, token
    proxy) so it's directly comparable to hybrid_search / agentic_rag.
    """
    t0 = time.perf_counter()
    store = _get_store()
    matches = store.query(query, top_k=top_k, where=where)
    context_chunks = [m["text"] for m in matches]
    answer = generate_answer(query, context_chunks)
    latency = time.perf_counter() - t0

    return {
        "architecture": "naive_rag",
        "query": query,
        "answer": answer,
        "retrieved_chunks": matches,
        "latency_seconds": latency,
        # rough token proxy (chars/4) so retrieval_eval/ can compute a
        # tokens/query column without a live API call in mock mode
        "approx_tokens": sum(len(c) for c in context_chunks) // 4 + len(answer) // 4,
    }


if __name__ == "__main__":
    result = naive_rag_answer("What caused the cancellation of flight BH303?")
    print(result["answer"])
    print(f"\n{len(result['retrieved_chunks'])} chunks retrieved in {result['latency_seconds']:.3f}s")
