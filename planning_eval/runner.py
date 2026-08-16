# planning_eval/runner.py
#
# Runs every required method (spec section 11) against the FIXED test
# suite (test_suite.py) and produces:
#   - one artifacts/<case_id>.json trace per case (spec section 14)
#   - a markdown comparison table (spec section 13)
#
# METRICS ARE REAL, NOT INVENTED: every number comes from
# planning/llm_client.py's UsageMeter, which is reset before each
# measured segment and read immediately after. In MOCK mode
# (no GEMINI_API_KEY set) every reported number is clearly labeled
# "(mock)" -- these exercise every code path correctly and honestly, but
# are NOT the numbers to put in a final submission. Set GEMINI_API_KEY
# and re-run for the numbers that belong in the README's final table
# (see planning/README section "Two ways to run this").

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from planning import mcp_tools_adapter as tools  # noqa: E402
from planning.algorithms.lats import lats, make_crew_assignment_evaluate, make_compensation_evaluate  # noqa: E402
from planning.algorithms.plan_and_solve import plan_and_solve  # noqa: E402
from planning.algorithms.reflexion import reflection_carried_forward, reflexion  # noqa: E402
from planning.algorithms.self_refine import self_refine  # noqa: E402
from planning.algorithms.tree_of_thoughts import tree_of_thoughts  # noqa: E402
from planning.decomposition import decompose_goal, execute_plan  # noqa: E402
from planning.dynamic_decomposition import dynamic_decomposition  # noqa: E402
from planning.environment import GroundedEnvironment, UngroundedCritique  # noqa: E402
from planning.llm_client import METER, MODE, estimated_cost_usd, generate_text  # noqa: E402
from planning_eval.test_suite import TEST_SUITE  # noqa: E402

ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"
ARTIFACTS_DIR.mkdir(exist_ok=True)

LABEL = "(mock)" if MODE == "mock" else "(live)"


def _measure(fn):
    """Reset the usage meter, run fn(), return (result, metrics_dict)."""
    METER.reset()
    start = time.monotonic()
    result = fn()
    wall_s = time.monotonic() - start
    metrics = {
        "llm_calls": METER.calls,
        "input_tokens": METER.input_tokens,
        "output_tokens": METER.output_tokens,
        "total_tokens": METER.total_tokens,
        "avg_llm_latency_s": round(METER.avg_latency_s, 4),
        "wall_time_s": round(wall_s, 4),
        "estimated_cost_usd": round(estimated_cost_usd(METER.input_tokens, METER.output_tokens), 6),
    }
    return result, metrics


async def _measure_async(coro_fn):
    METER.reset()
    start = time.monotonic()
    result = await coro_fn()
    wall_s = time.monotonic() - start
    metrics = {
        "llm_calls": METER.calls,
        "input_tokens": METER.input_tokens,
        "output_tokens": METER.output_tokens,
        "total_tokens": METER.total_tokens,
        "avg_llm_latency_s": round(METER.avg_latency_s, 4),
        "wall_time_s": round(wall_s, 4),
        "estimated_cost_usd": round(estimated_cost_usd(METER.input_tokens, METER.output_tokens), 6),
    }
    return result, metrics


def run_decomposition_comparison(case) -> dict:
    goal = f"Handle the full IROPS response for disrupted flight {case.flight_number}"

    def run_df():
        plan = decompose_goal(goal)
        outputs = execute_plan(plan, lambda t, prior: plan_and_solve(t.instruction, str(prior)))
        return {"plan": [t.model_dump() for t in plan.tasks], "outputs": outputs}

    df_result, df_metrics = _measure(run_df)

    ctx = tools.StubSupervisorContext(policy="approve")

    def run_dynamic():
        return dynamic_decomposition(goal, case.flight_number, ctx, max_steps=5)

    dyn_result, dyn_metrics = _measure(run_dynamic)
    diverged = any(step["diverged_from_plan"] for step in dyn_result)

    return {
        "decomposition_first": {"result": df_result, "metrics": df_metrics},
        "dynamic": {"result": dyn_result, "metrics": dyn_metrics, "diverged_from_naive_plan": diverged},
    }


def run_planning_algorithm_comparison(case) -> dict:
    status = tools.get_flight_status(case.flight_number)

    def run_ps():
        return plan_and_solve(f"Choose the best replacement flight for {case.flight_number}", status)

    ps_result, ps_metrics = _measure(run_ps)

    def run_tot():
        return tree_of_thoughts(f"Choose the best replacement flight for {case.flight_number}. {status}")

    tot_result, tot_metrics = _measure(run_tot)
    tot_best = max(tot_result, key=lambda t: t.score) if tot_result else None

    return {
        "plan_and_solve": {"result": ps_result, "metrics": ps_metrics},
        "tree_of_thoughts": {
            "result": [t.model_dump() for t in tot_result],
            "best": tot_best.model_dump() if tot_best else None,
            "metrics": tot_metrics,
        },
    }


async def run_lats_case(case) -> dict:
    env = GroundedEnvironment()
    ctx = tools.StubSupervisorContext(policy="approve")
    status_text = tools.get_flight_status(case.flight_number)
    origin = status_text.split(" ")[3] if "from" in status_text else "HRG"
    candidates = tools.find_reserve_crew(base_airport=origin, role="pilot")
    if not candidates:
        return {"skipped": "no reserve crew candidates for this route in seed data"}
    evaluate_fn = make_crew_assignment_evaluate(env, case.flight_number, ctx)
    # The proposer must pick from REAL crew_ids -- without this, a model
    # (mock or live) can only guess an id, which the grounded check will
    # correctly reject as "no crew member found", masking the actual duty-
    # hour-breach scenario this case exists to exercise. Caught by running
    # this case, not by reading the code.
    candidate_text = "; ".join(f"crew_id={c['crew_id']} ({c['full_name']})" for c in candidates)

    async def run():
        return await lats(
            f"Assign legal reserve crew to flight {case.flight_number}. "
            f"Real candidate crew_ids at this base: {candidate_text}. "
            "Propose one crew_id at a time from this real list only.",
            evaluate_fn, iterations=2, n_actions=2,
        )

    result, metrics = await _measure_async(run)
    return {"success": result.success, "best_score": result.best_score, "iterations": result.iterations,
            "output": result.output, "metrics": metrics}


async def run_self_correction_comparison(case) -> dict:
    if not case.passenger_email:
        return {"skipped": "case has no passenger_email (crew-focused case)"}

    env = GroundedEnvironment()
    ungrounded = UngroundedCritique()
    proposed = "Issue 120.00 USD compensation for the disruption."

    def run_self_refine():
        return self_refine("Draft the passenger disruption notice",
                            f"Flight: {case.flight_number}, passenger: {case.passenger_email}")

    sr_result, sr_metrics = _measure(run_self_refine)

    ctx = tools.StubSupervisorContext(policy="approve")

    async def attempt_fn(prompt: str) -> str:
        # REAL LLM call -- the model reads the full prompt, including any
        # reflections carried over from a prior failed trial (see
        # reflexion.py's `lessons` block), and decides what to propose.
        # Nothing here is scripted: trial 1 has no reflections yet and
        # will typically propose a naive compensation amount; if trial 1
        # is rejected, trial 2's prompt literally contains the reflection
        # text, and whether the model actually changes its behavior in
        # response is a genuine test, not a guaranteed outcome.
        return generate_text(
            system=(
                "You are resolving a passenger compensation request for a "
                "disrupted flight. Propose ONE concrete action. If nothing in "
                "your prior reflections tells you otherwise, propose a "
                "specific compensation amount (state it clearly, e.g. 'Issue "
                "150.00 USD compensation for the disruption.'). If a prior "
                "reflection tells you to check for an existing record before "
                "acting, do that instead: say explicitly that you are "
                "withholding a new compensation write pending that check, and "
                "do not state a new amount."
            ),
            user=prompt,
        )

    async def grounded_check(attempt: str):
        import re
        match = re.search(r"(\d+(?:\.\d+)?)\s*(USD|EUR|GBP|EGP)", attempt, re.IGNORECASE)
        if match:
            # The model proposed a concrete amount -- check it against the
            # real write tool, which is what will actually reject a
            # duplicate regardless of amount.
            amount, currency = float(match.group(1)), match.group(2).upper()
            return await env.evaluate_compensation(case.passenger_email, case.flight_number, amount, currency,
                                                     "flight disrupted", ctx)
        # The model withheld a new amount -- verify against the real DB
        # whether that caution was actually justified, rather than trusting
        # the model's own claim.
        precheck = env.precheck_duplicate_compensation(case.passenger_email, case.flight_number)
        confirmed_duplicate_exists = not precheck.success
        return type(precheck)(
            success=confirmed_duplicate_exists,
            score=1.0 if confirmed_duplicate_exists else 0.0,
            details=[f"Correctly withheld -- duplicate confirmed: {precheck.details[0]}"] if confirmed_duplicate_exists
                    else ["Withheld action but no duplicate found -- withholding was NOT justified."],
        )

    async def run_reflexion():
        return await reflexion(f"Issue correct compensation for {case.passenger_email} on {case.flight_number}",
                                attempt_fn, grounded_check, max_trials=2)

    reflexion_result, reflexion_metrics = await _measure_async(run_reflexion)

    # grounded vs ungrounded, real divergence evidence
    u = ungrounded.evaluate(proposed, context=f"Passenger: {case.passenger_email}, flight: {case.flight_number}")
    g = env.precheck_duplicate_compensation(case.passenger_email, case.flight_number)

    return {
        "self_refine": {"revised": sr_result["revised"], "final": sr_result["revision"], "metrics": sr_metrics},
        "reflexion": {
            "success": reflexion_result.success,
            "trials": len(reflexion_result.trials),
            "reflection_carried_forward": reflection_carried_forward(reflexion_result),
            "episodic_buffer": reflexion_result.episodic_buffer,
            "metrics": reflexion_metrics,
        },
        "grounded_vs_ungrounded": {
            "ungrounded": u.model_dump(),
            "grounded": g.model_dump(),
            "ungrounded_wrongly_accepted_grounded_caught_it": bool(u.success and not g.success),
        },
    }


async def run_case(case) -> dict:
    print(f"== {case.case_id} == ({case.exercises})")
    trace: dict = {"case_id": case.case_id, "exercises": case.exercises, "llm_mode": MODE}

    trace["decomposition"] = run_decomposition_comparison(case)
    trace["planning_algorithms"] = run_planning_algorithm_comparison(case)
    trace["lats"] = await run_lats_case(case)
    trace["self_correction"] = await run_self_correction_comparison(case)

    out_path = ARTIFACTS_DIR / f"{case.case_id}.json"
    out_path.write_text(json.dumps(trace, indent=2, default=str))
    print(f"   trace written -> {out_path}")
    return trace


def _avg_metrics(metric_dicts: list[dict]) -> dict:
    if not metric_dicts:
        return {}
    keys = metric_dicts[0].keys()
    return {k: round(sum(m[k] for m in metric_dicts) / len(metric_dicts), 4) for k in keys}


def build_comparison_table(traces: list[dict]) -> str:
    df_metrics = [t["decomposition"]["decomposition_first"]["metrics"] for t in traces]
    dyn_metrics = [t["decomposition"]["dynamic"]["metrics"] for t in traces]
    ps_metrics = [t["planning_algorithms"]["plan_and_solve"]["metrics"] for t in traces]
    tot_metrics = [t["planning_algorithms"]["tree_of_thoughts"]["metrics"] for t in traces]
    lats_metrics = [t["lats"]["metrics"] for t in traces if "metrics" in t["lats"]]
    sr_metrics = [t["self_correction"]["self_refine"]["metrics"] for t in traces if "self_refine" in t["self_correction"]]
    refl_metrics = [t["self_correction"]["reflexion"]["metrics"] for t in traces
                     if "reflexion" in t["self_correction"] and t["self_correction"]["reflexion"].get("metrics")]

    dyn_divergence_rate = sum(1 for t in traces if t["decomposition"]["dynamic"]["diverged_from_naive_plan"]) / len(traces)
    lats_success_rate = sum(1 for t in traces if t["lats"].get("success")) / max(len([t for t in traces if "success" in t["lats"]]), 1)
    refl_success_rate = sum(1 for t in traces if t["self_correction"].get("reflexion", {}).get("success"))
    refl_total = len([t for t in traces if "reflexion" in t["self_correction"]])
    # NOTE: refl_total counts every case where Reflexion actually ran, but
    # only cases with a real pre-seeded duplicate (BH808_reflexion_duplicate_comp)
    # exercise more than one trial. Reporting "success/total" alone hides
    # that most of these succeed on trial 1 with nothing to reflect on --
    # the multi-trial count below makes that visible instead of implying
    # every case demonstrated cross-trial memory.
    refl_multi_trial = sum(
        1 for t in traces
        if t["self_correction"].get("reflexion", {}).get("trials", 0) > 1
    )

    rows = [
        ("Decomposition-first", df_metrics, "N/A (structural)"),
        ("Dynamic decomposition", dyn_metrics, f"{dyn_divergence_rate:.0%} of cases diverged from naive plan"),
        ("Plan-and-Solve", ps_metrics, "N/A (no external success metric at this granularity)"),
        ("Tree of Thoughts", tot_metrics, "N/A (candidate quality, see traces)"),
        ("LATS", lats_metrics, f"{lats_success_rate:.0%} grounded success"),
        ("Self-Refine", sr_metrics, "N/A (rubric-based, no grounded success metric)"),
        ("Reflexion", refl_metrics,
         f"{refl_success_rate}/{refl_total} grounded success "
         f"({refl_multi_trial}/{refl_total} cases needed more than 1 trial)"),
    ]

    lines = [
        f"LLM mode this run: **{MODE.upper()}** {LABEL} -- token/latency/cost numbers below are "
        + ("deterministic mock estimates; re-run with GEMINI_API_KEY set for real numbers."
           if MODE == "mock" else "real, recorded from the live Gemini API."),
        "",
        "| Method | Task Success / Accuracy | Avg LLM Calls | Avg Tokens | Avg Latency | Estimated Cost |",
        "|---|---|---|---|---|---|",
    ]
    for name, metrics, success in rows:
        avg = _avg_metrics(metrics)
        lines.append(
            f"| {name} | {success} | {avg.get('llm_calls', 'N/A')} | {avg.get('total_tokens', 'N/A')} | "
            f"{avg.get('avg_llm_latency_s', 'N/A')}s | ${avg.get('estimated_cost_usd', 'N/A')} |"
        )
    return "\n".join(lines)


async def main() -> None:
    from planning_eval.reset_state import reset_eval_state
    reset_eval_state()  # required -- this suite performs REAL writes; see
    # planning_eval/reset_state.py for why re-running without this silently
    # changes what each fixed case is actually testing.
    traces = [await run_case(case) for case in TEST_SUITE]
    table = build_comparison_table(traces)
    print("\n" + table)
    (ARTIFACTS_DIR / "comparison_table.md").write_text(table)
    print(f"\nComparison table written -> {ARTIFACTS_DIR / 'comparison_table.md'}")


if __name__ == "__main__":
    asyncio.run(main())
