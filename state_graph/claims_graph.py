# state_graph/claims_graph.py
#
# State graph #2: Passenger Claims & Appeal.
#
# REVISION NOTE: this now runs on the TEAM-SHARED engine
# (graph_engine.StateGraph + checkpointer.MySQLCheckpointer), the same
# ones crew_reassignment_graph.py (Person A) uses -- not a separate
# engine. An earlier version of this file shipped with its own
# state_graph/engine.py and its own graph_checkpoints/hitl_tasks tables;
# that engine.py has been deleted and its tables dropped from this
# team's migration. If your local checkout still has state_graph/engine.py
# or a second graph_checkpoints/hitl_tasks definition, delete it -- it
# will collide with the shared schema in data base/state_graph_schema.sql.
#
# Ticket handling: this file NEVER writes to a tickets table directly.
# Every failure path returns NodeResult.fail(state, error), which
# graph_engine.StateGraph._execute_from() hands to _open_ticket() --
# currently a print stub there, to be replaced once Person C's ticket
# system exists. Nothing here should be changed when that lands.
#
# WHY THIS IS A GENUINE STATE GRAPH: see the original design rationale --
# a rejected claim opens an appeal window that may be used hours/days
# later (genuinely spans more than one sitting); whether a claim needs
# admin sign-off depends on policy grounding + a real dollar threshold,
# not the model's opinion; a malformed response from the (simulated)
# external appeal system is a real failure a retry alone can't fix.
#
# TWO LLM-CALL ADDITIONS:
#   1) RAG (rag_policy_check node) -- reuses agent/rag_integration.py from
#      the Memory & RAG lab, not reimplemented here.
#   2) Tree of Thoughts (appeal_strategy node) -- scores 3 candidate
#      appeal arguments (tot_appeal_strategy.py) before appeal_filing
#      commits to one via constrained ReAct.

from __future__ import annotations

import sys
import os
import json
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp_server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent"))
sys.path.insert(0, os.path.dirname(__file__))

from mcp_server.dbase import get_connection  # noqa: E402
from graph_engine import StateGraph, NodeResult  # noqa: E402
from checkpointer import MySQLCheckpointer  # noqa: E402
from tot_appeal_strategy import score_appeal_strategies  # noqa: E402
from appeal_filing_react import file_appeal_with_insurer  # noqa: E402

try:
    from agent.rag_integration import answer_policy_question as _rag_answer  # noqa: E402
except ImportError:  # pragma: no cover -- see original note, real deployment has this
    def _rag_answer(question: str, doc_type_filter=None):
        return {"grounded": False, "answer": "RAG integration not available.", "citations": []}

GRAPH_NAME = "passenger_claims"
MAX_AUTO_APPROVE_CLAIM = 500.00  # same bar as issue_compensation, for consistency
WHITELISTED_APPEAL_ACTIONS = {"file_appeal", "request_supervisor_review"}


# ---------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------

def intake(state: dict[str, Any]) -> NodeResult:
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT c.claim_id, c.amount, c.currency, c.reason, c.passenger_id, c.flight_id,
                   p.full_name AS passenger_name, f.flight_number, f.status AS flight_status,
                   f.disruption_reason
            FROM claims c
            JOIN passengers p ON c.passenger_id = p.passenger_id
            JOIN flights f ON c.flight_id = f.flight_id
            WHERE c.claim_id = %s
            """,
            (state["claim_id"],),
        )
        row = cursor.fetchone()
    finally:
        cursor.close()
        conn.close()

    if row is None:
        return NodeResult.fail(state, f"claim {state['claim_id']} not found in claims table")

    return NodeResult.goto("rag_policy_check", {**state, "claim": row})


def rag_policy_check(state: dict[str, Any]) -> NodeResult:
    claim = state["claim"]
    question = (
        f"Is a passenger eligible for compensation of {claim['amount']} {claim['currency']} "
        f"for a flight with status '{claim['flight_status']}' and disruption reason "
        f"'{claim['disruption_reason']}'? Reason given by passenger: {claim['reason']}"
    )
    try:
        result = _rag_answer(question, doc_type_filter="compensation_policy")
    except Exception as exc:  # noqa: BLE001 -- a real RAG-layer exception, a genuine ticket
        return NodeResult.fail(state, f"RAG lookup failed: {exc}")

    policy_check = {
        "grounded": bool(result.get("grounded")),
        "answer": result.get("answer", ""),
        "citations": result.get("citations", []),
    }
    return NodeResult.goto("decision", {**state, "policy_check": policy_check})


def decision(state: dict[str, Any]) -> NodeResult:
    claim = state["claim"]
    policy_check = state["policy_check"]
    needs_admin = (not policy_check["grounded"]) or (float(claim["amount"]) > MAX_AUTO_APPROVE_CLAIM)

    if not needs_admin:
        return NodeResult.goto("approved", state)
    return NodeResult.goto("request_claim_override", state)


def request_claim_override(state: dict[str, Any]) -> NodeResult:
    """HITL node -- opens a hitl_tasks row against the SHARED schema (run_id,
    node_name, reason, condition_type, payload_json), same table
    crew_reassignment_graph.py's request_duty_override writes to."""
    claim = state["claim"]
    policy_check = state["policy_check"]
    condition_type = "ungrounded_policy" if not policy_check["grounded"] else "amount_over_cap"
    reason = (
        f"Claim for {claim['passenger_name']} ({claim['amount']} {claim['currency']}): "
        f"{condition_type}. Policy check found: {policy_check['answer']}"
    )

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO hitl_tasks (run_id, node_name, reason, condition_type, payload_json)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (state["run_id"], "request_claim_override", reason, condition_type,
             json.dumps({"claim_id": claim["claim_id"], "amount": str(claim["amount"])})),
        )
        conn.commit()
    finally:
        cursor.close()
        conn.close()

    return NodeResult.pause("waiting_hitl", "handle_claim_decision", state)


def handle_claim_decision(state: dict[str, Any]) -> NodeResult:
    """Runs after an admin resolves the hitl_tasks row via the platform.
    state['hitl_decision'] is merged in by resume()'s patch_state."""
    if state["hitl_decision"] == "approved":
        return NodeResult.goto("approved", state)
    return NodeResult.goto("rejected", state)


def approved(state: dict[str, Any]) -> NodeResult:
    _update_claim(state["claim"]["claim_id"], final_status="approved")
    return NodeResult.finish({**state, "final_status": "approved"})


def rejected(state: dict[str, Any]) -> NodeResult:
    _update_claim(state["claim"]["claim_id"], final_status="rejected")
    return NodeResult.goto("appeal_open", {**state, "final_status": "rejected"})


def appeal_open(state: dict[str, Any]) -> NodeResult:
    """Genuinely waits, indefinitely, for the passenger to submit an appeal
    through the platform (submit_appeal() below), which resumes THIS run
    directly at 'appeal_strategy' with appeal_reason merged into state --
    same mechanism as await_crew_reply in crew_reassignment_graph.py."""
    return NodeResult.pause("waiting_external", "appeal_strategy", state)


def appeal_strategy(state: dict[str, Any]) -> NodeResult:
    claim = state["claim"]
    policy_check = state["policy_check"]
    try:
        scored = score_appeal_strategies(
            claim=claim, appeal_reason=state["appeal_reason"], policy_answer=policy_check["answer"]
        )
    except Exception as exc:  # noqa: BLE001
        return NodeResult.fail(state, f"ToT scoring failed: {exc}")

    best = max(scored, key=lambda s: s["score"])
    return NodeResult.goto("appeal_filing", {**state, "appeal_strategies": scored, "chosen_strategy": best})


def appeal_filing(state: dict[str, Any]) -> NodeResult:
    """Constrained ReAct: only ever executes a whitelisted action."""
    chosen = state["chosen_strategy"]
    action = chosen.get("action", "file_appeal")
    if action not in WHITELISTED_APPEAL_ACTIONS:
        return NodeResult.fail(state, f"chosen action '{action}' is not whitelisted")

    try:
        response = file_appeal_with_insurer(claim=state["claim"], strategy=chosen)
    except Exception as exc:  # noqa: BLE001
        return NodeResult.fail(state, f"insurer/appeal system call failed: {exc}")

    if response.get("status") not in ("accepted", "denied"):
        return NodeResult.fail(state, f"unparseable insurer response: {response!r}")

    return NodeResult.goto("appeal_review", {**state, "insurer_response": response})


def appeal_review(state: dict[str, Any]) -> NodeResult:
    claim = state["claim"]
    if state["insurer_response"]["status"] == "accepted" and float(claim["amount"]) > MAX_AUTO_APPROVE_CLAIM:
        return NodeResult.goto("request_appeal_override", state)

    final = "resolved_appeal_approved" if state["insurer_response"]["status"] == "accepted" else "resolved_appeal_rejected"
    _update_claim(claim["claim_id"], final_status=final)
    return NodeResult.finish({**state, "final_status": final})


def request_appeal_override(state: dict[str, Any]) -> NodeResult:
    claim = state["claim"]
    reason = (
        f"Appeal for claim #{claim['claim_id']} was accepted by the (simulated) insurer path for "
        f"{claim['amount']} {claim['currency']}, which exceeds the auto-approve cap. Confirm payout?"
    )
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO hitl_tasks (run_id, node_name, reason, condition_type, payload_json)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (state["run_id"], "request_appeal_override", reason, "appeal_amount_over_cap",
             json.dumps({"claim_id": claim["claim_id"], "amount": str(claim["amount"])})),
        )
        conn.commit()
    finally:
        cursor.close()
        conn.close()
    return NodeResult.pause("waiting_hitl", "handle_appeal_decision", state)


def handle_appeal_decision(state: dict[str, Any]) -> NodeResult:
    claim = state["claim"]
    final = "resolved_appeal_approved" if state["hitl_decision"] == "approved" else "resolved_appeal_rejected"
    _update_claim(claim["claim_id"], final_status=final)
    return NodeResult.finish({**state, "final_status": final})


def _update_claim(claim_id: int, final_status: str) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE claims SET final_status=%s WHERE claim_id=%s", (final_status, claim_id))
        conn.commit()
    finally:
        cursor.close()
        conn.close()


# ---------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------

def build_graph() -> StateGraph:
    graph = StateGraph(GRAPH_NAME, MySQLCheckpointer())
    for name, fn in [
        ("intake", intake),
        ("rag_policy_check", rag_policy_check),
        ("decision", decision),
        ("request_claim_override", request_claim_override),
        ("handle_claim_decision", handle_claim_decision),
        ("approved", approved),
        ("rejected", rejected),
        ("appeal_open", appeal_open),
        ("appeal_strategy", appeal_strategy),
        ("appeal_filing", appeal_filing),
        ("appeal_review", appeal_review),
        ("request_appeal_override", request_appeal_override),
        ("handle_appeal_decision", handle_appeal_decision),
    ]:
        graph.add_node(name, fn)
    return graph


# ---- entry points the MCP server / platform backend call --------------

def submit_claim(passenger_email: str, flight_number: str, amount: float, currency: str, reason: str, submitted_by: str) -> str:
    """Creates the claims business row, then starts a graph run the SAME
    way start_crew_reassignment() does: start_run() first so we have a
    real run_id, THEN resume() into the entry node with run_id merged into
    state -- graph.start() alone doesn't inject run_id into state, so
    every node that needs state['run_id'] (the HITL nodes here) requires
    this two-step start, exactly like crew_reassignment_graph.py."""
    conn = get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT passenger_id FROM passengers WHERE email=%s", (passenger_email,))
        passenger = cur.fetchone()
        if passenger is None:
            return f"Rejected: no passenger found with email {passenger_email}."
        cur.execute("SELECT flight_id FROM flights WHERE flight_number=%s", (flight_number,))
        flight = cur.fetchone()
        if flight is None:
            return f"Rejected: no flight found with number {flight_number}."

        cur2 = conn.cursor()
        cur2.execute(
            "INSERT INTO claims (passenger_id, flight_id, amount, currency, reason, submitted_by) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (passenger["passenger_id"], flight["flight_id"], amount, currency, reason, submitted_by),
        )
        conn.commit()
        claim_id = cur2.lastrowid
        cur2.close()
        cur.close()
    finally:
        conn.close()

    graph = build_graph()
    run_id = graph.checkpointer.start_run(
        graph_name=GRAPH_NAME, started_by=submitted_by, first_node="intake",
        initial_state={"claim_id": claim_id},
    )
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE claims SET run_id=%s WHERE claim_id=%s", (run_id, claim_id))
        conn.commit()
        cur.close()
    finally:
        conn.close()

    graph.resume(run_id, resume_node="intake", patch_state={"run_id": run_id, "claim_id": claim_id})
    return f"Claim #{claim_id} created (run_id={run_id})."


def submit_appeal(claim_id: int, appeal_reason: str) -> str:
    """Advances a claim sitting at 'appeal_open' -- the real external event
    that node was waiting for."""
    conn = get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT run_id FROM claims WHERE claim_id=%s", (claim_id,))
        row = cur.fetchone()
        cur.close()
    finally:
        conn.close()
    if row is None or row["run_id"] is None:
        return f"Rejected: no active graph run for claim #{claim_id}."

    graph = build_graph()
    graph.resume(row["run_id"], resume_node="appeal_strategy", patch_state={"appeal_reason": appeal_reason})
    return f"Appeal for claim #{claim_id} submitted."


def submit_claim_hitl_decision(run_id: str, approved_: bool, decided_by: str, note: str = "") -> None:
    """Called by the platform's generic HITL resolver (see
    mcp_server/tools_admin.py) once it looks up graph_name='passenger_claims'
    for a given hitl_tasks row -- mirrors
    crew_reassignment_graph.submit_hitl_decision exactly."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE hitl_tasks SET status=%s, decided_by=%s, decision_note=%s, decided_at=NOW()
            WHERE run_id=%s AND status='pending'
            """,
            ("approved" if approved_ else "rejected", decided_by, note, run_id),
        )
        conn.commit()
    finally:
        cursor.close()
        conn.close()

    graph = build_graph()
    checkpoint = graph.checkpointer.load_latest(run_id)
    # Both HITL pauses in this graph resume at a node named handle_*_decision
    # that the checkpointer already recorded as current_node -- resume_node
    # defaults to that, so this works for either pause point unchanged.
    graph.resume(run_id, resume_node=checkpoint.node_name if checkpoint else None,
                 patch_state={"hitl_decision": "approved" if approved_ else "rejected"})
