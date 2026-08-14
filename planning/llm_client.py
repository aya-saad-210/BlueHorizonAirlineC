# planning/llm_client.py
#
# The Planning Agent's own choke point for every model call, deliberately
# separate from Rag/llm_client.py (that file belongs to the Memory/RAG
# agent -- see planning/README section "Memory/RAG protection rule"; this
# one is not imported by, and does not import, anything under Rag/ or
# memory/).
#
# Same MODE pattern as Rag/llm_client.py on purpose (live if
# MISTRAL_API_KEY is set, mock otherwise), extended with two things the
# planning algorithms actually need that plain Q&A didn't:
#   1. generate_json(...)   -- forces a JSON object matching a Pydantic
#      schema back from the model, using Mistral's native tool-calling (a
#      single "emit" tool whose parameters = the Pydantic JSON schema,
#      tool_choice="any" so the model must call it). This is the
#      structured-output mechanism that replaces the toolkit's
#      `llm.with_structured_output(...)` (LangChain-only).
#   2. usage metering -- every call increments CALL_COUNT and TOKEN_COUNT
#      (from the real Mistral response.usage in live mode; from a
#      deterministic word-count estimate, clearly labeled, in mock mode) so
#      planning_eval/runner.py can report REAL numbers instead of inventing
#      them, per the spec's "never fabricate metrics" rule.

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Type, TypeVar

from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()

MODE = "live" if os.getenv("MISTRAL_API_KEY") else "mock"
MODEL = os.getenv("PLANNING_MISTRAL_MODEL", os.getenv("MISTRAL_MODEL", "mistral-large-latest"))

T = TypeVar("T", bound=BaseModel)


@dataclass
class UsageMeter:
    """Real, running totals for the current process -- reset per eval run
    by planning_eval/runner.py so per-method metrics aren't polluted by
    earlier runs in the same session."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latencies_s: list[float] = field(default_factory=list)

    def reset(self) -> None:
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.latencies_s = []

    def record(self, input_tokens: int, output_tokens: int, elapsed_s: float) -> None:
        self.calls += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.latencies_s.append(elapsed_s)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def avg_latency_s(self) -> float:
        return sum(self.latencies_s) / len(self.latencies_s) if self.latencies_s else 0.0


METER = UsageMeter()

# Mistral pricing changes over time and is out of scope to hardcode
# precisely; this is a clearly-labeled ESTIMATE using mistral-large-latest
# list pricing at time of writing ($0.50 / MTok in, $1.50 / MTok out), used
# only to produce an order-of-magnitude "Estimated Cost" column, never
# presented as an exact bill. Check https://mistral.ai/pricing before
# trusting this for a real budget decision.
_EST_PRICE_PER_MTOK_IN = 0.50
_EST_PRICE_PER_MTOK_OUT = 1.50


def estimated_cost_usd(input_tokens: int, output_tokens: int) -> float:
    return (input_tokens / 1_000_000) * _EST_PRICE_PER_MTOK_IN + (
        output_tokens / 1_000_000
    ) * _EST_PRICE_PER_MTOK_OUT


def _word_count_tokens(*texts: str) -> int:
    """Mock-mode-only, deterministic token stand-in (roughly 1.3 tokens per
    word). Clearly an estimate, not a real tokenizer -- used only so mock
    runs still produce a number instead of a blank cell, and every mock
    number is labeled MOCK in the eval output."""
    words = sum(len(t.split()) for t in texts)
    return max(1, round(words * 1.3))


def _live_text(system: str, user: str, max_tokens: int = 800) -> str:
    from mistralai import Mistral

    client = Mistral(api_key=os.getenv("MISTRAL_API_KEY"))
    start = time.monotonic()
    resp = client.chat.complete(
        model=MODEL,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    elapsed = time.monotonic() - start
    METER.record(resp.usage.prompt_tokens, resp.usage.completion_tokens, elapsed)
    return resp.choices[0].message.content or ""


def _live_json(system: str, user: str, schema: Type[T], max_tokens: int = 800) -> T:
    from mistralai import Mistral

    client = Mistral(api_key=os.getenv("MISTRAL_API_KEY"))
    tool = {
        "type": "function",
        "function": {
            "name": "emit",
            "description": "Emit the structured result. Call this exactly once.",
            "parameters": schema.model_json_schema(),
        },
    }
    start = time.monotonic()
    resp = client.chat.complete(
        model=MODEL,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        tools=[tool],
        tool_choice="any",  # force a tool call rather than a free-text reply
        parallel_tool_calls=False,  # exactly one "emit" call, not several
    )
    elapsed = time.monotonic() - start
    METER.record(resp.usage.prompt_tokens, resp.usage.completion_tokens, elapsed)
    message = resp.choices[0].message
    tool_calls = message.tool_calls or []
    for call in tool_calls:
        if call.function.name == "emit":
            return schema.model_validate(json.loads(call.function.arguments))
    raise RuntimeError("Model did not return the expected 'emit' tool call")


def generate_text(system: str, user: str, max_tokens: int = 800) -> str:
    if MODE == "mock":
        start = time.monotonic()
        text = _mock_text(system, user)
        elapsed = time.monotonic() - start
        METER.record(_word_count_tokens(system, user), _word_count_tokens(text), elapsed)
        return text
    return _live_text(system, user, max_tokens)


def generate_json(system: str, user: str, schema: Type[T], max_tokens: int = 800) -> T:
    if MODE == "mock":
        start = time.monotonic()
        payload = _mock_json(schema, system, user)
        elapsed = time.monotonic() - start
        result = schema.model_validate(payload)
        METER.record(
            _word_count_tokens(system, user),
            _word_count_tokens(json.dumps(payload)),
            elapsed,
        )
        return result
    return _live_json(system, user, schema, max_tokens)


# ----------------------------------------------------------------------
# Mock mode: deterministic stand-ins, same spirit as Rag/llm_client.py's
# mock functions -- clearly labeled, good enough to exercise every code
# path (routing, DAG execution, self-correction loop, grounded-vs-
# ungrounded comparison) end to end without a paid credential, NOT a
# substitute for the live run required for the final submitted numbers.
# ----------------------------------------------------------------------

def _mock_text(system: str, user: str) -> str:
    return f"[MOCK MODE] {user.strip().splitlines()[-1][:200]}"


def _mock_json(schema: Type[BaseModel], system: str, user: str) -> dict:
    name = schema.__name__
    if name == "GeneratedPlan":
        return _mock_plan(user)
    if name == "DynamicDecision":
        return _mock_dynamic_decision(user)
    if name == "ThoughtCandidates":
        return _mock_thought_candidates(user)
    if name == "ThoughtEvaluation":
        return _mock_thought_evaluation(user)
    if name == "SelfCritique":
        return {"passes": "duplicate" not in user.lower() and "breach" not in user.lower(),
                "issues": [] if "duplicate" not in user.lower() else ["mock: looks fine to me"],
                "revision_hint": "tighten the amount/authorization language"}
    if name == "SelfRefineCritique":
        missing_next_step = "next" not in user.lower() and "flight" not in user.lower()
        return {"passes_rubric": not missing_next_step,
                "issues": [] if not missing_next_step else ["mock: draft doesn't state a concrete next step"]}
    if name == "Reflection":
        return {"reflection": "The previous attempt hit a rejected business rule; "
                               "next attempt must check for an existing pending record "
                               "or an authorization requirement before acting."}
    if name == "SolveSteps":
        return {"steps": [
            f"Gather the concrete facts needed for: {user.strip().splitlines()[0][:80]}",
            "Apply the relevant Blue Horizon IROPS business rule",
            "State the concrete resulting action or decision",
        ]}
    if name == "LATSActionBatch":
        return _mock_lats_actions(user)
    if name == "ValueEstimate":
        return {"score": 0.6}
    raise ValueError(f"No mock generator registered for schema {name}")


_MOCK_LATS_CURSOR = {"i": 0}


def _mock_lats_actions(user: str) -> dict:
    # Cycle so the SAME crew_id/amount isn't proposed on every branch --
    # LATS needs at least one branch to fail and one to eventually succeed
    # to exercise select/expand/reflect/backpropagate meaningfully.
    i = _MOCK_LATS_CURSOR["i"]
    _MOCK_LATS_CURSOR["i"] += 1
    if "crew" in user.lower() or "duty" in user.lower():
        # Prefer REAL candidate crew_ids injected into the task text
        # (see planning_eval/runner.py / planning/agent.py) so a grounded
        # check can find the crew member at all -- an invented id like
        # crew_id=101 that doesn't exist just gets a "no crew member
        # found" rejection, which masks the actual duty-hour-breach
        # scenario these cases exist to exercise.
        real_ids = [int(m) for m in re.findall(r"crew_id=(\d+)", user)]
        if real_ids:
            pool = [
                {"action": f"assign crew {real_ids[i % len(real_ids)]}",
                 "state": f"Assign crew_id={real_ids[i % len(real_ids)]} as reserve crew to cover the disruption."},
                {"action": f"assign crew {real_ids[(i + 1) % len(real_ids)]}",
                 "state": f"Assign crew_id={real_ids[(i + 1) % len(real_ids)]} as reserve crew to cover the disruption."},
            ]
        else:
            pool = [
                {"action": "assign primary reserve", "state": "Assign crew_id=101 as reserve crew to cover the disruption."},
                {"action": "assign alternate reserve", "state": "Assign crew_id=102 as reserve crew to cover the disruption."},
            ]
    else:
        pool = [
            {"action": "issue standard compensation", "state": "Issue 120.00 USD compensation for the disruption."},
            {"action": "issue reduced compensation", "state": "Issue 75.00 USD compensation for the disruption."},
        ]
    return {"actions": pool}


def _mock_plan(user: str) -> dict:
    # A short, deterministic 4-node DAG shaped like the real IROPS flow:
    # check status -> {rebook, crew-check} in parallel -> notify (synthesis).
    return {
        "goal": user.split("Decompose this goal into", 1)[0].strip() or "goal",
        "tasks": [
            {"id": "t1", "instruction": "Check current flight status and affected bookings", "depends_on": []},
            {"id": "t2", "instruction": "Rebook affected passengers onto a suitable replacement flight", "depends_on": ["t1"]},
            {"id": "t3", "instruction": "Check crew duty-hour limits and assign reserve crew if needed", "depends_on": ["t1"]},
            {"id": "t4", "instruction": "Determine compensation and draft the passenger disruption notice", "depends_on": ["t2", "t3"]},
        ],
    }


def _mock_dynamic_decision(user: str) -> dict:
    # Isolate ONLY the actual observation block between the two known
    # markers -- splitting on just the start marker previously also
    # captured the trailing instruction sentence ("Decide the single best
    # next task...") on every call, which corrupted the step count and
    # made the mock planner report done=True after a single step. Always
    # sanity-check a mock generator's parsing with a real run before
    # trusting it -- this bug was caught by actually executing
    # dynamic_decomposition(), not by reading the code.
    after_marker = user.split("Completed work and observations:\n", 1)[-1]
    observation = after_marker.split("\n\nDecide the single best next task", 1)[0]
    steps_done = 0 if observation.strip() == "None" else observation.count("\n") + 1

    # REACTIVE branch: if the most recent grounded observation shows a real
    # duty-hour breach, the very next step must escalate for a supervisor
    # override instead of blindly continuing a fixed script -- this is the
    # divergence spec section 5 requires to be visible in the demo, driven
    # by the actual grounded text, not a hardcoded case id.
    if "requires supervisor approval" in observation.lower() and "escalate" not in observation.lower():
        return {"done": False, "next_task": "Escalate to supervisor: crew duty-hour cap reached, request override for reserve crew assignment"}

    if steps_done >= 3:
        return {"done": True, "next_task": ""}
    next_tasks = [
        "Check current flight status",
        "Rebook the next affected passenger",
        "Check crew duty-hour limits for assigned crew",
    ]
    return {"done": False, "next_task": next_tasks[min(steps_done, len(next_tasks) - 1)]}


_MOCK_CANDIDATE_POOL = [
    "Rebook onto the earliest direct replacement flight, preserving fare class",
    "Rebook onto a later replacement flight with a layover, downgraded fare",
    "Wait for the next scheduled flight on the same route tomorrow",
]
_MOCK_CANDIDATE_CURSOR = {"i": 0}


def _mock_thought_candidates(user: str) -> dict:
    # Cycle through the pool so repeated calls within one search (parent ->
    # children -> grandchildren) don't all return byte-identical text,
    # which would make the beam-search keep/prune step in
    # tree_of_thoughts.py meaningless to inspect in a trace.
    i = _MOCK_CANDIDATE_CURSOR["i"]
    picked = [_MOCK_CANDIDATE_POOL[i % 3], _MOCK_CANDIDATE_POOL[(i + 1) % 3]]
    _MOCK_CANDIDATE_CURSOR["i"] += 1
    return {"candidates": picked}


def _mock_thought_evaluation(user: str) -> dict:
    state = user.lower()
    if "direct" in state and "layover" not in state:
        return {"score": 0.85, "rationale": "mock: direct + fare class preserved"}
    if "layover" in state:
        return {"score": 0.45, "rationale": "mock: layover + downgraded fare"}
    return {"score": 0.2, "rationale": "mock: long delay, passenger stranded overnight"}
