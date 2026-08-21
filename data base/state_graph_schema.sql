-- state_graph_schema.sql
-- New tables for the Final Project's state-graph work, added on top of the
-- existing blue_horizon_db schema (database code.sql). Nothing here touches
-- an existing table -- these are pure additions.
--
-- Shared across ALL THREE graphs (Person A / B / C build their own graph on
-- top of the same two infra tables below, so this file is the thing all
-- three of you should agree on FIRST, before any Python is written):
--   1. graph_runs        -- one row per run of any graph, tracks current status/node
--   2. graph_checkpoints  -- append-only history of every transition of every run
--   3. hitl_tasks         -- the shared HITL node "contract": every graph's HITL
--                            pause opens one row here, regardless of which graph
--                            or which agent it belongs to
--
-- Person-A-specific (Crew Reassignment graph #1):
--   4. crew_reassignment_requests

USE blue_horizon_db;

-- ---------------------------------------------------------------------
-- 1) graph_runs
-- One row per run of any state graph. This is the row the platform/admin
-- surface queries to list "what's currently running / waiting / stuck".
-- ---------------------------------------------------------------------
CREATE TABLE graph_runs (
    run_id VARCHAR(36) NOT NULL PRIMARY KEY,          -- UUID, generated when a run starts
    graph_name ENUM('crew_reassignment', 'passenger_claims', 'aog_recovery') NOT NULL,
    status ENUM(
        'running',          -- actively executing a node right now
        'waiting_hitl',      -- paused at a HITL node, see hitl_tasks
        'waiting_external',  -- paused waiting on something outside the model
                             -- (e.g. crew/union reply, insurer response, parts ETA)
        'ticket_open',       -- failed mid-node, a ticket exists (Person C's system)
        'completed',
        'failed'
    ) NOT NULL DEFAULT 'running',
    current_node VARCHAR(100) NOT NULL,               -- name of the node it's at/paused at
    started_by VARCHAR(100) NOT NULL,                 -- ops agent / user id that triggered the run
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_graph_status (graph_name, status)        -- fast "show me everything waiting" query
);

-- ---------------------------------------------------------------------
-- 2) graph_checkpoints
-- Append-only. A new row is written after EVERY meaningful transition,
-- never only at the end and never only on failure. Resuming a run means:
--   SELECT * FROM graph_checkpoints
--   WHERE run_id = ? ORDER BY step_number DESC LIMIT 1
-- Keeping full history (not just the latest row) is what makes the
-- "inspect the graph's persisted state at the point it paused/failed"
-- requirement possible, and gives you a free audit trail / time-travel
-- for the demo.
-- ---------------------------------------------------------------------
CREATE TABLE graph_checkpoints (
    checkpoint_id INT AUTO_INCREMENT PRIMARY KEY,
    run_id VARCHAR(36) NOT NULL,
    step_number INT NOT NULL,                         -- 0, 1, 2... within this run_id
    node_name VARCHAR(100) NOT NULL,                  -- node that just finished (or is paused at)
    state_json JSON NOT NULL,                         -- full serialized graph state at this point
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_checkpoints_run
        FOREIGN KEY (run_id) REFERENCES graph_runs(run_id)
        ON DELETE CASCADE,
    UNIQUE KEY uq_run_step (run_id, step_number)
);

-- ---------------------------------------------------------------------
-- 3) hitl_tasks
-- The one shared shape for "an admin needs to act before this run can
-- continue" -- used by all three graphs. A HITL row is an EXPECTED pause
-- for a decision the agent isn't allowed to make alone (contrast with a
-- ticket: unplanned, an actual error).
-- ---------------------------------------------------------------------
CREATE TABLE hitl_tasks (
    hitl_task_id INT AUTO_INCREMENT PRIMARY KEY,
    run_id VARCHAR(36) NOT NULL,
    node_name VARCHAR(100) NOT NULL,                  -- which node raised this
    reason VARCHAR(300) NOT NULL,                      -- human-readable: why this needs a person
    condition_type VARCHAR(50) NOT NULL,               -- e.g. 'duty_hour_breach', 'compensation_over_cap'
    payload_json JSON NOT NULL,                        -- the specific data the admin needs to decide on
    status ENUM('pending', 'approved', 'rejected') NOT NULL DEFAULT 'pending',
    decided_by VARCHAR(100) NULL,                       -- admin id who acted
    decision_note VARCHAR(300) NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    decided_at TIMESTAMP NULL,
    CONSTRAINT fk_hitl_run
        FOREIGN KEY (run_id) REFERENCES graph_runs(run_id)
        ON DELETE CASCADE,
    INDEX idx_hitl_status (status)                      -- platform admin inbox query
);

-- ---------------------------------------------------------------------
-- 4) crew_reassignment_requests  (Person A -- graph #1 domain table)
-- This is the entity that makes the problem genuinely stateful: it tracks
-- a real external wait (the proposed crew member's own reply, and the
-- union's reply if the assignment breaches duty hours) that can take
-- hours or days and must survive a process restart -- unlike the existing
-- assign_reserve_crew() elicitation, which only lives inside one open
-- session.
-- ---------------------------------------------------------------------
CREATE TABLE crew_reassignment_requests (
    request_id INT AUTO_INCREMENT PRIMARY KEY,
    run_id VARCHAR(36) NOT NULL,
    flight_id INT NOT NULL,
    crew_id INT NOT NULL,                              -- the candidate reserve/replacement crew member
    disruption_reason VARCHAR(200) NULL,

    duty_hour_breach BOOLEAN NOT NULL DEFAULT FALSE,    -- computed once, drives whether union sign-off is needed

    crew_reply_status ENUM('pending', 'accepted', 'declined', 'no_response_timeout')
        NOT NULL DEFAULT 'pending',
    crew_replied_at TIMESTAMP NULL,

    union_reply_status ENUM('not_required', 'pending', 'approved', 'rejected')
        NOT NULL DEFAULT 'not_required',
    union_replied_at TIMESTAMP NULL,

    final_status ENUM('pending', 'assigned', 'rejected', 'expired')
        NOT NULL DEFAULT 'pending',

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    CONSTRAINT fk_crewreq_run
        FOREIGN KEY (run_id) REFERENCES graph_runs(run_id)
        ON DELETE CASCADE,
    CONSTRAINT fk_crewreq_flight
        FOREIGN KEY (flight_id) REFERENCES flights(flight_id),
    CONSTRAINT fk_crewreq_crew
        FOREIGN KEY (crew_id) REFERENCES crew(crew_id)
);
