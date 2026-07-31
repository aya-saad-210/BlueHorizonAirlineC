# GitHub Issues — Blue Horizon IROPS Assistant

Owners assigned per the team's actual split:
- **عبدالعزيز سامي** — `db/` (schema, seed data, ERD) and `client/` (agent integration, demo)
- **آية سعد** — `mcp_server/server.py`, `tools_read.py`, `tools_write.py`, `dbase.py` (DB connection)
- **خالد** — the protocol-concern modules: `elicitation_logic.py`, `notifications_logic.py`, `sampling_logic.py`, `progress_logic.py`

Reference each issue from the PR that resolves it (e.g. `Closes #4`), and leave
a short closing comment explaining what changed. Where an issue's acceptance
criteria touch a teammate's file, that's noted under **Depends on** — the
owner is still the one accountable for the issue, but should coordinate
before closing it.

---

### Issue 1 — Schema & seed data don't yet cover every tool's edge cases
**Owner:** عبدالعزيز سامي
**Problem:** Several write tools only behave correctly (or trigger their
elicitation path) under specific data conditions — a crew member at the duty
limit, a passenger with an existing compensation record, a flight with no
confirmed bookings left. Without seed rows for these, the tools can't be
demonstrated repeatably.
**Constraint:** Must not just add "some data" — each edge case needs to map
to a specific validation branch in `tools_write.py` / `progress_logic.py`.
**Depends on:** validation logic in `tools_write.py` (آية سعد) to confirm which exact thresholds the seed data needs to hit.
**Acceptance criteria:**
- [ ] `duty_time_logs` has at least one crew member at/over `MAX_DUTY_HOURS_PER_DAY` or `MAX_FLYING_HOURS_PER_DAY`, and one clearly under both, as a control case
- [ ] At least one passenger has an existing `approved`/`pending` compensation row on a disrupted flight (duplicate-payout rejection path)
- [ ] At least one disrupted/cancelled flight has zero confirmed bookings (empty-batch path for `rebook_all_passengers_on_flight`)
- [ ] ERD (`db/erd.png` or Mermaid source) matches the actual `CREATE TABLE` statements exactly, including enums and FKs

---

### Issue 2 — Capability negotiation isn't checked by the client, only declared by the server
**Owner:** عبدالعزيز سامي
**Problem:** `server.py` declares tools/resources/prompts/elicitation/sampling support during `initialize`, but the client currently doesn't inspect `init_result.capabilities` before assuming elicitation or sampling will work.
**Constraint:** A client without elicitation support must not silently break when `assign_reserve_crew` or `issue_compensation` needs to pause.
**Depends on:** the exact capability flags `server.py` declares (آية سعد) so the client checks the right fields.
**Acceptance criteria:**
- [ ] Client reads `init_result.capabilities` and logs/branches on what's actually declared
- [ ] If elicitation isn't supported, client either doesn't offer the two supervisor write tools or clearly surfaces that limitation instead of hanging/erroring

---

### Issue 3 — Front-desk sessions can see supervisor-only tools before authenticating
**Owner:** خالد
**Problem:** `assign_reserve_crew` and `issue_compensation` change real state (crew legally on duty, money paid out) and must not be callable by an unauthenticated front-desk session.
**Constraint:** The gate has to be enforced server-side at registration time, not just hidden in a UI — and the client needs a real signal (not polling) when the gate opens.
**Depends on:** `authenticate_supervisor`'s tool wiring and `mcp.add_tool(...)` calls live in `server.py` (آية سعد); `notifications_logic.py` (خالد) owns the credential check and session-state flag those calls rely on.
**Acceptance criteria:**
- [ ] `assign_reserve_crew` / `issue_compensation` are not registered on `mcp` at server start
- [ ] `check_supervisor_credentials` in `notifications_logic.py` validates supervisor_id/pin correctly, including rejecting unknown IDs and wrong PINs
- [ ] `ctx.session.send_tool_list_changed()` is called immediately after a successful authentication, and the client's `message_handler` visibly reacts to `ToolListChangedNotification` without reconnecting

---

### Issue 4 — Duty-hour overages have no human check before a crew assignment is written
**Owner:** خالد
**Problem:** A pilot already at/over the daily duty-time limit could be assigned to another flight with no one signing off — a legal/safety violation, not just a UX issue.
**Constraint:** The tool cannot decide this alone (it's a judgment call about an overage, not a hard reject), so it needs to pause for an explicit human decision rather than auto-approving or auto-rejecting.
**Depends on:** the duty-hour check and DB write itself live in `assign_reserve_crew` in `tools_write.py` (آية سعد); `elicitation_logic.py` (خالد) owns `request_supervisor_approval` and the `SupervisorDecision` schema it's built on.
**Acceptance criteria:**
- [ ] `request_supervisor_approval` sends a flat (no nested objects) schema via `ctx.elicit()` and correctly handles `accept` / `decline` / `cancel`
- [ ] When `assign_reserve_crew` calls it at/over the duty limit, the assignment blocks on the result — no DB write happens before a decision comes back
- [ ] `decline`/`cancel` results in a rejection with no DB write; `accept` with `approved=True` results in the assignment being written with an override note

---

### Issue 5 — Large compensation payouts have no approval gate
**Owner:** خالد
**Problem:** An ops agent (or a model acting on its behalf) could authorize an arbitrarily large payout with no second signature.
**Constraint:** Needs a real dollar threshold enforced in code, independent of what the caller claims, plus protection against double-paying the same passenger for the same flight.
**Depends on:** the duplicate-payout check and DB write live in `issue_compensation` in `tools_write.py` (آية سعد); the elicitation pause itself is `elicitation_logic.py` (خالد).
**Acceptance criteria:**
- [ ] `issue_compensation` rejects duplicate pending/approved compensation for the same `(passenger_id, flight_id)` before checking the amount
- [ ] Amounts over `MAX_COMPENSATION_WITHOUT_APPROVAL` trigger `request_supervisor_approval` and block on the supervisor's decision
- [ ] No row is inserted into `compensation` unless validation passes and (if applicable) approval is granted

---

### Issue 6 — Duty-time policy is duplicated knowledge instead of something the model can read
**Owner:** آية سعد
**Problem:** The 8h/14h duty-time limits are referenced by `assign_reserve_crew`'s validation but the model has no way to look them up on its own before deciding whether to even attempt a risky assignment.
**Constraint:** This is static reference data, not an action — wrapping it in a tool would be the wrong shape.
**Acceptance criteria:**
- [ ] Exposed via `@mcp.resource("policy://duty-time-limits")` in `server.py`, discoverable via `resources/list`
- [ ] Content matches the actual numeric limits enforced in `tools_write.py` (single source of truth risk: flag if these ever drift apart)

---

### Issue 7 — Every client re-writes the same disruption-apology prompt from scratch
**Owner:** آية سعد
**Problem:** Drafting a passenger-facing apology for a disrupted flight is a common enough task that it shouldn't be reinvented per client integration.
**Constraint:** This is a host-side fill-in-the-blanks template (whatever model the host uses), distinct from the server actively generating text itself (see Issue 8).
**Acceptance criteria:**
- [ ] Exposed via `@mcp.prompt()` in `server.py` as `draft_disruption_message(flight_number, disruption_reason)`
- [ ] Discoverable via `prompts/list` and returns a filled template via `prompts/get`

---

### Issue 8 — Generating a ready-to-send passenger notice needs real reasoning, not a template
**Owner:** خالد
**Problem:** Unlike the static prompt template, some tools need actual generated prose as part of their own output (e.g., a notice referencing the real DB-sourced disruption reason) — and the server shouldn't assume which model is doing the writing.
**Constraint:** The server must not call its own model; it has to borrow the connected client's, the same way elicitation borrows the client's human.
**Depends on:** the flight lookup inside `generate_disruption_notice` uses `dbase.get_connection()` (آية سعد).
**Acceptance criteria:**
- [ ] `generate_disruption_notice` pulls the real `disruption_reason` from `flights` before building the prompt
- [ ] Uses `ctx.session.create_message(...)` (`sampling/createMessage`), not a server-owned LLM call
- [ ] Returns a rejection (not a hallucinated notice) if the flight isn't actually disrupted/delayed/cancelled

---

### Issue 9 — Batch rebooking blocks silently until the whole flight is done
**Owner:** خالد
**Problem:** Rebooking every passenger on a cancelled flight, one at a time, can take a while — leaving the client with zero feedback until the entire batch finishes looks like a hang.
**Constraint:** Needs to be a genuinely multi-step operation with real intermediate state (not a fake sleep) to justify progress reporting.
**Depends on:** the booking queries and INSERT/UPDATE statements use `dbase.get_connection()` (آية سعد).
**Acceptance criteria:**
- [ ] Loops over every `confirmed` booking on the disrupted flight, calling `ctx.report_progress(progress, total, message)` per passenger
- [ ] Skips (and reports separately) passengers already booked on the target flight instead of double-booking
- [ ] Returns a clean "no confirmed bookings" result for an empty batch rather than erroring

---

### Issue 10 — Write tools only validate types, not business rules or who's asking
**Owner:** آية سعد
**Problem:** A schema that says `crew_id: int` doesn't stop a negative ID, an already-rebooked booking, or an unauthenticated caller from reaching the database.
**Constraint:** Validation and authorization need to live in the handler itself, independent of whatever the JSON Schema already checks.
**Acceptance criteria:**
- [ ] Every write tool schema uses `required` + `additionalProperties: false` with real typed fields (no bare `dict`/`**kwargs`)
- [ ] Each handler independently re-validates business rules (positive IDs, non-empty reason, valid flight status) before touching the DB
- [ ] Each handler checks `requested_by`/`issued_by` looks like a real ops agent ID (`agent_...`) before proceeding

---

### Issue 11 — Server-side transport doesn't match how the airline would actually deploy it
**Owner:** آية سعد
**Problem:** stdio is a single local subprocess per agent — fine for development, wrong for a multi-location airline where many ops agents' clients need to reach one shared server.
**Constraint:** Needs to preserve stdio as a fallback for local debugging (MCP Inspector) while making Streamable HTTP the real deployment path.
**Acceptance criteria:**
- [ ] `server.py` defaults to `streamable-http`, with `python server.py stdio` kept as an explicit fallback
- [ ] Commit history shows the stdio → Streamable HTTP transition happening over time, not committed as one block

---

### Issue 12 — Client only exists for local stdio testing, not the deployed transport
**Owner:** عبدالعزيز سامي
**Problem:** `client_stdio.py` spawns the server as a local subprocess, which won't reflect how ops agents actually connect once the server is deployed over Streamable HTTP.
**Constraint:** Needs to exercise the same tool calls (read tools, `authenticate_supervisor`, notification handling) against a live HTTP server, not just stdio.
**Depends on:** the server actually running in `streamable-http` mode (آية سعد, Issue 11) before this can be tested end-to-end.
**Acceptance criteria:**
- [ ] `client/client_http.py` connects to a running `python server.py` (HTTP) instance instead of spawning a subprocess
- [ ] Runs the same demo sequence as `client_stdio.py` (list tools, read-only call, authenticate, list tools again) successfully over HTTP

---

### Issue 13 — No single place shows every concern firing, for grading or for us
**Owner:** عبدالعزيز سامي
**Problem:** Without a fixed, repeatable demo script, the team risks a "lucky" live demo that skips a concern or hits an untested data state.
**Constraint:** Needs to use the same fixed seed data every time, not ad hoc IDs picked live.
**Depends on:** the seed data from Issue 1 (عبدالعزيز سامي) already covering every trigger condition.
**Acceptance criteria:**
- [ ] `demo_transcript.md` (or recording) shows every one of the 8 protocol concerns firing against the actual seed data
- [ ] README's comparison note (read-only vs. write, elicitation gating, missing-capability behavior) is included as evidence, not a separate afterthought
