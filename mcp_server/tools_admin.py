# mcp_server/tools_admin.py  (REVISED after reconciling with Person A's shared state-graph engine)


from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger("blue_horizon.admin")

_REPO_ROOT = Path(__file__).resolve().parent.parent
_STATE_GRAPH_DIR = _REPO_ROOT / "state_graph"
if str(_STATE_GRAPH_DIR) not in sys.path:
    sys.path.insert(0, str(_STATE_GRAPH_DIR))

from tools_read import get_flight_status, get_passenger_booking
from tools_write import assign_reserve_crew, issue_compensation, rebook_passenger
from progress_logic import rebook_all_passengers_on_flight
from sampling_logic import generate_disruption_notice
from tools_search import search_knowledge_base
from rag_tool import answer_policy_question
from tools_planning import plan_irops_response
from claims_graph import submit_claim, submit_appeal, submit_claim_hitl_decision  # noqa: E402

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
    "submit_claim": submit_claim,
    "submit_appeal": submit_appeal,
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
    mcp_instance.add_tool(AVAILABLE_TOOLS[tool_name])
    _audit(agent_name, tool_name, "registered", performed_by)
    return f"'{tool_name}' registered for {agent_name}."


def deregister_tool(mcp_instance, agent_name: str, tool_name: str, performed_by: str) -> str:
    if tool_name not in AVAILABLE_TOOLS:
        return f"Rejected: '{tool_name}' is not a known tool for this server."

    removed = False
    if hasattr(mcp_instance, "remove_tool"):
        mcp_instance.remove_tool(tool_name)
        removed = True
    elif hasattr(mcp_instance, "local_provider") and hasattr(mcp_instance.local_provider, "remove_tool"):
        mcp_instance.local_provider.remove_tool(tool_name)
        removed = True
    elif hasattr(mcp_instance, "_tool_manager") and hasattr(mcp_instance._tool_manager, "_tools"):
        mcp_instance._tool_manager._tools.pop(tool_name, None)
        removed = True
        logger.warning("deregister_tool used the internal _tool_manager._tools fallback for '%s'", tool_name)

    if not removed:
        return f"Rejected: no known way to remove '{tool_name}' from this FastMCP version."

    _audit(agent_name, tool_name, "deregistered", performed_by)
    return f"'{tool_name}' deregistered for {agent_name}."


def list_registered_tools(mcp_instance) -> list[str]:
    if hasattr(mcp_instance, "list_tools"):
        try:
            return [t.name for t in mcp_instance.list_tools()]
        except TypeError:
            pass
    if hasattr(mcp_instance, "_tool_manager"):
        return list(getattr(mcp_instance._tool_manager, "_tools", {}).keys())
    return []


# ---------------------------------------------------------------------
# Generic HITL resolution over the SHARED schema. Add one entry per graph.
# ---------------------------------------------------------------------

_HITL_DISPATCH = {
    "passenger_claims": submit_claim_hitl_decision,
    # "crew_reassignment": crew_reassignment_graph.submit_hitl_decision,  # Person A
    #     imported lazily below to avoid a hard dependency if that file
    #     isn't present in every checkout yet.
    # "aog_recovery": ...,  # Person C, once it exists
}


def _resolve_dispatch(graph_name: str):
    if graph_name in _HITL_DISPATCH:
        return _HITL_DISPATCH[graph_name]
    if graph_name == "crew_reassignment":
        from crew_reassignment_graph import submit_hitl_decision
        return submit_hitl_decision
    raise ValueError(f"No HITL dispatcher registered for graph '{graph_name}' -- add one to _HITL_DISPATCH.")


def resolve_hitl(task_id: int, approved: bool, decided_by: str, note: str = "") -> str:
    """The ONE function the platform's admin HITL inbox calls, regardless
    of which of the team's graphs the pending task belongs to."""
    from dbase import get_connection

    conn = get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT h.run_id, h.status, r.graph_name
            FROM hitl_tasks h
            JOIN graph_runs r ON r.run_id = h.run_id
            WHERE h.hitl_task_id = %s
            """,
            (task_id,),
        )
        row = cur.fetchone()
        cur.close()
    finally:
        conn.close()

    if row is None:
        raise ValueError(f"No hitl_tasks row with hitl_task_id={task_id}")
    if row["status"] != "pending":
        raise ValueError(f"hitl_tasks {task_id} is already '{row['status']}'")

    dispatch = _resolve_dispatch(row["graph_name"])
    dispatch(row["run_id"], approved, decided_by, note)
    return f"HITL task #{task_id} resolved ({'approved' if approved else 'rejected'}) for run {row['run_id']}."


def list_pending_hitl(graph_name: str | None = None) -> list[dict]:
    from dbase import get_connection

    conn = get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        # h.hitl_task_id AS task_id: the real PK column is hitl_task_id
        # (see data base/state_graph_schema.sql); aliased for the platform
        # API/frontend, which use the shorter `task_id` key.
        if graph_name:
            cur.execute(
                "SELECT h.*, h.hitl_task_id AS task_id, r.graph_name FROM hitl_tasks h "
                "JOIN graph_runs r ON r.run_id=h.run_id "
                "WHERE h.status='pending' AND r.graph_name=%s ORDER BY h.created_at",
                (graph_name,),
            )
        else:
            cur.execute(
                "SELECT h.*, h.hitl_task_id AS task_id, r.graph_name FROM hitl_tasks h "
                "JOIN graph_runs r ON r.run_id=h.run_id "
                "WHERE h.status='pending' ORDER BY h.created_at"
            )
        rows = cur.fetchall()
        cur.close()
        return rows
    finally:
        conn.close()


# NOTE: no resolve_ticket() / list_open_tickets() here anymore -- see
# module docstring. Re-add once Person C's ticket system exists, as a
# thin pass-through the same shape as resolve_hitl() above.
