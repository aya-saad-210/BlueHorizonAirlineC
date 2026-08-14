# planning/algorithms/tree_of_thoughts.py
#
# Routed to subtasks that need to compare multiple candidate options before
# committing (e.g. "which replacement flight to rebook onto" when several
# scheduled flights exist -- see planning_eval/test_suite.py case
# "BH707_lookahead"). This genuinely searches: generate -> evaluate ->
# keep top beam_width -> repeat, not one LLM call dressed up with a
# different prompt (spec section 6B).
#
# Interface preserved from the reference toolkit's tree_of_thoughts.py;
# llm.with_structured_output(...) replaced with generate_json.

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..llm_client import generate_json
from ..models import Thought


class ThoughtCandidates(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidates: list[str] = Field(min_length=1, max_length=3)


class ThoughtEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    score: float = Field(ge=0.0, le=1.0)
    rationale: str


def tree_of_thoughts(problem: str, depth: int = 2, beam_width: int = 2) -> list[Thought]:
    frontier = [Thought(state="Start", score=0.5, rationale="root")]
    for _ in range(depth):
        candidates: list[Thought] = []
        for parent in frontier:
            generated = generate_json(
                system="Generate distinct candidate next steps for Tree-of-Thoughts search over IROPS options.",
                user=f"Problem: {problem}\nPartial path: {parent.state}\nPropose two distinct promising continuations.",
                schema=ThoughtCandidates,
            )
            for state in generated.candidates[:2]:
                judged = generate_json(
                    system="Independently evaluate a partial IROPS solution. Score correctness, feasibility, and passenger impact. Do not reward confident wording.",
                    user=f"Problem: {problem}\nCandidate path: {state}",
                    schema=ThoughtEvaluation,
                )
                candidates.append(Thought(state=state, score=judged.score, rationale=judged.rationale))
        # KEEP/PRUNE: this is the actual beam search step -- only the top
        # beam_width candidates survive into the next depth.
        frontier = sorted(candidates, key=lambda item: item.score, reverse=True)[:beam_width]
        if not frontier:
            break
    return frontier
