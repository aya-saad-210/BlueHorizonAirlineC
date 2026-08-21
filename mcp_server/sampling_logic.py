# sampling_logic.py
# This is the SAMPLING concern: sampling/createMessage.
#
# draft_disruption_message (the @mcp.prompt() in server.py) is just a static
# template -- it hands the host a fill-in-the-blanks prompt and the HOST's
# model writes the message. That's fine for a simple case, but it means the
# server itself has no way to get reasoning done when IT is the one that
# needs a written artifact as part of a tool's own logic (e.g. producing a
# ready-to-send passenger notice as a side effect of a tool call, regardless
# of which model the host happens to be using).
#
# generate_disruption_notice below solves that: it's a tool that, mid-call,
# asks the CONNECTED CLIENT's model to do the writing via
# ctx.session.create_message(...). This is the actual sampling/createMessage
# request-response defined by the protocol. The server never calls its own
# LLM here -- it borrows the client's, the same way elicitation borrows the
# client's human.

from mcp.server.fastmcp import Context
from mcp.types import SamplingMessage, TextContent
from mcp_server.dbase import get_connection


async def generate_disruption_notice(flight_number: str, ctx: Context) -> str:
    """
    Generates a ready-to-send passenger notice for a disrupted flight by
    asking the connected client's LLM to write it (via MCP sampling),
    using the real disruption reason pulled from the database.

    flight_number: the flight number to generate a notice for, e.g. BH202
    """
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute(
        "SELECT flight_number, status, disruption_reason FROM flights WHERE flight_number = %s",
        (flight_number,),
    )
    flight = cursor.fetchone()
    cursor.close()
    conn.close()

    if flight is None:
        return f"Cannot generate a notice: no flight found with number {flight_number}."

    if flight["status"] not in ("disrupted", "delayed", "cancelled"):
        return (
            f"Cannot generate a notice: flight {flight_number} has status "
            f"'{flight['status']}', which does not need a disruption notice."
        )

    reason = flight["disruption_reason"] or "an operational issue"
    prompt = (
        f"Write a short, polite passenger notice (3-4 sentences) for flight "
        f"{flight_number}, which is currently {flight['status']} due to {reason}. "
        "Mention that affected passengers will be rebooked or compensated per policy. "
        "Do not invent details that were not given."
    )

    result = await ctx.session.create_message(
        messages=[
            SamplingMessage(
                role="user",
                content=TextContent(type="text", text=prompt),
            )
        ],
        max_tokens=200,
    )

    if result.content.type == "text":
        return result.content.text

    return str(result.content)