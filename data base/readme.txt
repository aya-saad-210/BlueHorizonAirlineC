Blue Horizon Airlines – IROPS Assistant Database
A MySQL / MariaDB database created for the Blue Horizon Airlines IROPS Assistant project (MCP Server).
IROPS = Irregular Operations (delays, cancellations, disruptions).
The database helps the system track passengers, flights, crew, and compensation when something goes wrong with a flight.
It is designed to run on XAMPP using phpMyAdmin.
Main Purpose
This database stores all the data needed to:
Know which passengers are on which flights
Track flight status (scheduled, delayed, cancelled, disrupted)
Manage crew assignments and check duty hours (to respect legal limits)
Record compensation given to passengers after disruptions
Database Structure
passengers
Stores basic passenger information:
Full name
Email (unique)
Loyalty tier (none, silver, gold, platinum)
flights
Contains flight details:
Flight number
Origin and destination airports
Scheduled departure & arrival times
Status (scheduled, delayed, cancelled, disrupted)
Disruption reason (e.g. weather, mechanical, crew shortage)
bookings
Links passengers to flights:
Seat number
Fare class (economy, premium, business)
Booking status (confirmed, rebooked, cancelled)
Note: If a passenger is deleted, their bookings are automatically deleted (CASCADE).
Flights cannot be deleted if they still have bookings.
crew
Stores crew members:
Full name
Role (pilot, co_pilot, flight_attendant)
Base airport
License type (important for pilots)
crew_assignments
Connects crew members to specific flights:
Duty start and end time
Assignment type (original or reserve)
Crew and flight records are not automatically deleted to protect historical data.
duty_time_logs
Tracks daily working hours for crew:
Hours flown
Hours on duty
This table is used to check if a pilot is close to the legal limit of flying/duty hours.
Data is protected and not deleted automatically.
compensation
Records money given to passengers after problems:
Amount and currency
Reason
Who issued it
Status (pending, approved, rejected)
Financial records are kept safe (no automatic deletion).
Relationships Overview
One passenger → many bookings
One flight → many bookings
One crew member → many assignments and duty logs
One flight → many crew assignments
Compensation is linked to both a passenger and a flight
Seed Data (Sample Records)
The SQL file includes ready-to-use test data:
3 passengers (with different loyalty tiers)
3 flights (one scheduled, one disrupted, one cancelled)
3 bookings
3 crew members
2 crew assignments
Duty time logs (including an edge case close to the legal limit)
1 compensation record
You can start testing the system right after importing the file.
How to Use
Open phpMyAdmin in XAMPP.
Create or select the database (the script creates blue_horizon_db automatically).
Import / run the full SQL file.
The tables and sample data will be ready.
Design Notes
Foreign keys are used carefully.
Important historical and financial data (duty logs, compensation, assignments) are protected from accidental deletion.
Only passenger bookings use ON DELETE CASCADE because it makes logical sense.