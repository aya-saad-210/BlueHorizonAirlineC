# planning/environment.py
#
# Replaces planning_lab/algorithms/environment.py's `random.betavariate`
# evaluator (spec section 9: "Do not leave the randomized evaluator in the
# final implementation"). GroundedEnvironment.evaluate(...) below never
# rolls a die -- it either re-derives the answer from a real DB query or
# actually calls the real write tool and reads its real "Approved:"/
# "Rejected:" response.
#
# UngroundedCritique is the other half of the required comparison: an
# LLM judging its OWN proposed action from the text alone, with no DB
# access. See planning_eval/test_suite.py case "reflexion_duplicate_comp"
# for the real recorded case where ungrounded critique accepts an action
# that the grounded environment correctly rejects (duplicate compensation
# -- the LLM has no way to know a pending row already exists unless it
# actually queries for it).

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from . import mcp_tools_adapter as tools
from .llm_client import generate_json
from .models import EnvironmentFeedback


class SelfCritique(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passes: bool
    issues: list[str]
    revision_hint: str


class GroundedEnvironment:
    """Real, external feedback for the IROPS planning subtasks. Every
    branch below is a real MCP tool call or real query against
    blue_horizon_db -- there is no randomness and no LLM in this class."""

    def evaluate_rebooking(self, passenger_email: str, old_flight_number: str, new_flight_number: str, requested_by: str = "agent_planning") -> EnvironmentFeedback:
        booking_id = tools.find_booking_id(passenger_email, old_flight_number)
        if booking_id is None:
            return EnvironmentFeedback(success=False, score=0.0, details=[
                f"No confirmed booking found for {passenger_email} on {old_flight_number}."
            ])
        result = tools.rebook_passenger(booking_id, new_flight_number, requested_by)
        success = result.startswith("Approved")
        return EnvironmentFeedback(success=success, score=1.0 if success else 0.0, details=[result])

    async def evaluate_crew_assignment(self, flight_number: str, crew_id: int, ctx, requested_by: str = "agent_planning") -> EnvironmentFeedback:
        result = await tools.assign_reserve_crew(flight_number, crew_id, requested_by, ctx)
        success = result.startswith("Approved")
        return EnvironmentFeedback(success=success, score=1.0 if success else 0.0, details=[result])

    async def evaluate_compensation(self, passenger_email: str, flight_number: str, amount: float, currency: str, reason: str, ctx, issued_by: str = "agent_planning") -> EnvironmentFeedback:
        result = await tools.issue_compensation(passenger_email, flight_number, amount, currency, reason, issued_by, ctx)
        success = result.startswith("Approved")
        return EnvironmentFeedback(success=success, score=1.0 if success else 0.0, details=[result])

    def precheck_duplicate_compensation(self, passenger_email: str, flight_number: str) -> EnvironmentFeedback:
        """Read-only grounded check used by Reflexion trial 2+: does a
        pending/approved compensation row already exist? This is exactly
        the condition issue_compensation() will reject on -- exposing it
        read-only lets a reflection be verified before spending another
        write attempt."""
        existing = tools.get_existing_compensation(passenger_email, flight_number)
        if existing is None:
            return EnvironmentFeedback(success=True, score=1.0, details=["No existing pending/approved compensation found."])
        return EnvironmentFeedback(success=False, score=0.0, details=[
            f"Existing {existing['status']} compensation of {existing['amount']} already on file."
        ])


class UngroundedCritique:
    """The LLM judging its own proposed action, from text alone -- no DB,
    no MCP call. This is the 'ungrounded' half of section 9's required
    comparison."""

    def evaluate(self, proposed_action: str, context: str) -> EnvironmentFeedback:
        critique = generate_json(
            system=(
                "You are reviewing a proposed IROPS action for obvious problems. "
                "You do NOT have database access -- judge only from the text given."
            ),
            user=f"Context:\n{context}\n\nProposed action:\n{proposed_action}\n\n"
                 "Does this action look correct? List any issues you can spot from the text alone.",
            schema=SelfCritique,
        )
        return EnvironmentFeedback(
            success=critique.passes,
            score=1.0 if critique.passes else 0.3,
            details=critique.issues or ["Ungrounded critique found no issues in the text."],
        )
