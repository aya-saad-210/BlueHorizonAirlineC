# state_graph/engine.py

from __future__ import annotations

import json
import traceback
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

from mcp_server.dbase import get_connection


class Outcome(Enum):
    CONTINUE = "continue"
    DONE = "done"
    HITL = "hitl"
    TICKET = "ticket"


@dataclass
class NodeResult:
    outcome: Outcome
    next_node: Optional[str] = None
    state_patch: dict = field(default_factory=dict)
    hitl_question: Optional[str] = None
    hitl_options: Optional[dict] = None
    ticket_error: Optional[str] = None


@dataclass
class GraphContext:
    entity_id: str
    graph_name: str
    resumed_hitl_decision: Optional[dict] = None


NodeFn = Callable[[dict, GraphContext], NodeResult]


class StateGraph:

    def __init__(
        self,
        name: str,
        nodes: dict[str, NodeFn],
        start_node: str,
        entity_table: str,
    ):
        self.name = name
        self.nodes = nodes
        self.start_node = start_node
        self.entity_table = entity_table

    # ---------------------------------------------------------
    # Persistence
    # ---------------------------------------------------------

    def _load(self, conn, entity_id: str) -> tuple[str, dict]:

        cur = conn.cursor(dictionary=True)

        cur.execute(
            f"""
            SELECT current_node, state_json
            FROM {self.entity_table}
            WHERE claim_id = %s
            """,
            (entity_id,),
        )

        row = cur.fetchone()
        cur.close()

        if row is None:
            raise ValueError(
                f"No {self.entity_table} row with claim_id={entity_id}"
            )

        node_name = row["current_node"] or self.start_node

        state = row["state_json"]

        if isinstance(state, str):
            state = json.loads(state) if state else {}

        return node_name, (state or {})

    # ---------------------------------------------------------
    # Checkpoint
    # ---------------------------------------------------------

    def _checkpoint(
        self,
        conn,
        entity_id: str,
        node_name: str,
        state: dict,
        status: str,
    ):
        state_json = json.dumps(
            state,
            default=str
        )

        claim_status = self._claim_status(status)

        cur = conn.cursor()

        # ---------------------------------------------------------
        # 1. Update the claim's current state
        # ---------------------------------------------------------
        cur.execute(
            """
            UPDATE claims
            SET
                current_node = %s,
                state_json = %s,
                status = %s
            WHERE claim_id = %s
            """,
            (
                node_name,
                state_json,
                claim_status,
                entity_id,
            ),
        )

        # ---------------------------------------------------------
        # 2. Update the single persistent checkpoint
        #
        # claim_checkpoints has claim_id as PRIMARY KEY,
        # so we keep the LATEST checkpoint for this claim.
        # The version number tells us how many transitions/checkpoints
        # have happened.
        # ---------------------------------------------------------
        cur.execute(
            """
            SELECT version
            FROM claim_checkpoints
            WHERE claim_id = %s
            """,
            (entity_id,),
        )

        row = cur.fetchone()

        if row is None:
            version = 0

            cur.execute(
                """
                INSERT INTO claim_checkpoints
                (
                    claim_id,
                    state_json,
                    status,
                    version,
                    updated_at
                )
                VALUES
                (
                    %s,
                    %s,
                    %s,
                    %s,
                    NOW()
                )
                """,
                (
                    entity_id,
                    state_json,
                    status,
                    version,
                ),
            )

        else:
            version = int(row[0]) + 1

            cur.execute(
                """
                UPDATE claim_checkpoints
                SET
                    state_json = %s,
                    status = %s,
                    version = %s,
                    updated_at = NOW()
                WHERE claim_id = %s
                """,
                (
                    state_json,
                    status,
                    version,
                    entity_id,
                ),
            )

        conn.commit()
        cur.close()
    # ---------------------------------------------------------
    # Convert engine status -> claims status
    # ---------------------------------------------------------

    def _claim_status(self, status: str) -> str:

        mapping = {
            "running": "under_review",
            "awaiting_admin": "awaiting_admin",
            "ticket_open": "ticket_open",
            "resolved": "resolved",
        }

        return mapping.get(status, "under_review")

    # ---------------------------------------------------------
    # HITL
    # ---------------------------------------------------------

    def _open_hitl(
        self,
        conn,
        entity_id: str,
        node_name: str,
        question: str,
        options: Optional[dict],
    ):

        task_id = f"HITL-{entity_id}-{node_name}"

        cur = conn.cursor()

        cur.execute(
            """
            SELECT task_id
            FROM hitl_tasks
            WHERE claim_id = %s
              AND status = 'pending'
            LIMIT 1
            """,
            (entity_id,),
        )

        existing = cur.fetchone()

        if existing:
            cur.close()
            return existing[0]

        cur.execute(
            """
            INSERT INTO hitl_tasks
            (
                task_id,
                claim_id,
                agent_name,
                reason,
                status,
                state_snapshot_json,
                decision_json,
                created_at
            )
            VALUES
            (
                %s,
                %s,
                %s,
                %s,
                'pending',
                %s,
                NULL,
                NOW()
            )
            """,
            (
                task_id,
                entity_id,
                self.name,
                node_name,
                json.dumps(options or {}, default=str),
            ),
        )

        conn.commit()
        cur.close()

        return task_id

    # ---------------------------------------------------------
    # Ticket
    # ---------------------------------------------------------

    def _open_ticket(
        self,
        conn,
        entity_id: str,
        node_name: str,
        error_message: str,
    ):

        ticket_id = f"TICKET-{entity_id}-{node_name}"

        cur = conn.cursor()

        cur.execute(
            """
            INSERT INTO tickets
            (
                ticket_id,
                claim_id,
                agent_name,
                failed_node,
                error_summary,
                error_traceback,
                status,
                created_at
            )
            VALUES
            (
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                'open',
                NOW()
            )
            """,
            (
                ticket_id,
                entity_id,
                self.name,
                node_name,
                error_message,
                error_message,
            ),
        )

        conn.commit()
        cur.close()

        return ticket_id

    # ---------------------------------------------------------
    # Main run
    # ---------------------------------------------------------

    def run(
        self,
        entity_id: str,
        resumed_hitl_decision: Optional[dict] = None,
    ) -> dict:

        conn = get_connection()

        try:

            node_name, state = self._load(
                conn,
                entity_id,
            )

            ctx = GraphContext(
                entity_id=entity_id,
                graph_name=self.name,
                resumed_hitl_decision=resumed_hitl_decision,
            )

            while True:

                node_fn = self.nodes.get(node_name)

                if node_fn is None:
                    raise ValueError(
                        f"Graph '{self.name}' has no node named '{node_name}'"
                    )

                # -------------------------------------------------
                # Execute node
                # -------------------------------------------------

                try:

                    result = node_fn(
                        state,
                        ctx,
                    )

                except Exception as exc:

                    err = (
                        f"{type(exc).__name__}: {exc}\n"
                        f"{traceback.format_exc()}"
                    )

                    self._checkpoint(
                        conn,
                        entity_id,
                        node_name,
                        state,
                        "ticket_open",
                    )

                    ticket_id = self._open_ticket(
                        conn,
                        entity_id,
                        node_name,
                        err,
                    )

                    return {
                        "status": "ticket_open",
                        "node": node_name,
                        "ticket_id": ticket_id,
                        "state": state,
                    }

                # -------------------------------------------------
                # Apply state
                # -------------------------------------------------

                state = {
                    **state,
                    **result.state_patch,
                }

                ctx.resumed_hitl_decision = None

                # -------------------------------------------------
                # DONE
                # -------------------------------------------------

                if result.outcome == Outcome.DONE:

                    self._checkpoint(
                        conn,
                        entity_id,
                        node_name,
                        state,
                        "resolved",
                    )

                    return {
                        "status": "resolved",
                        "node": node_name,
                        "state": state,
                    }

                # -------------------------------------------------
                # HITL
                # -------------------------------------------------

                if result.outcome == Outcome.HITL:

                    self._checkpoint(
                        conn,
                        entity_id,
                        node_name,
                        state,
                        "awaiting_admin",
                    )

                    task_id = self._open_hitl(
                        conn,
                        entity_id,
                        node_name,
                        result.hitl_question
                        or "Approval required.",
                        result.hitl_options,
                    )

                    return {
                        "status": "awaiting_admin",
                        "node": node_name,
                        "task_id": task_id,
                        "state": state,
                    }

                # -------------------------------------------------
                # TICKET
                # -------------------------------------------------

                if result.outcome == Outcome.TICKET:

                    self._checkpoint(
                        conn,
                        entity_id,
                        node_name,
                        state,
                        "ticket_open",
                    )

                    ticket_id = self._open_ticket(
                        conn,
                        entity_id,
                        node_name,
                        result.ticket_error
                        or "Node reported failure.",
                    )

                    return {
                        "status": "ticket_open",
                        "node": node_name,
                        "ticket_id": ticket_id,
                        "state": state,
                    }

                # -------------------------------------------------
                # CONTINUE
                # -------------------------------------------------

                if not result.next_node:
                    raise ValueError(
                        f"Node '{node_name}' returned CONTINUE "
                        f"without next_node"
                    )

                node_name = result.next_node

                # IMPORTANT:
                # checkpoint BEFORE executing next node.
                self._checkpoint(
                    conn,
                    entity_id,
                    node_name,
                    state,
                    "running",
                )

        finally:
            conn.close()


# =============================================================
# HITL RESUME
# =============================================================

def resume_after_hitl(
    graph: StateGraph,
    task_id: str,
    approved: bool,
    resolved_by: str,
    note: str = "",
):

    conn = get_connection()

    try:

        cur = conn.cursor(dictionary=True)

        cur.execute(
            """
            SELECT *
            FROM hitl_tasks
            WHERE task_id = %s
            """,
            (task_id,),
        )

        task = cur.fetchone()

        if task is None:
            raise ValueError(
                f"No HITL task with task_id={task_id}"
            )

        if task["status"] != "pending":
            raise ValueError(
                f"HITL task {task_id} is already resolved"
            )

        decision = {
            "approved": approved,
            "note": note,
            "resolved_by": resolved_by,
        }

        cur2 = conn.cursor()

        cur2.execute(
            """
            UPDATE hitl_tasks
            SET
                status = 'resolved',
                decision_json = %s,
                resolved_by = %s,
                resolved_at = NOW()
            WHERE task_id = %s
            """,
            (
                json.dumps(decision),
                resolved_by,
                task_id,
            ),
        )

        conn.commit()

        cur2.close()
        cur.close()

        return graph.run(
            entity_id=task["claim_id"],
            resumed_hitl_decision=decision,
        )

    finally:
        conn.close()


# =============================================================
# TICKET RESUME
# =============================================================

def resume_after_ticket(
    graph: StateGraph,
    ticket_id: str,
    resolved_by: str,
    note: str = "",
):

    conn = get_connection()

    try:

        cur = conn.cursor(dictionary=True)

        cur.execute(
            """
            SELECT *
            FROM tickets
            WHERE ticket_id = %s
            """,
            (ticket_id,),
        )

        ticket = cur.fetchone()

        if ticket is None:
            raise ValueError(
                f"No ticket with ticket_id={ticket_id}"
            )

        if ticket["status"] == "resolved":
            raise ValueError(
                f"Ticket {ticket_id} is already resolved"
            )

        cur2 = conn.cursor()

        cur2.execute(
            """
            UPDATE tickets
            SET
                status = 'resolved',
                resolution_note = %s,
                resolved_by = %s,
                resolved_at = NOW()
            WHERE ticket_id = %s
            """,
            (
                note,
                resolved_by,
                ticket_id,
            ),
        )

        conn.commit()

        cur2.close()
        cur.close()

    finally:
        conn.close()

    return graph.run(
        entity_id=ticket["claim_id"]
    )


# =============================================================
# Admin helpers
# =============================================================

def list_pending_hitl(
    graph_name: Optional[str] = None,
):

    conn = get_connection()

    try:

        cur = conn.cursor(dictionary=True)

        if graph_name:

            cur.execute(
                """
                SELECT *
                FROM hitl_tasks
                WHERE status = 'pending'
                  AND agent_name = %s
                ORDER BY created_at
                """,
                (graph_name,),
            )

        else:

            cur.execute(
                """
                SELECT *
                FROM hitl_tasks
                WHERE status = 'pending'
                ORDER BY created_at
                """
            )

        rows = cur.fetchall()

        cur.close()

        return rows

    finally:
        conn.close()


def list_open_tickets(
    graph_name: Optional[str] = None,
):

    conn = get_connection()

    try:

        cur = conn.cursor(dictionary=True)

        if graph_name:

            cur.execute(
                """
                SELECT *
                FROM tickets
                WHERE status IN ('open', 'investigating')
                  AND agent_name = %s
                ORDER BY created_at
                """,
                (graph_name,),
            )

        else:

            cur.execute(
                """
                SELECT *
                FROM tickets
                WHERE status IN ('open', 'investigating')
                ORDER BY created_at
                """
            )

        rows = cur.fetchall()

        cur.close()

        return rows

    finally:
        conn.close()