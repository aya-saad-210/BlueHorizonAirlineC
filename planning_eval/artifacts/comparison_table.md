LLM mode this run: **LIVE** (live) -- token/latency/cost numbers below are real, recorded from the live Gemini API.

| Method | Task Success / Accuracy | Avg LLM Calls | Avg Tokens | Avg Latency | Estimated Cost |
|---|---|---|---|---|---|
| Decomposition-first | N/A (structural) | 20.0 | 13247.25 | 6.6526s | $0.0121 |
| Dynamic decomposition | 100% of cases diverged from naive plan | 5.0 | 1777.5 | 5.7726s | $0.001 |
| Plan-and-Solve | N/A (no external success metric at this granularity) | 4.5 | 1704.75 | 6.1906s | $0.0019 |
| Tree of Thoughts | N/A (candidate quality, see traces) | 9.0 | 1460.75 | 5.8698s | $0.0019 |
| LATS | 100% grounded success | 2.0 | 417.75 | 5.8614s | $0.0004 |
| Self-Refine | N/A (rubric-based, no grounded success metric) | 3.0 | 1573.6667 | 6.198s | $0.0017 |
| Reflexion | 3/3 grounded success (1/3 cases needed more than 1 trial) | 1.6667 | 303.3333 | 5.2744s | $0.0002 |