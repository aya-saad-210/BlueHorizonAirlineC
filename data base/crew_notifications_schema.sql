-- data base/crew_notifications_schema.sql
--
-- Additive migration #2 for the Final Project (run AFTER state_graph_schema.sql).
-- Adds the two pieces needed for propose_crew() to actually notify a real
-- crew member instead of leaving a TODO comment:
--
--   1. crew.contact_email       -- where the notification actually goes
--   2. crew_notifications        -- a durable, inspectable record of every
--                                   notification the graph has ever sent,
--                                   so "did this crew member actually get
--                                   notified, and when" is a real query,
--                                   not a guess from reading logs.
--
-- Nothing here touches existing rows destructively -- ADD COLUMN is
-- additive, and crew_notifications is a brand new table.

USE blue_horizon_db;

-- ---------------------------------------------------------------------
-- 1) crew.contact_email
-- The existing crew table (data base/database code.sql) has no contact
-- info at all -- full_name, role, base_airport, license_type only.
-- ---------------------------------------------------------------------
ALTER TABLE crew
    ADD COLUMN contact_email VARCHAR(150) NULL AFTER license_type;

-- Demo data so notify_crew_member() has somewhere real to send to when you
-- test this locally. Replace with real addresses, or your own inbox, so
-- you can actually see the email land for the demo recording.
UPDATE crew SET contact_email = CONCAT(LOWER(REPLACE(full_name, ' ', '.')), '@bluehorizon-demo.test')
WHERE contact_email IS NULL;

-- ---------------------------------------------------------------------
-- 2) crew_notifications
-- One row per notification attempt. channel/status let you tell "sent for
-- real via SMTP" apart from "logged in mock mode because no SMTP
-- credentials were configured" at a glance, both in the DB and on
-- whatever platform view Person C/B build on top of this table.
-- ---------------------------------------------------------------------
CREATE TABLE crew_notifications (
    notification_id INT AUTO_INCREMENT PRIMARY KEY,
    run_id VARCHAR(36) NOT NULL,
    crew_id INT NOT NULL,
    channel ENUM('email_live', 'email_mock') NOT NULL,
    subject VARCHAR(200) NOT NULL,
    body TEXT NOT NULL,
    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_crewnotif_run
        FOREIGN KEY (run_id) REFERENCES graph_runs(run_id)
        ON DELETE CASCADE,
    CONSTRAINT fk_crewnotif_crew
        FOREIGN KEY (crew_id) REFERENCES crew(crew_id),
    INDEX idx_crewnotif_run (run_id)
);
