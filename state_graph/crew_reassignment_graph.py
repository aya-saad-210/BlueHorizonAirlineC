# state_graph/crew_reassignment_graph.py
#
# State graph #1 -- Crew Reassignment Escalation.
#
# WHY THIS IS A STATE GRAPH AND NOT A RE-SKIN OF THE PLANNING LAB'S BH606:
# BH606 (planning/) escalates a duty-hour breach to a supervisor INSIDE one
# live MCP session via ctx.elicit() -- the pause only exists as long as
# that one connection is open. This graph tracks a genuinely different,
# longer-lived wait: the proposed crew member's own reply (accept/decline
# the reassignment), which can take hours, plus -- only if that reply
# would breach duty hours -- an admin's sign-off through the platform.
# Both waits must survive the process being killed and restarted, which
# ctx.elicit() cannot do.
#
# NODES (see data base/state_graph_schema.sql for crew_reassignment_requests):
#
#   intake_disruption
#         |
#         v
#   propose_crew  <---------------------------+
#         |                                   |
#         v                                   |
#   await_crew_reply  (waiting_external)      |
#         |                                   |
#   [external: submit_crew_reply(...)]        |
#         |                                   |
#         v                                   |
#   handle_crew_reply --- declined/timeout ----+   (cycle: try next candidate)
#         |
#      accepted
#         |
#         v
#   duty_hour_breach? --no--> finalize_assignment --> [finish]
#         |
#        yes
#         v
#   request_duty_override  (waiting_hitl, opens a hitl_tasks row)
#         |
#   [external: admin decides via platform]
#         |
#         v
#   handle_hitl_decision --- rejected ---> propose_crew (cycle, try next candidate)
#         |
#      approved
#         v
#   finalize_assignment --> [finish]
#
# TWO LLM-CALL ADDITIONS (pick reasoning here, wire in your own model calls):
#   - constrained ReAct inside propose_crew: the model may only choose from
#     the actual list of eligible reserve crew returned by a real DB query
#     (base_airport match, role match, not already assigned) -- it cannot
#     invent a candidate.
#   - RAG inside request_duty_override: pulls the actual duty-time policy
#     text (Rag/policy_docs/duty_time_policy.md) into the admin-facing
#     explanation of why this needs sign-off, instead of the model
#     paraphrasing the policy from memory.
# (Fill in the real model calls where marked TODO -- the control flow /
# state-graph shape below is what this file is responsible for.)

from __future__ import annotations

import importlib.util
import json
import sys
import os
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp_server"))
from dbase import get_connection

from graph_engine import StateGraph, NodeResult
from checkpointer import MySQLCheckpointer

MAX_DUTY_HOURS_PER_DAY = 14.00  # kept in sync with mcp_server/tools_write.py -- see
                                 # ISSUES.md Issue 6, the intent is one source of truth
GRAPH_NAME = "crew_reassignment"

# ---------------------------------------------------------------------
# LLM-call additions plumbing
#
# We reuse the two model-call choke points the team already built in the
# MCP Server / Memory-RAG / Planning labs instead of standing up a third
# one here (planning/llm_client.py's generate_json for the constrained
# ReAct choice, Rag/naive_rag.py for the RAG lookup). Both modules are
# named llm_client.py, so we can't just sys.path-insert both directories
# and `import llm_client` -- the second import would silently shadow the
# first. planning/llm_client.py has no sibling top-level imports of its
# own (google.genai is imported lazily inside its functions), so it's
# safe to load it directly from its file path under a private module
# name and never touch sys.path for it at all. Rag/naive_rag.py DOES do
# `from llm_client import ...` / `from vector_store import ...` at module
# scope, so Rag/ genuinely needs to be on sys.path for that import to
# resolve -- but by then "llm_client" as a bare name is still free
# because we loaded planning's copy under a different name, so Rag's own
# llm_client.py is what naive_rag.py actually gets. No collision either
# way.
# ---------------------------------------------------------------------

_PLANNING_LLM_CLIENT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "planning", "llm_client.py"
)
_spec = importlib.util.spec_from_file_location("crew_graph_planning_llm_client", _PLANNING_LLM_CLIENT_PATH)
_planning_llm_client = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _planning_llm_client  # required before exec_module: dataclasses'
                                                 # @dataclass decorator (used inside this file)
                                                 # looks itself up via sys.modules[cls.__module__]
_spec.loader.exec_module(_planning_llm_client)
generate_json = _planning_llm_client.generate_json

_RAG_DIR = os.path.join(os.path.dirname(__file__), "..", "Rag")
sys.path.insert(0, _RAG_DIR)
from naive_rag import naive_rag_answer  # noqa: E402  (import after sys.path setup, on purpose)


class CrewChoice(BaseModel):
    """Constrained ReAct's action space: chosen_crew_id is validated
    against the real candidate list returned by the DB query in
    propose_crew below -- the model cannot pick a crew_id that query
    didn't actually return (see the check right after this call)."""

    model_config = ConfigDict(extra="forbid")
    thought: str = Field(description="brief reasoning comparing the candidates")
    chosen_crew_id: int
    reasoning: str = Field(description="one sentence explaining the pick, shown to the admin")


# ---------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------

def intake_disruption(state: dict[str, Any]) -> NodeResult:
    """Confirms the flight is actually disrupted and needs a reassignment."""
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT flight_id, status, disruption_reason FROM flights WHERE flight_number = %s",
            (state["flight_number"],),
        )
        flight = cursor.fetchone()
    finally:
        cursor.close()
        conn.close()

    if flight is None:
        return NodeResult.fail(state, f"no flight found with number {state['flight_number']}")
    if flight["status"] not in ("disrupted", "delayed", "cancelled"):
        return NodeResult.fail(state, f"flight {state['flight_number']} is not disrupted (status={flight['status']})")

    state = {**state, "flight_id": flight["flight_id"], "disruption_reason": flight["disruption_reason"],
              "declined_crew_ids": state.get("declined_crew_ids", [])}
    return NodeResult.goto("propose_crew", state)


def _query_eligible_crew(flight_id: int, excluded_ids: list[int],
                          require_role: str | None, require_base_airport: str | None) -> list[dict]:
    """One real DB 'Action' in the ReAct loop below. Returns up to 5 real,
    currently-eligible reserve candidates (not already assigned to this
    flight, not already tried and declined this run), ordered by lowest
    duty hours logged today so the model's own choice still lands on a
    sensible pick when several rows come back."""
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        excluded = excluded_ids or [0]
        placeholders = ",".join(["%s"] * len(excluded))
        filters = [f"c.crew_id NOT IN ({placeholders})",
                   "c.crew_id NOT IN (SELECT crew_id FROM crew_assignments WHERE flight_id = %s)"]
        params: list[Any] = list(excluded) + [flight_id]
        if require_role:
            filters.append("c.role = %s")
            params.append(require_role)
        if require_base_airport:
            filters.append("c.base_airport = %s")
            params.append(require_base_airport)
        cursor.execute(
            f"""
            SELECT c.crew_id, c.full_name, c.role, c.base_airport,
                   COALESCE(SUM(d.hours_on_duty), 0) AS duty_hours_today
            FROM crew c
            LEFT JOIN duty_time_logs d ON d.crew_id = c.crew_id AND d.log_date = CURDATE()
            WHERE {" AND ".join(filters)}
            GROUP BY c.crew_id, c.full_name, c.role, c.base_airport
            ORDER BY duty_hours_today ASC
            LIMIT 5
            """,
            tuple(params),
        )
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()


def _needed_role_and_origin(flight_id: int) -> tuple[str | None, str | None]:
    """Looks up the role of whichever crew member was ORIGINALLY rostered
    on this flight (so the reserve candidate actually covers the right
    seat) and the flight's origin airport (so the reserve candidate is
    actually based somewhere that can reach this flight)."""
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT c.role FROM crew_assignments ca
            JOIN crew c ON c.crew_id = ca.crew_id
            WHERE ca.flight_id = %s AND ca.assignment_type = 'original'
            LIMIT 1
            """,
            (flight_id,),
        )
        row = cursor.fetchone()
        role = row["role"] if row else None

        cursor.execute("SELECT origin_airport FROM flights WHERE flight_id = %s", (flight_id,))
        frow = cursor.fetchone()
        origin = frow["origin_airport"] if frow else None
    finally:
        cursor.close()
        conn.close()
    return role, origin


def propose_crew(state: dict[str, Any]) -> NodeResult:
    """
    Constrained-ReAct node: picks the next eligible reserve-crew candidate
    the model hasn't already tried for this run.

    Thought/Action/Observation loop, each Action a REAL SQL query, each
    Observation the REAL rows it returned:
      1. Act:  query candidates matching both the needed role AND the
               flight's origin airport (the tight, ideal match).
      2. If that Observation is empty, Act again with the airport
         constraint relaxed (role still required -- a wrong-role crew
         member can never legally cover this seat, so that constraint is
         never relaxed).
      3. If still empty, fail -- there is genuinely no eligible reserve
         crew for this flight, and no amount of reasoning invents one.

    Once an Observation is non-empty, ONE final LLM call reasons over
    those exact rows and must return a chosen_crew_id from that exact
    set (constrained action space) -- the model cannot invent a
    candidate outside what the query actually returned. If it ever
    returns an id outside that set anyway (a live model not honoring the
    constraint), we don't trust it: we fall back to the top-ranked real
    candidate and record that the fallback fired.
    """
    excluded = state.get("declined_crew_ids", []) or []
    needed_role, origin_airport = _needed_role_and_origin(state["flight_id"])

    candidates = _query_eligible_crew(state["flight_id"], excluded, needed_role, origin_airport)
    relaxed_airport = False
    if not candidates and origin_airport is not None:
        # Step 2: relax the base-airport constraint, keep role fixed.
        candidates = _query_eligible_crew(state["flight_id"], excluded, needed_role, None)
        relaxed_airport = True

    if not candidates:
        return NodeResult.fail(state, "no eligible reserve crew candidates left")

    if len(candidates) == 1:
        candidate = candidates[0]
        choice_reasoning = "only one eligible candidate matched the query, no choice needed"
    else:
        candidate_lines = "\n".join(
            f"- crew_id={c['crew_id']}, name={c['full_name']}, role={c['role']}, "
            f"base_airport={c['base_airport']}, duty_hours_today={c['duty_hours_today']}"
            for c in candidates
        )
        system = (
            "You are choosing a reserve crew member for an IROPS reassignment. "
            "You may ONLY choose a crew_id that appears in the candidate list below -- "
            "picking any other crew_id is invalid, there is no such candidate."
        )
        user = (
            f"Flight needs a: {needed_role or 'any role'}\n"
            f"Airport constraint was {'relaxed (no exact base match available)' if relaxed_airport else 'an exact base-airport match'}.\n"
            f"Real eligible candidates (Observation from a live DB query):\n{candidate_lines}\n\n"
            "Prefer the candidate with the lowest duty_hours_today (least risk of a duty-hour "
            "breach once assigned), unless another candidate is clearly a better operational fit. "
            "Return the chosen crew_id exactly as one of the crew_id values shown above."
        )
        result = generate_json(system=system, user=user, schema=CrewChoice)

        valid_ids = {c["crew_id"] for c in candidates}
        if result.chosen_crew_id in valid_ids:
            candidate = next(c for c in candidates if c["crew_id"] == result.chosen_crew_id)
            choice_reasoning = result.reasoning
        else:
            # Constraint enforcement: the model didn't pick from the real
            # observation, so its choice is discarded and we fall back to
            # the top-ranked real candidate instead of trusting it.
            candidate = candidates[0]
            choice_reasoning = (
                f"model chose crew_id={result.chosen_crew_id}, which is outside the real "
                f"candidate list ({sorted(valid_ids)}) -- fell back to the top-ranked real "
                f"candidate instead"
            )

    state = {**state, "propose_crew_reasoning": choice_reasoning}

    # Duty-hour check happens now so we know, once the crew member replies,
    # whether their acceptance would need HITL sign-off. duty_hours_today
    # was already computed as part of the same observation query above
    # (_query_eligible_crew), so we reuse it instead of re-querying.
    total_duty = float(candidate["duty_hours_today"])
    duty_hour_breach = total_duty >= MAX_DUTY_HOURS_PER_DAY

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO crew_reassignment_requests
                (run_id, flight_id, crew_id, disruption_reason, duty_hour_breach)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (state["run_id"], state["flight_id"], candidate["crew_id"],
             state.get("disruption_reason"), duty_hour_breach),
        )
        conn.commit()
        request_id = cursor.lastrowid
    finally:
        cursor.close()
        conn.close()

    state = {**state, "candidate_crew_id": candidate["crew_id"],
              "candidate_name": candidate["full_name"],
              "duty_hour_breach": duty_hour_breach,
              "current_request_id": request_id,
              "propose_crew_reasoning": choice_reasoning}
    # TODO: actually notify the crew member here (SMS/app push/email --
    # whatever channel your company would really use). The wait for their
    # reply is real, so we pause rather than block a thread.
    return NodeResult.pause("waiting_external", "await_crew_reply", state)


def handle_crew_reply(state: dict[str, Any]) -> NodeResult:
    """
    Runs when submit_crew_reply(run_id, accepted) is called from outside
    (the MCP tool / platform endpoint the crew member's app hits). The
    reply itself is merged into state by StateGraph.resume()'s patch_state
    before this node runs.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE crew_reassignment_requests SET crew_reply_status = %s, crew_replied_at = NOW() WHERE request_id = %s",
            (state["crew_reply_status"], state["current_request_id"]),
        )
        conn.commit()
    finally:
        cursor.close()
        conn.close()

    if state["crew_reply_status"] in ("declined", "no_response_timeout"):
        declined = list(state.get("declined_crew_ids", []))
        declined.append(state["candidate_crew_id"])
        state = {**state, "declined_crew_ids": declined}
        return NodeResult.goto("propose_crew", state)  # cycle: try the next candidate

    if not state.get("duty_hour_breach"):
        return NodeResult.goto("finalize_assignment", state)

    return NodeResult.goto("request_duty_override", state)


def request_duty_override(state: dict[str, Any]) -> NodeResult:
    """
    HITL node. Opens a hitl_tasks row and pauses -- this run only continues
    once an admin acts on it through the platform, never by anything
    inside this process auto-approving.
    """
    # RAG call: ground the admin-facing explanation in the ACTUAL duty-time
    # policy text instead of letting the model paraphrase the rule from
    # memory (which is exactly what could drift from the real policy over
    # time). We reuse the same vector store the Memory/RAG agent already
    # built (Rag/naive_rag.py -> Rag/vector_store.py), filtered to the one
    # source document this decision actually depends on, rather than
    # standing up a second, parallel retrieval pipeline just for this node.
    rag_query = (
        f"What is the maximum duty hours per day, and what is required before "
        f"a crew member can be assigned duty hours above that limit?"
    )
    rag_result = naive_rag_answer(
        rag_query, top_k=3, where={"source": "duty_time_policy.md"}
    )
    policy_grounding = rag_result["answer"]
    cited_chunks = [c["text"] for c in rag_result["retrieved_chunks"]]

    # hitl_tasks.reason is a short VARCHAR meant for the admin's task-list
    # row, not a document viewer -- the full RAG-grounded explanation and
    # its citations go in payload_json below instead, where the platform's
    # HITL detail view (not the list row) is expected to render them.
    reason = (
        f"{state['candidate_name']} accepted reassignment to flight "
        f"{state['flight_number']} but this would exceed the legal duty-hour limit. "
        f"See the linked policy grounding for the exact rule and citation."
    )[:300]
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO hitl_tasks (run_id, node_name, reason, condition_type, payload_json)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (state["run_id"], "request_duty_override", reason, "duty_hour_breach",
             json.dumps({
                 "crew_id": state["candidate_crew_id"],
                 "flight_number": state["flight_number"],
                 "request_id": state["current_request_id"],
                 # RAG addition's actual output: the model's grounded
                 # explanation PLUS the real retrieved policy chunks it
                 # was grounded in (not a paraphrase from model memory),
                 # for the platform's HITL detail view to render in full.
                 "policy_grounding": policy_grounding,
                 "policy_citations": cited_chunks,
             })),
        )
        conn.commit()
    finally:
        cursor.close()
        conn.close()

    return NodeResult.pause("waiting_hitl", "handle_hitl_decision", state)


def handle_hitl_decision(state: dict[str, Any]) -> NodeResult:
    """
    Runs after an admin resolves the hitl_tasks row through the platform.
    The decision ('approved' / 'rejected') is merged into state via
    patch_state on resume(), same mechanism as the crew reply above.
    """
    if state["hitl_decision"] == "approved":
        return NodeResult.goto("finalize_assignment", state)

    declined = list(state.get("declined_crew_ids", []))
    declined.append(state["candidate_crew_id"])
    state = {**state, "declined_crew_ids": declined}
    return NodeResult.goto("propose_crew", state)  # cycle: try the next candidate


def finalize_assignment(state: dict[str, Any]) -> NodeResult:
    """Writes the real assignment -- reuses the existing crew_assignments table."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO crew_assignments (crew_id, flight_id, duty_start, duty_end, assignment_type)
            VALUES (%s, %s, NOW(), DATE_ADD(NOW(), INTERVAL 8 HOUR), 'reserve')
            """,
            (state["candidate_crew_id"], state["flight_id"]),
        )
        cursor.execute(
            "UPDATE crew_reassignment_requests SET final_status = 'assigned' WHERE request_id = %s",
            (state["current_request_id"],),
        )
        conn.commit()
    finally:
        cursor.close()
        conn.close()

    return NodeResult.finish(state)


# ---------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------

def build_graph() -> StateGraph:
    graph = StateGraph(GRAPH_NAME, MySQLCheckpointer())
    graph.add_node("intake_disruption", intake_disruption)
    graph.add_node("propose_crew", propose_crew)
    graph.add_node("handle_crew_reply", handle_crew_reply)
    graph.add_node("request_duty_override", request_duty_override)
    graph.add_node("handle_hitl_decision", handle_hitl_decision)
    graph.add_node("finalize_assignment", finalize_assignment)
    return graph


# ---- entry points the MCP server / platform backend will call ---------

def start_crew_reassignment(flight_number: str, started_by: str) -> str:
    graph = build_graph()
    run_id = graph.checkpointer.start_run(
        graph_name=GRAPH_NAME, started_by=started_by,
        first_node="intake_disruption",
        initial_state={"flight_number": flight_number},
    )
    # run_id doesn't exist yet at the moment initial_state is built above,
    # so it's merged in here via patch_state before intake_disruption runs --
    # every node after this one can rely on state["run_id"] being present.
    graph.resume(run_id, resume_node="intake_disruption", patch_state={"run_id": run_id})
    return run_id


def submit_crew_reply(run_id: str, accepted: bool) -> None:
    graph = build_graph()
    status = "accepted" if accepted else "declined"
    graph.resume(run_id, resume_node="handle_crew_reply", patch_state={"crew_reply_status": status})


def submit_hitl_decision(run_id: str, approved: bool, decided_by: str, note: str = "") -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE hitl_tasks SET status = %s, decided_by = %s, decision_note = %s, decided_at = NOW()
            WHERE run_id = %s AND status = 'pending'
            """,
            ("approved" if approved else "rejected", decided_by, note, run_id),
        )
        conn.commit()
    finally:
        cursor.close()
        conn.close()

    graph = build_graph()
    graph.resume(run_id, resume_node="handle_hitl_decision",
                 patch_state={"hitl_decision": "approved" if approved else "rejected"})
