# Blue Horizon Airlines — Week 4 Demo Transcript

## Demo Goal

This demo shows the Decomposition & Planning extension for Blue Horizon Airlines and covers the required concerns:

- Decomposition-first
- Dynamic/interleaved decomposition
- Plan-and-Solve
- Tree of Thoughts
- LATS
- Self-Refine
- Reflexion with cross-trial memory
- Grounded vs. ungrounded critique

---

## 1. Real Planning Request

**User:**

> Resolve the IROPS situation for flight BH606. Before continuing with the disruption resolution, verify the crew's legal duty and flight-hour limits. If a crew member is over the limit, do not proceed blindly; escalate and re-plan based on the supervisor decision.

**Agent:**

> I will decompose the request into dependent sub-tasks and select the appropriate planning strategy for each non-trivial step.

---

## 2. Decomposition-First

**Agent — initial plan:**

```text
BH606 IROPS resolution
|
+-- Check flight state
|
+-- Verify crew duty/flight-hour limits
|
+-- Escalate any duty breach
|
+-- Await supervisor decision
|
+-- Replace crew if required
|
+-- Re-verify compliance
|
+-- Continue IROPS resolution
```

The complete plan is generated before execution.

For a stable case such as `BH404_stable_single`, the recheck confirms:

```text
Expected divergence: False
Recomputed divergence: False
```

This demonstrates a case where dynamic replanning is unnecessary.

---

## 3. Dynamic / Interleaved Decomposition

**Agent:**

> I will now execute the same request using dynamic decomposition. The next sub-task will depend on the result of the previous observation.

### Step 1 — Verify crew duty limits

**Sub-task:**

> Perform crew duty and flight-hour limit verification for BH606 flight crew to ensure regulatory compliance before proceeding with IROPS resolution.

**Grounded result:**

```text
Capt. Hossam Zaher (pilot, original):
8.0 flying hrs / 14.0 duty hrs today
AT/OVER the legal duty-hour cap
requires supervisor approval to assign...
```

**Agent:**

> The crew verification produced an unexpected constraint. I should not continue with the original plan blindly.

### Step 2 — Re-plan

**New sub-task:**

> Escalate BH606 crew duty-hour breach for Capt. Hossam Zaher to supervisor for authorization or reassignment.

### Step 3 — Continue dynamically

**Next sub-task:**

> Await supervisor decision on Capt. Hossam Zaher's duty-hour breach; if denied, initiate crew replacement procedure.

### Step 4 — Re-verification

**Next sub-task:**

> Confirm supervisor decision regarding Capt. Hossam Zaher and, if denied, initiate crew replacement procedure for BH606 before re-verifying legal compliance for the new crew.

### Evidence

The divergence recheck reports:

```text
BH606_duty_breach
Expected: True
Recomputed: True
```

All five recorded steps are marked as triggered by the duty-breach observation.

**Conclusion:**

> Dynamic decomposition changed course because an early real observation invalidated the assumptions behind the original sequence.

---

## 4. Routing to Planning Algorithms

The planning router does not treat Plan-and-Solve, Tree of Thoughts, and LATS as interchangeable.

```text
Sub-task shape
     |
     +-- predictable single-pass reasoning --> Plan-and-Solve
     |
     +-- multiple candidate branches --------> Tree of Thoughts
     |
     +-- high-risk search + external feedback -> LATS
```

---

## 5. Plan-and-Solve

**Sub-task:**

> Produce the single-pass plan for a deterministic IROPS verification step.

**Plan phase:**

```text
1. Identify required verification.
2. Perform the required check.
3. Inspect the result.
4. Return the verified outcome.
```

**Solve phase:**

> Execute the planned steps in order without branching.

**Result:**

> Plan-and-Solve is appropriate when the sub-task has a clear sequence and does not need branch exploration.

---

## 6. Tree of Thoughts

**Sub-task:**

> Consider candidate next actions for an IROPS resolution step where several valid alternatives are possible.

**Agent:**

```text
Candidate A -> continue original plan
Candidate B -> escalate and wait for authorization
Candidate C -> initiate reassignment path
```

Each candidate is evaluated and the search keeps/prunes branches according to the Tree of Thoughts procedure.

**Agent:**

> Candidate B is preferred because the observed constraint makes continuing the original plan unsafe.

**Result:**

> Tree of Thoughts is useful when comparing multiple plausible next actions before committing.

---

## 7. LATS with Grounded Feedback

**Sub-task:**

> Select a safe compensation action for a disrupted passenger while validating the proposed action against the actual system state.

LATS explores candidate actions using external feedback rather than relying only on the model's opinion.

### Grounded feedback

A proposed USD 120 compensation action is checked against the actual state.

**Environment:**

```text
Existing pending compensation of 120.00 already on file.
```

**Grounded result:**

```text
success = false
score = 0.0
```

The candidate is rejected.

The latest evaluation reports:

```text
LATS
100% grounded success
2.0 average LLM calls
417.75 average tokens
```

---

## 8. Grounded vs. Ungrounded Critique

The same candidate is evaluated by an ungrounded textual critic.

### Ungrounded critique

```text
success = true
score = 1.0

Ungrounded critique found no textual issues -- looks well-formed.
```

### Grounded environment

```text
success = false
score = 0.0

Existing pending compensation of 120.00 already on file.
```

### Required distinction

```text
ungrounded_wrongly_accepted_grounded_caught_it = true
```

**Agent:**

> The textual answer looked valid, but the real system state showed that the compensation already existed. Grounded feedback caught a duplicate action that ungrounded critique missed.

---

## 9. Self-Refine

**Draft:**

> Issue the proposed compensation to the passenger.

**Critique against explicit rubric:**

```text
- Is the action justified?
- Is the compensation type specified?
- Is policy eligibility verified?
- Is duplicate compensation ruled out?
```

**Revision:**

> Verify policy eligibility and existing compensation records before issuing any new compensation.

**Result:**

> Self-Refine is appropriate when the output is cheap to revise and one explicit critique/revision cycle is sufficient.

---

## 10. Reflexion

**Trial 1**

**Agent proposes:**

> Issue the USD 120 compensation.

**Grounded feedback:**

```text
Existing pending compensation of 120.00 already on file.
```

**Reflection stored:**

> Verify existing compensation records for the specific passenger and flight before initiating a new claim to prevent duplicate entries.

### Trial 2

The reflection is carried forward through the episodic buffer.

**Agent:**

> Before issuing compensation, I will check for an existing pending compensation record.

**Result:**

```text
success = true
trials = 2
reflection_carried_forward = true
```

**Conclusion:**

> Reflexion learned from the failed first trial and used the stored reflection in the next trial.

---

## 11. Final Evaluation Evidence

Latest live evaluation:

| Method | Result |
|---|---|
| Decomposition-first | Structural evaluation |
| Dynamic decomposition | 100% cases diverged from naive plan |
| Plan-and-Solve | 4.5 average LLM calls |
| Tree of Thoughts | 9.0 average LLM calls |
| LATS | 100% grounded success |
| Self-Refine | 3.0 average LLM calls |
| Reflexion | 3/3 grounded success; 1/3 cases needed >1 trial |

The evaluation writes four JSON traces and the comparison table to:

```text
planning_eval/artifacts/
```

---

## 12. Demo Conclusion

**Agent:**

> The planning extension demonstrates both decomposition strategies, three planning algorithms, two scopes of self-correction, and grounded external feedback. The evaluation shows that dynamic decomposition reacts to the BH606 duty-hour breach, LATS succeeds with grounded feedback, and Reflexion carries useful experience across trials.

**End of demo.**
