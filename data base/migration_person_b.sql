
CREATE TABLE IF NOT EXISTS claims (
    claim_id INT AUTO_INCREMENT PRIMARY KEY,
    run_id VARCHAR(36) NULL UNIQUE,        -- FK to graph_runs.run_id (data base/state_graph_schema.sql),
                                            -- set once submit_claim() starts the graph run
    passenger_id INT NOT NULL,
    flight_id INT NOT NULL,
    amount DECIMAL(8,2) NOT NULL,
    currency VARCHAR(3) NOT NULL DEFAULT 'USD',
    reason VARCHAR(255) NOT NULL,
    submitted_by VARCHAR(100) NOT NULL,    -- agent_### or passenger email
    final_status VARCHAR(64) NULL,         -- 'approved' / 'rejected' /
                                            -- 'resolved_appeal_approved' / 'resolved_appeal_rejected'
                                            -- written by claims_graph.py's terminal nodes; NULL while running
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_claims_passenger FOREIGN KEY (passenger_id) REFERENCES passengers(passenger_id),
    CONSTRAINT fk_claims_flight FOREIGN KEY (flight_id) REFERENCES flights(flight_id),
    CONSTRAINT fk_claims_run FOREIGN KEY (run_id) REFERENCES graph_runs(run_id) ON DELETE SET NULL
);

-- ---------------------------------------------------------------------
-- Admin platform: runtime tool management audit trail. Unrelated to the
-- state-graph schema conflict -- kept as-is from the first version.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent_tools_registry (
    entry_id INT AUTO_INCREMENT PRIMARY KEY,
    agent_name VARCHAR(100) NOT NULL,
    tool_name VARCHAR(100) NOT NULL,
    action ENUM('registered', 'deregistered') NOT NULL,
    performed_by VARCHAR(100) NOT NULL,
    performed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_tools_agent (agent_name, tool_name)
);

-- ---------------------------------------------------------------------
-- Admin platform: RAG document registry. Also unrelated to the
-- state-graph schema conflict -- kept as-is.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rag_documents_registry (
    doc_id INT AUTO_INCREMENT PRIMARY KEY,
    filename VARCHAR(255) NOT NULL,
    doc_type VARCHAR(64) NOT NULL,
    chunk_count INT NOT NULL DEFAULT 0,
    status ENUM('active', 'removed') NOT NULL DEFAULT 'active',
    added_by VARCHAR(100) NOT NULL,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    removed_at TIMESTAMP NULL
);
