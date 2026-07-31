"""
db.py
-----
Database access layer for the MySQL database (blue_horizon_db) you set up
in phpMyAdmin/XAMPP. Every tool in server.py calls the functions here
instead of writing SQL directly in the handler — this makes it easier to
test the business logic on its own, without needing a full MCP session.

There is no raw-SQL tool exposed to the model — every query here is
parameterized (defensive: prevents SQL injection even if someone tries to
pass strange values through the tool arguments).
"""

import os
import mysql.connector
from mysql.connector import Error as MySQLError
from dotenv import load_dotenv

load_dotenv()

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", "blue_horizon_db"),
}

LEGAL_DUTY_LIMIT_HOURS = float(os.getenv("LEGAL_DUTY_LIMIT_HOURS", "14.0"))
COMPENSATION_ELICITATION_THRESHOLD_USD = float(
    os.getenv("COMPENSATION_ELICITATION_THRESHOLD_USD", "300.00")
)


def get_connection():
    return mysql.connector.connect(**DB_CONFIG)


def _fetch_one(query, params):
    conn = get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(query, params)
        return cur.fetchone()
    finally:
        conn.close()


def _fetch_all(query, params):
    conn = get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(query, params)
        return cur.fetchall()
    finally:
        conn.close()


def _execute(query, params):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(query, params)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


# ---------------------------------------------------------------
# READ QUERIES (used by the read-only tools)
# ---------------------------------------------------------------

def get_passenger(passenger_id: int):
    return _fetch_one(
        "SELECT * FROM passengers WHERE passenger_id = %s", (passenger_id,)
    )


def get_flight(flight_id: int):
    return _fetch_one("SELECT * FROM flights WHERE flight_id = %s", (flight_id,))


def get_flight_by_number(flight_number: str):
    return _fetch_one(
        "SELECT * FROM flights WHERE flight_number = %s ORDER BY scheduled_departure DESC LIMIT 1",
        (flight_number,),
    )


def list_bookings_for_flight(flight_id: int):
    return _fetch_all(
        """SELECT b.booking_id, b.seat_number, b.fare_class, b.booking_status,
                  p.passenger_id, p.full_name, p.email, p.loyalty_tier
           FROM bookings b
           JOIN passengers p ON b.passenger_id = p.passenger_id
           WHERE b.flight_id = %s""",
        (flight_id,),
    )


def get_booking(booking_id: int):
    return _fetch_one("SELECT * FROM bookings WHERE booking_id = %s", (booking_id,))


def get_crew(crew_id: int):
    return _fetch_one("SELECT * FROM crew WHERE crew_id = %s", (crew_id,))


def get_duty_hours_on_date(crew_id: int, log_date: str) -> float:
    """Total hours_on_duty logged for a given crew member on a given date."""
    row = _fetch_one(
        """SELECT COALESCE(SUM(hours_on_duty), 0) AS total_hours
           FROM duty_time_logs
           WHERE crew_id = %s AND log_date = %s""",
        (crew_id, log_date),
    )
    return float(row["total_hours"]) if row else 0.0


# ---------------------------------------------------------------
# WRITE QUERIES (only called by write tools, after handler-level validation)
# ---------------------------------------------------------------

def update_booking_flight(booking_id: int, new_flight_id: int):
    _execute(
        "UPDATE bookings SET flight_id = %s, booking_status = 'rebooked' WHERE booking_id = %s",
        (new_flight_id, booking_id),
    )


def insert_compensation(passenger_id, flight_id, amount, currency, reason, issued_by, status):
    return _execute(
        """INSERT INTO compensation
           (passenger_id, flight_id, amount, currency, reason, issued_by, status)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (passenger_id, flight_id, amount, currency, reason, issued_by, status),
    )


def insert_crew_assignment(crew_id, flight_id, duty_start, duty_end, assignment_type):
    return _execute(
        """INSERT INTO crew_assignments
           (crew_id, flight_id, duty_start, duty_end, assignment_type)
           VALUES (%s, %s, %s, %s, %s)""",
        (crew_id, flight_id, duty_start, duty_end, assignment_type),
    )


def insert_duty_time_log(crew_id, log_date, hours_flown, hours_on_duty):
    return _execute(
        """INSERT INTO duty_time_logs (crew_id, log_date, hours_flown, hours_on_duty)
           VALUES (%s, %s, %s, %s)""",
        (crew_id, log_date, hours_flown, hours_on_duty),
    )