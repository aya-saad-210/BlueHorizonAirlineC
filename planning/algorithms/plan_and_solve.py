# planning/algorithms/plan_and_solve.py
#
# Plan-and-Solve, routed to mechanical/sequential subtasks (checking
# status, drafting the notice) by planning/routing.py. One explicit plan,
# executed step by step, no branching search -- deliberately the cheapest
# of the three algorithms, which is exactly why the comparison table
# (spec section 13) should show it winning on LLM-calls/latency/cost for
# these subtask types.

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from ..llm_client import generate_json, generate_text


class SolveSteps(BaseModel):
    model_config = ConfigDict(extra="forbid")
    steps: list[str]


def plan_and_solve(instruction: str, context: str) -> str:
    steps = generate_json(
        system="Break this IROPS subtask into 2-4 concrete sequential steps. No branching -- one path only.",
        user=f"Subtask: {instruction}\nContext:\n{context}",
        schema=SolveSteps,
    )
    running_context = context
    last_result = ""
    for step in steps.steps:
        last_result = generate_text(
            system="Execute exactly this one step of an IROPS subtask plan. Be concrete.",
            user=f"Step: {step}\nContext so far:\n{running_context}",
        )
        running_context += f"\n{step}: {last_result}"
    return last_result
