# Contract: decision object, snapshot, hash, fingerprint (C3) and policy (C4)

Modules:
- C3: `backend/adapt/decide/{decisions,snapshot,hashing,fingerprint}.py`
- C4: `backend/adapt/policy/{engine,locks}.py`; config `guardrails.yaml` (the same object the optimizer is generated
  from), `objectives.yaml` and `health.yaml` (together they form the policy bundle)

## Decision object (spec §8.4)
- `intel.decisions` holds the immutable content and `decision_hash`. Fields: class (OPTIMIZATION | SAFETY), type,
  objective, legs (unit / platform / budget / campaigns, before → after), expected, inventory_risk_after,
  unallocated, reserve_floor, why_not, checks, trigger, remaining_shortfall.
- expected = p10 / p50 / p90 / E, model P(loss), Δnet revenue, raw_pred, calibrated_pred and the optimism
  correction factor.
- Stage 2 fields are null or empty and say so: `cost_of_inaction_7d`, `alternatives`.
- `ops.decision_events` is append-only (created, submitted, blocked, approved, rejected, expired, superseded).
- **Status is derived, never stored**: the latest lifecycle event before execution, then the saga state
  (SUCCEEDED → EXECUTED; ACCEPTED_PARTIAL / COMPENSATED / HUMAN_RESOLUTION_REQUIRED → PARTIAL; BLOCKED → BLOCKED;
  RESOLVED_MANUALLY → its final resolution).
- **Supersession**: a new run supersedes overlapping DRAFT / PENDING_APPROVAL decisions with an
  `invalidation_reason`. APPROVED and later decisions are never touched; a decision whose saga started is never
  superseded.

## Snapshot and hash (spec §22.4)
**Artifacts**: exact JSON, content-addressed under `<workspace>/artifacts/sha256/<hash>.json`, written once. They
cover:
- the state: units, curves with all 200 draws, SKUs
- the flags
- the policy bundle
- the calibration factor
- the cooldown set
- the kill switch

`manifest_sha256` identifies the snapshot. **Replay reads only these artifacts**.

**`decision_hash`** = sha256(RFC 8785 JCS(payload)). The payload is the content plus:
- `manifest_sha256`, `economics_hash`, `model_artifact_hashes`
- `optimizer_version`, `solver_config`
- `policy_version`, `policy_config_hash`, `config_hash`
- `lock_hash`, `code_sha`, `replay_environment_fingerprint`
- `seed`, `tz`

Normalisation: rupees → integers, other floats → 4 decimals, −0 → 0. NaN or ±Inf raises `NonFiniteValue`, and no
decision is created (T20).

`ops.replay_environments` records the code sha (+dirty), the uv / npm lock hashes, the Python version and the config
hashes.

**Replay** (`replay(db, decision_id)`) re-runs economics + optimizer (+ the safety candidate) + policy from the
artifacts and compares hashes. It runs in-process with snapshotted inputs only. Replay inside the archived git
worktree + lockfile environment (T68) is a script for Stage 2; the environment record it needs is already stored.

## Fingerprint and staleness (spec §2; T34, T44, T60, T69)
| Class | Contents | Expires when |
|---|---|---|
| EXACT | `inputs_manifest` (budgets, curve status, SKU weights incl. unmapped, ucr, nrpu, unit contribution, safety stock) + objective, λ, guardrails, policy version, autonomy mode, kill switch | any change; hashed into `economics_hash`, and the diff names the field |
| TOLERANCE | available units per SKU; each unit's inventory gate | stock moves > 10%, or the gate label changes |
| STATUS | open incidents on manifest campaigns; data health of the dependency sources | a downgrade |
| POLICY | policy version, kill switch | any change |
| TTL | decision age | older than 6 h (logical time) |

The check runs at approval and again at execution; a stale decision gets an `expired` event with the diff, and a
fresh decision needs new approval. Pacing and observed ROAS are not staleness inputs (they move daily and are
re-estimated by each run).

**Approval**: manager or admin only, bound to the exact `decision_hash` (HASH_MISMATCH otherwise), and only from
PENDING_APPROVAL.

## Policy (C4; spec §9.1, §9.2; Stage 1 = Approve mode)
**Version**: `pv-<hash>` of the policy bundle, recorded once in `ops.policy_versions`. Any change expires
approved-but-unexecuted decisions.

**validate** re-checks every rule against `optimizer.build_constraints` over the same guardrails:
- KILL_SWITCH
- TOTAL_BUDGET (SAFETY: never raises total spend)
- MAX_DAILY_CHANGE, UNIT_MIN_BUDGET, DAILY_RUPEES_MOVED_CAP, CHANNEL_SHARE
- NO_INCREASE_MODEL_UNAVAILABLE / MIX_UNCERTAIN
- FROZEN_OR_FIXED_UNITS: SAFETY may cut under TRACKING_FREEZE / DATA_DEPENDENCY, never under an execution freeze
- COOLDOWN: a verified ADAPT change within 3 days
- SAFETY_ONLY_REDUCES
- INVENTORY_GATE: the same predicate and draw subset as the optimizer

**Result**: all checks pass → PENDING_APPROVAL (SAFETY with `requires_review`); otherwise → BLOCKED with the failed
rules. A blocked OPTIMIZATION output is tagged `OPTIMIZER_POLICY_MISMATCH`, a bug signal, since both share one
feasible set.

**Locks**:
- `ops.entity_freezes`: frozen iff `cleared_at IS NULL`. A terminal saga clears only its own EXECUTION_UNCERTAINTY
  freezes; an unreconciled CONFLICT stays frozen.
- `ops.entity_reservations`: one active holder per entity. RECOVERY priority takes over a failed saga's reservation.
- `ops.kill_switch`.

**Flags fed to the optimizer** (`decide/run.policy_flags`):
- DATA_DEPENDENCY: a required source is YELLOW or RED
- TRACKING_FREEZE: an open tracking incident
- EXECUTION_FREEZE / SAFETY_COOLDOWN: from the freezes table

## TESTS
- **Unit** (`backend/tests/test_decisions.py`, 15 tests):
  - replay reproduces the hash
  - snapshot is real: mutating live inputs does not change the replay
  - approval is bound to the hash and role
  - EXACT change expires with the diff
  - a −2% stock move does not expire; a −30% move does
  - TTL
  - tightening max change to 5% expires the approval, and the regenerated decision respects 5%
  - kill switch
  - supersession with `invalidation_reason`
  - idempotent per run
  - NaN forbidden
  - policy is real: box, total, freezes, safety-only-reduces
  - inventory gate
  - safety requires review and replays
- **Integration**: the closed loop in `integration/test_pipeline_cycle.py` (replay match on a real-path decision).
