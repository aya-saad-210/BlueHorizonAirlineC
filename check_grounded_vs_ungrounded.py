"""
check_grounded_vs_ungrounded.py

Runs ONLY run_self_correction_comparison() for the BH808 case (the
duplicate-compensation Reflexion case) instead of the full eval suite --
this is the part of the trace that shows whether the grounded environment
catches a failure the ungrounded self-critique missed.

This still calls the live Gemini API (self_refine + reflexion each make
real calls), but it's a small fraction of a full suite run, so it's much
cheaper/faster to re-check after any change to environment.py or the
self-correction comparison logic.

Usage (from the repo root, BlueHorizonAirlineC_Memory_Rag, with venv active):
    python check_grounded_vs_ungrounded.py
"""

import asyncio
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from planning_eval.reset_state import reset_eval_state  # noqa: E402
from planning_eval.runner import run_self_correction_comparison  # noqa: E402
from planning_eval.test_suite import case_by_id  # noqa: E402


async def main():
    # Reset first -- BH808 depends on the seeded pending compensation row
    # existing; if a previous full/partial run already consumed or
    # mutated it, this case would no longer be testing what it's designed
    # to test. Cheap (no API calls), so always safe to run.
    reset_eval_state()

    case = case_by_id("BH808_reflexion_duplicate_comp")
    result = await run_self_correction_comparison(case)

    print("\n=== grounded_vs_ungrounded ===")
    print(json.dumps(result["grounded_vs_ungrounded"], indent=2))

    caught = result["grounded_vs_ungrounded"]["ungrounded_wrongly_accepted_grounded_caught_it"]
    print(f"\nungrounded_wrongly_accepted_grounded_caught_it = {caught}")
    if caught:
        print("✅ Grounded environment caught a failure the ungrounded critique missed.")
    else:
        print("⚠️  Both agreed this run -- ungrounded critique did NOT wrongly accept "
              "what grounded correctly rejected. May need investigation.")

    print("\n=== reflexion summary ===")
    print(json.dumps({
        "success": result["reflexion"]["success"],
        "trials": result["reflexion"]["trials"],
        "reflection_carried_forward": result["reflexion"]["reflection_carried_forward"],
        "episodic_buffer": result["reflexion"]["episodic_buffer"],
    }, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
