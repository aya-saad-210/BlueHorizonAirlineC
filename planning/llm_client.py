# planning/llm_client.py
#
# The Planning Agent's own choke point for every model call, deliberately
# separate from Rag/llm_client.py (that file belongs to the Memory/RAG
# agent -- see planning/README section "Memory/RAG protection rule"; this
# one is not imported by, and does not import, anything under Rag/ or
# memory/).
#
# Same MODE pattern as Rag/llm_client.py on purpose (live if
# GEMINI_API_KEY is set, mock otherwise), extended with two things the
# planning algorithms actually need that plain Q&A didn't:
#   1. generate_json(...)   -- forces a JSON object matching a Pydantic
#      schema back from the model, using Gemini's native structured-output
#      mode (response_mime_type="application/json" +
#      response_schema=<cleaned schema dict>, the google-genai SDK's built-in
#      replacement for manual tool-calling JSON extraction). This is the
#      structured-output mechanism that replaces the toolkit's
#      `llm.with_structured_output(...)` (LangChain-only).
#   2. usage metering -- every call increments CALL_COUNT and TOKEN_COUNT
#      (from the real Gemini response.usage_metadata in live mode; from a
#      deterministic word-count estimate, clearly labeled, in mock mode) so
#      planning_eval/runner.py can report REAL numbers instead of inventing
#      them, per the spec's "never fabricate metrics" rule.

from __future__ import annotations

import json
import os
import random
import re
import time
from dataclasses import dataclass, field
from typing import Type, TypeVar

from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

load_dotenv()

MODE = "live" if os.getenv("GEMINI_API_KEY") else "mock"
# NOTE: os.getenv(key, default) only falls back to `default` when the key
# is completely UNSET -- if `.env` defines PLANNING_GEMINI_MODEL= (present
# but empty, exactly what .env.example ships), os.getenv returns "" and
# the nested-default chain silently produces an empty MODEL, which the
# genai SDK then rejects with "model is required." `or`-chaining treats
# an empty string the same as unset, which is what we actually want here.
MODEL = os.getenv("PLANNING_GEMINI_MODEL") or os.getenv("GEMINI_MODEL") or "gemini-2.5-flash"

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

# Gemini pricing changes over time and is out of scope to hardcode
# precisely; this is a clearly-labeled ESTIMATE using gemini-2.5-flash list
# pricing at time of writing ($0.30 / MTok in, $2.50 / MTok out), used only
# to produce an order-of-magnitude "Estimated Cost" column, never
# presented as an exact bill. Check
# https://ai.google.dev/gemini-api/docs/pricing before trusting this for a
# real budget decision.
_EST_PRICE_PER_MTOK_IN = 0.30
_EST_PRICE_PER_MTOK_OUT = 2.50


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


# Retry policy for transient errors: mainly HTTP 429 rate limits (common on
# free-tier Gemini API keys once planning_eval/runner.py fires several
# sequential calls per test case across the whole suite), but also raw
# connection drops -- some providers reset the TCP connection under load
# instead of returning a clean 429 response, and that failure mode is just
# as transient and just as worth retrying. This is NOT hidden from the
# metrics: METER.record still only fires on the call that actually
# succeeds, so latency/token numbers reflect the real, successful call, not
# the retries around it.
#
# Two layers, not just one: reactive retry-after-failure (below) AND a
# proactive minimum spacing between call *attempts* (_throttle, used by
# both _live_text and _live_json before every attempt including the
# first). A workspace with a low sustained rate limit will keep 429'ing a
# burst of back-to-back retries just as fast as it 429's a burst of fresh
# calls -- backoff alone doesn't fix that if the calls immediately before
# and after it are still packed close together. Spacing every attempt out
# reduces how often the limit gets hit in the first place, not just how
# hard we retry once it has been.
_MAX_RETRIES = 8
_BASE_BACKOFF_S = 2.0
_MAX_BACKOFF_S = 60.0
# Default spacing assumes Gemini's free-tier Flash limit (commonly
# reported around 10-15 RPM as of mid-2026) -- 4.5s keeps you comfortably
# under ~13 RPM. Free-tier limits vary by model/account/region, so check
# your actual cap at https://aistudio.google.com (or your Cloud console's
# quota page) and override with GEMINI_MIN_CALL_INTERVAL_S if needed.
_MIN_CALL_INTERVAL_S = float(os.getenv("GEMINI_MIN_CALL_INTERVAL_S") or "4.5")

_last_call_at = 0.0


def _throttle() -> None:
    """Block until at least _MIN_CALL_INTERVAL_S has passed since the last
    call attempt (success or failure). Runs before every attempt, not just
    after a 429, so a sustained low rate limit gets fewer opportunities to
    fire in the first place."""
    global _last_call_at
    wait_s = _MIN_CALL_INTERVAL_S - (time.monotonic() - _last_call_at)
    if wait_s > 0:
        time.sleep(wait_s)
    _last_call_at = time.monotonic()


def _is_rate_limited(exc: Exception) -> bool:
    status = (
        getattr(exc, "status_code", None)
        or getattr(exc, "code", None)  # google.genai.errors.ClientError exposes .code
        or getattr(exc, "raw_status_code", None)
    )
    if status == 429:
        return True
    text = str(exc).lower()
    return (
        "429" in text
        or "rate limit" in text
        or "rate_limited" in text
        or "resource_exhausted" in text  # Gemini's 429 error status string
    )


def _is_connection_error(exc: Exception) -> bool:
    """Raw network failure (dropped/reset connection, timeout, DNS hiccup)
    rather than a clean HTTP response -- these come from httpx/httpcore
    underneath the google-genai SDK, or occasionally as bare builtin
    ConnectionError/TimeoutError, and are just as transient as a 429."""
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    try:
        import httpx
        if isinstance(exc, httpx.RequestError):  # covers ConnectError,
            # ReadError, WriteError, ConnectTimeout, PoolTimeout, etc.
            return True
    except ImportError:
        pass
    return False


def _is_transient(exc: Exception) -> bool:
    return _is_rate_limited(exc) or _is_connection_error(exc)


def _call_with_retry(fn):
    """Run fn() (a zero-arg call to client.models.generate_content(...)), retrying on
    HTTP 429 or a dropped/reset connection, with exponential backoff +
    jitter (capped at _MAX_BACKOFF_S) and a minimum spacing before every
    attempt (see _throttle). Re-raises immediately for any error that
    isn't transient (e.g. a real auth or schema error -- those should fail
    fast, not get silently retried away)."""
    for attempt in range(_MAX_RETRIES):
        _throttle()
        try:
            return fn()
        except Exception as exc:
            if not _is_transient(exc) or attempt == _MAX_RETRIES - 1:
                raise
            reason = "429 rate limited" if _is_rate_limited(exc) else "connection dropped"
            wait_s = min(_MAX_BACKOFF_S, _BASE_BACKOFF_S * (2 ** attempt) + random.uniform(0, 1))
            print(f"[llm_client] {reason}, retrying in {wait_s:.1f}s "
                  f"(attempt {attempt + 1}/{_MAX_RETRIES})")
            time.sleep(wait_s)


def _live_text(system: str, user: str, max_tokens: int = 800) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    start = time.monotonic()
    resp = _call_with_retry(lambda: client.models.generate_content(
        model=MODEL,
        contents=user,
        config=types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,
        ),
    ))
    elapsed = time.monotonic() - start
    usage = resp.usage_metadata
    METER.record(usage.prompt_token_count, usage.candidates_token_count, elapsed)
    return resp.text or ""


# Structured-output truncation retry: separate from _call_with_retry above
# (that one handles network/429 failures on the request itself; this one
# handles a request that *succeeded* but got cut off mid-JSON because
# max_tokens was too small for what the model tried to emit -- e.g.
# json.JSONDecodeError: Unterminated string). Doubling max_tokens and
# asking again is the correct fix, not just raising the default once,
# since the same schema can legitimately need very different output
# lengths call to call.
_JSON_RETRY_ATTEMPTS = 3
_JSON_MAX_TOKENS_CAP = 6000

# Cache of cleaned schemas keyed by the Pydantic class -- _clean_schema_for_gemini
# does a small amount of recursive dict work per call; every generate_json call
# for the same schema (e.g. every decompose_goal call reusing GeneratedPlan)
# would otherwise redo it for no reason.
_SCHEMA_CACHE: dict[Type[BaseModel], dict] = {}


def _clean_schema_for_gemini(schema: Type[BaseModel]) -> dict:
    """Build a Gemini-compatible JSON schema from a Pydantic model.

    Gemini's response_schema uses a restricted OpenAPI subset that rejects
    two things Pydantic v2's model_json_schema() emits by default:
      1. "additionalProperties" on every object -- this is exactly what
         produced: 'Unknown name "additional_properties" at
         generation_config.response_schema... Cannot find field.'
      2. "$ref"/"$defs" for nested models -- not seen yet in this codebase's
         error output, but it fails the same way the moment a schema has a
         nested BaseModel (e.g. GeneratedPlan.tasks: List[Task]), so it's
         resolved inline here proactively rather than waiting to hit that
         error separately later.

    Cached per schema class since the shape never changes between calls.
    """
    if schema in _SCHEMA_CACHE:
        return _SCHEMA_CACHE[schema]

    raw = schema.model_json_schema()
    defs = raw.pop("$defs", {}) or raw.pop("definitions", {})

    def resolve(node):
        if isinstance(node, dict):
            if "$ref" in node:
                ref_name = node["$ref"].rsplit("/", 1)[-1]
                return resolve(defs[ref_name])
            cleaned = {
                k: resolve(v)
                for k, v in node.items()
                if k not in ("additionalProperties", "default")
            }
            if "allOf" in cleaned and len(cleaned["allOf"]) == 1:
                merged = resolve(node["allOf"][0])
                merged.update({k: v for k, v in cleaned.items() if k != "allOf"})
                return merged
            return cleaned
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    cleaned_schema = resolve(raw)
    _SCHEMA_CACHE[schema] = cleaned_schema
    return cleaned_schema


def _live_json(system: str, user: str, schema: Type[T], max_tokens: int = 1500) -> T:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

    current_max_tokens = max_tokens
    last_error: Exception = RuntimeError("Model did not return valid structured output")
    cleaned_schema = _clean_schema_for_gemini(schema)

    for json_attempt in range(_JSON_RETRY_ATTEMPTS):
        start = time.monotonic()
        resp = _call_with_retry(lambda mt=current_max_tokens: client.models.generate_content(
            model=MODEL,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                max_output_tokens=mt,
                response_mime_type="application/json",  # forces JSON-only output
                response_schema=cleaned_schema,  # cleaned dict, not the raw Pydantic
                # class -- Gemini's schema subset rejects "additionalProperties"
                # and "$ref" (see _clean_schema_for_gemini above)
            ),
        ))
        elapsed = time.monotonic() - start
        usage = resp.usage_metadata
        METER.record(usage.prompt_token_count, usage.candidates_token_count, elapsed)

        try:
            raw_text = resp.text
            if not raw_text:
                raise RuntimeError("Model returned no text content (empty or blocked response)")
            return schema.model_validate_json(raw_text)
        except (json.JSONDecodeError, ValidationError, RuntimeError, AttributeError, ValueError) as exc:
            last_error = exc

        if json_attempt < _JSON_RETRY_ATTEMPTS - 1:
            current_max_tokens = min(current_max_tokens * 2, _JSON_MAX_TOKENS_CAP)
            print(f"[llm_client] structured output truncated/malformed "
                  f"({type(last_error).__name__}: {last_error}), retrying with "
                  f"max_tokens={current_max_tokens} "
                  f"(attempt {json_attempt + 2}/{_JSON_RETRY_ATTEMPTS})")

    raise last_error


def generate_text(system: str, user: str, max_tokens: int = 800) -> str:
    if MODE == "mock":
        start = time.monotonic()
        text = _mock_text(system, user)
        elapsed = time.monotonic() - start
        METER.record(_word_count_tokens(system, user), _word_count_tokens(text), elapsed)
        return text
    return _live_text(system, user, max_tokens)


def generate_json(system: str, user: str, schema: Type[T], max_tokens: int = 1500) -> T:
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
    if name == "CrewChoice":
        return _mock_crew_choice(user)
    raise ValueError(f"No mock generator registered for schema {name}")


def _mock_crew_choice(user: str) -> dict:
    # state_graph/crew_reassignment_graph.py's constrained-ReAct node lists
    # real candidates as "- crew_id=<n>, ...duty_hours_today=<x>" lines;
    # deterministically pick the one with the LOWEST duty_hours_today
    # (mirrors the instruction in that node's own prompt) so mock mode
    # exercises the same "prefer least duty risk" logic a live model
    # would, instead of just grabbing the first line.
    rows = re.findall(r"crew_id=(\d+).*?duty_hours_today=([\d.]+)", user)
    if not rows:
        return {"thought": "[MOCK MODE] no candidate rows parsed from prompt",
                "chosen_crew_id": 0, "reasoning": "[MOCK MODE] fallback, no candidates found"}
    best_id, best_hours = min(rows, key=lambda r: float(r[1]))
    return {
        "thought": f"[MOCK MODE] comparing {len(rows)} real candidates by duty_hours_today",
        "chosen_crew_id": int(best_id),
        "reasoning": f"[MOCK MODE] crew_id={best_id} has the lowest duty_hours_today ({best_hours}) of the real candidates",
    }


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
