# recheck_divergence.py
#
# ZERO API COST. Loads the already-saved trace JSON files under
# planning_eval/artifacts/ and re-evaluates the NEW _looks_like_divergence
# logic against each case's saved dynamic-decomposition step history.
# Does not call Gemini at all -- just replays the new heuristic against
# real, already-recorded data, so you can confirm the fix behaves as
# expected BEFORE spending any more live quota re-running the full suite.
#
# Usage (from the repo root, BlueHorizonAirlineC_Memory_Rag):
#   python recheck_divergence.py

import json
from pathlib import Path

ARTIFACTS_DIR = Path("planning_eval") / "artifacts"

CASE_IDS = [
    "BH404_stable_single",
    "BH606_duty_breach",
    "BH707_lookahead",
    "BH808_reflexion_duplicate_comp",
]


def looks_like_divergence(history: list[dict]) -> bool:
    """Exact copy of the NEW logic in planning/dynamic_decomposition.py --
    kept in sync manually since this script only reads saved JSON and
    does not import the real module (avoids pulling in DB/MCP
    dependencies just to replay text)."""
    if not history:
        return False

    last = history[-1]
    result_lower = last["result"].lower()
    task_lower = last["task"].lower()

    is_duty_or_crew_check = "duty" in task_lower or "crew" in task_lower
    is_compensation_check = "compensation" in task_lower or "duplicate" in task_lower

    if is_duty_or_crew_check and "at/over the legal duty-hour cap" in result_lower:
        return True
    if is_compensation_check and "already on file" in result_lower:
        return True
    if "rejected:" in result_lower:
        return True
    return False


def main():
    print(f"{'Case':<32} {'OLD flag (saved)':<18} {'NEW flag (recomputed)':<22} Steps")
    print("-" * 95)
    for case_id in CASE_IDS:
        path = ARTIFACTS_DIR / f"{case_id}.json"
        if not path.exists():
            print(f"{case_id:<32} -- file not found: {path}")
            continue
        trace = json.loads(path.read_text())
        dyn = trace["decomposition"]["dynamic"]
        history = dyn["result"]
        old_flag = dyn.get("diverged_from_naive_plan")

        # Recompute step-by-step, exactly like dynamic_decomposition() does
        # (checking divergence after each step using only history up to
        # and including that step).
        recomputed_steps = []
        running_history = []
        for step in history:
            running_history.append({"task": step["task"], "result": step["result"]})
            recomputed_steps.append(looks_like_divergence(running_history))
        new_flag = any(recomputed_steps)

        print(f"{case_id:<32} {str(old_flag):<18} {str(new_flag):<22} {recomputed_steps}")

        # Show exactly which step (if any) triggered it, and why -- so you
        # can eyeball whether the trigger is a genuine grounded signal.
        for i, (step, triggered) in enumerate(zip(history, recomputed_steps)):
            if triggered:
                print(f"    -> triggered at step {i+1}: task={step['task']!r}")
                print(f"       result snippet: {step['result'][:150]!r}")

    print("\nExpected per test_suite.py case design:")
    print("  BH404_stable_single             -> should be False (decomposition-first should win)")
    print("  BH606_duty_breach               -> should be True  (dynamic should win, genuine breach)")
    print("  BH707_lookahead                 -> not specifically about decomposition divergence")
    print("  BH808_reflexion_duplicate_comp  -> not specifically about decomposition divergence")


if __name__ == "__main__":
    main()
