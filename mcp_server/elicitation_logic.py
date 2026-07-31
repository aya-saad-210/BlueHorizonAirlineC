# elicitation_logic.py
# This file holds the shared "ask a human" logic used by more than one write tool.
# We keep it in one place so both assign_reserve_crew and issue_compensation ask
# for supervisor approval in exactly the same shape/format.
#
# How it fits in: when a write tool hits a risky situation (duty-hour limit,
# compensation over the auto-approve cap), instead of just rejecting the request,
# it calls ctx.elicit(...) which pauses the tool call and sends a structured
# question to whoever is on the client side. The tool only continues once a real
# answer comes back (approve / decline / cancel).

from pydantic import BaseModel, Field


class SupervisorDecision(BaseModel):
    """
    The structured shape of the answer we need back from a human supervisor.
    Kept flat (no nested objects) because elicitation schemas must be flat,
    with only primitive field types (str, bool, etc.).
    """
    approved: bool = Field(description="True if the supervisor approves this action, False if not")
    supervisor_id: str = Field(description="ID of the supervisor giving this decision, e.g. sup_002")
    note: str = Field(default="", description="Optional short note explaining the decision")


async def request_supervisor_approval(ctx, message: str):
    """
    Sends an elicitation request through the connected client and returns
    a simple (approved: bool, info: str) result that the calling tool can act on.

    ctx: the FastMCP Context object, passed in automatically by the tool function
    message: the human-readable question shown to the supervisor
    """
    result = await ctx.elicit(message=message, schema=SupervisorDecision)

    if result.action == "decline":
        return False, "Supervisor declined to answer. Action not taken."

    if result.action == "cancel":
        return False, "Elicitation was cancelled before a decision was made. Action not taken."

    # action == "accept" from here on, so result.data is populated.
    if not result.data.approved:
        note = result.data.note or "no reason given"
        return False, f"Rejected by supervisor {result.data.supervisor_id}: {note}"

    return True, f"Approved by supervisor {result.data.supervisor_id}."