# planning/algorithms/lats.py
#
# Routed to subtasks where being wrong is expensive AND a real external
# validator exists: crew duty-hour legality, compensation caps/duplicates
# (spec section 6C: "LATS MUST use a real external/environment feedback
# source, not the toolkit's randomized evaluator").
#
# select -> expand/simulate -> evaluate/reflect -> backpropagate is
# preserved verbatim in structure from the reference toolkit's lats.py.
# The only real change: `environment.evaluate(state)` here is an async
# callable that performs a REAL grounded check (planning/environment.py +
# planning/mcp_tools_adapter.py, i.e. a real MCP tool call against real
# blue_horizon_db rows), passed in by the caller for the specific action
# type (crew assignment vs. compensation) instead of a fixed toolkit
# Environment instance.

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from pydantic import BaseModel, ConfigDict, Field

from ..llm_client import generate_json, generate_text
from ..models import EnvironmentFeedback

EvaluateFn = Callable[[str], Awaitable[EnvironmentFeedback]]


class LATSAction(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    action: str = Field(min_length=2)
    state: str = Field(min_length=2)


class LATSActionBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actions: list[LATSAction] = Field(min_length=1, max_length=3)


class ValueEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    score: float = Field(ge=0.0, le=1.0)


@dataclass
class LATSNode:
    state: str
    action: str = "root"
    parent: "LATSNode | None" = field(default=None, repr=False)
    children: list["LATSNode"] = field(default_factory=list, repr=False)
    visits: int = 0
    value_sum: float = 0.0
    environment_score: float = 0.0
    model_score: float = 0.0
    feedback: EnvironmentFeedback | None = None
    reflections: list[str] = field(default_factory=list)

    @property
    def mean_value(self) -> float:
        return self.value_sum / self.visits if self.visits else 0.0


@dataclass
class LATSResult:
    success: bool
    output: str
    best_score: float
    iterations: int
    root: LATSNode


def _uct(node: LATSNode, exploration_weight: float) -> float:
    if node.visits == 0:
        return float("inf")
    parent_visits = max(node.parent.visits if node.parent else 1, 1)
    return node.mean_value + exploration_weight * math.sqrt(math.log(parent_visits) / node.visits)


def _select_leaf(root: LATSNode, exploration_weight: float) -> LATSNode:
    node = root
    while node.children:
        node = max(node.children, key=lambda child: _uct(child, exploration_weight))
    return node


def _backpropagate(node: LATSNode, value: float) -> None:
    while node is not None:
        node.visits += 1
        node.value_sum += value
        node = node.parent


def _trajectory_reflections(node: LATSNode) -> list[str]:
    path: list[str] = []
    while node is not None:
        path.extend(node.reflections)
        node = node.parent
    return list(reversed(path))


async def lats(
    task: str,
    evaluate: EvaluateFn,
    iterations: int = 2,
    n_actions: int = 2,
    exploration_weight: float = 1.414,
) -> LATSResult:
    if iterations < 1 or n_actions < 1:
        raise ValueError("iterations and n_actions must be positive")
    root = LATSNode(state="No attempt yet.")
    best = root
    completed_iterations = 0
    for iteration in range(1, iterations + 1):
        completed_iterations = iteration
        leaf = _select_leaf(root, exploration_weight)
        lessons = _trajectory_reflections(leaf)
        lesson_text = "\n".join(f"- {item}" for item in lessons[-4:]) or "- None yet."
        proposed = generate_json(
            system="You are the action generator in LATS for a Blue Horizon Airlines IROPS decision.",
            user=f"Task: {task}\nCurrent trajectory/state:\n{leaf.state}\n"
                 f"Reflections learned from failed branches:\n{lesson_text}\n\n"
                 f"Propose exactly {n_actions} distinct complete candidate solution(s). "
                 "Each state must contain the fully written solution (concrete IDs/amounts), "
                 "not a placeholder or description.",
            schema=LATSActionBatch,
        )
        for item in proposed.actions[:n_actions]:
            child = LATSNode(state=item.state.strip(), action=item.action, parent=leaf)
            leaf.children.append(child)
            feedback = await evaluate(child.state)  # <-- REAL grounded call, no randomness
            child.feedback = feedback
            child.environment_score = feedback.score
            value_judgment = generate_json(
                system="You are the LATS value function for an IROPS decision.",
                user=f"Task: {task}\nCandidate state:\n{child.state}\n"
                     f"External score: {feedback.score}\nExternal feedback: {feedback.details}\n"
                     "Estimate the candidate's future usefulness.",
                schema=ValueEstimate,
            )
            child.model_score = value_judgment.score
            combined_value = 0.75 * child.environment_score + 0.25 * child.model_score
            if not feedback.success:
                reflection = generate_text(
                    system="Create a branch-level LATS reflection grounded in real environment feedback.",
                    user=f"Task: {task}\nAction: {child.action}\nResulting state: {child.state}\n"
                         f"External feedback: {feedback.details}\n"
                         "Explain briefly why this branch failed and how a later expansion should change.",
                ).strip()
                child.reflections.append(reflection)
            _backpropagate(child, combined_value)
            if best is root or child.environment_score > best.environment_score:
                best = child
            if feedback.success:
                return LATSResult(True, child.state, child.environment_score, completed_iterations, root)
    return LATSResult(False, best.state, best.environment_score, completed_iterations, root)


def flatten_lats_tree(root: LATSNode) -> list[dict]:
    records: list[dict] = []
    queue: list[tuple[LATSNode, str | None]] = [(root, None)]
    next_id = 0
    while queue:
        node, parent_id = queue.pop(0)
        node_id = f"n{next_id}"
        next_id += 1
        records.append({
            "id": node_id, "parent_id": parent_id, "action": node.action, "state": node.state,
            "visits": node.visits, "mean_value": node.mean_value,
            "environment_score": node.environment_score, "model_score": node.model_score,
            "feedback": node.feedback.model_dump() if node.feedback else None,
            "reflections": node.reflections,
        })
        queue.extend((child, node_id) for child in node.children)
    return records


# ---------------------------------------------------------------------
# Concrete grounded evaluate() factories for the two LATS-routed subtask
# types in this project (crew assignment / compensation). These are what
# make evaluate() real instead of theoretical: they parse the candidate's
# free-text `state` for the concrete id/amount the LLM proposed, then
# actually call the real write tool through planning/environment.py.
# ---------------------------------------------------------------------

_CREW_ID_RE = re.compile(r"crew[_\s]?id\D{0,5}(\d+)", re.IGNORECASE)
_AMOUNT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(USD|EUR|GBP|EGP)", re.IGNORECASE)


def make_crew_assignment_evaluate(grounded_env, flight_number: str, ctx) -> EvaluateFn:
    async def evaluate(state: str) -> EnvironmentFeedback:
        match = _CREW_ID_RE.search(state)
        if not match:
            return EnvironmentFeedback(success=False, score=0.0, details=[
                "Candidate did not name a concrete crew_id; cannot ground this check."
            ])
        crew_id = int(match.group(1))
        return await grounded_env.evaluate_crew_assignment(flight_number, crew_id, ctx)
    return evaluate


def make_compensation_evaluate(grounded_env, passenger_email: str, flight_number: str, reason: str, ctx) -> EvaluateFn:
    async def evaluate(state: str) -> EnvironmentFeedback:
        match = _AMOUNT_RE.search(state)
        if not match:
            return EnvironmentFeedback(success=False, score=0.0, details=[
                "Candidate did not name a concrete amount/currency; cannot ground this check."
            ])
        amount, currency = float(match.group(1)), match.group(2).upper()
        return await grounded_env.evaluate_compensation(passenger_email, flight_number, amount, currency, reason, ctx)
    return evaluate
