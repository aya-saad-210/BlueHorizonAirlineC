---
doc_type: duty_time_policy
last_reviewed: 2026-05-15
owner: Blue Horizon Flight Operations
---

# Blue Horizon Airlines — Crew Duty-Time & Reserve Operations Manual

This manual is the full version behind the simplified `policy://duty-time-limits`
MCP resource, which only exposes the two headline numbers (8h flying / 14h
duty) to every session. Ops agents handling reserve-crew assignment during
an IROPS event need the sub-cases below, which the short resource
intentionally omits to keep the always-loaded context small.

## Section 1 — Purpose

1.1 This manual governs how `duty_time_logs` are checked before any crew
assignment (`assign_reserve_crew`), and what happens when an assignment
would exceed a legal limit.

## Section 2 — Definitions

2.1 **Duty period**: the time from when a crew member reports for duty
(`duty_start`) until released from duty (`duty_end`), including ground time
between flights, not only time airborne.

2.2 **Flight time**: time from the aircraft moving under its own power for
takeoff until it comes to rest after landing. This is what `hours_flown`
tracks in `duty_time_logs`.

2.3 **Rest period**: the required time off between duty periods. Minimum
rest is 10 consecutive hours, extended to 12 hours if the preceding duty
period exceeded 12 hours on duty.

## Section 3 — Daily Limits

3.1 **Maximum flying hours per day: 8.00 hours.** This matches
`MAX_FLYING_HOURS_PER_DAY` in `tools_write.py`.

3.2 **Maximum hours on duty per day: 14.00 hours.** This matches
`MAX_DUTY_HOURS_PER_DAY` in `tools_write.py`.

3.3 Both limits are checked against the sum of `hours_flown` /
`hours_on_duty` already logged for that crew member on the current date
(`log_date = CURDATE()`) before a new assignment is added, not after.

## Section 4 — Extensions and Overrides

4.1 **Supervisor override conditions.** A supervisor may approve an
assignment that would push a crew member over either limit in Section 3
only when both of the following hold:
   (a) the disruption is itself IROPS-driven (the flight being crewed has
       `status` in `disrupted`, `delayed`, or `cancelled` — the same
       precondition already enforced in code before the override prompt
       ever fires), and
   (b) no untasked reserve crew member at the same `base_airport` is
       available within the next 2 hours.

4.2 **Documentation requirement.** Every override must be logged with the
supervisor's ID and a note (this is already captured by
`SupervisorDecision.note` in the elicitation flow). An override approved
with an empty note should be flagged for the weekly Flight Ops audit.

4.3 **Consecutive-day restriction.** A crew member who has been overridden
above the duty limit on a given day may not be assigned another overridden
duty period within the following 48 hours, even with a new supervisor
approval, unless the Chief Pilot personally co-signs. This 48-hour
cooldown is not currently enforced in code and must be checked manually by
whoever is approving the second override.

## Section 5 — Reserve Crew Activation Rules

5.1 Reserve crew (`assignment_type = 'reserve'`) should be drawn first from
the same `base_airport` as the disrupted flight's origin.

5.2 If no reserve crew member is available at the origin base within 2
hours, an ops agent may request reserve crew from the nearest base with
direct routing, but this always requires supervisor approval regardless of
duty-hour status, because it involves positioning cost and schedule risk
beyond the individual assignment.

5.3 A recurring pattern of reserve-crew activation for the same base
(three or more IROPS events at the same base within a rolling 30-day
window) should be escalated to Flight Ops as a staffing-level problem, not
handled as a one-off assignment each time.

## Section 6 — License-Specific Considerations

6.1 `pilot` and `co_pilot` roles require an active ATP (Airline Transport
Pilot) license type; an assignment to a crew record with a null or expired
`license_type` must be rejected regardless of duty-hour status.

6.2 `flight_attendant` roles have no license-type restriction in this
system but are still subject to the same Section 3 duty-hour limits.

6.3 A `co_pilot` may be assigned as the sole relief pilot on a long-haul
augmented crew only if they have logged at least 500 hours on the aircraft
type; this data is not currently tracked in `duty_time_logs` or `crew` and
must be verified against the paper logbook until the schema is extended.

## Section 7 — International Route Considerations

7.1 For flights crossing more than 6 time zones (for example CAI–JFK),
Flight Ops recommends augmented crew (an additional pilot) whenever
scheduled flight time exceeds 10 hours, so that in-flight rest can be taken
in rotation. This is a recommendation, not a hard block, and does not by
itself trigger the Section 4 override flow.

7.2 Destination-country duty regulations may be stricter than Blue
Horizon's internal Section 3 limits (for example some jurisdictions cap
duty at 13 hours instead of 14 for long-haul arrivals). When the
destination airport's local regulation is stricter, the stricter limit
governs, and the override conditions in Section 4 must reference the local
limit, not the Blue Horizon default.

## Section 8 — Non-Compliance Consequences

8.1 An assignment made in violation of Section 3 without a supervisor
override on record is a reportable safety event and must be flagged in the
weekly Flight Ops audit regardless of outcome.

8.2 Repeated non-compliance by the same approving supervisor (two or more
un-documented overrides in a rolling 90-day window) results in a review of
that supervisor's elicitation-approval privileges by Flight Ops.
