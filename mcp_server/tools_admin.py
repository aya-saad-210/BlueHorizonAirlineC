# mcp_server/tools_admin.py
#
# Everything the platform's admin surface needs FROM the live MCP server,
# in one place:
#   1) Runtime tool registration/de-registration (register_tool /
#      deregister_tool) -- these are called BY THE PLATFORM BACKEND, which
#      holds a reference to the same `mcp` FastMCP instance server.py
#      creates (see platform/backend/mcp_bridge.py in the platform folder --
#      it imports `mcp` from server.py directly, same process or via the
#      MCP client, so a toggle in the admin UI reaches the SAME instance
#      agents are actually calling, not a copy).
#   2) Claim submission / appeal submission, exposed BOTH as plain
#      functions (called by the platform's user-chat backend) and as real
#      @mcp.tool() registrations in server.py, so a chat agent can also
#      submit a claim conversationally.
#   3) Thin wrappers around state_graph.engine's admin resume helpers, so
#      the platform's HITL/ticket resolution endpoints have one place to
#      import from instead of reaching into state_graph/ directly.
#
# DE-REGISTRATION NOTE: this repo's server.py imports FastMCP from the
# OFFICIAL `mcp` package (mcp.server.fastmcp), pinned at mcp==1.29.0 in
# requirements.txt -- NOT the standalone `fastmcp` v2/v3 package, which is
# the one with a documented `local_provider.remove_tool()`. The official
# SDK's tool manager may or may not expose an equivalent in this version;
# deregister_tool() below tries the public API first and falls back to
# direct removal from the tool manager's internal registry if it isn't
# there, and logs which path it took so this is easy to spot and fix for
# real once you've checked your installed `mcp` version. Please run:
#     python -c "from mcp.server.fastmcp import FastMCP; print([m for m in dir(FastMCP) if 'tool' in m.lower()])"
# and send me the output -- I'll tighten this to the one real code path
# instead of a try/except ladder once we know which methods actually exist.

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("blue_horizon.admin")

# All tool modules already used by server.py -- the registry of "things an
# admin is allowed to add back after removing them". Kept as plain function
# references (not yet decorated) so add/remove is symmetric with how
# server.py itself registers them at import time.
from mcp_server.tools_read import get_flight_status, get_passenger_booking
from mcp_server.tools_write import assign_reserve_crew, issue_compensation, rebook_passenger
from mcp_server.progress_logic import rebook_all_passengers_on_flight
from mcp_server.sampling_logic import generate_disruption_notice
from mcp_server.tools_search import search_knowledge_base
from mcp_server.rag_tool import answer_policy_question
from mcp_server.tools_planning import plan_irops_response

AVAILABLE_TOOLS = {
    "get_flight_status": get_flight_status,
    "get_passenger_booking": get_passenger_booking,
    "assign_reserve_crew": assign_reserve_crew,
    "issue_compensation": issue_compensation,
    "rebook_passenger": rebook_passenger,
    "rebook_all_passengers_on_flight": rebook_all_passengers_on_flight,
    "generate_disruption_notice": generate_disruption_notice,
    "search_knowledge_base": search_knowledge_base,
    "answer_policy_question": answer_policy_question,
    "plan_irops_response": plan_irops_response,
    # "submit_claim" / "submit_appeal" registered below, added to this dict
    # once defined so they're also add/remove-able like every other tool.
}


def _audit(agent_name: str, tool_name: str, action: str, performed_by: str):
    from dbase import get_connection

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO agent_tools_registry (agent_name, tool_name, action, performed_by) VALUES (%s,%s,%s,%s)",
            (agent_name, tool_name, action, performed_by),
        )
        conn.commit()
        cur.close()
    finally:
        conn.close()


def register_tool(mcp_instance, agent_name: str, tool_name: str, performed_by: str) -> str:
    if tool_name not in AVAILABLE_TOOLS:
        return f"Rejected: '{tool_name}' is not a known tool for this server."

    fn = AVAILABLE_TOOLS[tool_name]
    mcp_instance.add_tool(fn)  # documented, stable across FastMCP versions
    _audit(agent_name, tool_name, "registered", performed_by)
    logger.info("registered tool %s for %s (by %s)", tool_name, agent_name, performed_by)
    return f"'{tool_name}' registered for {agent_name}."


def deregister_tool(mcp_instance, agent_name: str, tool_name: str, performed_by: str) -> str:
    if tool_name not in AVAILABLE_TOOLS:
        return f"Rejected: '{tool_name}' is not a known tool for this server."

    removed = False
    # Path 1: a documented remove_tool on the FastMCP instance itself.
    if hasattr(mcp_instance, "remove_tool"):
        mcp_instance.remove_tool(tool_name)
        removed = True
    # Path 2: standalone fastmcp v2/v3's local_provider.remove_tool.
    elif hasattr(mcp_instance, "local_provider") and hasattr(mcp_instance.local_provider, "remove_tool"):
        mcp_instance.local_provider.remove_tool(tool_name)
        removed = True
    # Path 3: reach into the internal tool manager directly. Brittle by
    # design -- this is the fallback, not the intended path, and is called
    # out explicitly in the module docstring above for you to verify/replace.
    elif hasattr(mcp_instance, "_tool_manager") and hasattr(mcp_instance._tool_manager, "_tools"):
        mcp_instance._tool_manager._tools.pop(tool_name, None)
        removed = True
        logger.warning("deregister_tool used the internal _tool_manager._tools fallback for '%s' -- "
                        "verify against your installed mcp version and replace with a public API if one exists.",
                        tool_name)

    if not removed:
        return f"Rejected: no known way to remove '{tool_name}' from this FastMCP version -- see module docstring."

    _audit(agent_name, tool_name, "deregistered", performed_by)
    logger.info("deregistered tool %s for %s (by %s)", tool_name, agent_name, performed_by)
    return f"'{tool_name}' deregistered for {agent_name}."


def list_registered_tools(mcp_instance) -> list[str]:
    """
    Reads the CURRENT live tool list from the server instance itself
    (not from AVAILABLE_TOOLS, which is just the catalog of what CAN be
    registered) -- this is what the admin UI's "tools currently on this
    agent" view should call.
    """
    if hasattr(mcp_instance, "list_tools"):
        try:
            return [t.name for t in mcp_instance.list_tools()]  # some FastMCP versions return objects
        except TypeError:
            pass
    if hasattr(mcp_instance, "_tool_manager"):
        return list(getattr(mcp_instance._tool_manager, "_tools", {}).keys())
    return []


# ---------------------------------------------------------------------
# Claim / appeal entrypoints -- these are what turn a passenger's message
# ("my flight was disrupted, I want compensation") into a running graph
# instance, and what the platform's admin resolution endpoints call into.
# ---------------------------------------------------------------------

def submit_claim(passenger_email: str, flight_number: str, amount: float, currency: str, reason: str, submitted_by: str) -> str:
    """
    Creates a claims row and runs the claims_appeal_graph from the start.
    Exposed as an MCP tool in server.py (mcp.tool()(submit_claim)) so a
    chat agent can call it directly; also called by the platform's
    user-facing chat backend the same way.
    """
    from dbase import get_connection
    from claims_graph import CLAIMS_APPEAL_GRAPH

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
            "INSERT INTO claims (passenger_id, flight_id, amount, currency, reason, submitted_by, status) "
            "VALUES (%s,%s,%s,%s,%s,%s,'submitted')",
            (passenger["passenger_id"], flight["flight_id"], amount, currency, reason, submitted_by),
        )
        conn.commit()
        claim_id = cur2.lastrowid
        cur2.close()
        cur.close()
    finally:
        conn.close()

    result = CLAIMS_APPEAL_GRAPH.run(entity_id=claim_id)
    return f"Claim #{claim_id} created. Current status: {result['status']} (node: {result['node']})."


def submit_appeal(claim_id: int, appeal_reason: str) -> str:
    """
    Advances a claim sitting at 'appeal_open' by supplying the missing
    appeal_reason the node was waiting on (see appeal_open() in
    claims_graph.py) -- this is the real external event the state graph's
    wait is waiting for, not a poll or a timeout.
    """
    from dbase import get_connection
    from claims_graph import CLAIMS_APPEAL_GRAPH
    import json

    conn = get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT status, state_json FROM claims WHERE claim_id=%s", (claim_id,))
        row = cur.fetchone()
        cur.close()
        if row is None:
            return f"Rejected: no claim #{claim_id}."
        if row["status"] != "appeal_open":
            return f"Rejected: claim #{claim_id} is not awaiting an appeal (status={row['status']})."

        state = row["state_json"]
        if isinstance(state, str):
            state = json.loads(state) if state else {}
        state["appeal_reason"] = appeal_reason

        cur2 = conn.cursor()
        cur2.execute("UPDATE claims SET state_json=%s WHERE claim_id=%s", (json.dumps(state), claim_id))
        conn.commit()
        cur2.close()
    finally:
        conn.close()

    result = CLAIMS_APPEAL_GRAPH.run(entity_id=claim_id)
    return f"Appeal for claim #{claim_id} submitted. Current status: {result['status']} (node: {result['node']})."


AVAILABLE_TOOLS["submit_claim"] = submit_claim
AVAILABLE_TOOLS["submit_appeal"] = submit_appeal


# ---------------------------------------------------------------------
# Admin HITL / ticket resolution -- thin pass-throughs to
# state_graph.engine so the platform only needs to import ONE module for
# every admin action, regardless of which of the team's 3 graphs a given
# task/ticket belongs to (graph_name on the row tells you which).
# ---------------------------------------------------------------------

_GRAPH_REGISTRY = {}  # populated below to avoid a circular import at module load


def _graphs():
    if not _GRAPH_REGISTRY:
        from claims_graph import CLAIMS_APPEAL_GRAPH
        _GRAPH_REGISTRY["claims_appeal_graph"] = CLAIMS_APPEAL_GRAPH
        # Teammates' graphs get added here the same way once they exist:
        # _GRAPH_REGISTRY["other_graph_name"] = OTHER_GRAPH
    return _GRAPH_REGISTRY


def resolve_hitl(task_id: int, approved: bool, resolved_by: str, note: str = "") -> dict:
    from state_graph.engine import resume_after_hitl
    from dbase import get_connection

    conn = get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT graph_name FROM hitl_tasks WHERE task_id=%s", (task_id,))
        row = cur.fetchone()
        cur.close()
    finally:
        conn.close()
    if row is None:
        raise ValueError(f"No hitl_tasks row with task_id={task_id}")

    graph = _graphs()[row["graph_name"]]
    return resume_after_hitl(graph, task_id, approved, resolved_by, note)


def resolve_ticket(ticket_id: int, resolved_by: str, note: str = "") -> dict:
    from state_graph.engine import resume_after_ticket
    from dbase import get_connection

    conn = get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT graph_name FROM tickets WHERE ticket_id=%s", (ticket_id,))
        row = cur.fetchone()
        cur.close()
    finally:
        conn.close()
    if row is None:
        raise ValueError(f"No tickets row with ticket_id={ticket_id}")

    graph = _graphs()[row["graph_name"]]
    return resume_after_ticket(graph, ticket_id, resolved_by, note)
