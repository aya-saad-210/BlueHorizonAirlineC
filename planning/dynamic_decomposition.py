# planning/dynamic_decomposition.py
#
# Dynamic/interleaved decomposition (spec section 4B): generate the NEXT
# subtask only after observing the result of the previous one. Unlike
# decomposition-first, this can react mid-run -- see
# planning_eval/test_suite.py case "BH606_duty_breach", where the crew
# duty-hour breach is only discoverable by actually querying
# duty_time_logs, so the plan for "assign crew" genuinely changes after
# step 1 runs (decomposition-first can't see this coming; dynamic can).

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from . import mcp_tools_adapter as tools
from .llm_client import generate_json, generate_text
from .routing import route_subtask


class DynamicDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    done: bool
    next_task: str


def dynamic_decomposition(goal: str, flight_number: str, ctx, max_steps: int = 5) -> list[dict]:
    """Returns the run history: [{task, algorithm, result, diverged_from_plan}, ...]
    `ctx` is either the real FastMCP Context (interactive mode) or
    StubSupervisorContext (eval mode) -- see planning/mcp_tools_adapter.py."""
    history: list[dict] = []
    for step in range(max_steps):
        observation = "\n".join(f"{h['task']}: {h['result']}" for h in history) or "None"
        decision = generate_json(
            system=(
                "You are an ADAPTIVE IROPS planner. Use prior observations before "
                "deciding what comes next. If an observation reveals a problem "
                "(e.g. a duty-hour breach, no available replacement flight, "
                "existing compensation on file), change course instead of "
                "continuing the original assumption."
            ),
            user=f"Goal: {goal}\nFlight: {flight_number}\n"
                 f"Completed work and observations:\n{observation}\n\n"
                 "Decide the single best next task. Set done to true only when "
                 "the goal is fully handled. When done is true, next_task is "
                 "empty.",
            schema=DynamicDecision,
        )
        if decision.done:
            break
        task_instruction = decision.next_task.strip()
        if not task_instruction:
            raise ValueError(f"Dynamic planner omitted next_task at step {step + 1}")

        algo = route_subtask(task_instruction)
        result = _execute_dynamic_step(task_instruction, flight_number, observation, ctx)

        diverged = _looks_like_divergence(task_instruction, history)
        history.append({
            "task": task_instruction,
            "algorithm": algo.value,
            "result": result,
            "diverged_from_plan": diverged,
        })
    return history


def _looks_like_divergence(task_instruction: str, history: list[dict]) -> bool:
    """Cheap, explicit heuristic (not vibes): a step counts as a divergence
    from a naive up-front plan when its wording reacts to something an
    up-front planner couldn't have known yet -- an approval/override, a
    fallback, or a rejection carried over from the previous step."""
    text = task_instruction.lower()
    reactive_markers = ("override", "escalate", "fallback", "alternative", "instead", "supervisor")
    return any(m in text for m in reactive_markers)


def _execute_dynamic_step(task_instruction: str, flight_number: str, observation: str, ctx) -> str:
    """Ground the step in real data where the instruction clearly maps to
    one of the real read tools; otherwise fall back to an LLM completion
    describing the step (still logged, still part of the trace)."""
    text = task_instruction.lower()
    if "status" in text and "flight" in text:
        return tools.get_flight_status(flight_number)
    if "duty" in text or "crew" in text:
        # GROUNDED, not an LLM guess: real duty_time_logs joined to
        # whoever is actually assigned to this flight. This is exactly
        # what lets the mid-run divergence in test case BH606_duty_breach
        # be a genuine reaction to real data, not a scripted branch.
        assigned = tools.list_crew_assigned_to_flight(flight_number)
        if not assigned:
            return f"No crew currently assigned to {flight_number} in the system."
        lines = []
        for c in assigned:
            flown, duty = float(c["total_flown"]), float(c["total_duty"])
            breach = flown >= 8.00 or duty >= 14.00
            status = "AT/OVER the legal duty-hour cap -- requires supervisor approval to assign" if breach else "within legal limits"
            lines.append(f"{c['full_name']} ({c['role']}, {c['assignment_type']}): "
                         f"{flown} flying hrs / {duty} duty hrs today -- {status}")
        return "\n".join(lines)
    return generate_text(
        system="Execute the next adaptive IROPS sub-task using the observations provided.",
        user=f"Flight: {flight_number}\nStep: {task_instruction}\nPrior observations:\n{observation}",
    )
