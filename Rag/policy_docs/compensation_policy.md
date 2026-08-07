---
doc_type: compensation_policy
last_reviewed: 2026-06-01
owner: Blue Horizon Passenger Relations
---

# Blue Horizon Airlines — Passenger Compensation Policy Manual

This manual governs how ops agents and supervisors calculate, approve, and
issue compensation to passengers affected by irregular operations (IROPS)
events: delays, cancellations, denied boarding, and missed connections.
It is the authoritative source behind the `issue_compensation` tool.
The tool enforces the numeric caps below in code
(`MAX_COMPENSATION_WITHOUT_APPROVAL = 500.00`); this manual explains the
reasoning an agent or supervisor needs when a case is not a simple lookup.

## Section 1 — Purpose and Scope

1.1 This policy applies to all passengers holding a confirmed booking
(`booking_status = 'confirmed'` or `'rebooked'`) on a flight that enters
`delayed`, `cancelled`, or `disrupted` status in the Blue Horizon reservation
system.

1.2 This policy does not apply to passengers who cancelled their own booking
voluntarily, or to no-shows.

1.3 Compensation issued under this policy is recorded in the `compensation`
table and is independent of any rebooking performed under the Rebooking
Procedures (see the separate Duty-Time & Crew Operations manual for
crew-side procedures during the same disruption).

## Section 2 — Eligibility Criteria

2.1 **Delay-based eligibility.** A passenger becomes eligible for delay
compensation once the flight's actual departure is delayed by 3 hours or
more relative to `scheduled_departure`, provided the delay is not caused by
an extraordinary circumstance under Section 4.

2.2 **Cancellation-based eligibility.** A passenger on a flight with
`status = 'cancelled'` is eligible for compensation unless the cancellation
was communicated to the passenger at least 14 days before
`scheduled_departure`, or unless Section 4 applies.

2.3 **Denied boarding.** A passenger who holds a confirmed booking but is
denied boarding due to overbooking is eligible for the long-haul
compensation tier under Section 3.3 regardless of route distance, plus a
mandatory rebooking under the standard rebooking procedure.

2.4 **Missed connections.** If a passenger's booking includes a connecting
Blue Horizon flight and the delay or cancellation of the first flight causes
the passenger to miss the connection, eligibility is calculated using the
total itinerary distance (origin to final destination), not the distance of
the disrupted leg alone.

## Section 3 — Compensation Amounts by Distance and Delay Length

Distance bands are measured using the great-circle distance between
`origin_airport` and `destination_airport`.

3.1 **Short-haul (under 1,500 km).**
- Delay 3–4 hours: 125 USD
- Delay over 4 hours or cancellation: 250 USD

3.2 **Medium-haul (1,500–3,500 km).**
- Delay 3–4 hours: 200 USD
- Delay over 4 hours or cancellation: 400 USD

3.3 **Long-haul (over 3,500 km).**
- Delay 3–5 hours: 300 USD
- Delay over 5 hours or cancellation: 600 USD

3.4 Amounts in this section are denominated in USD by default. If the
passenger requests a different settlement currency (`EUR`, `GBP`, `EGP`),
convert using the daily reference rate published by Finance; do not use a
rate more than 24 hours old.

## Section 4 — Exceptions and Extraordinary Circumstances

Extraordinary circumstances reduce or eliminate compensation eligibility
under Section 2, but a rebooking or refund is still owed to the passenger
regardless of this section.

4.1 **Weather.** Compensation is waived when `disruption_reason` is weather
and the weather event is confirmed by the origin or destination airport's
official advisory (e.g. a ground stop or severe weather warning covering
the scheduled departure or arrival window).

4.2 **Mechanical issues** are split into two sub-cases with different
outcomes:

4.2a **Unscheduled maintenance discovered during a routine pre-flight check
that could not have been reasonably foreseen** (e.g. a bird-strike found on
walk-around inspection) is treated as an extraordinary circumstance.
Compensation is waived under this sub-case.

4.2b **Mechanical failure attributable to a known, pre-existing maintenance
issue that Blue Horizon Engineering had already logged, or a failure of a
component that was due for scheduled replacement** is NOT an extraordinary
circumstance. Full compensation under Section 3 applies. This is the most
common category ops agents get wrong, because both 4.2a and 4.2b show up in
the system as `disruption_reason = 'mechanical'` — the distinction is
determined by checking the maintenance log, not the reservation system, so
an agent must escalate to Engineering before waiving compensation on a
mechanical disruption.

4.3 **Crew shortage.** Waived only if the shortage was caused by a
crew member's own medical emergency reported less than 4 hours before duty
start. If the shortage results from Blue Horizon failing to schedule
adequate reserve crew (see the Duty-Time manual, Section 5), it is treated
as within airline control and full compensation applies.

4.4 **Security events.** Always treated as an extraordinary circumstance.
Compensation is waived, but passengers are still owed rebooking and, for
delays over 2 hours, meal vouchers per the customer-care desk procedure
(not covered in this manual).

## Section 5 — Approval Thresholds and Supervisor Escalation

5.1 Any compensation amount up to and including 500 USD (or currency
equivalent) may be issued by an authenticated ops agent
(`issued_by` starting with `agent_`) without further approval, matching the
`MAX_COMPENSATION_WITHOUT_APPROVAL` constant enforced in
`tools_write.issue_compensation`.

5.2 Any amount exceeding 500 USD requires explicit supervisor approval via
the elicitation flow (`request_supervisor_approval`). This includes:
combined compensation for a single passenger across a multi-leg missed
connection (Section 2.4), and any long-haul denied-boarding case
(Section 2.3, always 600 USD, always requires approval).

5.3 A supervisor approving an amount above 500 USD must record a note
explaining the justification. Approvals without a note should be flagged
for later audit.

## Section 6 — Loyalty Tier Adjustments

6.1 `platinum` tier passengers receive a 20% uplift on top of the base
amount calculated in Section 3, automatically, without requiring separate
supervisor approval unless the uplifted total itself exceeds 500 USD.

6.2 `gold` tier passengers receive a 10% uplift under the same rule.

6.3 `silver` and `none` tier passengers receive the base amount from
Section 3 with no adjustment.

6.4 Loyalty adjustments apply on top of Section 4 exceptions — i.e. if
compensation is waived under Section 4, the loyalty uplift does not create
a compensation obligation on its own.

## Section 7 — Documentation and Audit Requirements

7.1 Every compensation record must have a non-empty `reason` field citing
the applicable section of this manual (e.g. "Sec 3.2 + Sec 6.1 platinum
uplift") so audits do not require re-deriving the calculation from scratch.

7.2 Duplicate compensation for the same passenger and flight is prohibited;
this is enforced in code by `issue_compensation`, which rejects a second
payout while an existing `pending` or `approved` record exists for the same
`passenger_id` and `flight_id` pair.

7.3 Records in `status = 'rejected'` must retain the original requested
amount and reason for historical audit; they are never deleted.
