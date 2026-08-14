-- seed_planning_eval.sql
--
-- Extra seed data ONLY for the Planning Agent's fixed evaluation suite
-- (planning_eval/test_suite.py). Additive only -- does not touch or
-- redefine any existing table, row, or the Memory/RAG agent's data.
--
-- Run this once, after "data base/database code.sql", against the same
-- blue_horizon_db database:
--   mysql -u root -p blue_horizon_db < "data base/seed_planning_eval.sql"
--
-- Design notes (why these rows exist -- maps directly to planning_eval/test_suite.py):
--   BH404  -> single affected passenger, one obvious replacement flight.
--             Used as the STABLE case where decomposition-first should win
--             (no surprises, so the up-front DAG plan holds all the way through).
--   BH505  -> three affected passengers, all independently rebookable.
--             Used as the PARALLEL/DAG case (execution_batches() should place
--             all three rebooking nodes in the same batch).
--   BH606  -> disrupted flight whose original + first reserve crew are BOTH
--             already near/at the duty-hour cap. Assigning either legally
--             requires a supervisor override. Used as the DYNAMIC-BEATS-
--             DECOMPOSITION-FIRST case: a decomposition-first plan can't know
--             the duty-hour breach until it actually queries duty_time_logs,
--             so the up-front plan's "assign reserve crew" node has to be
--             revised mid-execution -- exactly what dynamic decomposition
--             is supposed to handle and decomposition-first is not.
--   BH707  -> disrupted flight with THREE candidate replacement flights of
--             different quality (fare class carried over vs. not, layover vs.
--             direct). Used as the LOOKAHEAD/SEARCH case for Tree of Thoughts.
--   BH808  -> disrupted flight where the "obvious" compensation amount
--             exceeds the auto-approve cap AND a first attempt at issuing
--             it will collide with an already-existing pending compensation
--             row (duplicate rejection). Used as the REFLEXION case: trial 1
--             fails on the duplicate-compensation business rule, the
--             reflection must carry that fact into trial 2, which then
--             issues correctly-scoped compensation only where none exists yet.

INSERT INTO passengers (full_name, email, loyalty_tier) VALUES
('Salma Nabil', 'salma.nabil@example.com', 'silver'),
('Omar Farid', 'omar.farid@example.com', 'none'),
('Nourane Tarek', 'nourane.tarek@example.com', 'gold'),
('Hady Fathallah', 'hady.fathallah@example.com', 'none'),
('Rania Adly', 'rania.adly@example.com', 'platinum'),
('Karim Zaki', 'karim.zaki@example.com', 'silver');

-- Replacement/target flights (all 'scheduled' so rebook_passenger accepts them)
INSERT INTO flights (flight_number, origin_airport, destination_airport, scheduled_departure, scheduled_arrival, status, disruption_reason) VALUES
('BH108', 'CAI', 'JFK', '2026-08-13 10:00:00', '2026-08-13 18:00:00', 'scheduled', NULL),
('BH710', 'CAI', 'CDG', '2026-08-13 08:00:00', '2026-08-13 12:30:00', 'scheduled', NULL),
('BH711', 'CAI', 'CDG', '2026-08-13 14:00:00', '2026-08-13 20:00:00', 'scheduled', NULL),
('BH712', 'CAI', 'FRA', '2026-08-13 09:00:00', '2026-08-13 14:00:00', 'delayed', NULL);

-- BH404: single-passenger, single-target stable case
INSERT INTO flights (flight_number, origin_airport, destination_airport, scheduled_departure, scheduled_arrival, status, disruption_reason) VALUES
('BH404', 'CAI', 'JFK', '2026-08-13 10:00:00', '2026-08-13 18:00:00', 'disrupted', 'mechanical');

-- BH505: three-passenger parallel-rebooking case
INSERT INTO flights (flight_number, origin_airport, destination_airport, scheduled_departure, scheduled_arrival, status, disruption_reason) VALUES
('BH505', 'CAI', 'LHR', '2026-08-13 09:00:00', '2026-08-13 13:00:00', 'cancelled', 'weather');

-- BH606: crew duty-limit breach case
INSERT INTO flights (flight_number, origin_airport, destination_airport, scheduled_departure, scheduled_arrival, status, disruption_reason) VALUES
('BH606', 'HRG', 'DXB', '2026-08-13 15:00:00', '2026-08-13 19:00:00', 'disrupted', 'mechanical');

-- BH707: lookahead/search case with 3 replacement candidates (BH710/BH711/BH712 above)
INSERT INTO flights (flight_number, origin_airport, destination_airport, scheduled_departure, scheduled_arrival, status, disruption_reason) VALUES
('BH707', 'CAI', 'CDG', '2026-08-13 08:30:00', '2026-08-13 13:00:00', 'disrupted', 'weather');

-- BH808: Reflexion case (duplicate compensation trap)
INSERT INTO flights (flight_number, origin_airport, destination_airport, scheduled_departure, scheduled_arrival, status, disruption_reason) VALUES
('BH808', 'CAI', 'LHR', '2026-08-13 11:00:00', '2026-08-13 15:00:00', 'disrupted', 'mechanical');

-- Bookings tying passengers to the disrupted flights above
-- (booking_id auto-increments; we look flights/passengers up by natural keys)
INSERT INTO bookings (passenger_id, flight_id, seat_number, fare_class, booking_status)
SELECT p.passenger_id, f.flight_id, '14C', 'economy', 'confirmed'
FROM passengers p, flights f
WHERE p.email = 'salma.nabil@example.com' AND f.flight_number = 'BH404';

INSERT INTO bookings (passenger_id, flight_id, seat_number, fare_class, booking_status)
SELECT p.passenger_id, f.flight_id, '2A', 'business', 'confirmed'
FROM passengers p, flights f
WHERE p.email = 'omar.farid@example.com' AND f.flight_number = 'BH505';

INSERT INTO bookings (passenger_id, flight_id, seat_number, fare_class, booking_status)
SELECT p.passenger_id, f.flight_id, '9D', 'economy', 'confirmed'
FROM passengers p, flights f
WHERE p.email = 'nourane.tarek@example.com' AND f.flight_number = 'BH505';

INSERT INTO bookings (passenger_id, flight_id, seat_number, fare_class, booking_status)
SELECT p.passenger_id, f.flight_id, '11F', 'premium', 'confirmed'
FROM passengers p, flights f
WHERE p.email = 'hady.fathallah@example.com' AND f.flight_number = 'BH505';

INSERT INTO bookings (passenger_id, flight_id, seat_number, fare_class, booking_status)
SELECT p.passenger_id, f.flight_id, '1A', 'business', 'confirmed'
FROM passengers p, flights f
WHERE p.email = 'rania.adly@example.com' AND f.flight_number = 'BH707';

INSERT INTO bookings (passenger_id, flight_id, seat_number, fare_class, booking_status)
SELECT p.passenger_id, f.flight_id, '6B', 'economy', 'confirmed'
FROM passengers p, flights f
WHERE p.email = 'karim.zaki@example.com' AND f.flight_number = 'BH808';

-- Crew for the BH606 duty-breach case: BOTH the original and the first
-- reserve are already near/at the daily cap, so a legal assignment
-- genuinely requires the elicitation/approval path -- not fabricated,
-- it follows the exact MAX_DUTY_HOURS_PER_DAY / MAX_FLYING_HOURS_PER_DAY
-- checks already in mcp_server/tools_write.py.
INSERT INTO crew (full_name, role, base_airport, license_type) VALUES
('Capt. Hossam Zaher', 'pilot', 'HRG', 'ATP'),
('Nadia Selim', 'flight_attendant', 'HRG', NULL);

INSERT INTO crew_assignments (crew_id, flight_id, duty_start, duty_end, assignment_type)
SELECT c.crew_id, f.flight_id, NOW(), DATE_ADD(NOW(), INTERVAL 8 HOUR), 'original'
FROM crew c, flights f
WHERE c.full_name = 'Capt. Hossam Zaher' AND f.flight_number = 'BH606';

-- AT the caps (MAX_FLYING_HOURS_PER_DAY=8.00, MAX_DUTY_HOURS_PER_DAY=14.00 in
-- tools_write.py, checked with >=), so assigning either crew member
-- genuinely triggers the elicitation/approval path, not just close to it.
INSERT INTO duty_time_logs (crew_id, log_date, hours_flown, hours_on_duty)
SELECT c.crew_id, CURDATE(), 8.00, 14.00
FROM crew c WHERE c.full_name = 'Capt. Hossam Zaher';

INSERT INTO duty_time_logs (crew_id, log_date, hours_flown, hours_on_duty)
SELECT c.crew_id, CURDATE(), 8.00, 14.00
FROM crew c WHERE c.full_name = 'Nadia Selim';

-- BH808 Reflexion trap: an existing PENDING compensation row for the same
-- passenger + flight already exists, so a first, naive attempt to issue
-- compensation will hit the exact duplicate-compensation rejection in
-- issue_compensation() -- a real business-rule failure to reflect on.
INSERT INTO compensation (passenger_id, flight_id, amount, currency, reason, issued_by, status)
SELECT p.passenger_id, f.flight_id, 120.00, 'USD', 'flight disrupted due to mechanical issue', 'agent_003', 'pending'
FROM passengers p, flights f
WHERE p.email = 'karim.zaki@example.com' AND f.flight_number = 'BH808';
