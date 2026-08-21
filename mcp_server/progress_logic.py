# progress_logic.py
# This is the PROGRESS TRACKING concern: a tool whose work genuinely takes a
# while (rebooking every passenger on a cancelled flight, one at a time) that
# reports real intermediate progress instead of leaving the client blocked
# with no feedback until the whole batch finishes.
#
# This reuses the same validation rules as the single rebook_passenger tool
# in tools_write.py (only disrupted/cancelled/delayed flights, no double
# booking), just applied to every affected booking in a loop.

import asyncio
from mcp.server.fastmcp import Context
from mcp_server.dbase import get_connection


async def rebook_all_passengers_on_flight(
    old_flight_number: str,
    new_flight_number: str,
    requested_by: str,
    ctx: Context,
) -> str:
    """
    Rebooks every confirmed passenger currently on a disrupted/cancelled flight
    onto a new flight, one at a time, reporting progress as it goes.

    old_flight_number: the disrupted/cancelled flight to move passengers off of, e.g. BH303
    new_flight_number: the flight to move passengers onto, e.g. BH101
    requested_by: the ops agent ID making this request (required for authorization)
    """
    if not requested_by or not requested_by.strip() or not requested_by.startswith("agent_"):
        return "Rejected: batch rebooking requires an authenticated ops agent (expected an ID like agent_014)."

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute(
            "SELECT flight_id, status FROM flights WHERE flight_number = %s",
            (old_flight_number,),
        )
        old_flight = cursor.fetchone()
        if old_flight is None:
            return f"Rejected: no flight found with number {old_flight_number}."

        if old_flight["status"] not in ("disrupted", "delayed", "cancelled"):
            return (
                f"Rejected: flight {old_flight_number} has status '{old_flight['status']}'. "
                "Batch rebooking is only allowed for disrupted, delayed, or cancelled flights."
            )

        cursor.execute(
            "SELECT flight_id, status FROM flights WHERE flight_number = %s",
            (new_flight_number,),
        )
        new_flight = cursor.fetchone()
        if new_flight is None:
            return f"Rejected: no flight found with number {new_flight_number}."

        if new_flight["status"] not in ("scheduled", "delayed"):
            return (
                f"Rejected: cannot rebook onto flight {new_flight_number} because its status "
                f"is '{new_flight['status']}'."
            )

        cursor.execute(
            """
            SELECT b.booking_id, b.passenger_id, b.fare_class, p.full_name
            FROM bookings b
            JOIN passengers p ON b.passenger_id = p.passenger_id
            WHERE b.flight_id = %s AND b.booking_status = 'confirmed'
            """,
            (old_flight["flight_id"],),
        )
        affected_bookings = cursor.fetchall()

        if not affected_bookings:
            return f"No confirmed bookings found on flight {old_flight_number} to rebook."

        total = len(affected_bookings)
        moved = []
        skipped = []

        for index, booking in enumerate(affected_bookings, start=1):
            await ctx.report_progress(
                progress=index,
                total=total,
                message=f"Rebooking {booking['full_name']} ({index}/{total})",
            )

            cursor.execute(
                """
                SELECT booking_id FROM bookings
                WHERE passenger_id = %s AND flight_id = %s AND booking_status = 'confirmed'
                """,
                (booking["passenger_id"], new_flight["flight_id"]),
            )
            if cursor.fetchone() is not None:
                skipped.append(booking["full_name"])
                continue

            cursor.execute(
                "UPDATE bookings SET booking_status = 'rebooked' WHERE booking_id = %s",
                (booking["booking_id"],),
            )
            cursor.execute(
                """
                INSERT INTO bookings (passenger_id, flight_id, seat_number, fare_class, booking_status)
                VALUES (%s, %s, 'TBD', %s, 'confirmed')
                """,
                (booking["passenger_id"], new_flight["flight_id"], booking["fare_class"]),
            )
            moved.append(booking["full_name"])

            await asyncio.sleep(0.3)

        conn.commit()

        summary = f"Rebooked {len(moved)}/{total} passengers from {old_flight_number} to {new_flight_number}."
        if skipped:
            summary += f" Skipped (already booked on new flight): {', '.join(skipped)}."

        return summary

    finally:
        cursor.close()
        conn.close()