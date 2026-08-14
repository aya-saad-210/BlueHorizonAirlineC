# planning/algorithms/self_refine.py
#
# Self-Refine (spec section 8A): draft -> critique against an explicit
# rubric -> exactly one revision. Used where retrying/revising is cheap
# (e.g. wording the passenger disruption notice) -- not the same code
# path as Reflexion, which is for multi-trial, cross-trial-memory cases.

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from ..llm_client import generate_json, generate_text

RUBRIC = """Rubric for an IROPS passenger disruption notice:
1. States the flight number and the disruption reason in plain language.
2. States the concrete next step for the passenger (new flight, or how
   they'll be contacted) -- never vague ("we'll be in touch").
3. Mentions compensation ONLY if compensation was actually confirmed;
   never promises compensation that wasn't decided.
4. Professional, empathetic tone, under 120 words."""


class SelfRefineCritique(BaseModel):
    model_config = ConfigDict(extra="forbid")
    passes_rubric: bool
    issues: list[str]


def self_refine(instruction: str, context: str) -> dict:
    draft = generate_text(
        system="Draft the requested IROPS artifact. Be concrete.",
        user=f"Task: {instruction}\nContext:\n{context}",
    )
    critique = generate_json(
        system=f"Critique the draft strictly against this rubric:\n{RUBRIC}",
        user=f"Draft:\n{draft}",
        schema=SelfRefineCritique,
    )
    if critique.passes_rubric:
        return {"draft": draft, "critique": critique.model_dump(), "revision": draft, "revised": False}
    revision = generate_text(
        system=f"Revise the draft to fix ONLY the listed issues, per this rubric:\n{RUBRIC}",
        user=f"Draft:\n{draft}\nIssues to fix:\n" + "\n".join(f"- {i}" for i in critique.issues),
    )
    return {"draft": draft, "critique": critique.model_dump(), "revision": revision, "revised": True}
