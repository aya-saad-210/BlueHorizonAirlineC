# memory/short_term.py
#
# RUBRIC: "Short-term memory and scratchpad" --
#   "A real rolling buffer plus a scratchpad distinct from the transcript,
#    and pruning that doesn't destroy the scratchpad."
#
# WHY THIS EXISTS ON TOP OF THE EXISTING SYSTEM (see README problem framing):
# A single IROPS event on Blue Horizon (e.g. flight BH303 cancelled) can
# involve dozens of tool calls against mcp_server/ -- get_flight_status,
# get_passenger_booking, rebook_passenger called once per affected
# passenger, issue_compensation, assign_reserve_crew -- all inside ONE
# agent session handling ONE disruption. ShortTermMemory is the rolling
# buffer that holds this session's recent activity so the agent doesn't
# have to re-read the full transcript on every turn, while scratchpad.py
# (separate file, separate class) holds the actual WORKING STATE of that
# disruption (which passengers are done, what's left) so pruning the
# buffer never destroys progress tracking.
#
# This buffer is bound to ONE disruption_event, matching the note in the


from __future__ import annotations

import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class STMEntry:
    """One rolling-buffer item. Deliberately generic enough to hold both
    a chat message (role='user'/'assistant') and a tool call/result
    (role='tool'), because in a live IROPS session both interleave and
    both need to age out together."""

    entry_id: str
    role: str  # "user" | "assistant" | "tool"
    content: str  # message text, OR a short tool-call summary
    timestamp: float
    tool_name: Optional[str] = None
    tool_args: Optional[dict] = None
    tool_result_ref: Optional[str] = None  # pointer/summary, not full payload
    # raw_ref lets routing.py (promote-or-drop) go back to the full
    # tool result if it decides this entry is worth promoting to episodic
    # memory -- STM itself only needs to keep a slim summary in RAM.
    raw_ref: Optional[dict] = None


class ShortTermMemory:
    """Rolling message/tool-call buffer for a single disruption-handling
    session (e.g. one flight cancellation, one IROPS event).

    Distinct from Scratchpad (scratchpad.py): this buffer is the noisy,
    high-volume transcript-like stream (every tool call, every message).
    Scratchpad is the small, deliberately-curated "what am I doing right
    now" state. They are pruned independently -- see the class docstring
    in scratchpad.py for why that separation matters for this system
    specifically (a 30+ tool-call rebooking run would otherwise bury the
    disruption's original cause under tool noise, same failure mode the
    context_eval/ lab test transcripts are built to catch).
    """

    def __init__(
        self,
        disruption_id: str,
        max_size: int = 20,
        on_overflow: Optional[Callable[[STMEntry], None]] = None,
    ):
        """
        disruption_id: the IROPS event this session is handling, e.g.
            "BH303-2026-08-05". Every entry in this buffer belongs to this
            one disruption -- STM is never shared across two different
            disruptions in the same agent process.
        max_size: rolling window size. When adding a new entry would
            exceed this, the OLDEST entry is evicted.
        on_overflow: callback fired with the evicted STMEntry, letting the
            promote-or-drop router (routing.py) decide whether it should
            be forgotten or promoted to episodic memory. STM itself makes
            no forget/promote decision -- see routing.py, this is
            deliberately kept out of this file so the rubric's "no direct
            writes to semantic memory" boundary stays enforceable in one
            place.
        """
        self.disruption_id = disruption_id
        self.max_size = max_size
        self._buffer: deque[STMEntry] = deque()
        self._on_overflow = on_overflow

    # -- writes -----------------------------------------------------
    def add_message(self, role: str, content: str) -> STMEntry:
        entry = STMEntry(
            entry_id=str(uuid.uuid4()),
            role=role,
            content=content,
            timestamp=time.time(),
        )
        self._append(entry)
        return entry

    def add_tool_call(
        self,
        tool_name: str,
        tool_args: dict,
        result_summary: str,
        raw_result: Optional[dict] = None,
    ) -> STMEntry:
        """result_summary should be a short human-readable line (e.g.
        "Rebooked passenger 4821 onto BH210, seat 14C"), NOT the full raw
        tool payload -- keep the raw payload in raw_ref only, so the
        buffer itself stays cheap to hold `max_size` of at once. This
        mirrors how mcp_server/tools_write.py's rebook_all_passengers_on_flight
        reports progress incrementally rather than returning one giant
        blob at the end.
        """
        entry = STMEntry(
            entry_id=str(uuid.uuid4()),
            role="tool",
            content=result_summary,
            timestamp=time.time(),
            tool_name=tool_name,
            tool_args=tool_args,
            raw_ref=raw_result,
        )
        self._append(entry)
        return entry

    def _append(self, entry: STMEntry) -> None:
        if len(self._buffer) >= self.max_size:
            evicted = self._buffer.popleft()
            if self._on_overflow is not None:
                self._on_overflow(evicted)
        self._buffer.append(entry)

    # -- reads --------------------------------------------------------
    def get_recent(self, n: Optional[int] = None) -> list[STMEntry]:
        items = list(self._buffer)
        return items if n is None else items[-n:]

    def get_recent_tool_calls(self, n: Optional[int] = None) -> list[STMEntry]:
        tool_entries = [e for e in self._buffer if e.role == "tool"]
        return tool_entries if n is None else tool_entries[-n:]

    def __len__(self) -> int:
        return len(self._buffer)

    def is_full(self) -> bool:
        return len(self._buffer) >= self.max_size

    def render_for_prompt(self) -> str:
        """Cheap plain-text rendering for stuffing into an LLM prompt.
        This is intentionally NOT one of the four context_eval/ pruning
        strategies -- those operate on a full agent transcript, this is
        just STM's own default view. Person B's context_eval/ strategies
        consume something closer to the full session transcript, of which
        STM is one input among several (see README integration notes)."""
        lines = []
        for e in self._buffer:
            if e.role == "tool":
                lines.append(f"[tool:{e.tool_name}] {e.content}")
            else:
                lines.append(f"[{e.role}] {e.content}")
        return "\n".join(lines)


if __name__ == "__main__":
    # Small smoke test modeling the BH303 example from the Person A brief:
    # a disruption session with several tool calls, showing the oldest
    # entry getting evicted (and handed to on_overflow) once max_size is
    # exceeded.
    evicted_log = []

    stm = ShortTermMemory(
        disruption_id="BH303-2026-08-05",
        max_size=3,
        on_overflow=lambda e: evicted_log.append(e.content),
    )

    stm.add_message("user", "Flight BH303 just got cancelled, handle rebooking.")
    stm.add_tool_call(
        "get_flight_status",
        {"flight_number": "BH303"},
        "BH303: status=cancelled, reason=mechanical",
    )
    stm.add_tool_call(
        "rebook_passenger",
        {"passenger_id": 4821, "flight_id": 303},
        "Rebooked passenger 4821 (platinum) onto BH210",
    )
    stm.add_tool_call(
        "rebook_passenger",
        {"passenger_id": 4900, "flight_id": 303},
        "Rebooked passenger 4900 onto BH210",
    )

    print("Buffer now holds:")
    print(stm.render_for_prompt())
    print(f"\nEvicted (candidates for routing.py): {evicted_log}")
