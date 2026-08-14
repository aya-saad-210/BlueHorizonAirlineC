# planning/mcp_tools_adapter.py
#
# The Planning Agent must reuse the existing MCP server/tools/database, not
# rebuild them (spec sections 16-17). This file is the ONLY place the
# Planning Agent touches mcp_server/* or the database directly.
#
# Two kinds of things live here:
#
#  1. Thin pass-throughs to the EXISTING tool functions in
#     mcp_server/tools_read.py and mcp_server/tools_write.py. Nothing about
#     those functions is changed -- this just imports and calls them.
#
#  2. A small number of NEW read-only queries (list_affected_passengers,
#     list_candidate_replacement_flights, get_crew_duty_totals,
#     get_existing_compensation) that the planning problem genuinely needs
#     and that no existing tool exposes (the existing read tools are keyed
#     by a single passenger email or flight number, not "every passenger on
#     flight X" or "every scheduled flight to airport Y"). These reuse the
#     same dbase.get_connection() the rest of the server uses -- no new
#     database, no new connection logic.
#
# StubSupervisorContext exists ONLY for automated grounded-evaluation runs
# (planning_eval/) where nothing is watching a real MCP client to answer an
# elicitation prompt. It implements the same `.elicit(message, schema)`
# surface FastMCP's real Context provides (see mcp_server/elicitation_logic.py)
# so assign_reserve_crew/issue_compensation run unmodified, but the
# "supervisor" answer is a deterministic, declared-up-front policy instead
# of a live human -- every eval case states its policy explicitly so
# nobody can mistake a stub decision for a real approval.

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# mcp_server/*.py uses bare `from dbase import get_connection` (not a
# package-relative import), so it expects mcp_server/ on sys.path. We add it
# here rather than editing any existing file.
_MCP_SERVER_DIR = Path(__file__).resolve().parent.parent / "mcp_server"
if str(_MCP_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_MCP_SERVER_DIR))

from dbase import get_connection  # noqa: E402  (existing, shared DB helper)
from tools_read import get_flight_status, get_passenger_booking  # noqa: E402
from tools_write import (  # noqa: E402
    assign_reserve_crew,
    issue_compensation,
    rebook_passenger,
)


# ---------------------------------------------------------------------
# New read-only queries (additive; do not modify tools_read.py)
# ---------------------------------------------------------------------

def list_affected_passengers(flight_number: str) -> list[dict]:
    """Every passenger with a still-'confirmed' booking on a disrupted/
    delayed/cancelled flight -- the planning agent's starting point for
    'who needs to be rebooked'."""
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT b.booking_id, p.passenger_id, p.full_name, p.email,
                   b.fare_class, b.booking_status
            FROM bookings b
            JOIN passengers p ON b.passenger_id = p.passenger_id
            JOIN flights f ON b.flight_id = f.flight_id
            WHERE f.flight_number = %s AND b.booking_status = 'confirmed'
            """,
            (flight_number,),
        )
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()


def list_candidate_replacement_flights(origin: str, destination: str) -> list[dict]:
    """Every 'scheduled' or 'delayed' flight on the same route -- the
    candidate set Tree of Thoughts searches over when more than one
    replacement option exists."""
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT flight_number, scheduled_departure, scheduled_arrival, status
            FROM flights
            WHERE origin_airport = %s AND destination_airport = %s
              AND status IN ('scheduled', 'delayed')
            ORDER BY scheduled_departure
            """,
            (origin, destination),
        )
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()


def get_crew_duty_totals(crew_id: int) -> dict:
    """Today's logged flying/duty hours for one crew member -- the exact
    figures assign_reserve_crew checks internally, exposed read-only so the
    planning layer (routing + grounded environment) can reason about them
    BEFORE attempting a write."""
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT COALESCE(SUM(hours_flown), 0) AS total_flown,
                   COALESCE(SUM(hours_on_duty), 0) AS total_duty
            FROM duty_time_logs
            WHERE crew_id = %s AND log_date = CURDATE()
            """,
            (crew_id,),
        )
        return cursor.fetchone()
    finally:
        cursor.close()
        conn.close()


def find_reserve_crew(base_airport: str, role: str) -> list[dict]:
    """Crew based at the disrupted flight's origin, by role -- candidates
    for assign_reserve_crew."""
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT crew_id, full_name, role FROM crew WHERE base_airport = %s AND role = %s",
            (base_airport, role),
        )
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()


def list_crew_assigned_to_flight(flight_number: str) -> list[dict]:
    """Crew currently assigned (original or reserve) to a flight, with
    today's real duty totals joined in -- what the dynamic-decomposition
    'check crew duty-hour limits' step actually grounds itself in."""
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT c.crew_id, c.full_name, c.role, ca.assignment_type,
                   COALESCE(SUM(d.hours_flown), 0) AS total_flown,
                   COALESCE(SUM(d.hours_on_duty), 0) AS total_duty
            FROM crew_assignments ca
            JOIN crew c ON ca.crew_id = c.crew_id
            JOIN flights f ON ca.flight_id = f.flight_id
            LEFT JOIN duty_time_logs d ON d.crew_id = c.crew_id AND d.log_date = CURDATE()
            WHERE f.flight_number = %s
            GROUP BY c.crew_id, c.full_name, c.role, ca.assignment_type
            """,
            (flight_number,),
        )
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()


def get_existing_compensation(passenger_email: str, flight_number: str) -> dict | None:
    """Whether this passenger already has pending/approved compensation for
    this flight -- lets the grounded environment (and Reflexion's second
    trial) check the exact condition issue_compensation() will reject on,
    BEFORE spending a write attempt on it."""
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT c.compensation_id, c.amount, c.status
            FROM compensation c
            JOIN passengers p ON c.passenger_id = p.passenger_id
            JOIN flights f ON c.flight_id = f.flight_id
            WHERE p.email = %s AND f.flight_number = %s
              AND c.status IN ('pending', 'approved')
            """,
            (passenger_email, flight_number),
        )
        return cursor.fetchone()
    finally:
        cursor.close()
        conn.close()


def find_booking_id(passenger_email: str, flight_number: str) -> int | None:
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT b.booking_id
            FROM bookings b
            JOIN passengers p ON b.passenger_id = p.passenger_id
            JOIN flights f ON b.flight_id = f.flight_id
            WHERE p.email = %s AND f.flight_number = %s AND b.booking_status = 'confirmed'
            """,
            (passenger_email, flight_number),
        )
        row = cursor.fetchone()
        return row["booking_id"] if row else None
    finally:
        cursor.close()
        conn.close()


# ---------------------------------------------------------------------
# Elicitation stub for unattended/automated grounded-evaluation runs only.
# The interactive demo (planning/cli.py --interactive) uses the REAL
# FastMCP Context/client instead -- see planning/README section "Two ways
# to run this".
# ---------------------------------------------------------------------

@dataclass
class _ElicitResult:
    action: Literal["accept", "decline", "cancel"]
    data: object | None


class StubSupervisorContext:
    """Deterministic stand-in for FastMCP's Context, used only by
    planning_eval/ and by planning/environment.py's automated grounded
    checks. `policy` must be declared explicitly by the caller for every
    eval case -- there is no hidden default that silently approves
    everything."""

    def __init__(self, policy: Literal["approve", "decline"], supervisor_id: str = "sup_eval"):
        self.policy = policy
        self.supervisor_id = supervisor_id

    async def elicit(self, message: str, schema):
        if self.policy == "approve":
            data = schema(approved=True, supervisor_id=self.supervisor_id, note="stub: eval-policy approve")
            return _ElicitResult(action="accept", data=data)
        data = schema(approved=False, supervisor_id=self.supervisor_id, note="stub: eval-policy decline")
        return _ElicitResult(action="accept", data=data)


__all__ = [
    "get_flight_status",
    "get_passenger_booking",
    "assign_reserve_crew",
    "issue_compensation",
    "rebook_passenger",
    "list_affected_passengers",
    "list_candidate_replacement_flights",
    "list_crew_assigned_to_flight",
    "get_crew_duty_totals",
    "find_reserve_crew",
    "get_existing_compensation",
    "find_booking_id",
    "StubSupervisorContext",
]
