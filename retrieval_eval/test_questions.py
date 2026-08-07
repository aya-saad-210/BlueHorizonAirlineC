"""Benchmark questions for retrieval evaluation in the Blue Horizon Airlines IROPS Assistant."""

from __future__ import annotations

TEST_QUESTIONS: list[dict[str, str | list[str]]] = [
    {
        "id": "naive-001",
        "question": "What is the boarding gate for flight BH123?",
        "ground_truth_answer": "Flight BH123 departs from gate C5 at terminal 2.",
        "expected_keywords": ["flight", "BH123", "gate"],
        "architecture": "naive",
    },
    {
        "id": "naive-002",
        "question": "Find the passenger contact details for Jane Doe on flight BH814.",
        "ground_truth_answer": "Passenger Jane Doe on flight BH814 can be reached at jane.doe@example.com.",
        "expected_keywords": ["passenger", "Jane Doe", "BH814"],
        "architecture": "naive",
    },
    {
        "id": "naive-003",
        "question": "What is the status of booking reference ABC123?",
        "ground_truth_answer": "Booking reference ABC123 is confirmed for seat 14A on flight BH212.",
        "expected_keywords": ["booking", "ABC123", "confirmed"],
        "architecture": "naive",
    },
    {
        "id": "hybrid-001",
        "question": "Retrieve the rule text for policy ID POL-204.",
        "ground_truth_answer": "Policy POL-204 describes delay reimbursement and crew standby compensation.",
        "expected_keywords": ["POL-204", "policy", "reimbursement"],
        "architecture": "hybrid",
    },
    {
        "id": "hybrid-002",
        "question": "What is the assignment summary for crew ID CREW-907?",
        "ground_truth_answer": "Crew ID CREW-907 is assigned to long-haul flight FL-5567 with reserve status.",
        "expected_keywords": ["CREW-907", "assignment", "FL-5567"],
        "architecture": "hybrid",
    },
    {
        "id": "hybrid-003",
        "question": "Search the manifest details for flight ID FL-5567.",
        "ground_truth_answer": "Flight FL-5567 is scheduled from JFK to LAX with crew standby support.",
        "expected_keywords": ["FL-5567", "flight", "manifest"],
        "architecture": "hybrid",
    },
    {
        "id": "hybrid-004",
        "question": "Which regulation covers maximum duty hours under REG-109?",
        "ground_truth_answer": "Regulation REG-109 caps the pilot duty block at 14 hours with mandatory rest periods.",
        "expected_keywords": ["REG-109", "regulation", "duty hours"],
        "architecture": "hybrid",
    },
    {
        "id": "agentic-001",
        "question": "What should we do if a pilot's duty period exceeds 14 hours?",
        "ground_truth_answer": "If a pilot exceeds 14 hours, apply the duty limitation policy and reassign the flight to a rested crew member.",
        "expected_keywords": ["pilot", "14 hours", "reassign"],
        "architecture": "agentic",
    },
    {
        "id": "agentic-002",
        "question": "Describe the supervisor approval workflow for a late departure request.",
        "ground_truth_answer": "The supervisor approval workflow requires documenting the delay, checking regulatory limits, and obtaining formal sign-off before departure.",
        "expected_keywords": ["supervisor", "approval", "workflow"],
        "architecture": "agentic",
    },
    {
        "id": "agentic-003",
        "question": "How should weather disruption and crew replacement be handled together?",
        "ground_truth_answer": "For weather disruption, trigger the replacement procedure, notify the crew roster, and confirm alternate crew availability before resuming operations.",
        "expected_keywords": ["weather", "crew replacement", "procedure"],
        "architecture": "agentic",
    },
    {
        "id": "agentic-004",
        "question": "Does regulation REG-109 override policy POL-204 for a diversion decision?",
        "ground_truth_answer": "Regulation REG-109 takes precedence for diversion and duty limits, while policy POL-204 provides operational guidance on reimbursement.",
        "expected_keywords": ["REG-109", "POL-204", "override"],
        "architecture": "agentic",
    },
]
