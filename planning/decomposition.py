# planning/decomposition.py
#
# Decomposition-first (spec section 4A): the complete plan is generated
# up front as a validated DAG (cycle check happens inside Plan itself --
# see planning/models.py), then every node is executed in topological
# order via plan.execution_batches().
#
# Interface preserved from the reference toolkit's decomposition.py, with
# llm.with_structured_output(...) (LangChain/Mistral) replaced by
# planning/llm_client.py's generate_json (Mistral tool-use JSON).

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from .llm_client import generate_json, generate_text
from .models import Plan
from .routing import route_plan

PLANNER_SYSTEM = """You are the Blue Horizon Airlines IROPS planning agent.
Produce a small executable DAG, not a prose checklist, for responding to one
flight disruption. Every task must make a concrete contribution: checking
status, rebooking passengers, checking/assigning crew, or determining
compensation and drafting the notice. The plan must end with exactly one
synthesis task (drafting the passenger notice) depending on every necessary
branch."""


class PlannedTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    instruction: str
    depends_on: list[str]


class GeneratedPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal: str
    tasks: list[PlannedTask]


def decompose_goal(goal: str) -> Plan:
    generated = generate_json(
        system=PLANNER_SYSTEM,
        user=(
            f"Decompose this goal into 3-6 tasks: {goal!r}\n"
            "Use short task ids such as t1. Dependencies may refer only to "
            "tasks in the plan. Preserve the supplied goal exactly in the "
            "plan's goal field."
        ),
        schema=GeneratedPlan,
    )
    payload = generated.model_dump()
    payload["goal"] = goal  # the caller's goal stays authoritative
    plan = Plan.model_validate(payload)  # cycle check runs here, automatically
    route_plan(plan)
    return plan


def execute_plan(plan: Plan, node_executor) -> dict[str, str]:
    """node_executor(task, prior_outputs: dict[str, str]) -> str.
    Runs plan.execution_batches() in order; within a batch, nodes could run
    concurrently, but for this project we run them sequentially so grounded
    write tools never race each other against the same rows."""
    outputs: dict[str, str] = {}
    for batch in plan.execution_batches():
        for task_id in batch:
            task = plan.task(task_id)
            prior = {dep: outputs[dep] for dep in task.depends_on}
            outputs[task_id] = node_executor(task, prior)
    return outputs


def default_node_executor(task, prior_outputs: dict[str, str]) -> str:
    """Fallback executor used when a caller doesn't supply a routed one
    (e.g. quick manual testing): a single Plan-and-Solve-style LLM call."""
    context = "\n".join(f"{k}: {v}" for k, v in prior_outputs.items()) or "No prerequisite outputs."
    return generate_text(
        system="You execute one node in a validated task DAG for Blue Horizon Airlines IROPS.",
        user=f"Task: {task.instruction}\nPrior outputs:\n{context}",
    )


def final_output(plan: Plan, outputs: dict[str, str]) -> str:
    terminals = plan.terminal_tasks()
    if len(terminals) != 1:
        raise ValueError(f"Expected exactly one terminal synthesis task, found {terminals}")
    return outputs[terminals[0]]
