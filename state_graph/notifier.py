# state_graph/notifier.py
#
# Real notification channel for propose_crew()'s "notify the crew member"
# step -- replaces the TODO comment that used to sit there with an actual
# send, using the same LIVE-if-credentials-else-MOCK pattern already
# established by planning/llm_client.py (GEMINI_API_KEY) and
# Rag/llm_client.py in this repo, instead of inventing a new convention.
#
# LIVE mode: sends a real email over SMTP (stdlib smtplib -- no new
# dependency, no paid API, works with any SMTP account incl. a free Gmail
# App Password) when SMTP_HOST/SMTP_USER/SMTP_PASSWORD/NOTIFY_FROM_EMAIL
# are set in .env.
#
# MOCK mode: no SMTP credentials configured -- prints a clearly-labeled
# [MOCK NOTIFICATION] line so a developer running locally without SMTP
# creds still sees exactly what would have been sent.
#
# BOTH modes write a real row to crew_notifications (see
# data base/crew_notifications_schema.sql) -- the channel used
# (email_live vs email_mock) is recorded per row, so "was this crew
# member actually notified, and how" is answerable from the DB by anyone
# building a platform view on top of it, not just from whoever was
# watching the terminal when it happened.

from __future__ import annotations

import os
import smtplib
from email.mime.text import MIMEText

from dotenv import load_dotenv

from dbase import get_connection

load_dotenv()

SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
NOTIFY_FROM_EMAIL = os.getenv("NOTIFY_FROM_EMAIL")

MODE = "live" if (SMTP_HOST and SMTP_USER and SMTP_PASSWORD and NOTIFY_FROM_EMAIL) else "mock"


def _send_live(to_email: str, subject: str, body: str) -> None:
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = NOTIFY_FROM_EMAIL
    msg["To"] = to_email

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(NOTIFY_FROM_EMAIL, [to_email], msg.as_string())


def notify_crew_member(run_id: str, crew_id: int, crew_name: str,
                        flight_number: str, request_id: int) -> None:
    """Called from propose_crew() once a candidate is chosen. Sends the
    actual reassignment notification and records it in crew_notifications
    regardless of which mode fired, so the record exists either way."""
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT contact_email FROM crew WHERE crew_id = %s", (crew_id,))
        row = cursor.fetchone()
    finally:
        cursor.close()
        conn.close()

    to_email = row["contact_email"] if row else None
    subject = f"[Blue Horizon IROPS] Reassignment request for flight {flight_number}"
    body = (
        f"Hi {crew_name},\n\n"
        f"You've been proposed as reserve crew for disrupted flight {flight_number}.\n"
        f"Please reply through the crew app to accept or decline this reassignment "
        f"(request #{request_id}). If we don't hear back, this request will time out "
        f"and a different reserve crew member will be proposed instead.\n\n"
        f"-- Blue Horizon IROPS"
    )

    if MODE == "live" and to_email:
        _send_live(to_email, subject, body)
        channel = "email_live"
    else:
        reason = "no SMTP credentials configured" if MODE == "mock" else "crew member has no contact_email on file"
        print(f"[MOCK NOTIFICATION] ({reason}) to={to_email!r} subject={subject!r}")
        channel = "email_mock"

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO crew_notifications (run_id, crew_id, channel, subject, body)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (run_id, crew_id, channel, subject, body),
        )
        conn.commit()
    finally:
        cursor.close()
        conn.close()
