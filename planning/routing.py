# planning/routing.py
#
# ROUTING LIVES HERE. This is the single function a grader needs to open
# to see how a subtask gets assigned to Plan-and-Solve, Tree of Thoughts,
# or LATS (spec section 7: "Make the routing easy for a grader to
# locate" / "must exist in executable code", not just prose).
#
# The rule is deliberately about the SHAPE of the subtask, not a lookup
# table of task ids, so it generalizes to dynamic decomposition's
# on-the-fly tasks too, not just the fixed decomposition-first plan.

from __future__ import annotations

from enum import Enum


class Algorithm(str, Enum):
    PLAN_AND_SOLVE = "plan_and_solve"
    TREE_OF_THOUGHTS = "tree_of_thoughts"
    LATS = "lats"


# Keyword groups drive the decision. Order matters: LATS (expensive,
# real-feedback-gated) is checked before ToT, and ToT before the
# Plan-and-Solve default, so a subtask that matches more than one signal
# gets the more powerful algorithm it actually needs.
_LATS_SIGNALS = (
    "crew", "duty", "compensation", "assign reserve", "issue compensation",
)
_TOT_SIGNALS = (
    "candidate", "replacement flight", "rebook", "choose", "select", "which flight",
)


def route_subtask(instruction: str) -> Algorithm:
    """Decide which planning algorithm should execute one DAG node.

    - LATS: subtasks whose real cost of being wrong is high AND where a
      real external validator exists (crew duty-hour legality, compensation
      caps/duplicates) -- exactly the 'expensive decisions requiring real
      validation' case from the spec.
    - Tree of Thoughts: subtasks where multiple candidate options must be
      compared before picking one (which replacement flight to rebook
      onto) -- genuine branching/lookahead.
    - Plan-and-Solve: everything else -- mechanical, single-path subtasks
      (checking status, drafting the notice) where one plan executed
      sequentially is sufficient and cheaper.
    """
    text = instruction.lower()
    if any(signal in text for signal in _LATS_SIGNALS):
        return Algorithm.LATS
    if any(signal in text for signal in _TOT_SIGNALS):
        return Algorithm.TREE_OF_THOUGHTS
    return Algorithm.PLAN_AND_SOLVE


def route_plan(plan) -> dict[str, Algorithm]:
    """Route every node in a validated Plan (planning/models.py) at once,
    and write the decision back onto each Task.algorithm so it's visible
    in traces/artifacts, not just recomputed silently at execution time."""
    routing: dict[str, Algorithm] = {}
    for task in plan.tasks:
        algo = route_subtask(task.instruction)
        task.algorithm = algo.value
        routing[task.id] = algo
    return routing
