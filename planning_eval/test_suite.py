# planning_eval/test_suite.py
#
# FIXED test suite (spec section 10). Every case is a REAL request against
# the actual seeded data in data base/seed_planning_eval.sql -- run
# `mysql -u root blue_horizon_db < "data base/seed_planning_eval.sql"`
# once before running this suite. Do not edit case bodies between runs;
# add a new case instead if coverage is missing (append-only).

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    flight_number: str
    passenger_email: str
    exercises: str  # which required-difference this case is FOR (spec section 10, items 1-4)
    notes: str


TEST_SUITE: list[EvalCase] = [
    EvalCase(
        case_id="BH404_stable_single",
        flight_number="BH404",
        passenger_email="salma.nabil@example.com",
        exercises="decomposition-first should outperform dynamic decomposition",
        notes=(
            "Single passenger, one obvious replacement flight, no mid-run "
            "surprises. The up-front DAG holds all the way through, so "
            "decomposition-first pays no extra LLM-call overhead versus "
            "dynamic decomposition re-deciding the next step after every "
            "observation."
        ),
    ),
    EvalCase(
        case_id="BH606_duty_breach",
        flight_number="BH606",
        passenger_email="",  # not a compensation case; crew-focused
        exercises="dynamic decomposition should outperform decomposition-first",
        notes=(
            "The crew duty-hour breach (both original + first reserve crew "
            "already AT the daily cap) is only discoverable by actually "
            "querying duty_time_logs mid-run. A decomposition-first plan's "
            "'assign crew' node cannot anticipate this; dynamic "
            "decomposition observes the breach after the status/duty check "
            "step and reacts (escalates to supervisor approval) instead of "
            "blindly following a stale up-front node."
        ),
    ),
    EvalCase(
        case_id="BH707_lookahead",
        flight_number="BH707",
        passenger_email="rania.adly@example.com",
        exercises="benefits from lookahead/search",
        notes=(
            "Three real candidate replacement flights (BH710 direct/fare-"
            "matched, BH711 later same-day, BH712 delayed/different "
            "destination-adjacent) genuinely differ in quality. Tree of "
            "Thoughts' generate->evaluate->keep/prune loop should surface "
            "BH710 as the best candidate; a single-shot Plan-and-Solve call "
            "has no mechanism to compare options against each other."
        ),
    ),
    EvalCase(
        case_id="BH808_reflexion_duplicate_comp",
        flight_number="BH808",
        passenger_email="karim.zaki@example.com",
        exercises="one retry is insufficient and Reflexion's cross-trial memory is useful",
        notes=(
            "A pending compensation row already exists for this passenger/"
            "flight (seeded on purpose). Trial 1's naive compensation "
            "amount is REJECTED by the real issue_compensation() duplicate "
            "check. A single Self-Refine pass (rubric-only, no grounded "
            "check) would not catch this at all -- the rubric never asked "
            "about duplicates. Reflexion's grounded trial 1 failure "
            "produces a reflection that must be visible in trial 2's "
            "prompt (see reflection_carried_forward in "
            "planning/algorithms/reflexion.py)."
        ),
    ),
]


def case_by_id(case_id: str) -> EvalCase:
    return next(c for c in TEST_SUITE if c.case_id == case_id)
