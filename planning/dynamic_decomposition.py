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
                "deciding what comes next. A full IROPS resolution is not just "
                "passenger-facing -- it MUST also verify that any crew assigned "
                "to the disrupted or replacement flight is legally within duty-"
                "hour and flight-hour limits before the plan can be considered "
                "handled. If you have not yet checked crew duty/flight hours for "
                "this flight, that check belongs early in the plan, before "
                "passenger notifications are finalized. If an observation "
                "reveals a problem (e.g. a duty-hour breach, no available "
                "replacement flight, existing compensation on file), change "
                "course instead of continuing the original assumption -- for a "
                "duty-hour breach specifically, escalate to supervisor approval "
                "rather than assigning the breaching crew member anyway."
            ),
            user=f"Goal: {goal}\nFlight: {flight_number}\n"
                 f"Completed work and observations:\n{observation}\n\n"
                 "Decide the single best next task. Set done to true only when "
                 "the goal is fully handled, INCLUDING a crew duty/flight-hour "
                 "check for this flight. When done is true, next_task is "
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

        diverged = _looks_like_divergence(history + [{"task": task_instruction, "result": result}])
        history.append({
            "task": task_instruction,
            "algorithm": algo.value,
            "result": result,
            "diverged_from_plan": diverged,
        })
    return history


def _looks_like_divergence(history: list[dict]) -> bool:
    """Grounded AND specific -- a step counts as a genuine divergence from
    an up-front plan only when the MOST RECENT completed step was itself a
    duty/crew/compensation check AND its real, tool-grounded result shows
    an actual problem an up-front planner could not have anticipated.

    This replaces two earlier, looser attempts:
      1. Keyword-matching the *next* instruction's wording ("override",
         "escalate", ...) -- silently missed genuine reactions phrased
         differently, and could fire on a step that had nothing to do
         with a real observation.
      2. Keyword-matching ANY prior result for generic markers including
         "no crew currently assigned" and bare "rejected" -- both false-
         positived constantly: "no crew currently assigned" is the
         ORDINARY pre-assignment state for almost every flight (seed data
         only pre-assigns crew for the duty-breach case), not a surprise;
         bare "rejected" matches substrings like "was NOT rejected" too.
         That version produced 100% divergence across every test case,
         including BH404 -- a case explicitly designed to show NO
         divergence (see its "exercises" field in test_suite.py) -- which
         would have contradicted the README's own claims if left in.

    This version requires the recent step to have actually been a
    duty/crew/compensation check (via its task wording) before trusting
    its result as a "problem" signal, and only matches specific,
    tool-grounded phrases that only appear when something is genuinely
    wrong -- not the default/expected state of "nothing assigned yet"."""
    if not history:
        return False

    last = history[-1]
    result_lower = last["result"].lower()
    task_lower = last["task"].lower()

    is_duty_or_crew_check = "duty" in task_lower or "crew" in task_lower
    is_compensation_check = "compensation" in task_lower or "duplicate" in task_lower

    if is_duty_or_crew_check and "at/over the legal duty-hour cap" in result_lower:
        # A real, tool-grounded duty-hour breach -- the exact condition
        # BH606_duty_breach exists to exercise. Deliberately does NOT
        # match "no crew currently assigned" (that's the ordinary state
        # before assignment, not a breach).
        return True

    if is_compensation_check and "already on file" in result_lower:
        # A real, tool-grounded existing-compensation record.
        return True

    if "rejected:" in result_lower:
        # A real write-tool rejection (see planning/environment.py:
        # success = result.startswith("Approved"); a failed write starts
        # with "Rejected: ...", a specific prefix -- not the bare
        # substring "rejected", which also matches "was not rejected".
        return True

    return False


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
