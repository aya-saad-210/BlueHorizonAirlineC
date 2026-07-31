CREATE DATABASE IF NOT EXISTS blue_horizon_db;
USE blue_horizon_db;

CREATE TABLE passengers (
    passenger_id INT AUTO_INCREMENT PRIMARY KEY,
    full_name VARCHAR(100) NOT NULL,
    email VARCHAR(100) NOT NULL UNIQUE,
    loyalty_tier ENUM('none', 'silver', 'gold', 'platinum') NOT NULL DEFAULT 'none',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE flights (
    flight_id INT AUTO_INCREMENT PRIMARY KEY,
    flight_number VARCHAR(10) NOT NULL,
    origin_airport VARCHAR(5) NOT NULL,
    destination_airport VARCHAR(5) NOT NULL,
    scheduled_departure DATETIME NOT NULL,
    scheduled_arrival DATETIME NOT NULL,
    status ENUM('scheduled', 'delayed', 'cancelled', 'disrupted') NOT NULL DEFAULT 'scheduled',
    disruption_reason VARCHAR(200) NULL
);

CREATE TABLE bookings (
    booking_id INT AUTO_INCREMENT PRIMARY KEY,
    passenger_id INT NOT NULL,
    flight_id INT NOT NULL,
    seat_number VARCHAR(5) NOT NULL,
    fare_class ENUM('economy', 'premium', 'business') NOT NULL DEFAULT 'economy',
    booking_status ENUM('confirmed', 'rebooked', 'cancelled') NOT NULL DEFAULT 'confirmed',
    CONSTRAINT fk_bookings_passenger
        FOREIGN KEY (passenger_id) REFERENCES passengers(passenger_id)
        ON DELETE CASCADE
        ON UPDATE CASCADE,
    CONSTRAINT fk_bookings_flight
        FOREIGN KEY (flight_id) REFERENCES flights(flight_id)
);

CREATE TABLE crew (
    crew_id INT AUTO_INCREMENT PRIMARY KEY,
    full_name VARCHAR(100) NOT NULL,
    role ENUM('pilot', 'co_pilot', 'flight_attendant') NOT NULL,
    base_airport VARCHAR(5) NOT NULL,
    license_type VARCHAR(50) NULL
);

CREATE TABLE crew_assignments (
    assignment_id INT AUTO_INCREMENT PRIMARY KEY,
    crew_id INT NOT NULL,
    flight_id INT NOT NULL,
    duty_start DATETIME NOT NULL,
    duty_end DATETIME NOT NULL,
    assignment_type ENUM('original', 'reserve') NOT NULL DEFAULT 'original',
    CONSTRAINT fk_assignments_crew
        FOREIGN KEY (crew_id) REFERENCES crew(crew_id),
    CONSTRAINT fk_assignments_flight
        FOREIGN KEY (flight_id) REFERENCES flights(flight_id)
);

CREATE TABLE duty_time_logs (
    log_id INT AUTO_INCREMENT PRIMARY KEY,
    crew_id INT NOT NULL,
    log_date DATE NOT NULL,
    hours_flown DECIMAL(4,2) NOT NULL DEFAULT 0,
    hours_on_duty DECIMAL(4,2) NOT NULL DEFAULT 0,
    CONSTRAINT fk_dutylogs_crew
        FOREIGN KEY (crew_id) REFERENCES crew(crew_id)
);

CREATE TABLE compensation (
    compensation_id INT AUTO_INCREMENT PRIMARY KEY,
    passenger_id INT NOT NULL,
    flight_id INT NOT NULL,
    amount DECIMAL(8,2) NOT NULL,
    currency VARCHAR(3) NOT NULL DEFAULT 'USD',
    reason VARCHAR(200) NOT NULL,
    issued_by VARCHAR(100) NOT NULL,
    status ENUM('pending', 'approved', 'rejected') NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_compensation_passenger
        FOREIGN KEY (passenger_id) REFERENCES passengers(passenger_id),
    CONSTRAINT fk_compensation_flight
        FOREIGN KEY (flight_id) REFERENCES flights(flight_id)
);

INSERT INTO passengers (full_name, email, loyalty_tier) VALUES
('Ahmed Samir', 'ahmed.samir@example.com', 'gold'),
('Mona Khaled', 'mona.khaled@example.com', 'none'),
('Youssef Adel', 'youssef.adel@example.com', 'platinum');

INSERT INTO flights (flight_number, origin_airport, destination_airport, scheduled_departure, scheduled_arrival, status, disruption_reason) VALUES
('BH101', 'CAI', 'JFK', '2026-08-01 10:00:00', '2026-08-01 18:00:00', 'scheduled', NULL),
('BH202', 'CAI', 'LHR', '2026-08-02 09:00:00', '2026-08-02 13:00:00', 'disrupted', 'mechanical'),
('BH303', 'HRG', 'DXB', '2026-08-03 15:00:00', '2026-08-03 19:00:00', 'cancelled', 'weather');

INSERT INTO bookings (passenger_id, flight_id, seat_number, fare_class, booking_status) VALUES
(1, 1, '12A', 'economy', 'confirmed'),
(2, 2, '3C', 'business', 'confirmed'),
(3, 3, '7B', 'premium', 'cancelled');

INSERT INTO crew (full_name, role, base_airport, license_type) VALUES
('Capt. Karim Mostafa', 'pilot', 'CAI', 'ATP'),
('Capt. Laila Hassan', 'co_pilot', 'CAI', 'ATP'),
('Nourhan Fathy', 'flight_attendant', 'CAI', NULL);

INSERT INTO crew_assignments (crew_id, flight_id, duty_start, duty_end, assignment_type) VALUES
(1, 2, '2026-08-02 06:00:00', '2026-08-02 14:00:00', 'original'),
(2, 2, '2026-08-02 06:00:00', '2026-08-02 14:00:00', 'reserve');

INSERT INTO duty_time_logs (crew_id, log_date, hours_flown, hours_on_duty) VALUES
(1, '2026-08-02', 7.50, 13.00),
(2, '2026-08-02', 2.00, 4.00);

INSERT INTO compensation (passenger_id, flight_id, amount, currency, reason, issued_by, status) VALUES
(2, 2, 150.00, 'USD', 'flight delayed due to mechanical issue', 'agent_007', 'approved');