# mcp_server/tools_planning.py
#
# The ONE new tool the Planning Agent adds to the existing MCP server.
# Registered in server.py exactly the way every other tool is (mcp.tool()),
# nothing about the existing tools/notifications/sampling/elicitation
# wiring changes.
#
# This file lives in mcp_server/ (not planning/) because it is the
# MCP-facing adapter, following the same split the project already uses
# (rag_tool.py in mcp_server/ wraps the Rag/ package the same way this
# wraps the planning/ package).

from __future__ import annotations

import sys
from pathlib import Path

from mcp.server.fastmcp import Context

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from planning.agent import run_irops_demo, usage_snapshot  # noqa: E402


async def plan_irops_response(
    flight_number: str,
    passenger_email: str,
    ctx: Context,
) -> str:
    """
    Runs the full Planning Agent for one disrupted flight: decomposition-first
    AND dynamic decomposition, routed through Plan-and-Solve / Tree of
    Thoughts / LATS, Self-Refine on the passenger notice, and Reflexion +
    grounded-vs-ungrounded comparison on compensation. Uses the connected
    client's real elicitation for any supervisor-approval prompts, exactly
    like assign_reserve_crew/issue_compensation already do.

    flight_number: the disrupted flight, e.g. BH606
    passenger_email: one affected passenger's registered email, used for
        the compensation/Reflexion portion of the run
    """
    result = await run_irops_demo(flight_number, passenger_email, ctx)

    lines = [
        f"IROPS planning run for {flight_number}",
        "",
        "-- Decomposition-first plan --",
        *[f"  {t.id} [{t.algorithm}]: {t.instruction}" for t in result.decomposition_first_plan.tasks],
        "",
        "-- Dynamic decomposition trace --",
        *[f"  step {i+1} [{h['algorithm']}]{' (diverged)' if h['diverged_from_plan'] else ''}: {h['task']}"
          for i, h in enumerate(result.dynamic_history)],
        "",
        "-- Tree of Thoughts (replacement flight choice) --",
        *[f"  score={t.score:.2f}: {t.state}" for t in result.tot_thoughts],
        "",
        "-- LATS (crew assignment) --",
        f"  success={result.lats_result.success if result.lats_result else 'N/A'} "
        f"output={result.lats_result.output if result.lats_result else 'no reserve crew candidates found'}",
        "",
        "-- Self-Refine (passenger notice) --",
        f"  revised={result.self_refine_result['revised']}",
        f"  final: {result.self_refine_result['revision']}",
        "",
        "-- Reflexion (compensation) --",
        f"  success={result.reflexion_result.success if result.reflexion_result else 'N/A'} "
        f"trials={len(result.reflexion_result.trials) if result.reflexion_result else 0}",
        "",
        "-- Grounded vs ungrounded critique (compensation) --",
        f"  {result.grounded_vs_ungrounded}",
        "",
        f"-- LLM usage this run -- {usage_snapshot()}",
    ]
    return "\n".join(lines)
