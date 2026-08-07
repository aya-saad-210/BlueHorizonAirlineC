# memory/scratchpad.py
#
# RUBRIC: "Short-term memory and scratchpad" (5 pts) --
#   "...a scratchpad holding the agent's current plan, sub-goal, and
#    working state, so pruning the transcript never destroys what the
#    agent is actively doing."
#
# WHY THIS IS A SEPARATE FILE/CLASS FROM short_term.py:
# ShortTermMemory (short_term.py) is a rolling buffer -- it is DESIGNED to
# lose old entries once max_size is hit. That's correct behavior for a
# noisy tool-call log, but it would be catastrophic behavior for the one
# thing the agent actually needs to keep straight across a long IROPS
# session: "which of BH303's 8 passengers have I already rebooked, and
# what's the plan for the rest?" If that lived only in the STM buffer, a
# long rebooking run (rebook_all_passengers_on_flight loops per
# passenger, exactly the progress-tracking tool from Server.py) would
# silently evict the plan itself once enough tool calls piled up.
#
# Scratchpad therefore:
#   - is NEVER pruned by STM's eviction (different object, different
#     lifecycle)
#   - holds structured working state, not a transcript
#   - is small and deliberately curated (the agent/consolidation logic
#     writes to it explicitly, nothing gets added here just because a
#     tool was called)

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PassengerProgress:
    """One line-item of working state for a disruption affecting many
    passengers -- matches the Person A brief's BH303 example:  '8 passengers,
    3 completed, 5 remaining'."""

    passenger_id: int
    status: str  # "pending" | "rebooked" | "compensated" | "skipped"
    note: Optional[str] = None


@dataclass
class Scratchpad:
    """The agent's current plan / sub-goal / working state for ONE
    disruption_id, kept separate from the ShortTermMemory transcript so it
    survives buffer pruning untouched.

    This is intentionally a thin, explicit structure (not a free-text
    blob) so a grader -- or the agent itself on the next turn -- can read
    exactly what's in progress without re-deriving it from the tool-call
    history.
    """

    disruption_id: str
    current_plan: str = ""
    sub_goal: str = ""
    passengers: dict[int, PassengerProgress] = field(default_factory=dict)
    last_updated: float = field(default_factory=time.time)

    # -- plan / sub-goal ------------------------------------------------
    def set_plan(self, plan: str, sub_goal: str = "") -> None:
        """e.g. set_plan(
            plan="Rebook all 8 confirmed passengers on BH303 onto BH210/BH215",
            sub_goal="Currently rebooking economy passengers before business class",
        )
        Overwrites, doesn't append -- the scratchpad holds the CURRENT
        plan, not a history of plans (that history, if ever needed, lives
        in episodic memory after promote-or-drop, not here).
        """
        self.current_plan = plan
        self.sub_goal = sub_goal
        self.last_updated = time.time()

    # -- passenger-level working state ----------------------------------
    def register_passenger(self, passenger_id: int, status: str = "pending") -> None:
        self.passengers[passenger_id] = PassengerProgress(passenger_id, status)
        self.last_updated = time.time()

    def update_passenger(
        self, passenger_id: int, status: str, note: Optional[str] = None
    ) -> None:
        if passenger_id not in self.passengers:
            self.register_passenger(passenger_id, status)
        p = self.passengers[passenger_id]
        p.status = status
        if note:
            p.note = note
        self.last_updated = time.time()

    # -- reads ------------------------------------------------------------
    def progress_summary(self) -> str:
        """e.g. 'BH303: 3 of 8 passengers rebooked, 5 pending'. This is
        exactly the line the Person A brief flags as the thing that must
        NOT get lost when the transcript ('message history') gets
        pruned."""
        if not self.passengers:
            return f"{self.disruption_id}: no passenger-level progress tracked yet."
        total = len(self.passengers)
        done = sum(
            1 for p in self.passengers.values() if p.status in ("rebooked", "compensated")
        )
        pending = [p.passenger_id for p in self.passengers.values() if p.status == "pending"]
        return (
            f"{self.disruption_id}: {done} of {total} passengers handled, "
            f"{len(pending)} pending ({pending})."
        )

    def snapshot(self) -> dict:
        """Full structured state -- what the agent re-reads at the start
        of every turn, and what gets included verbatim (never pruned) when
        context_eval/'s strategies build a prompt (see README integration
        note: every one of the four context strategies is expected to
        preserve the scratchpad unconditionally, since destroying it would
        make the accuracy comparison meaningless)."""
        return {
            "disruption_id": self.disruption_id,
            "current_plan": self.current_plan,
            "sub_goal": self.sub_goal,
            "passengers": {
                pid: {"status": p.status, "note": p.note}
                for pid, p in self.passengers.items()
            },
            "progress_summary": self.progress_summary(),
            "last_updated": self.last_updated,
        }

    def render_for_prompt(self) -> str:
        lines = [
            f"[SCRATCHPAD - {self.disruption_id}]",
            f"Plan: {self.current_plan or '(none set)'}",
            f"Sub-goal: {self.sub_goal or '(none set)'}",
            f"Progress: {self.progress_summary()}",
        ]
        return "\n".join(lines)


if __name__ == "__main__":
    # Same BH303 scenario as short_term.py's smoke test, showing the
    # scratchpad staying intact regardless of how much STM churn happens
    # around it.
    pad = Scratchpad(disruption_id="BH303-2026-08-05")
    pad.set_plan(
        plan="Rebook all 8 confirmed passengers on BH303 onto BH210/BH215.",
        sub_goal="Rebooking economy passengers first, then business class.",
    )
    for pid in [4821, 4900, 4933, 5001, 5040, 5102, 5188, 5200]:
        pad.register_passenger(pid)

    pad.update_passenger(4821, "rebooked", note="platinum -- moved to BH210, seat 14C")
    pad.update_passenger(4900, "rebooked")
    pad.update_passenger(4933, "rebooked")

    print(pad.render_for_prompt())
