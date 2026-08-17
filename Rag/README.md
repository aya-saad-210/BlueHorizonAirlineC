# Blue Horizon IROPS Assistant — Memory & RAG Lab

Extension of the MCP Server Lab project: same `mcp_server/`, same `database/`,
same agent. This lab adds (1) a long-term memory system and (2) a
retrieval-grounded knowledge layer on top of it. See the team's three
work areas below; **this section is written and owned by the RAG/Retrieval
lead (Person C)** — the Memory lead and Evaluation lead should extend this
README with their own sections rather than replace this one.

## The problem we found (shared framing, all three of us agreed on this before writing code)

Blue Horizon's IROPS Assistant already has real DB-backed tools
(`get_flight_status`, `rebook_passenger`, `assign_reserve_crew`,
`issue_compensation`...). Two gaps showed up once we looked at real usage:

1. **Context gets buried during a live disruption event.** An ops agent
   handling one cancelled flight makes dozens of tool calls in a row
   (rebooking every affected passenger, checking crew duty hours, issuing
   compensation). An early, important detail — e.g. a passenger with a
   critical connecting flight — can get buried under later tool noise.
   *(Owned by the Memory lead + the Evaluation lead's `context_eval/`.)*

2. **The compensation and duty-time rules are real but ungoverned.** Before
   this lab, the ONLY policy knowledge exposed to the agent was two
   hardcoded numbers in the `policy://duty-time-limits` MCP resource
   (8h flying / 14h duty). Everything else — compensation eligibility,
   extraordinary-circumstance exceptions, supervisor-override conditions,
   loyalty-tier adjustments — did not exist anywhere the agent could reach,
   which is exactly the "forty new tools nobody wants to write" problem the
   lab describes. **This is the gap Person C's part of the system closes.**

The real cost of getting this wrong: waiving compensation that was legally
owed (Section 4.2a vs. 4.2b in the compensation manual is the textbook
case — same `disruption_reason = 'mechanical'` in the database, opposite
compensation outcome), or approving a duty-time override that violates
Section 4's actual conditions. Nothing here works "the same on three lines
of throwaway chat" — both gaps only show up under real IROPS call volume.

---

## RAG / Retrieval layer (`rag/`, `agent/rag_integration.py`, `mcp_server/rag_tool.py`) — Person C

### What's in `rag/policy_docs/`

Two manuals that did not exist before this lab, written to be the real
knowledge base behind compensation and duty-time decisions:

- `compensation_policy.md` — eligibility, distance/delay compensation
  tiers, the 4.2a/4.2b extraordinary-circumstances distinction, supervisor
  approval thresholds, loyalty-tier uplifts, audit requirements.
- `duty_time_policy.md` — the full version behind the 2-line resource:
  override conditions, reserve-crew activation rules, license-specific
  rules, international route considerations.

Both are chunked at the numbered sub-clause level (`4.2b`, `5.2`, ...) on
purpose — see `rag/chunking.py` — so a citation-heavy question retrieves
exactly one clause instead of a whole section, and so the clause ID itself
survives as its own piece of metadata for hybrid search to exploit.

### Chunking + embedding pipeline

- `rag/chunking.py` — section/sub-clause-aware chunker, attaches metadata
  (`source`, `doc_type`, `section`, `clause`, `last_reviewed`) to every chunk.
- `rag/embeddings.py` — **decision, documented in-file**: embeddings are
  computed locally (scikit-learn `HashingVectorizer`, 384-dim, no API key,
  no network call) rather than via a paid provider. This keeps `ingest.py`
  and the whole retrieval pipeline runnable offline and reproducible for
  grading, per the guardrail against committing embedding-provider
  credentials. `GeminiEmbeddingBackend` is a real, opt-in provider (set
  `EMBEDDING_PROVIDER=gemini`) that calls the actual gemini-embedding-001 endpoint.
- `rag/ingest.py` — the runnable pipeline: `python rag/ingest.py` chunks
  both manuals, embeds every chunk, and upserts into the vector store.
  Currently produces **47 chunks** (27 compensation, 20 duty-time).

### Vector database architecture

`rag/vector_store.py` wraps Chroma (`chromadb.PersistentClient`, stored at
`rag/vector_db/`, excluded from git and rebuilt by `ingest.py`):

- **ANN index**: HNSW, explicitly configured (`hnsw:space: "cosine"`), not
  a bare Python list of vectors.
- **Metadata payload store**: every chunk's `(source, doc_type, section,
  clause, last_reviewed)` is stored alongside its vector.
- **Metadata index**: `VectorStore.query(..., where={...})` passes the
  filter straight into Chroma's query engine, applied **before/during** the
  ANN traversal — not a Python-side post-filter on the results. Used by
  `mcp_server/rag_tool.py`'s `policy_area` parameter to restrict search to
  one manual when the caller already knows which one applies.

### Three retrieval architectures (all implemented against the same corpus)

| Architecture | File | Mechanism |
|---|---|---|
| Naive RAG | `rag/naive_rag.py` | single ANN search → generate |
| Hybrid search | `rag/hybrid_search.py` | ANN + BM25 (`rag/keyword_index.py`), merged by Reciprocal Rank Fusion |
| Agentic RAG | `rag/agentic_rag.py` | reason → retrieve → observe → reason again → retrieve again (capped at 3 hops), reuses the hybrid retriever for each hop |

**Verified failure mode that motivates hybrid search** (this repo, real
output, not hypothetical): for the query *"What does clause 4.2b say about
mechanical failure compensation?"*, the vector-only top-1 result was
`duty_time_policy:sec3:3.2` — completely wrong manual — while the
BM25-only top-1 was `compensation_policy:sec4:4.2b` — exactly right. This
is the citation-precision gap the lab's grading criteria describe, observed
directly rather than assumed.

### Self-RAG-style verification (`rag/self_rag_verify.py`)

Two explicit checks run before any RAG answer reaches a user:

1. **Post-retrieval relevance** (`verify_retrieved_chunks`) — every
   retrieved chunk is individually judged relevant or not; irrelevant ones
   are dropped from context before generation.
2. **Post-generation support** (`verify_answer_support`) — the generated
   answer is checked against the surviving (relevance-filtered) chunks.

**Visible consequence when a check fails**: the caller never gets the raw
model output. `verify_rag_response()` returns a fallback message
("I don't have enough grounded information...") instead, and every
judgment is written to `VERIFICATION_LOG` for inspection. Demonstrated
live in this repo: asking *"What is the CEO's personal cell phone
number?"* — a question the corpus cannot answer — causes all 5 retrieved
chunks to be marked irrelevant and the gate to fail; the fallback message
is what's returned, not a hallucinated phone number.

These two judge functions (`judge_relevance`, `judge_support` in
`rag/llm_client.py`) are written generic over "query + text", not RAG-specific,
so the Memory lead's episodic/semantic recall path can call the same
functions — satisfying the requirement that Self-RAG-style verification
"applies to both your RAG answers and to memories recalled from the
episodic and semantic store" with one shared implementation.

### `mcp_server` / `agent` integration

- `agent/rag_integration.py` — the one call site (`answer_policy_question`)
  any part of the agent loop should use for a policy question. Always
  routes through the Self-RAG gate; never returns an unverified answer.
  Default architecture is **hybrid search** (see comparison table below).
- `mcp_server/rag_tool.py` — new MCP tool, `answer_policy_question`,
  registered in `Server.py` alongside the existing tools. Does not
  duplicate any DB logic; calls straight through to `agent/rag_integration.py`.

**Decision: `policy://duty-time-limits` resource vs. the new vector
store.** Kept the resource unchanged. It stays the free, always-loaded,
two-number quick reference (most duty checks only need "8h / 14h"). The
new `answer_policy_question` tool is for the cases that resource never
covered — override sub-clauses, exceptions, compensation eligibility —
which previously did not exist anywhere in the system at all.

### How to run it

```bash
pip install -r rag/requirements-rag.txt
cp Rag/env.example Rag/.env       # optional: set GEMINI_API_KEY for live-mode generation
python rag/ingest.py              # builds rag/vector_db/ from policy_docs/
python agent/rag_integration.py   # smoke test: one hybrid hit, one Self-RAG-caught failure
```

Without `GEMINI_API_KEY` set, `Rag/llm_client.py` runs in a documented
deterministic **mock mode** so the full pipeline (chunk → embed → index →
retrieve → hybrid-merge → agentic loop → Self-RAG check) is reproducible
offline without a paid credential. Set the key for the real demo recording.

### Retrieval comparison table

*(Owned by the Evaluation lead — `retrieval_eval/` runs all three
architectures against the domain test question set and fills this in with
real accuracy/tokens/latency numbers, then the final architecture choice
below gets justified against it, not against intuition.)*

| Architecture | Accuracy (N test questions) | Avg. tokens/query | Avg. latency/query |
|---|---|---|---|
| Naive RAG | *TBD* | *TBD* | *TBD* |
| Hybrid search | *TBD* | *TBD* | *TBD* |
| Agentic RAG | *TBD* | *TBD* | *TBD* |

---

## Memory layer (`memory/`) — Memory lead

*(Section to be added by the Memory lead: short-term buffer + scratchpad,
episodic/semantic stores, promote-or-drop routing, consolidation +
conflict resolution example, and the memory-recall Self-RAG-style check
reusing `rag/llm_client.judge_relevance` / `judge_support`.)*

## Context management evaluation (`context_eval/`) — Evaluation lead

*(Section to be added: the four strategies, the long-context test suite,
and the comparison table with the final strategy choice justified against it.)*

## Retrieval evaluation (`retrieval_eval/`) — Evaluation lead

*(Section to be added: the domain-specific test question set and the
script that produced the retrieval comparison table above.)*

## Security note

`mcp_server/.env` already exists in this repo with DB credentials. Before
pushing, confirm it is untracked (`git rm --cached mcp_server/.env` if it
was ever committed) — the root `.gitignore` added in this lab now covers
`.env` everywhere, including `rag/.env`.
