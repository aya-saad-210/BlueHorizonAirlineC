LLM mode this run: **MOCK** (mock) -- token/latency/cost numbers below are deterministic mock estimates; re-run with MISTRAL_API_KEY set for real numbers.

| Method | Task Success / Accuracy | Avg LLM Calls | Avg Tokens | Avg Latency | Estimated Cost |
|---|---|---|---|---|---|
| Decomposition-first | N/A (structural) | 17.0 | 2608.0 | 0.0s | $0.0159 |
| Dynamic decomposition | 25% of cases diverged from naive plan | 5.25 | 739.75 | 0.0s | $0.003 |
| Plan-and-Solve | N/A (no external success metric at this granularity) | 4.0 | 456.0 | 0.0s | $0.003 |
| Tree of Thoughts | N/A (candidate quality, see traces) | 9.0 | 721.0 | 0.0s | $0.004 |
| LATS | 100% grounded success | 2.0 | 241.75 | 0.0001s | $0.0012 |
| Self-Refine | N/A (rubric-based, no grounded success metric) | 2.0 | 137.0 | 0.0s | $0.0006 |
| Reflexion | 3/3 grounded success | 0.3333 | 36.6667 | 0.0s | $0.0002 |