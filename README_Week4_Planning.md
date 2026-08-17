# Blue Horizon Airlines — Decomposition & Planning Agent

## 1. Overview

Blue Horizon Airlines operates an IROPS (Irregular Operations) system in which some requests cannot be safely resolved by a single MCP tool call. A disruption can require several dependent decisions, such as checking flight/crew constraints, reacting to a duty-hour breach, escalating for approval, re-planning after an observation, and validating a proposed compensation action against the live system state.

This Week 4 extension adds a dedicated planning agent alongside the existing Memory/RAG agent. It reuses the existing MCP server and database and applies decomposition, planning, search, and self-correction methods to multi-step airline operations requests.

The implementation is based on the required reference toolkit:

`github.com/AmrSheta22/task_decomposition_and_planning`

The toolkit algorithms are adapted to the Blue Horizon Airlines request types and real environment feedback rather than being rebuilt as independent algorithms.

## 2. Planning Problem

A representative request is an IROPS resolution request for a disrupted flight where the correct next action depends on observations made during execution.

For example, BH606 can require:

1. Inspect the affected flight and crew state.
2. Verify crew duty-hour and flight-hour constraints.
3. React if a crew member is at or over the legal duty-hour limit.
4. Escalate the breach for supervisor authorization or reassignment.
5. Re-plan based on the supervisor decision.
6. Re-verify compliance before continuing the IROPS resolution.

This is a genuine planning problem because an early result can invalidate a previously generated plan. The BH606 evaluation demonstrates this: the duty-hour verification produced a real breach for Capt. Hossam Zaher, causing the dynamic plan to change course.

## 3. System Structure

The planning agent is a separate agent from the Memory/RAG path.

High-level flow:

```text
Real IROPS request
       |
       v
DAG decomposition
       |
       +------------------------------+
       |                              |
       v                              v
Decomposition-first          Dynamic / Interleaved
       |                              |
       +---------------+--------------+
                       |
                       v
              Sub-task routing
                       |
          +------------+------------+
          |            |            |
          v            v            v
        PS           ToT           LATS
          |            |            |
          +------------+------------+
                       |
                       v
              Self-correction
              /             \
        Self-Refine       Reflexion
                              |
                              v
                    Grounded feedback
                              |
                              v
                         MCP / DB
```

## 4. Required Concerns

### 4.1 DAG Decomposition

Two decomposition strategies are implemented against the same real request type:

- **Decomposition-first:** generate the complete DAG before execution, then execute nodes in topological order.
- **Dynamic/interleaved decomposition:** generate the next sub-task after observing the previous result, allowing the plan to change after an unexpected result.

The DAG construction enforces acyclicity. A cyclic plan is rejected rather than being allowed to deadlock.

### 4.2 Planning Algorithms

Three planning algorithms are included:

- **Plan-and-Solve:** one explicit planning phase followed by a single-pass execution.
- **Tree of Thoughts:** generates multiple candidate next steps, evaluates them, and searches/prunes branches.
- **LATS:** performs MCTS-guided search using external environment feedback and reflections from failed branches.

The router selects the method according to the shape and risk of the sub-task rather than treating the algorithms as interchangeable defaults.

### 4.3 Self-Correction

Two different scopes are implemented:

- **Self-Refine:** one draft → explicit rubric critique → one revision. Appropriate for outputs that are inexpensive to redo.
- **Reflexion:** multiple trials with a capped episodic reflection buffer. Reflections from failed trials are carried into later trials when a single retry is insufficient.

### 4.4 Grounded Environment

The planning evaluation uses a real grounded feedback path rather than the reference toolkit's randomized evaluator.

The grounded check can verify the proposed action against the actual Blue Horizon Airlines state. One demonstrated case concerns compensation duplication:

- The proposed action appears acceptable to an ungrounded textual critic.
- The grounded environment checks the existing state.
- It finds an existing pending compensation of USD 120.
- The grounded environment rejects the duplicate action.
- The ungrounded critic incorrectly accepts it.

This produces the required grounded-vs-ungrounded distinction.

## 5. Evaluation Suite

The fixed evaluation suite contains four scenarios:

| Case | Purpose |
|---|---|
| `BH404_stable_single` | Stable request; decomposition-first should avoid unnecessary dynamic divergence. |
| `BH606_duty_breach` | Real duty-hour breach; dynamic decomposition should change course after the early observation. |
| `BH707_lookahead` | Request that benefits from lookahead/search. |
| `BH808_reflexion_duplicate_comp` | Duplicate-compensation case where cross-trial Reflexion memory is useful. |

The divergence recheck confirms the intended decomposition behavior:

| Case | Expected | Recomputed |
|---|---:|---:|
| BH404_stable_single | False | False |
| BH606_duty_breach | True | True |
| BH707_lookahead | Not a divergence test | False |
| BH808_reflexion_duplicate_comp | Not a divergence test | False |

For BH606, the divergence is grounded in the observed crew result:

> Capt. Hossam Zaher: 8.0 flying hours / 14.0 duty hours today — at/over the legal duty-hour cap.

The subsequent dynamic steps escalate the breach and reconsider crew assignment.

## 6. Latest Evaluation Results

The latest live Gemini evaluation produced the following results:

| Method | Task Success / Accuracy | Avg LLM Calls | Avg Tokens | Avg Latency | Estimated Cost |
|---|---:|---:|---:|---:|---:|
| Decomposition-first | N/A (structural) | 20.0 | 13,247.25 | 6.6526s | $0.0121 |
| Dynamic decomposition | 100% cases diverged from naive plan | 5.0 | 1,777.5 | 5.7726s | $0.0010 |
| Plan-and-Solve | N/A (no external success metric at this granularity) | 4.5 | 1,704.75 | 6.1906s | $0.0019 |
| Tree of Thoughts | N/A (candidate quality; see traces) | 9.0 | 1,460.75 | 5.8698s | $0.0019 |
| LATS | 100% grounded success | 2.0 | 417.75 | 5.8614s | $0.0004 |
| Self-Refine | N/A (rubric-based) | 3.0 | 1,573.67 | 6.198s | $0.0017 |
| Reflexion | 3/3 grounded success; 1/3 needed more than one trial | 1.6667 | 303.33 | 5.2744s | $0.0002 |

### Grounding check

The grounded-vs-ungrounded test produced:

```text
Ungrounded:
  success = true
  score = 1.0

Grounded:
  success = false
  score = 0.0

Reason:
  Existing pending compensation of 120.00 already on file.

ungrounded_wrongly_accepted_grounded_caught_it = true
```

The same run also confirmed Reflexion behavior:

```text
success = true
trials = 2
reflection_carried_forward = true
```

## 7. Method Selection

The final method should be selected by sub-task shape and evaluation evidence:

- Use **decomposition-first** for stable, predictable requests where early observations are unlikely to invalidate the remaining plan.
- Use **dynamic decomposition** for requests such as BH606 where execution results can force a different sequence of actions.
- Use **Plan-and-Solve** for sub-tasks that need a clear single-pass plan without meaningful branching.
- Use **Tree of Thoughts** where several candidate next steps should be explored and compared before committing.
- Use **LATS** for high-risk decisions where external environment feedback should guide search.
- Use **Self-Refine** for cheap-to-redo outputs that benefit from one explicit critique and revision.
- Use **Reflexion** where one retry is insufficient and the agent should learn from a failed trial within the same run.

The quantitative comparison above is the basis for these choices; the JSON traces in `planning_eval/artifacts/` provide the underlying evidence.

## 8. Evaluation Artifacts

Each evaluation run writes traces to:

```text
planning_eval/artifacts/
```

Current artifacts include:

```text
BH404_stable_single.json
BH606_duty_breach.json
BH707_lookahead.json
BH808_reflexion_duplicate_comp.json
comparison_table.md
```

The traces contain the evidence used for planning and self-correction evaluation, including plans, node outputs, feedback, reflections, and search information where applicable.

## 9. Running the Evaluation

Activate the project virtual environment and run:

```powershell
.\venv\Scripts\Activate.ps1
python -m planning_eval.runner
```

The runner executes the fixed planning evaluation suite and writes the comparison table and JSON traces.

Additional verification scripts used during development include:

```powershell
python recheck_divergence.py
python check_grounded_vs_ungrounded.py
```

`recheck_divergence.py` verifies that the stable case does not diverge while BH606 does.

`check_grounded_vs_ungrounded.py` verifies that grounded feedback can catch a failure missed by ungrounded critique and also verifies Reflexion's cross-trial reflection.

## 10. Demo Coverage

The demo transcript included with this repository demonstrates:

1. A real IROPS request.
2. Decomposition-first.
3. Dynamic decomposition and the BH606 divergence.
4. Plan-and-Solve.
5. Tree of Thoughts.
6. LATS with grounded feedback.
7. Self-Refine revision.
8. Reflexion carrying a reflection between trials.
9. Grounded environment rejecting a duplicate compensation that ungrounded critique accepted.

## 11. Safety and Reproducibility

- API keys and database credentials must remain in `.env`.
- `.env` must remain listed in `.gitignore`.
- No credentials should be committed.
- The planning test suite should remain fixed while collecting comparison numbers.
- Evaluation artifacts should be kept with the corresponding run so the reported metrics remain traceable.

## 12. Reference

This work extends the required task decomposition and planning reference toolkit:

`github.com/AmrSheta22/task_decomposition_and_planning`

The project adapts the reference algorithms to the Blue Horizon Airlines planning problem and connects evaluation to grounded airline state rather than the toolkit's randomized default evaluator.
