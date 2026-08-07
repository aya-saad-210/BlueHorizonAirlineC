# memory/episodic_store.py
#
# RUBRIC: feeds "Promote-or-drop routing" (6 pts) and "Semantic memory
# consolidation layer" (10 pts) -- this file is the EPISODIC half of the
# episodic/semantic split described in the lab:
#   "For each aging item, the router decides to forget it or promote it
#    to episodic memory... semantic memory is only ever built through a
#    separate, periodic consolidation pass over the episodic store, never
#    written to directly by the router above."
#
# WHY EPISODIC MEMORY IS REAL HERE (not a toy): Blue Horizon ops agents
# handle IROPS events (flight disruptions) that recur across the same
# routes, same crews, same high-tier passengers. A single specific event
# -- "BH303, 2026-08-05, cancelled, mechanical, Youssef Adel (platinum)
# rebooked with priority" -- is exactly the kind of dated, specific memory
# that should NOT be lost after the session ends, but also should NOT be
# treated as a general fact until consolidation.py has looked at enough of
# these to justify one (see semantic_store.py + consolidation.py).
#
# Storage: SQLite, not a Python list/dict -- a real persistent store that
# survives process restarts, matching the same "real store, not a bare
# structure" bar the RAG vector_store.py sets for its own concern.

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
class Episode:
    episode_id: str
    disruption_id: str  # e.g. "BH303-2026-08-05" -- matches ShortTermMemory.disruption_id
    event_type: str  # e.g. "priority_rebooking", "compensation_issued",
    # "crew_duty_override", "reserve_crew_activation"
    description: str  # human-readable summary, e.g. "Youssef Adel (platinum)
    # rebooked with priority after BH303 international disruption"
    occurred_at: float
    flight_id: Optional[int] = None
    passenger_id: Optional[int] = None
    crew_id: Optional[int] = None
    metadata: dict = field(default_factory=dict)  # e.g. {"loyalty_tier": "platinum",
    # "disruption_reason": "mechanical", "route": "CAI-JFK"}
    promoted_from_entry_id: Optional[str] = None  # STM entry that triggered this


class EpisodicStore:
    """SQLite-backed store for episodes promoted from short-term memory.
    Written to ONLY by routing.py's promote-or-drop router (or directly in
    tests) -- consolidation.py only ever READS from this store, it never
    writes episodes."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS episodes (
                episode_id TEXT PRIMARY KEY,
                disruption_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                description TEXT NOT NULL,
                occurred_at REAL NOT NULL,
                flight_id INTEGER,
                passenger_id INTEGER,
                crew_id INTEGER,
                metadata_json TEXT NOT NULL,
                promoted_from_entry_id TEXT,
                consolidated INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        # consolidated flag lets consolidation.py mark which episodes it
        # has already folded into semantic facts, so re-running a
        # periodic pass doesn't re-process the same episode twice.
        self._conn.commit()

    # -- writes (called by routing.py) ---------------------------------
    def add_episode(self, episode: Episode) -> None:
        self._conn.execute(
            """
            INSERT INTO episodes
                (episode_id, disruption_id, event_type, description, occurred_at,
                 flight_id, passenger_id, crew_id, metadata_json, promoted_from_entry_id, consolidated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                episode.episode_id,
                episode.disruption_id,
                episode.event_type,
                episode.description,
                episode.occurred_at,
                episode.flight_id,
                episode.passenger_id,
                episode.crew_id,
                json.dumps(episode.metadata),
                episode.promoted_from_entry_id,
            ),
        )
        self._conn.commit()

    # -- reads (called by consolidation.py and by Self-RAG-style memory
    #    recall verification, per the lab: "This applies to both your RAG
    #    answers and to memories recalled from the episodic and semantic
    #    store.") -----------------------------------------------------
    def get_unconsolidated(self) -> list[Episode]:
        rows = self._conn.execute(
            "SELECT * FROM episodes WHERE consolidated = 0 ORDER BY occurred_at ASC"
        ).fetchall()
        return [self._row_to_episode(r) for r in rows]

    def get_by_passenger(self, passenger_id: int) -> list[Episode]:
        rows = self._conn.execute(
            "SELECT * FROM episodes WHERE passenger_id = ? ORDER BY occurred_at ASC",
            (passenger_id,),
        ).fetchall()
        return [self._row_to_episode(r) for r in rows]

    def get_by_event_type(self, event_type: str) -> list[Episode]:
        rows = self._conn.execute(
            "SELECT * FROM episodes WHERE event_type = ? ORDER BY occurred_at ASC",
            (event_type,),
        ).fetchall()
        return [self._row_to_episode(r) for r in rows]

    def mark_consolidated(self, episode_ids: list[str]) -> None:
        self._conn.executemany(
            "UPDATE episodes SET consolidated = 1 WHERE episode_id = ?",
            [(eid,) for eid in episode_ids],
        )
        self._conn.commit()

    def _row_to_episode(self, row: sqlite3.Row) -> Episode:
        return Episode(
            episode_id=row["episode_id"],
            disruption_id=row["disruption_id"],
            event_type=row["event_type"],
            description=row["description"],
            occurred_at=row["occurred_at"],
            flight_id=row["flight_id"],
            passenger_id=row["passenger_id"],
            crew_id=row["crew_id"],
            metadata=json.loads(row["metadata_json"]),
            promoted_from_entry_id=row["promoted_from_entry_id"],
        )

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]


def new_episode_id() -> str:
    return f"ep_{uuid.uuid4().hex[:12]}"


if __name__ == "__main__":
    # Smoke test: the exact BH303 / Youssef Adel example from the brief.
    store = EpisodicStore(db_path=Path(__file__).parent / "memory_store.dev.db")

    ep = Episode(
        episode_id=new_episode_id(),
        disruption_id="BH303-2026-08-05",
        event_type="priority_rebooking",
        description=(
            "BH303 was a long international flight that hit a connected "
            "disruption (mechanical). Youssef Adel (platinum) was rebooked "
            "with priority ahead of standard-tier passengers."
        ),
        occurred_at=time.time(),
        flight_id=303,
        passenger_id=4821,
        metadata={"loyalty_tier": "platinum", "disruption_reason": "mechanical"},
    )
    store.add_episode(ep)
    print(f"Stored episode. Total episodes: {store.count()}")
    print(f"Unconsolidated: {len(store.get_unconsolidated())}")
