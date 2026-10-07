# Contract: execution saga (C5) and pipeline cycle (C2)

## C5: `backend/adapt/execute/{adapters,saga}.py`, `config/platforms.yaml`
**Adapters** (absolute setters only; no relative mutation exists):
- **Mock Google Ads v25**: `campaignBudgets:mutate` with `amountMicros` (multiples of 10,000). Read-back is a
  separate GAQL `googleAds:search` call; the request payload is never echoed back as the observed state.
- **Mock Meta v25.0**: `POST /{campaign_id}` with `daily_budget` in USD cents; read-back is
  `GET ?fields=daily_budget,status`.
- Verification tolerances are one minor unit of the platform currency.
- Execution mode is fixed at startup in `platforms.yaml`. `live` without a built adapter → every leg BLOCKED; there
  is never a mock fallback (T23, T30).
- Ledger request and response bodies are redacted (Authorization, developer-token, access / refresh tokens,
  `*_secret`).

**Execution** (`execute_decision`):
- **Pre-checks**: the decision must be APPROVED. The fingerprint is re-checked first; stale → EXPIRED with the diff,
  and nothing is sent. The unique `(decision_id, kind)` saga row is the compare-and-set: a second or concurrent
  request gets 409 (T16, T27).
- **Reservations**: acquired on every budget and campaign touched; a holder elsewhere → 409.
- **Leg order**: risk-reducing legs first.
- **Per leg**, every transition is persisted before the next external call:
  1. Pre-read. A value different from the decision's `before` → CONFLICT; the saga aborts and the entity freezes
     (T13).
  2. SENT.
  3. On a timeout, 5xx or lost response, **read-after-write first**: the desired value present → VERIFIED with no
     second mutation (T17). Otherwise retry as an absolute set, up to 3 attempts.
  4. Verify by polling read-back 3 times. Still the old value → UNKNOWN (never assumed) plus a freeze (T25).
- **Saga outcome**:
  - all VERIFIED → SUCCEEDED
  - an UNKNOWN leg → PARTIAL (frozen) until `reverify` resolves it
  - nothing applied → BLOCKED
  - some legs applied: if every completed leg is risk-reducing → ACCEPTED_PARTIAL (T11); otherwise → COMPENSATING →
    COMPENSATED, or COMPENSATION_FAILED → HUMAN_RESOLUTION_REQUIRED → `resolve_manually` (after a fresh read-back
    confirms the claimed state) → RESOLVED_MANUALLY
  - a CONFLICT leg leaves only through `reconcile_conflict`: RECONCILED_VERIFIED or ABANDONED, after which the
    decision is BLOCKED (never SUPERSEDED)
- **Terminal states** release reservations, clear only this saga's EXECUTION_UNCERTAINTY freezes, and emit
  `action_executed` events (dirty marks).
- **Crash recovery** (`recover`): a leg left in PREREAD_OK or SENT is re-read. The desired value present →
  VERIFIED; the pre-read value unchanged → resend the same absolute state; anything else → UNKNOWN. The saga then
  continues.
- **Rollback**: the platform must still show the executed values, else ROLLBACK_CONFLICT and nothing is sent
  (T14, T26). Otherwise a ROLLBACK saga runs through the same machinery (T12).
- The simulation mirror (`sim_sync_state`) is NOT_REQUIRED in mock mode; the LIVE hybrid mirror belongs to the
  Stage 2 Google adapter.

**TESTS** (`integration/test_execution_saga.py`, against the world's mock platform APIs, 11 tests):
- happy path: decrease first, read-back, reservations released, events emitted
- duplicate and concurrent execution: one saga, one mutation
- timeout-after-success
- 503 on the increase leg → ACCEPTED_PARTIAL
- compensation
- external edit → CONFLICT → EXECUTION_FREEZE seen by the optimizer flags (T42) → reconcile → BLOCKED
- stale read-back → UNKNOWN → re-verify → EXECUTED
- crash after send → recover without resending
- live mode never mutates
- rollback and ROLLBACK_CONFLICT
- reservations serialise sagas; ledger redaction

## C2: `backend/adapt/pipeline/{cycle,events,scheduler}.py`, `backend/adapt/predict/forecasts.py`
**Cycle** (`run_cycle`): ingest → reconcile (canonical + marts + health) → DQ gate → detect → diagnose → predict →
forecast → optimize → decide → policy → auto_execute (NOT_BUILT: Approve mode) → verify (re-verify UNKNOWN legs) →
safety_monitor (NOT_BUILT: Stage 2) → measure (matured outcomes of executed decisions) → learn.
- Every step is logged in `ops.pipeline_steps`.
- **Idempotent per run_id**: a COMPLETED run returns its stored summary without recomputing.
- **predict** refits only when there is no champion, the champion is ≥ 7 days old, or an outcome matured since the
  last fit (`outcome_matured` events). Otherwise it is inference only.

**Events**: `ops.events` is append-only with a dedupe key. A duplicate event is stored once and marks its entity
dirty once (T15). The run clears the marks it consumed.

**Scheduler** (`catch_up`): runs every simulated day between the last completed run and the world's today, in order.
In the simulator the clock moves when the Scenario Lab advances the world; a live cron would call the same function.

**Stored forecasts** (they close the B8 residual-pool limitation):
- Every run stores tomorrow's forecast per unit at the current budget, using the champion model.
- On the first run, the 56 days before it are backfilled with weekly refits on data dated before each origin.
- **Measured fact**: the sources expose current entities and budgets only, so ADAPT's knowledge of them starts at
  its first sync. As a result:
  - the canonical state cannot be rebuilt as of an earlier origin, so the fits use the first run's marts restricted
    to earlier dates (the only look-ahead is refund maturation on training days, already unbiased through the
    conditional expectation)
  - backfilled forecasts condition on each day's realized spend (the revenue-given-spend error the counterfactual
    needs)
  - live forecasts condition on the planned budget, and a residual counts only if that budget was in effect
- The outcome counterfactual uses these 56 days of rolling-origin residuals. Fewer than 28 valid days falls back to
  the curve's P + D errors, and the outcome's method names the pool it used.

**TESTS** (`integration/test_pipeline_cycle.py`): events mark once; the full closed loop on the fixture world through
the real execution path:
1. Day-0 cycle: all 15 steps logged, the 2 later-stage steps NOT_BUILT; the replay of the run_id is idempotent; ≥ 56
   forecast days stored.
2. The decision replays to the same hash; its basis uses rolling-origin residuals.
3. Approve → saga SUCCEEDED → EXECUTED.
4. Daily catch-up cycles until the outcome matures (day 3): a verdict, a CI containing the realized value, and
   calibration exactly once or a stated reason.
5. The next cycle refits because an outcome matured.
