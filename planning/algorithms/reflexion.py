# planning/algorithms/reflexion.py
#
# Reflexion (spec section 8B), distinct from Self-Refine: multiple trials
# against a REAL grounded check (not a rubric opinion), a capped episodic
# reflection buffer that persists across trials within the run, and each
# reflection is actually read by the next trial's prompt -- verified in
# planning_eval by asserting trial 2's prompt literally contains trial 1's
# reflection text (see planning_eval/test_suite.py case
# "reflexion_duplicate_comp").

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable

from pydantic import BaseModel, ConfigDict

from ..llm_client import generate_json, generate_text
from ..models import EnvironmentFeedback

GroundedCheckFn = Callable[[str], Awaitable[EnvironmentFeedback]]


class Reflection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reflection: str


@dataclass
class ReflexionTrial:
    attempt: str
    feedback: EnvironmentFeedback
    reflection: str | None
    prompt_used: str  # kept so a grader/eval can prove the reflection was actually in-prompt


@dataclass
class ReflexionResult:
    success: bool
    trials: list[ReflexionTrial]
    episodic_buffer: list[str]


async def reflexion(
    task: str,
    attempt_fn: Callable[[str], Awaitable[str] | str],
    grounded_check: GroundedCheckFn,
    max_trials: int = 3,
    buffer_cap: int = 4,
) -> ReflexionResult:
    """attempt_fn(prompt: str) -> the model's concrete proposed attempt text
    (already including any reflections, since attempt_fn is given the full
    prompt to work from). grounded_check(attempt) -> a REAL EnvironmentFeedback,
    e.g. planning/environment.py's GroundedEnvironment.evaluate_compensation."""
    episodic_buffer: list[str] = []
    trials: list[ReflexionTrial] = []

    for trial_num in range(1, max_trials + 1):
        lessons = "\n".join(f"- {r}" for r in episodic_buffer[-buffer_cap:]) or "- None yet (first trial)."
        prompt = (
            f"Task: {task}\n"
            f"Reflections from previous failed trials (READ THESE before attempting):\n{lessons}\n"
            f"This is trial {trial_num}. Produce one concrete, complete attempt."
        )
        result = attempt_fn(prompt)
        attempt = await result if hasattr(result, "__await__") else result

        feedback = await grounded_check(attempt)

        reflection_text: str | None = None
        if not feedback.success:
            reflection_obj = generate_json(
                system="Write ONE concise, actionable reflection from a failed IROPS attempt so the next trial avoids the same real, verified mistake.",
                user=f"Task: {task}\nFailed attempt: {attempt}\nGrounded feedback (real, not guessed): {feedback.details}",
                schema=Reflection,
            )
            reflection_text = reflection_obj.reflection.strip()
            episodic_buffer.append(reflection_text)  # persists across trials in this run

        trials.append(ReflexionTrial(attempt=attempt, feedback=feedback, reflection=reflection_text, prompt_used=prompt))

        if feedback.success:
            return ReflexionResult(success=True, trials=trials, episodic_buffer=episodic_buffer)

    return ReflexionResult(success=False, trials=trials, episodic_buffer=episodic_buffer)


def reflection_carried_forward(result: ReflexionResult) -> bool:
    """Explicit, checkable proof (used by planning_eval) that a reflection
    from an earlier trial actually appeared in a later trial's prompt --
    not merely stored, but read."""
    if len(result.trials) < 2:
        return False
    for earlier, later in zip(result.trials, result.trials[1:]):
        if earlier.reflection and earlier.reflection in later.prompt_used:
            return True
    return False
