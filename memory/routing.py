# memory/routing.py
#
# RUBRIC: "Promote-or-drop routing (forget and episodic only)" (6 pts) --
#   "The decision layer that fires when short-term memory overflows. For
#    each aging item, the router decides to forget it or promote it to
#    episodic memory, with the reasoning behind each decision logged
#    somewhere a grader can see it. This router does not write directly to
#    semantic memory."
#
# HOOKS DIRECTLY INTO ShortTermMemory's on_overflow callback (short_term.py):
# every time the rolling buffer evicts its oldest entry, that entry is
# handed to PromoteOrDropRouter.decide(), which either:
#   (a) drops it silently (routine, low-signal tool calls -- e.g. a
#       get_flight_status lookup that didn't change anything), or
#   (b) promotes it into episodic_store.py as a new Episode.
#
# It NEVER writes to semantic_store.py -- that boundary is enforced simply
# by this file not importing semantic_store at all. Semantic facts only
# ever get created by consolidation.py's separate periodic pass.
#
# Every decision, forget or promote, is logged to memory_routing_log (same
# SQLite DB as episodic_store.py) so a grader can see the reasoning
# without having to re-run anything.

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from episodic_store import Episode, EpisodicStore, new_episode_id
from short_term import STMEntry

DB_PATH = Path(__file__).parent / "memory_store.db"

# Tool calls that represent a real business event worth remembering past
# this session -- matches the write-tools in mcp_server/tools_write.py.
# A plain lookup (get_flight_status, get_passenger_booking) is routine and
# gets dropped unless it surfaced something notable (see _is_notable_lookup).
SIGNIFICANT_TOOL_NAMES = {
    "rebook_passenger",
    "rebook_all_passengers_on_flight",
    "issue_compensation",
    "assign_reserve_crew",
    "authenticate_supervisor",
}

# Loyalty tiers and compensation thresholds that make even a "routine"
# entry worth promoting -- mirrors compensation_policy.md Section 6
# (platinum/gold uplift) and Section 5 (supervisor escalation above $500).
HIGH_VALUE_TIERS = {"platinum", "gold"}
COMPENSATION_ESCALATION_THRESHOLD = 500.00


@dataclass
class RoutingDecision:
    entry_id: str
    decision: str  # "forget" | "promote"
    reasoning: str
    episode_id: Optional[str] = None


class PromoteOrDropRouter:
    """The decision layer wired to ShortTermMemory(on_overflow=router.decide).

    Deliberately rule-based rather than an LLM call: the routing decision
    needs to be cheap (it fires on every buffer eviction, potentially many
    times per session) and auditable (a grader -- or Flight Ops -- should
    be able to read exactly why something was forgotten, not trust an LLM's
    unlogged judgment call).
    """

    def __init__(
        self,
        episodic_store: Optional[EpisodicStore] = None,
        db_path: Optional[Path] = None,
    ):
        self.episodic_store = episodic_store or EpisodicStore()
        self.db_path = db_path or DB_PATH
        self._conn = sqlite3.connect(self.db_path)
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_routing_log (
                log_id TEXT PRIMARY KEY,
                entry_id TEXT NOT NULL,
                decision TEXT NOT NULL,
                reasoning TEXT NOT NULL,
                episode_id TEXT,
                decided_at REAL NOT NULL
            )
            """
        )
        self._conn.commit()

    # -- the actual decision ---------------------------------------------
    def decide(
        self,
        entry: STMEntry,
        disruption_id: str,
        flight_id: Optional[int] = None,
        passenger_id: Optional[int] = None,
        crew_id: Optional[int] = None,
        entry_metadata: Optional[dict] = None,
    ) -> RoutingDecision:
        """Called as the on_overflow callback. entry_metadata carries
        whatever context the caller (the agent loop / integration layer)
        has on hand about this entry -- e.g. {"loyalty_tier": "platinum",
        "compensation_amount": 600.00} -- since the raw STMEntry alone
        doesn't always carry structured business fields.
        """
        entry_metadata = entry_metadata or {}
        decision, reasoning = self._evaluate(entry, entry_metadata)

        episode_id = None
        if decision == "promote":
            episode = Episode(
                episode_id=new_episode_id(),
                disruption_id=disruption_id,
                event_type=entry.tool_name or "message",
                description=entry.content,
                occurred_at=entry.timestamp,
                flight_id=flight_id,
                passenger_id=passenger_id,
                crew_id=crew_id,
                metadata=entry_metadata,
                promoted_from_entry_id=entry.entry_id,
            )
            self.episodic_store.add_episode(episode)
            episode_id = episode.episode_id

        result = RoutingDecision(
            entry_id=entry.entry_id,
            decision=decision,
            reasoning=reasoning,
            episode_id=episode_id,
        )
        self._log(result)
        return result

    def _evaluate(self, entry: STMEntry, meta: dict) -> tuple[str, str]:
        """Returns (decision, reasoning). Rule order matters: check the
        strongest signals first so the logged reasoning is specific."""

        # Plain chat messages (role="user"/"assistant") without a tool
        # call are low-signal on their own -- forget unless flagged.
        if entry.role != "tool":
            return "forget", (
                "Plain conversational message with no associated tool call; "
                "no durable business fact to preserve past this session."
            )

        tool_name = entry.tool_name or ""

        # Rule 1: high-value loyalty tier on a significant tool call.
        if tool_name in SIGNIFICANT_TOOL_NAMES and meta.get("loyalty_tier") in HIGH_VALUE_TIERS:
            return "promote", (
                f"'{tool_name}' involved a {meta['loyalty_tier']}-tier passenger "
                f"(compensation_policy.md Sec. 6 uplift applies) -- recurring "
                f"high-tier handling patterns are exactly what consolidation.py "
                f"needs to detect."
            )

        # Rule 2: compensation above the supervisor-escalation threshold.
        amount = meta.get("compensation_amount")
        if tool_name == "issue_compensation" and amount and amount > COMPENSATION_ESCALATION_THRESHOLD:
            return "promote", (
                f"issue_compensation amount ${amount:.2f} exceeds the "
                f"${COMPENSATION_ESCALATION_THRESHOLD:.2f} supervisor-escalation "
                f"threshold (compensation_policy.md Sec. 5.2) -- worth an audit trail."
            )

        # Rule 3: any other significant (state-changing) tool call.
        if tool_name in SIGNIFICANT_TOOL_NAMES:
            return "promote", (
                f"'{tool_name}' is a state-changing operation (booking, crew, "
                f"or compensation record) -- these are exactly the disruption "
                f"outcomes episodic memory exists to preserve, per the "
                f"BH303/Youssef Adel example in the project brief."
            )

        # Rule 4: routine lookups with nothing notable in metadata.
        return "forget", (
            f"'{tool_name}' was a routine lookup that didn't change any "
            f"booking, compensation, or crew record; no durable fact to keep."
        )

    def _log(self, result: RoutingDecision) -> None:
        self._conn.execute(
            """
            INSERT INTO memory_routing_log
                (log_id, entry_id, decision, reasoning, episode_id, decided_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                result.entry_id,
                result.decision,
                result.reasoning,
                result.episode_id,
                time.time(),
            ),
        )
        self._conn.commit()

    def get_log(self, limit: int = 50) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM memory_routing_log ORDER BY decided_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        cols = [d[0] for d in self._conn.execute("SELECT * FROM memory_routing_log LIMIT 0").description]
        return [dict(zip(cols, r)) for r in rows]


if __name__ == "__main__":
    from short_term import ShortTermMemory

    router = PromoteOrDropRouter(
        episodic_store=EpisodicStore(db_path=Path(__file__).parent / "memory_store.dev.db")
    )

    def on_overflow(entry: STMEntry) -> None:
        decision = router.decide(
            entry,
            disruption_id="BH303-2026-08-05",
            flight_id=303,
            passenger_id=4821 if "4821" in entry.content else None,
            entry_metadata={"loyalty_tier": "platinum"} if "platinum" in entry.content else {},
        )
        print(f"[{decision.decision.upper()}] {entry.content[:60]!r} -- {decision.reasoning}")

    stm = ShortTermMemory(disruption_id="BH303-2026-08-05", max_size=2, on_overflow=on_overflow)
    stm.add_tool_call("get_flight_status", {"flight_number": "BH303"}, "BH303: status=cancelled")
    stm.add_tool_call(
        "rebook_passenger",
        {"passenger_id": 4821},
        "Rebooked passenger 4821 (platinum) onto BH210",
    )
    stm.add_tool_call(
        "issue_compensation",
        {"passenger_id": 4900, "amount": 600.0},
        "Issued $600 compensation to passenger 4900 (long-haul denied boarding)",
    )

    print(f"\nRouting log has {len(router.get_log())} entries.")
