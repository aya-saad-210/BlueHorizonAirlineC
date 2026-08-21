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

import sys
import os
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp_server"))
from dbase import get_connection

from graph_engine import StateGraph, NodeResult
from checkpointer import MySQLCheckpointer

MAX_DUTY_HOURS_PER_DAY = 14.00  # kept in sync with mcp_server/tools_write.py -- see
                                 # ISSUES.md Issue 6, the intent is one source of truth
GRAPH_NAME = "crew_reassignment"


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


def propose_crew(state: dict[str, Any]) -> NodeResult:
    """
    Picks the next eligible reserve-crew candidate the model hasn't already
    tried for this run. TODO: replace the plain SQL filter below with the
    constrained-ReAct call described above -- the model chooses among
    exactly the rows this query returns, nothing outside that list.
    """
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        excluded = state.get("declined_crew_ids", []) or [0]
        placeholders = ",".join(["%s"] * len(excluded))
        cursor.execute(
            f"""
            SELECT crew_id, full_name, role FROM crew
            WHERE crew_id NOT IN ({placeholders})
            ORDER BY crew_id
            LIMIT 1
            """,
            tuple(excluded),
        )
        candidate = cursor.fetchone()
    finally:
        cursor.close()
        conn.close()

    if candidate is None:
        return NodeResult.fail(state, "no eligible reserve crew candidates left")

    # Duty-hour check happens now so we know, once the crew member replies,
    # whether their acceptance would need HITL sign-off.
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT COALESCE(SUM(hours_on_duty), 0) AS total_duty
            FROM duty_time_logs WHERE crew_id = %s AND log_date = CURDATE()
            """,
            (candidate["crew_id"],),
        )
        total_duty = float(cursor.fetchone()["total_duty"])
    finally:
        cursor.close()
        conn.close()

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
              "current_request_id": request_id}
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
    # TODO: RAG call here -- retrieve the real duty-time policy text from
    # Rag/policy_docs/duty_time_policy.md and include it in `reason` so the
    # admin sees the actual policy, not a paraphrase from the model's memory.
    reason = (
        f"{state['candidate_name']} accepted reassignment to flight "
        f"{state['flight_number']} but this would exceed the legal duty-hour limit."
    )
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO hitl_tasks (run_id, node_name, reason, condition_type, payload_json)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (state["run_id"], "request_duty_override", reason, "duty_hour_breach",
             __import__("json").dumps({
                 "crew_id": state["candidate_crew_id"],
                 "flight_number": state["flight_number"],
                 "request_id": state["current_request_id"],
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
