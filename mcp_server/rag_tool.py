# mcp_server/rag_tool.py
#
# New MCP tool for this lab: answer_policy_question.
#
# RUBRIC: "Any changes to mcp_server/ or agent/ needed to wire memory and
# RAG into the existing agent loop. This should visibly reuse the existing
# server and database, not duplicate them."
#
# This tool does NOT duplicate any DB access or reservation logic already
# in tools_read.py / tools_write.py -- it answers a different class of
# question entirely: things that live in the compensation and duty-time
# POLICY MANUALS (rag/policy_docs/), not in the `flights` / `bookings` /
# `crew` tables. It calls straight into agent/rag_integration.py, which is
# itself a thin pass-through into rag/ -- no retrieval logic is
# reimplemented here.
#
# DECISION on the existing `policy://duty-time-limits` resource (see
# Server.py): kept AS-IS, unchanged. It stays the fast, always-loaded,
# two-number quick reference every session gets for free, because most
# duty-hour checks genuinely only need "8h flying / 14h duty" and paying a
# retrieval round-trip for that would be wasted latency. This tool is for
# the cases the short resource explicitly does not cover: sub-clauses,
# override conditions, exceptions, and anything in the compensation manual,
# which was never a resource at all before this lab. See README for the
# full "resource vs. vector store" write-up this decision is based on.

import sys
from pathlib import Path

_AGENT_DIR = Path(__file__).resolve().parent.parent / "agent"
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from rag_integration import answer_policy_question as _answer_policy_question  # noqa: E402


async def answer_policy_question(
    question: str,
    policy_area: str = "any",
) -> str:
    """
    Answers a question grounded in Blue Horizon's compensation or
    duty-time policy manuals (rag/policy_docs/), using retrieval-augmented
    generation with a Self-RAG-style relevance/support check before the
    answer is returned. Use this for anything the short duty-time resource
    doesn't cover: exceptions, sub-clauses, override conditions, or any
    compensation-eligibility question.

    question: the policy question in plain language, e.g.
        "does a mechanical delay found during pre-flight inspection still
        get compensation?"
    policy_area: "compensation", "duty_time", or "any" (default) to narrow
        the search to one manual when you already know which one applies.
    """
    doc_type_filter = None
    if policy_area == "compensation":
        doc_type_filter = "compensation_policy"
    elif policy_area == "duty_time":
        doc_type_filter = "duty_time_policy"

    result = _answer_policy_question(question, doc_type_filter=doc_type_filter)

    if not result["grounded"]:
        # Visible consequence of a failed Self-RAG check -- the tool
        # returns the fallback message, not a fabricated answer, and says
        # so explicitly rather than silently returning prose that looks
        # like a normal answer.
        return f"[UNGROUNDED -- no answer returned] {result['answer']}"

    citations = ", ".join(result["citations"]) if result["citations"] else "none"
    return f"{result['answer']}\n\n(Sources: {citations})"
