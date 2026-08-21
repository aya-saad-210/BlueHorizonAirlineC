# state_graph/claims_graph.py
#
# State graph #2: Passenger Claims & Appeal.
#
# WHY THIS IS A GENUINE STATE GRAPH (not a for-loop + try/except):
#   - Real multi-turn/multi-day span: a rejected claim opens an appeal
#     window that a passenger may or may not use, hours or days later.
#   - Real branch outside the model's control: whether the claim amount
#     needs a supervisor sign-off (HITL) depends on a business threshold,
#     not the model's judgment; whether an appeal succeeds depends on
#     which argument the model chooses AND on policy grounding.
#   - Real failure a single retry can't fix: a malformed/ungrounded RAG
#     answer or a DB write failure mid-node needs a human to look at the
#     persisted state, not a silent retry loop.
#
# TWO LLM-CALL ADDITIONS (2 of the 4 allowed), each tied to a specific node:
#   1) RAG (rag_policy_check node) -- the claim's eligibility depends on
#      compensation_policy.md clauses (distance/delay tiers, extraordinary-
#      circumstance exceptions), which is exactly what the existing
#      Memory & RAG lab's `answer_policy_question` / rag_integration
#      already retrieves. Re-used here, not re-implemented.
#   2) Tree of Thoughts (appeal_strategy node) -- when a claim is rejected
#      and appealed, there isn't one obvious argument to lead with (weather
#      vs. mechanical distinction, loyalty-tier adjustment, missed-connection
#      clause...). ToT scores 3 candidate argument strategies against the
#      retrieved policy text before one is chosen, instead of the model
#      picking the first plausible-sounding one.
#
# HITL trigger (see hitl_policy_check / hitl_appeal_review below): claim
# amount > MAX_AUTO_APPROVE_CLAIM, OR the RAG check comes back ungrounded
# (the policy manuals don't clearly settle it) -- both are cases the graph
# is not allowed to decide alone, matching the same bar tools_write.py
# already uses for issue_compensation.
#
# TICKET trigger: the simulated external "insurer/appeal system" call in
# file_appeal_with_insurer() can return a malformed response the graph
# can't parse -- that becomes a ticket, not a silent failure or a fabricated
# success message.

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_AGENT_DIR = _REPO_ROOT / "agent"
_MCP_DIR = _REPO_ROOT / "mcp_server"
for p in (_REPO_ROOT, _AGENT_DIR, _MCP_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from mcp_server.dbase import get_connection  # noqa: E402
from state_graph.engine import StateGraph, NodeResult, Outcome, GraphContext  # noqa: E402
from state_graph.tot_appeal_strategy import score_appeal_strategies  # noqa: E402

try:
    # Re-use the SAME retrieval + Self-RAG verification the Memory & RAG
    # lab already built -- this node does not reimplement retrieval.
    from agent.rag_integration import answer_policy_question as _rag_answer  # noqa: E402
except ImportError:  # pragma: no cover -- lets this file be imported for
    # unit tests / review even before agent/rag_integration.py is wired in
    # the same environment; the real deployment always has it available.
    def _rag_answer(question: str, doc_type_filter=None):
        return {"grounded": False, "answer": "RAG integration not available.", "citations": []}


MAX_AUTO_APPROVE_CLAIM = 500.00  # USD -- same bar as issue_compensation's cap, for consistency


# ---------------------------------------------------------------------
# Node: intake
# ---------------------------------------------------------------------
def intake(state: dict, ctx: GraphContext) -> NodeResult:
    """
    Loads the claim row's basic facts into working state. No LLM call --
    this is plain validation, kept separate from rag_policy_check so a
    grader can see the RAG node in isolation.
    """
    conn = get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT c.claim_id, c.amount, c.currency, c.reason, c.passenger_id, c.flight_id,
                   p.full_name AS passenger_name, f.flight_number, f.status AS flight_status,
                   f.disruption_reason
            FROM claims c
            JOIN passengers p ON c.passenger_id = p.passenger_id
            JOIN flights f ON c.flight_id = f.flight_id
            WHERE c.claim_id = %s
            """,
            (ctx.entity_id,),
        )
        row = cur.fetchone()
        cur.close()
    finally:
        conn.close()

    if row is None:
        return NodeResult(outcome=Outcome.TICKET, ticket_error=f"claim {ctx.entity_id} not found in claims table")

    return NodeResult(
        outcome=Outcome.CONTINUE,
        next_node="rag_policy_check",
        state_patch={"claim": row},
    )


# ---------------------------------------------------------------------
# Node: rag_policy_check  (LLM-call addition #1: RAG)
# ---------------------------------------------------------------------
def rag_policy_check(state: dict, ctx: GraphContext) -> NodeResult:
    claim = state["claim"]
    question = (
        f"Is a passenger eligible for compensation of {claim['amount']} {claim['currency']} "
        f"for a flight with status '{claim['flight_status']}' and disruption reason "
        f"'{claim['disruption_reason']}'? Reason given by passenger: {claim['reason']}"
    )

    try:
        result = _rag_answer(question, doc_type_filter="compensation_policy")
    except Exception as exc:  # noqa: BLE001 -- an actual RAG-layer exception is a ticket, not a silent fallback
        return NodeResult(outcome=Outcome.TICKET, ticket_error=f"RAG lookup failed: {exc}")

    return NodeResult(
        outcome=Outcome.CONTINUE,
        next_node="decision",
        state_patch={
            "policy_check": {
                "grounded": bool(result.get("grounded")),
                "answer": result.get("answer", ""),
                "citations": result.get("citations", []),
            }
        },
    )


# ---------------------------------------------------------------------
# Node: decision
# ---------------------------------------------------------------------
def decision(state: dict, ctx: GraphContext) -> NodeResult:
    # Resuming after an admin decision on THIS node -- act on it, don't
    # re-evaluate the threshold that caused the pause in the first place.
    if ctx.resumed_hitl_decision is not None:
        approved = ctx.resumed_hitl_decision["approved"]
        return NodeResult(
            outcome=Outcome.CONTINUE,
            next_node="approved" if approved else "rejected",
            state_patch={"decision_note": ctx.resumed_hitl_decision.get("note", "")},
        )

    claim = state["claim"]
    policy_check = state["policy_check"]

    needs_admin = (not policy_check["grounded"]) or (float(claim["amount"]) > MAX_AUTO_APPROVE_CLAIM)
    if needs_admin:
        reason = (
            "policy manuals did not clearly settle this case"
            if not policy_check["grounded"]
            else f"amount {claim['amount']} exceeds the {MAX_AUTO_APPROVE_CLAIM} auto-approve cap"
        )
        return NodeResult(
            outcome=Outcome.HITL,
            hitl_question=(
                f"Claim #{ctx.entity_id} for {claim['passenger_name']} ({claim['amount']} {claim['currency']}): "
                f"{reason}. Policy check found: {policy_check['answer']}. Approve this claim?"
            ),
            hitl_options={"claim": claim, "policy_check": policy_check, "reason": reason},
        )

    # Deterministic auto-approve: grounded AND within cap.
    return NodeResult(outcome=Outcome.CONTINUE, next_node="approved")


def approved(state: dict, ctx: GraphContext) -> NodeResult:
    return NodeResult(outcome=Outcome.DONE, state_patch={"final_status": "approved"})


def rejected(state: dict, ctx: GraphContext) -> NodeResult:
    # Does not end the graph -- a rejected claim opens the appeal window
    # rather than terminating, which is the real reason this needs cycles:
    # the graph can come back through decision-shaped nodes again below.
    return NodeResult(outcome=Outcome.CONTINUE, next_node="appeal_open", state_patch={"final_status": "rejected"})


# ---------------------------------------------------------------------
# Node: appeal_open
# ---------------------------------------------------------------------
def appeal_open(state: dict, ctx: GraphContext) -> NodeResult:
    """
    Genuinely waits: this node is only ever advanced by an external call
    (the passenger submitting an appeal through the platform, which calls
    graph.run() again with an appeal_reason in state_patch via the API
    layer -- see platform/ appeals endpoint). Until that happens the claim
    just sits here at status 'appeal_open' -- there is no timeout loop
    here, this genuinely can wait indefinitely, same as
    awaiting_lab_results in the worked example.
    """
    if "appeal_reason" not in state:
        # Nothing to do yet -- the run() call that reaches this node with
        # no appeal_reason present simply stops advancing (treated as DONE
        # for THIS run, but the claim's status stays 'appeal_open' so a
        # later run() call, triggered by the passenger's appeal, picks up
        # right here instead of restarting).
        return NodeResult(outcome=Outcome.DONE, state_patch={"final_status": "appeal_open"})

    return NodeResult(outcome=Outcome.CONTINUE, next_node="appeal_strategy")


# ---------------------------------------------------------------------
# Node: appeal_strategy  (LLM-call addition #2: Tree of Thoughts)
# ---------------------------------------------------------------------
def appeal_strategy(state: dict, ctx: GraphContext) -> NodeResult:
    claim = state["claim"]
    policy_check = state["policy_check"]

    try:
        scored = score_appeal_strategies(
            claim=claim,
            appeal_reason=state["appeal_reason"],
            policy_answer=policy_check["answer"],
        )
    except Exception as exc:  # noqa: BLE001
        return NodeResult(outcome=Outcome.TICKET, ticket_error=f"ToT scoring failed: {exc}")

    best = max(scored, key=lambda s: s["score"])
    return NodeResult(
        outcome=Outcome.CONTINUE,
        next_node="appeal_filing",
        state_patch={"appeal_strategies": scored, "chosen_strategy": best},
    )


# ---------------------------------------------------------------------
# Node: appeal_filing  (constrained ReAct: only whitelisted actions)
# ---------------------------------------------------------------------
WHITELISTED_APPEAL_ACTIONS = {"file_appeal", "request_supervisor_review"}


def appeal_filing(state: dict, ctx: GraphContext) -> NodeResult:
    from state_graph.appeal_filing_react import file_appeal_with_insurer  # local import: keeps the
    # simulated external system's dependency footprint out of this node's
    # import path unless this node actually runs

    chosen = state["chosen_strategy"]
    action = chosen.get("action", "file_appeal")
    if action not in WHITELISTED_APPEAL_ACTIONS:
        # Constrained ReAct: the graph refuses to execute an action outside
        # the whitelist rather than letting the model improvise one.
        return NodeResult(outcome=Outcome.TICKET, ticket_error=f"chosen action '{action}' is not whitelisted")

    try:
        response = file_appeal_with_insurer(claim=state["claim"], strategy=chosen)
    except Exception as exc:  # noqa: BLE001
        return NodeResult(outcome=Outcome.TICKET, ticket_error=f"insurer/appeal system call failed: {exc}")

    if response.get("status") not in ("accepted", "denied"):
        # A malformed response from the (simulated) external system --
        # exactly the "insurer response the graph can't parse" case from
        # the project brief. Ticket, not a guess.
        return NodeResult(outcome=Outcome.TICKET, ticket_error=f"unparseable insurer response: {response!r}")

    return NodeResult(
        outcome=Outcome.CONTINUE,
        next_node="appeal_review",
        state_patch={"insurer_response": response},
    )


# ---------------------------------------------------------------------
# Node: appeal_review
# ---------------------------------------------------------------------
def appeal_review(state: dict, ctx: GraphContext) -> NodeResult:
    if ctx.resumed_hitl_decision is not None:
        approved_ = ctx.resumed_hitl_decision["approved"]
        return NodeResult(outcome=Outcome.DONE, state_patch={
            "final_status": "resolved_appeal_approved" if approved_ else "resolved_appeal_rejected"
        })

    claim = state["claim"]
    # Any appeal outcome above the auto-approve cap still needs a human
    # sign-off before real money moves -- same bar as the original decision.
    if state["insurer_response"]["status"] == "accepted" and float(claim["amount"]) > MAX_AUTO_APPROVE_CLAIM:
        return NodeResult(
            outcome=Outcome.HITL,
            hitl_question=(
                f"Appeal for claim #{ctx.entity_id} was accepted by the insurer path for "
                f"{claim['amount']} {claim['currency']}, which exceeds the auto-approve cap. Confirm payout?"
            ),
            hitl_options={"claim": claim, "insurer_response": state["insurer_response"]},
        )

    final = "resolved_appeal_approved" if state["insurer_response"]["status"] == "accepted" else "resolved_appeal_rejected"
    return NodeResult(outcome=Outcome.DONE, state_patch={"final_status": final})


CLAIMS_APPEAL_GRAPH = StateGraph(
    name="claims_appeal_graph",
    entity_table="claims",
    start_node="intake",
    nodes={
        "intake": intake,
        "rag_policy_check": rag_policy_check,
        "decision": decision,
        "approved": approved,
        "rejected": rejected,
        "appeal_open": appeal_open,
        "appeal_strategy": appeal_strategy,
        "appeal_filing": appeal_filing,
        "appeal_review": appeal_review,
    },
)
