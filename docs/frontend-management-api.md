# Workspace, registry, evaluation and archive handoff

This frontend extension preserves the existing ADAPT plan and design. Its view-model contracts are provisional. The current backend HTTP app exposes health and Data Hub reads; the management routes below need backend implementation and OpenAPI alignment. No browser test in this change establishes live integration or model/evaluation correctness.

Contract source: `adapt/web/src/api/management-contracts.ts`; adapter: `management.ts`. Paths are relative to `/api/v1`. Every response is validated, cookies are included, and failed API requests never substitute fixture data. POST requests carry request and idempotency headers. Backend semantic idempotency, permissions, context isolation and transactional precondition checks remain authoritative.

| Method | Path | Contract and behavior |
| --- | --- | --- |
| GET | `/workspaces` | `{active_id,items:[{id,name,currency:"INR",timezone:"Asia/Kolkata"}]}`. Unique IDs; active ID must exist. Up to 20 items in this MVP; larger installations need pagination. |
| POST | `/workspaces` | `{name,currency:"INR",timezone:"Asia/Kolkata"}` → created workspace. Creation must not implicitly activate it. |
| POST | `/workspaces/{id}/activate` | Proposed session-context endpoint, body `{}` → workspace list with requested active ID. Client rejects a mismatched acknowledgement and reloads the application only after a valid response. |
| GET | `/models` | Existing `modelSchema[]`: name/version, CHAMPION/CHALLENGER/NOT_AVAILABLE, nullable training timestamp and note. |
| GET | `/models/{name}/{version}` | `modelDetailSchema`: exact family/version, registry revision, role, nullable artifact/training identities, previous champion, metrics, checks and `allowed_actions`. |
| POST | `/models/{name}/promote` | **Optional proposed request endpoint**: reviewed version, registry revision, artifact hash and reason → `modelActionSchema`. The plan's automatic promotion remains backend-owned; expose PROMOTE only if server policy permits an operator request. |
| POST | `/models/{name}/rollback` | Same identity-bound request → acknowledged family/version/new registry revision/message. Server restores its recorded prior champion. |
| GET | `/eval/report` | `{status:"AVAILABLE"|"NOT_AVAILABLE",report:evaluationReport|null,note}`. Read a precomputed report; never starts an evaluation. |
| GET | `/decisions/{id}/archive` | Proposed `archiveSchema` manifest/processing trace. Response must match the selected decision ID and hash. |

## Workspace context

The UI reads current executions before enabling a switch; unknown/conflicting legs, unresolved execution states and unsynchronized mirrors block it. This is advisory. The backend must recheck access, execution state and concurrent operations atomically when activating, and bind every subsequent query/mutation to the authenticated workspace. A stale or ambiguous activation response requires backend context inspection; no local context change is inferred.

Fixture mode implements local demo isolation only. The default workspace retains the legacy storage key; other workspace IDs use separate keys. Decisions, scenarios, clock, calibration, outcomes, execution state, anomalies and ledger restore independently. CSV preview metadata is also workspace-scoped. Creation/switching fail visibly on storage errors. Workspace switching is blocked while an execution remains unresolved.

Each loaded browser tab retains its own in-memory fixture context. Another tab's selection changes the default context for a future reload, without relabeling data in an already loaded tab. Two tabs editing the **same** fixture workspace have no conflict-resolution protocol; use one tab per demo. API-mode import acknowledgement history is session-only and is not persisted to browser storage; the backend must supply canonical import status across refreshes.

## Model detail and mutation gates

Detail fields: `name`, `version`, `registry_revision`, `role` (CHAMPION/CANDIDATE/RETIRED), `artifact_hash` (nullable SHA-256), `training_snapshot_hash`, `trained_at`, `rollback_version`, `promotion_reason`, `checks:[{id,label,passed,detail}]`, `metrics:[{label,candidate,champion,baseline,unit}]`, `allowed_actions`, `note`. Metric values may be null; display unavailable rather than zero. Detail identity must match the requested version.

PROMOTE requires a candidate, an artifact, a nonempty set of passing server gates and explicit server permission. These response checks defend against malformed data; the browser does not train models, derive the gates or establish statistical validity. Backend promotion must recheck family-specific baseline performance, champion non-inferiority, interval coverage, replayability and registry revision. Rollback requires a champion and a nonempty recorded previous version. Missing permission leaves controls disabled. A confirmation requires a reason of at least ten characters and reviewed-artifact acknowledgement. Approved decision snapshots retain their original model artifacts.

Mutation body:

```json
{"version":"v2","registry_revision":"revision-2","artifact_hash":"<64 lowercase hexadecimal characters>","reason":"Reviewed artifact and backend validation gates."}
```

Acknowledgement: `{name,version,registry_revision,message}`. The UI invalidates server reads, does not locally promote a model, and preserves a stale-registry conflict in the dialog. Fixture mode provides no trained artifact or simulated registry changes.

## Head-to-Head report

Required top-level fields: `report_id`, ISO `generated_at`, `code_sha`, unique `seeds` (1–100), `horizon_days` (1–365), positive daily `budget_ceiling`, `reserve_floor` within the ceiling, `currency:"INR"`, `common_random_numbers`, `feasibility_envelope`, `fairness_statement`, `rows`. Each seed needs exactly one row for each strategy: safe-static, safe-contribution, adapt and oracle. Reject unknown header fields, duplicate/missing pairs, invalid numeric values and stock-risk days exceeding the horizon.

Rows: `{seed,strategy,realized_caa,spend,stock_risk_days,constraint_breaches,forced_interventions}`. Negative CAA is allowed; spend/counts are nonnegative. The table sums selected seeds and calculates total CAA / total spend; zero spend produces Unavailable. It does not infer confidence intervals, causal uplift, equal starting state or fair execution from a reported flag. The oracle is benchmark-only.

Users may inspect a JSON artifact smaller than 1 MB even with an unavailable backend. Uploaded reports remain component-local, are labelled as schema checked with unverified provenance/results, and never modify the engine. The displayed report can be exported; clearing it returns to the backend response. Synthetic reports exist only in tests/capture scripts and are not bundled into the application.

## Archived replay evidence

Manifest fields: decision ID/hash, snapshot ID, nullable environment fingerprint/code SHA/lock hash/seed, availability, note, artifacts and steps. AVAILABLE requires nonempty environment identities. Artifacts have unique IDs, kind/label, nullable hash, safe internal href and PRESENT/MISSING status. Steps have unique ID, ISO timestamp, label/detail and nullable artifact reference; references must resolve within the manifest. Links render only for present artifacts.

The frontend displays supplied provenance and steps. Availability does not prove reproduction or validate artifact bytes. Existing hash-bound `/replay` verification remains separate. Fixture mode supplies only captured UI evidence, marks engine/environment artifacts unavailable and leaves full processing steps empty. Never expose hidden world ground truth to the decision/Copilot UI; oracle aggregates belong only in the evaluation report.
