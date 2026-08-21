# state_graph/tot_appeal_strategy.py
#
# Tree of Thoughts over candidate appeal arguments. Generates 3 candidate
# strategies, scores each against the retrieved policy text, and returns
# all three (the calling node picks the max) so the choice is auditable
# in state_json rather than a single opaque LLM call.
#
# Same offline-first pattern as the rest of this repo (see rag/llm_client.py,
# planning/llm_client.py, .env.example): if GEMINI_API_KEY is unset, this
# runs a deterministic mock scorer so the whole pipeline stays reproducible
# and free to grade. If you already have rag/llm_client.py or
# planning/llm_client.py, tell me and I'll swap this to import that instead
# of keeping a third copy of the same offline/online switch.

import os
from typing import Optional

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")


CANDIDATE_ACTIONS = ["file_appeal", "file_appeal", "request_supervisor_review"]


def _candidate_strategies(claim: dict, appeal_reason: str, policy_answer: str) -> list[dict]:
    return [
        {
            "name": "mechanical_vs_weather_distinction",
            "argument": (
                f"Argue the disruption reason on flight {claim['flight_number']} should be reclassified "
                f"as mechanical rather than the recorded reason, which changes eligibility per policy: "
                f"{policy_answer[:180]}"
            ),
            "action": "file_appeal",
        },
        {
            "name": "loyalty_tier_adjustment",
            "argument": (
                f"Argue for a loyalty-tier-based compensation adjustment for {claim['passenger_name']}, "
                f"citing the passenger-supplied reason: {appeal_reason[:180]}"
            ),
            "action": "file_appeal",
        },
        {
            "name": "escalate_ambiguous_clause",
            "argument": (
                "The retrieved policy text does not clearly resolve this case either way; "
                "escalate directly for supervisor review rather than re-arguing the same clause."
            ),
            "action": "request_supervisor_review",
        },
    ]


def _mock_score(strategy: dict, policy_answer: str) -> float:
    """
    Deterministic, offline scoring: rewards strategies whose argument text
    overlaps with words actually present in the retrieved policy answer
    (a crude but reproducible proxy for "grounded in what we retrieved"),
    and penalizes the escalate-only strategy slightly so it's only chosen
    when the other two don't overlap with the policy text at all --
    mirroring "policy text didn't clearly settle it" from decision().
    """
    policy_words = set(policy_answer.lower().split())
    arg_words = set(strategy["argument"].lower().split())
    overlap = len(policy_words & arg_words)
    base = min(1.0, overlap / 8.0)
    if strategy["name"] == "escalate_ambiguous_clause":
        base = max(0.0, base - 0.15)
    return round(base, 3)


def _live_score(strategy: dict, policy_answer: str) -> Optional[float]:  # pragma: no cover
    """
    Real scoring path via Gemini, used only when GEMINI_API_KEY is set.
    Left as a narrow hook (single float back) so swapping in the repo's
    existing llm_client.py later is a one-function change.
    """
    try:
        from google import genai
    except ImportError:
        return None

    client = genai.Client(api_key=GEMINI_API_KEY)
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    prompt = (
        "Score, from 0.0 to 1.0, how well this appeal argument is supported by the "
        "retrieved policy text below. Respond with ONLY the number.\n\n"
        f"Policy text: {policy_answer}\n\nArgument: {strategy['argument']}"
    )
    try:
        resp = client.models.generate_content(model=model, contents=prompt)
        return max(0.0, min(1.0, float(resp.text.strip())))
    except Exception:  # noqa: BLE001 -- fall back to mock scoring rather than crash the node
        return None


def score_appeal_strategies(claim: dict, appeal_reason: str, policy_answer: str) -> list[dict]:
    strategies = _candidate_strategies(claim, appeal_reason, policy_answer)
    for s in strategies:
        score = None
        if GEMINI_API_KEY:
            score = _live_score(s, policy_answer)
        if score is None:
            score = _mock_score(s, policy_answer)
        s["score"] = score
    return strategies
