# rag/ -- Person C's ownership area for the Memory & RAG Lab.
#
# Everything a grader needs to check the "Retrieval architectures" (15 pts),
# "Vector database architecture" (8 pts), and the RAG half of "Self-RAG-style
# verification" (8 pts) rubric rows lives in this package. See README.md at
# the repo root for the write-up; this file just maps concern -> file so
# nothing requires reading the whole package to locate.
#
#   concern                                   -> file
#   -----------------------------------------------------------------
#   chunking + metadata                       -> chunking.py
#   embeddings (pluggable, local by default)  -> embeddings.py
#   vector DB: ANN index + metadata store/idx -> vector_store.py
#   keyword/BM25 index (for hybrid search)    -> keyword_index.py
#   LLM call abstraction (real + mock mode)   -> llm_client.py
#   naive RAG                                 -> naive_rag.py
#   hybrid search RAG                         -> hybrid_search.py
#   agentic RAG (multi-hop reasoning loop)    -> agentic_rag.py
#   Self-RAG-style verification               -> self_rag_verify.py
#   build-the-index script                    -> ingest.py
