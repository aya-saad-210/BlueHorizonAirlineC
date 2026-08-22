# platform/backend/admin_api.py  (REVISED after reconciling with Person A's shared engine)
#
# WHAT CHANGED:
#   - /api/hitl* now reads/writes the SHARED hitl_tasks/graph_runs schema
#     and resolves through tools_admin.resolve_hitl(), which dispatches to
#     whichever graph the task belongs to (see _HITL_DISPATCH there).
#   - /api/tickets* is REMOVED. Tickets are Person C's system and don't
#     exist yet in the shared schema -- this file no longer pretends
#     otherwise. Re-add once that table/API exists.
#   - /api/claims/{id}/checkpoints now calls checkpointer.MySQLCheckpointer
#     ().history(run_id) directly -- reuses Person A's class instead of
#     hand-rolling the same query against a table this file doesn't own.

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
for p in (_REPO_ROOT, _REPO_ROOT / "mcp_server", _REPO_ROOT / "state_graph", _REPO_ROOT / "agent"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import tools_admin  # noqa: E402
from checkpointer import MySQLCheckpointer  # noqa: E402
from dbase import get_connection  # noqa: E402

router = APIRouter(prefix="/api")
AGENT_NAME = "irops_assistant"


def _get_mcp_instance():
    from server import mcp
    return mcp


# ---------------------------------------------------------------------
# Agents & tools -- unchanged
# ---------------------------------------------------------------------

@router.get("/agents")
def list_agents():
    mcp = _get_mcp_instance()
    return [{"agent_name": AGENT_NAME, "live_tools": tools_admin.list_registered_tools(mcp)}]


@router.get("/tools/catalog")
def tools_catalog():
    mcp = _get_mcp_instance()
    live = set(tools_admin.list_registered_tools(mcp))
    return [{"tool_name": name, "registered": name in live} for name in tools_admin.AVAILABLE_TOOLS]


class ToolActionRequest(BaseModel):
    tool_name: str
    performed_by: str


@router.post("/tools/register")
def register_tool(body: ToolActionRequest):
    mcp = _get_mcp_instance()
    msg = tools_admin.register_tool(mcp, AGENT_NAME, body.tool_name, body.performed_by)
    if msg.startswith("Rejected"):
        raise HTTPException(status_code=400, detail=msg)
    return {"message": msg}


@router.post("/tools/deregister")
def deregister_tool(body: ToolActionRequest):
    mcp = _get_mcp_instance()
    msg = tools_admin.deregister_tool(mcp, AGENT_NAME, body.tool_name, body.performed_by)
    if msg.startswith("Rejected"):
        raise HTTPException(status_code=400, detail=msg)
    return {"message": msg}


@router.get("/tools/audit-log")
def tools_audit_log(limit: int = 50):
    conn = get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM agent_tools_registry ORDER BY performed_at DESC LIMIT %s", (limit,))
        rows = cur.fetchall()
        cur.close()
        return rows
    finally:
        conn.close()


# ---------------------------------------------------------------------
# RAG documents -- unchanged, still pending rag/vector_store.py etc.
# ---------------------------------------------------------------------

@router.get("/rag/documents")
def list_rag_documents():
    conn = get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM rag_documents_registry WHERE status='active' ORDER BY added_at DESC")
        rows = cur.fetchall()
        cur.close()
        return rows
    finally:
        conn.close()


class RagDocumentRequest(BaseModel):
    filename: str
    doc_type: str
    added_by: str


@router.post("/rag/documents", status_code=501)
def add_rag_document(body: RagDocumentRequest):
    raise HTTPException(status_code=501, detail="Not wired yet -- needs rag/vector_store.py + ingest.py + chunking.py.")


@router.delete("/rag/documents/{doc_id}", status_code=501)
def remove_rag_document(doc_id: int):
    raise HTTPException(status_code=501, detail="Not wired yet -- same reason as add_rag_document above.")


# ---------------------------------------------------------------------
# HITL inbox -- shared schema, generic dispatcher
# ---------------------------------------------------------------------

@router.get("/hitl")
def hitl_inbox(graph_name: str | None = None):
    return tools_admin.list_pending_hitl(graph_name)


class HitlResolveRequest(BaseModel):
    approved: bool
    decided_by: str
    note: str = ""


@router.post("/hitl/{task_id}/resolve")
def resolve_hitl(task_id: int, body: HitlResolveRequest):
    try:
        message = tools_admin.resolve_hitl(task_id, body.approved, body.decided_by, body.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"message": message}


# NOTE: /api/tickets* intentionally removed -- see module docstring.
# Add back once Person C's ticket system exists, following the same
# thin-pass-through shape as the HITL routes above.


# ---------------------------------------------------------------------
# Claims
# ---------------------------------------------------------------------

@router.get("/claims")
def list_claims(limit: int = 50):
    conn = get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT c.claim_id, c.run_id, c.amount, c.currency, c.reason, c.final_status,
                   c.created_at, c.updated_at, p.full_name AS passenger_name, f.flight_number
            FROM claims c
            JOIN passengers p ON c.passenger_id = p.passenger_id
            JOIN flights f ON c.flight_id = f.flight_id
            ORDER BY c.updated_at DESC LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()
        cur.close()
        return rows
    finally:
        conn.close()


@router.get("/claims/{claim_id}/checkpoints")
def claim_checkpoints(claim_id: int):
    """Reuses Person A's MySQLCheckpointer.history() directly -- this file
    does not know or care about graph_checkpoints' column layout, only
    that checkpointer.py does."""
    conn = get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT run_id FROM claims WHERE claim_id=%s", (claim_id,))
        row = cur.fetchone()
        cur.close()
    finally:
        conn.close()

    if row is None:
        raise HTTPException(status_code=404, detail=f"No claim #{claim_id}")
    if row["run_id"] is None:
        return []

    history = MySQLCheckpointer().history(row["run_id"])
    return [
        {"node_name": h.node_name, "status": h.status, "step_number": h.step_number}
        for h in history
    ]


class SubmitClaimRequest(BaseModel):
    passenger_email: str
    flight_number: str
    amount: float
    currency: str = "USD"
    reason: str
    submitted_by: str


@router.post("/claims")
def submit_claim(body: SubmitClaimRequest):
    from claims_graph import submit_claim as _submit_claim

    msg = _submit_claim(
        body.passenger_email, body.flight_number, body.amount, body.currency, body.reason, body.submitted_by
    )
    if msg.startswith("Rejected"):
        raise HTTPException(status_code=400, detail=msg)
    return {"message": msg}


class SubmitAppealRequest(BaseModel):
    appeal_reason: str


@router.post("/claims/{claim_id}/appeal")
def submit_appeal(claim_id: int, body: SubmitAppealRequest):
    from claims_graph import submit_appeal as _submit_appeal

    msg = _submit_appeal(claim_id, body.appeal_reason)
    if msg.startswith("Rejected"):
        raise HTTPException(status_code=400, detail=msg)
    return {"message": msg}
