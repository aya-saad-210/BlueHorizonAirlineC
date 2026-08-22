# 18. Final Project — State Graph #1: Crew Reassignment Escalation (Person A)

## 18.1 Why This Is a Genuine State Graph, Not a Re-Skin of BH606

The Decomposition & Planning lab's BH606 case already escalates a duty-hour
breach to a supervisor — but it does so through `ctx.elicit()`, inside a
single live MCP session. That pause only exists for as long as the
connection stays open; kill the process and the pending elicitation is
gone. It also only tracks one wait (the supervisor's sign-off).

`state_graph/crew_reassignment_graph.py` tracks something genuinely
different and longer-lived:

* **Two independent external waits, not one.** First, the proposed reserve
  crew member's own reply (accept/decline) — which can realistically take
  hours, and has nothing to do with a supervisor. Second, only if that
  reply would breach the legal duty-hour limit, an admin's sign-off. A
  single `ctx.elicit()` cannot model two sequential, independently-timed
  waits that both need to survive a restart.
* **A real cycle.** If the proposed crew member declines, times out, or
  the admin rejects the override, the graph doesn't fail — it loops back
  to `propose_crew` and tries the next real candidate. This is a directed
  cycle in the literal graph-theoretic sense, which a DAG (by definition)
  cannot express.
* **A real failure mode a retry can't fix.** If no eligible reserve crew
  member exists at all (see `_query_eligible_crew`'s constraint-relaxation
  loop below), retrying the same query changes nothing — the graph fails
  the run and opens a ticket, distinct from the HITL pause path.
* **Durability is load-bearing, not decorative.** Both waits (crew reply,
  admin sign-off) are backed by real DB rows (`crew_reassignment_requests`,
  `hitl_tasks`) restored via `graph_checkpoints`, not in-memory state — see
  §18.5 for the actual crash-and-resume proof.

## 18.2 HITL Escalation Conditions (Written, Not Just Coded)

The graph must **not** let the model decide alone under exactly this
condition, enforced in `propose_crew`/`handle_crew_reply`:

> **Condition:** the candidate reserve crew member has accepted the
> reassignment, AND assigning them would push their logged duty hours for
> the current day to or above `MAX_DUTY_HOURS_PER_DAY` (14.00 hours, the
> same constant `mcp_server/tools_write.py` already enforces for the
> synchronous elicitation path — see `ISSUES.md` Issue 6 on keeping this
> one source of truth).
>
> **Why the model can't decide this alone:** duty-hour limits exist for
> real crew-fatigue/safety reasons, not just a business rule the model
> could reasonably relax under pressure to fill a gap. The person actually
> accountable for a duty-hour exception (an ops admin/supervisor) has to
> be the one who signs off, with the real policy text in front of them —
> that's exactly what `request_duty_override`'s RAG call is for (§18.3).

When this condition fires, `request_duty_override`:

1. writes a `hitl_tasks` row (`condition_type='duty_hour_breach'`) with
   the RAG-grounded policy explanation and citations in `payload_json`,
2. pauses the run (`status='waiting_hitl'`) — the graph does **not**
   auto-approve anywhere, in code or otherwise,
3. only resumes via `submit_hitl_decision(run_id, approved, decided_by,
   note)`, which is the entry point the platform's admin surface calls
   when a real admin acts on the task through the UI. The admin's actual
   decision (`approved`/`rejected`) is merged into graph state before
   `handle_hitl_decision` runs — the resumed run genuinely branches on
   what the admin chose, it doesn't just continue as if nothing happened.

## 18.3 Why Constrained ReAct + RAG (and Not the Other Two)

Two LLM-call additions are required per graph; the other two candidates
were **task decomposition** (nothing to decompose — this is a single
crew-picking decision, not a multi-step deliverable) and **Tree of
Thoughts/LATS** (there's no multi-step lookahead search here; the "which
candidate" decision is a single, one-shot comparison, not a branching
sequence of future actions worth exploring in a tree). That leaves:

* **Constrained ReAct in `propose_crew`.** The action space (which crew
  member to propose) must be restricted to real, currently-eligible rows
  a live DB query returns — the model cannot be allowed to invent a
  crew_id or ignore role/base-airport/already-assigned constraints, since
  proposing an ineligible crew member wastes a real, time-boxed
  notification cycle. The node runs a genuine Thought → Action →
  Observation loop: Act (query role+airport match) → if the Observation
  is empty, Act again with the airport constraint relaxed (role is never
  relaxed — a wrong-role crew member can never legally cover the seat) →
  once the Observation is non-empty, one LLM call reasons over exactly
  those rows and must return a `chosen_crew_id` from that set. If a live
  model ever returns an id outside the real Observation anyway, that
  choice is discarded and the top-ranked real candidate is used instead
  (see the `valid_ids` check in `propose_crew`) — the constraint is
  enforced in code, not just requested in the prompt.
* **RAG in `request_duty_override`.** The admin-facing explanation of
  *why* a duty-hour override needs sign-off has to be grounded in the
  actual policy text (`Rag/policy_docs/duty_time_policy.md`, Sections
  2–4: rest-period definitions, the 14-hour/day limit, and the
  supervisor-override conditions), not a paraphrase from the model's
  training data that could silently drift from the real, current policy.
  This reuses `Rag/naive_rag.py` (the Memory/RAG agent's own naive-RAG
  pipeline) filtered to `where={"source": "duty_time_policy.md"}`, rather
  than standing up a second, parallel retrieval pipeline — the retrieved
  chunks and citations are stored in `hitl_tasks.payload_json` alongside
  the short `reason` string, so the admin sees the real policy clauses,
  not just the model's summary of them.

## 18.4 Node Map

```
intake_disruption
      |
      v
propose_crew  <---------------------------+   (constrained ReAct)
      |                                   |
      v                                   |
await_crew_reply  (waiting_external)      |
      |                                   |
[external: submit_crew_reply(...)]        |
      |                                   |
      v                                   |
handle_crew_reply --- declined/timeout ---+   (cycle: try next candidate)
      |
   accepted
      |
      v
duty_hour_breach? --no--> finalize_assignment --> [finish]
      |
     yes
      v
request_duty_override  (waiting_hitl, opens a hitl_tasks row)   (RAG)
      |
[external: admin decides via platform]
      |
      v
handle_hitl_decision --- rejected ---> propose_crew (cycle, try next candidate)
      |
   approved
      v
finalize_assignment --> [finish]
```

## 18.5 Crash-and-Resume Proof

`state_graph/crash_resume_demo.py` starts a real run in one OS process,
pauses it at `await_crew_reply`, and blocks on `input()` so the process
stays genuinely alive (not just logically paused) until it's killed from
a second terminal:

```powershell
# Terminal 1
python crash_resume_demo.py start
# prints: this process pid = <PID>, run_id = <UUID>
# blocks on input() -- process is alive and waiting

# Terminal 2
taskkill /F /PID <PID>          # real, forceful kill
python crash_resume_demo.py resume <UUID>   # a DIFFERENT, fresh process
```

The resumed process reconstructs the run purely from `graph_checkpoints`
(nothing was kept in memory across the kill), continues from
`await_crew_reply` — not from `intake_disruption` — and reaches
`finalize_assignment` with no re-executed steps. See the demo recording
for the full terminal transcript of both processes and the `taskkill`
in between.

## 18.6 Real Crew Notification

`propose_crew` calls `state_graph/notifier.py`'s `notify_crew_member(...)`
once a candidate is chosen — this is a real channel, not a stub:

* **Live mode:** sends an actual email over SMTP (stdlib `smtplib`, no
  paid API, works with any SMTP account incl. a free Gmail App Password)
  when `SMTP_HOST` / `SMTP_USER` / `SMTP_PASSWORD` / `NOTIFY_FROM_EMAIL`
  are set in `.env`.
* **Mock mode:** if those aren't configured, prints a clearly-labeled
  `[MOCK NOTIFICATION]` line instead of silently doing nothing.
* **Either way**, a real row is written to `crew_notifications`
  (`data base/crew_notifications_schema.sql`) recording which channel
  actually fired (`email_live` / `email_mock`), so "was this crew member
  actually notified, and how" is a real query against the DB, not a log
  line someone has to have been watching for.

## 18.7 Known Limitation

`checkpointer.history()`'s per-step `status` currently reflects
`graph_runs.status` (one value for the whole run) rather than a
per-checkpoint status column, so re-running `history()` after a run has
completed shows every earlier step as `completed` even though it was
genuinely `waiting_external` at the time it was written. The
`graph_checkpoints` table would need its own `status` column to make the
audit trail exact per-step rather than only exact for the run's current
state. Not fixed in this submission; noted here so it's a documented,
understood limitation rather than a silent inaccuracy.
