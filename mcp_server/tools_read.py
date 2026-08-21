# tools_read.py
# All READ-ONLY tools live here. These tools never change state in the database,
# so they don't need authorization checks or elicitation -- they're safe by nature.
#
# This file only defines plain functions. The actual @mcp.tool() registration happens
# in server.py, so this file has no dependency on the FastMCP instance itself.

from mcp_server.dbase import get_connection


def get_flight_status(flight_number: str) -> str:
    """
    Returns the status of a specific flight (scheduled / delayed /
    cancelled / disrupted) along with the disruption reason, if any.

    flight_number: the flight number, e.g. BH101
    """
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    query = """
        SELECT flight_number, origin_airport, destination_airport,
               scheduled_departure, status, disruption_reason
        FROM flights
        WHERE flight_number = %s
    """
    cursor.execute(query, (flight_number,))
    result = cursor.fetchone()

    cursor.close()
    conn.close()

    if result is None:
        return f"No flight found with number: {flight_number}"

    return (
        f"Flight {result['flight_number']}: "
        f"from {result['origin_airport']} to {result['destination_airport']} - "
        f"Status: {result['status']} - "
        f"Reason: {result['disruption_reason'] or 'None'}"
    )


def get_passenger_booking(passenger_email: str) -> str:
    """
    Returns all bookings for a passenger using their email address.

    passenger_email: the email address the passenger registered with
    """
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    query = """
        SELECT b.booking_id, b.seat_number, b.fare_class, b.booking_status,
               f.flight_number, f.status AS flight_status
        FROM bookings b
        JOIN passengers p ON b.passenger_id = p.passenger_id
        JOIN flights f ON b.flight_id = f.flight_id
        WHERE p.email = %s
    """
    cursor.execute(query, (passenger_email,))
    results = cursor.fetchall()

    cursor.close()
    conn.close()

    if not results:
        return f"No bookings found for this passenger: {passenger_email}"

    lines = []
    for row in results:
        lines.append(
            f"- Flight {row['flight_number']} | Seat {row['seat_number']} | "
            f"{row['fare_class']} | Booking status: {row['booking_status']} | "
            f"Flight status: {row['flight_status']}"
        )

    return "\n".join(lines)