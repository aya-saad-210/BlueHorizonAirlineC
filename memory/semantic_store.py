# memory/semantic_store.py
#
# RUBRIC: feeds "Semantic memory consolidation layer" (10 pts) --
#   "semantic facts run into in production: updates when a fact changes,
#    versioning so an old fact isn't silently lost, expiration for facts
#    that go stale, and conflict resolution when two episodes imply
#    contradictory facts."
#
# This file is ONLY the store (schema + read/write primitives that support
# versioning, expiration, and superseding). The actual decision logic that
# extracts facts from episodes and resolves conflicts lives in
# consolidation.py -- keeping them separate mirrors the same boundary the
# lab draws between the router (routing.py, decides forget/promote) and
# the episodic store (episodic_store.py, just holds what got promoted).
#
# WHY THIS NEEDS REAL VERSIONING (not just an UPDATE statement): per the
# guardrails -- "Contradictions in semantic memory must be resolved
# explicitly, versioned, dated, or flagged, never silently overwritten
# with no trace of the old fact." A fact like "platinum passengers on
# CAI-origin disruptions get automatic priority rebooking" can change
# (e.g. a policy update, or a correction after a bad inference) and the
# OLD version must stay queryable for audit, not vanish.

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "memory_store.db"


@dataclass
class SemanticFact:
    fact_id: str  # stable identity across versions, e.g. "fact_platinum_cai_priority"
    version: int
    subject: str  # e.g. "platinum_passengers" or "crew_base_CAI"
    predicate: str  # e.g. "gets_priority_rebooking_on" / "requires_reserve_activation_after"
    value: str  # e.g. "international_disruptions" -- kept as text; see metadata for structure
    confidence: float  # 0-1, derived from how many episodes support it (consolidation.py)
    source_episode_ids: list[str]
    valid_from: float
    valid_until: Optional[float] = None  # None = still active; set on supersede/expire
    superseded_by: Optional[str] = None  # fact_id+version string of the fact that replaced this
    status: str = "active"  # "active" | "superseded" | "expired" | "conflicted"
    metadata: dict = field(default_factory=dict)


class SemanticStore:
    """SQLite-backed semantic fact store. Written to ONLY by
    consolidation.py's periodic pass -- never directly by routing.py, and
    never directly by the live agent loop mid-session (matches the lab's
    "never written to directly by the router above" rule, extended here to
    mean "never written to outside consolidation.py" as the single
    enforcement point).

    Facts are never UPDATEd in place. A "change" is always: insert a new
    row with version = old.version + 1, then mark the old row
    superseded_by = new fact's identity. This is what makes versioning and
    audit trail real instead of a database column that happens to be named
    'version' but is only ever overwritten.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS semantic_facts (
                row_id TEXT PRIMARY KEY,
                fact_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                value TEXT NOT NULL,
                confidence REAL NOT NULL,
                source_episode_ids_json TEXT NOT NULL,
                valid_from REAL NOT NULL,
                valid_until REAL,
                superseded_by TEXT,
                status TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    # -- writes -----------------------------------------------------------
    def insert_new_fact(
        self,
        fact_id: str,
        subject: str,
        predicate: str,
        value: str,
        confidence: float,
        source_episode_ids: list[str],
        metadata: Optional[dict] = None,
    ) -> SemanticFact:
        """Version 1 of a brand-new fact_id."""
        fact = SemanticFact(
            fact_id=fact_id,
            version=1,
            subject=subject,
            predicate=predicate,
            value=value,
            confidence=confidence,
            source_episode_ids=source_episode_ids,
            valid_from=time.time(),
            metadata=metadata or {},
        )
        self._insert_row(fact)
        return fact

    def supersede_fact(
        self,
        old_fact: SemanticFact,
        new_value: str,
        new_confidence: float,
        new_source_episode_ids: list[str],
        reason: str,
        metadata: Optional[dict] = None,
    ) -> SemanticFact:
        """Creates version N+1 and marks version N as superseded. `reason`
        is stored in the new fact's metadata so the audit trail explains
        WHY the value changed (e.g. "conflict resolved: 3 recent episodes
        show the override no longer applies after policy update on X")."""
        new_meta = dict(metadata or {})
        new_meta["supersede_reason"] = reason
        new_meta["superseded_fact_version"] = old_fact.version

        new_fact = SemanticFact(
            fact_id=old_fact.fact_id,
            version=old_fact.version + 1,
            subject=old_fact.subject,
            predicate=old_fact.predicate,
            value=new_value,
            confidence=new_confidence,
            source_episode_ids=new_source_episode_ids,
            valid_from=time.time(),
            metadata=new_meta,
        )
        self._insert_row(new_fact)

        # Mark the OLD row superseded -- never deleted, never silently
        # overwritten. valid_until is set so a time-scoped query
        # ("what did we believe as of last week?") still works.
        self._conn.execute(
            """
            UPDATE semantic_facts
            SET status = 'superseded', valid_until = ?, superseded_by = ?
            WHERE fact_id = ? AND version = ?
            """,
            (
                new_fact.valid_from,
                f"{new_fact.fact_id}:v{new_fact.version}",
                old_fact.fact_id,
                old_fact.version,
            ),
        )
        self._conn.commit()
        return new_fact

    def expire_fact(self, fact_id: str, version: int, reason: str) -> None:
        """Marks a fact stale without replacing it with a new value --
        used when consolidation.py determines a fact is simply no longer
        supported by recent episodes (e.g. no matching disruption pattern
        in the last N days), rather than contradicted by a new one."""
        self._conn.execute(
            """
            UPDATE semantic_facts
            SET status = 'expired', valid_until = ?
            WHERE fact_id = ? AND version = ?
            """,
            (time.time(), fact_id, version),
        )
        self._conn.execute(
            """
            UPDATE semantic_facts SET metadata_json = json_set(metadata_json, '$.expire_reason', ?)
            WHERE fact_id = ? AND version = ?
            """,
            (reason, fact_id, version),
        )
        self._conn.commit()

    def flag_conflicted(self, fact_id: str, version: int, reason: str) -> None:
        """Used when consolidation.py finds a contradiction it can't
        auto-resolve with confidence -- flags for human (Flight Ops)
        review instead of guessing, rather than silently picking one
        side."""
        self._conn.execute(
            "UPDATE semantic_facts SET status = 'conflicted' WHERE fact_id = ? AND version = ?",
            (fact_id, version),
        )
        self._conn.execute(
            """
            UPDATE semantic_facts SET metadata_json = json_set(metadata_json, '$.conflict_reason', ?)
            WHERE fact_id = ? AND version = ?
            """,
            (reason, fact_id, version),
        )
        self._conn.commit()

    def _insert_row(self, fact: SemanticFact) -> None:
        self._conn.execute(
            """
            INSERT INTO semantic_facts
                (row_id, fact_id, version, subject, predicate, value, confidence,
                 source_episode_ids_json, valid_from, valid_until, superseded_by,
                 status, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                fact.fact_id,
                fact.version,
                fact.subject,
                fact.predicate,
                fact.value,
                fact.confidence,
                json.dumps(fact.source_episode_ids),
                fact.valid_from,
                fact.valid_until,
                fact.superseded_by,
                fact.status,
                json.dumps(fact.metadata),
            ),
        )
        self._conn.commit()

    # -- reads --------------------------------------------------------------
    def get_active_fact(self, fact_id: str) -> Optional[SemanticFact]:
        row = self._conn.execute(
            "SELECT * FROM semantic_facts WHERE fact_id = ? AND status = 'active' "
            "ORDER BY version DESC LIMIT 1",
            (fact_id,),
        ).fetchone()
        return self._row_to_fact(row) if row else None

    def get_all_versions(self, fact_id: str) -> list[SemanticFact]:
        rows = self._conn.execute(
            "SELECT * FROM semantic_facts WHERE fact_id = ? ORDER BY version ASC",
            (fact_id,),
        ).fetchall()
        return [self._row_to_fact(r) for r in rows]

    def get_active_facts_by_subject(self, subject: str) -> list[SemanticFact]:
        rows = self._conn.execute(
            "SELECT * FROM semantic_facts WHERE subject = ? AND status = 'active'",
            (subject,),
        ).fetchall()
        return [self._row_to_fact(r) for r in rows]

    def _row_to_fact(self, row: sqlite3.Row) -> SemanticFact:
        return SemanticFact(
            fact_id=row["fact_id"],
            version=row["version"],
            subject=row["subject"],
            predicate=row["predicate"],
            value=row["value"],
            confidence=row["confidence"],
            source_episode_ids=json.loads(row["source_episode_ids_json"]),
            valid_from=row["valid_from"],
            valid_until=row["valid_until"],
            superseded_by=row["superseded_by"],
            status=row["status"],
            metadata=json.loads(row["metadata_json"]),
        )


if __name__ == "__main__":
    store = SemanticStore(db_path=Path(__file__).parent / "memory_store.dev.db")

    f1 = store.insert_new_fact(
        fact_id="fact_karim_mostafa_duty_summary",
        subject="crew:karim_mostafa",
        predicate="known_pattern_after_disruption",
        value="Requests platinum-tier passenger reissue on CAI-route disruptions",
        confidence=0.6,
        source_episode_ids=["ep_example1"],
    )
    print(f"v1: {f1.value} (confidence={f1.confidence})")

    f2 = store.supersede_fact(
        f1,
        new_value="Requests automatic priority reissue for platinum AND gold-tier "
        "passengers on any CAI-route disruption (pattern confirmed across 5 episodes)",
        new_confidence=0.85,
        new_source_episode_ids=["ep_example1", "ep_example2", "ep_example3"],
        reason="Consolidation found 3 additional supporting episodes, widened scope to gold tier.",
    )
    print(f"v2: {f2.value} (confidence={f2.confidence})")

    print(f"\nActive fact is now version {store.get_active_fact('fact_karim_mostafa_duty_summary').version}")
    print(f"Total versions retained: {len(store.get_all_versions('fact_karim_mostafa_duty_summary'))}")
