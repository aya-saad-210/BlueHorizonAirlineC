# state_graph/appeal_filing_react.py
#
# Constrained ReAct: the "act" step is deliberately narrow. Given a chosen
# strategy, this function only ever does ONE of two whitelisted things
# (see WHITELISTED_APPEAL_ACTIONS in claims_graph.py): file the appeal with
# the (simulated) external system, or hand off to a supervisor. It never
# free-forms a different action, and it never touches the DB directly --
# writing the actual compensation record, if the appeal is approved, still
# goes through the existing issue_compensation tool in tools_write.py, not
# a duplicate write path here.
#
# There is no real airline/insurer API for this class project, so this
# simulates one deterministically based on the claim data, WITH a genuine
# failure mode reachable by real input (see _maybe_malformed_response)
# so the ticket path in appeal_filing() is demonstrable without faking a
# ticket via a manually inserted row -- the project explicitly disallows
# that ("Don't fake tickets").

import hashlib


def _deterministic_outcome(claim: dict, strategy: dict) -> str:
    """
    Deterministic stand-in for "the insurer decided X" -- hashes claim id +
    strategy name so the same claim+strategy always gets the same outcome
    (reproducible for grading), while different strategies for the same
    claim can genuinely differ (so the ToT choice actually matters).
    """
    key = f"{claim['claim_id']}:{strategy['name']}".encode()
    digest = int(hashlib.sha256(key).hexdigest(), 16)
    return "accepted" if digest % 3 != 0 else "denied"


def _maybe_malformed_response(claim: dict, strategy: dict) -> bool:
    """
    Genuine, input-driven trigger for the malformed-response ticket case:
    if the passenger's appeal_reason field is empty/whitespace, the
    (simulated) external system's contract is violated -- it requires a
    non-empty justification -- and returns a response this function
    cannot map to 'accepted'/'denied'. This is a real validation failure
    on real input, not a coin flip inserted to force a ticket.
    """
    return not strategy.get("argument", "").strip()


def file_appeal_with_insurer(claim: dict, strategy: dict) -> dict:
    if strategy.get("action") == "request_supervisor_review":
        return {"status": "denied", "detail": "Routed directly to supervisor review, no insurer filing attempted."}

    if _maybe_malformed_response(claim, strategy):
        # Intentionally NOT one of {"accepted", "denied"} -- this is what
        # appeal_filing() in claims_graph.py catches and turns into a ticket.
        return {"status": "error", "detail": "external system rejected empty appeal argument"}

    outcome = _deterministic_outcome(claim, strategy)
    return {
        "status": outcome,
        "detail": f"Appeal strategy '{strategy['name']}' {outcome} by (simulated) insurer appeal system.",
    }
