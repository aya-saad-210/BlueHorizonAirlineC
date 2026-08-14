# planning_eval/reset_state.py
#
# WHY THIS EXISTS (found by actually running the suite twice, not by
# inspection): planning_eval cases call REAL write tools (rebook_passenger,
# assign_reserve_crew, issue_compensation) against the real database. Spec
# section 10 requires the fixed test suite to stay fixed across algorithm
# runs -- but a second run of the SAME case against data already mutated
# by the first run is not the same case anymore (e.g. a passenger who was
# already rebooked, or a compensation row that already exists where the
# suite's design assumes none does). Concretely: running the suite twice
# without a reset turned BH404/BH707's reflexion case from "no duplicate
# exists, trial 1 succeeds outright" into "a duplicate now exists because
# the previous run created it, trial 1 fails" -- silently changing which
# behaviour was actually being measured.
#
# This resets ONLY the rows that planning_eval's own cases can touch
# (identified by the eval's own flight numbers / seeded passengers), never
# any pre-existing Memory/RAG or original-3-flights data.

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from planning.mcp_tools_adapter import get_connection  # noqa: E402

EVAL_FLIGHT_NUMBERS = ["BH404", "BH505", "BH606", "BH707", "BH808"]
REPLACEMENT_FLIGHT_NUMBERS = ["BH108", "BH710", "BH711", "BH712"]
EVAL_PASSENGER_EMAILS = [
    "salma.nabil@example.com", "omar.farid@example.com", "nourane.tarek@example.com",
    "hady.fathallah@example.com", "rania.adly@example.com", "karim.zaki@example.com",
]


def reset_eval_state() -> None:
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        placeholders_eval = ",".join(["%s"] * len(EVAL_FLIGHT_NUMBERS))
        placeholders_repl = ",".join(["%s"] * len(REPLACEMENT_FLIGHT_NUMBERS))
        placeholders_pax = ",".join(["%s"] * len(EVAL_PASSENGER_EMAILS))

        # 1) Delete any compensation this suite issued, THEN restore the
        #    one seed row the BH808 case depends on (a pending compensation
        #    for karim.zaki on BH808) -- see data base/seed_planning_eval.sql.
        cursor.execute(
            f"""
            DELETE c FROM compensation c
            JOIN flights f ON c.flight_id = f.flight_id
            JOIN passengers p ON c.passenger_id = p.passenger_id
            WHERE f.flight_number IN ({placeholders_eval})
              AND p.email IN ({placeholders_pax})
            """,
            (*EVAL_FLIGHT_NUMBERS, *EVAL_PASSENGER_EMAILS),
        )
        cursor.execute(
            """
            INSERT INTO compensation (passenger_id, flight_id, amount, currency, reason, issued_by, status)
            SELECT p.passenger_id, f.flight_id, 120.00, 'USD', 'flight disrupted due to mechanical issue', 'agent_003', 'pending'
            FROM passengers p, flights f
            WHERE p.email = 'karim.zaki@example.com' AND f.flight_number = 'BH808'
            """
        )

        # 2) Delete any 'reserve' crew_assignments this suite created for
        #    the eval flights (the seed's 'original' assignment for BH606
        #    is left untouched).
        cursor.execute(
            f"""
            DELETE ca FROM crew_assignments ca
            JOIN flights f ON ca.flight_id = f.flight_id
            WHERE f.flight_number IN ({placeholders_eval}) AND ca.assignment_type = 'reserve'
            """,
            EVAL_FLIGHT_NUMBERS,
        )

        # 3) Delete any NEW bookings this suite created on replacement
        #    flights via rebook_passenger, then flip the original eval
        #    bookings back from 'rebooked' to 'confirmed'.
        cursor.execute(
            f"""
            DELETE b FROM bookings b
            JOIN flights f ON b.flight_id = f.flight_id
            JOIN passengers p ON b.passenger_id = p.passenger_id
            WHERE f.flight_number IN ({placeholders_repl}) AND p.email IN ({placeholders_pax})
            """,
            (*REPLACEMENT_FLIGHT_NUMBERS, *EVAL_PASSENGER_EMAILS),
        )
        cursor.execute(
            f"""
            UPDATE bookings b
            JOIN flights f ON b.flight_id = f.flight_id
            SET b.booking_status = 'confirmed'
            WHERE f.flight_number IN ({placeholders_eval}) AND b.booking_status = 'rebooked'
            """,
            EVAL_FLIGHT_NUMBERS,
        )

        conn.commit()
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    reset_eval_state()
    print("planning_eval fixture state reset to seeded values.")
