# Blue Horizon Airlines — Crew Duty-Time Limit (v1, illustrative)

## The limit
No crew member (pilot, co_pilot, flight_attendant) may exceed
**14 total duty hours in the same day** (single `log_date`), summing
across all flights/assignments recorded for them in
`duty_time_logs.hours_on_duty`.

## Enforcement
Any attempt to assign a crew member as a replacement on a new flight
(`assign_replacement_crew`) must check: (hours already logged for them
today) + (duration of the proposed new shift) ≤ 14 hours. If exceeded,
the tool automatically rejects the assignment and asks for a different
crew member — this decision is made in the server code itself, not left
to the model's judgment.

## Why this is a resource, not a tool
This rule is relatively static and needs the model to "read and
understand" it before proposing a solution, rather than data that needs
to be computed or changes in real time — which makes it a good fit for
`resources/read` instead of a tool that returns the same text every time.