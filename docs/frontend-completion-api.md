# Frontend completion integration handoff

This extends the existing frontend contracts, rather than replacing the accepted ADAPT design. Actual Data Hub reads below match the backend source at `3f813d5`. All other additions are **proposed HTTP contracts**, not implemented backend endpoints. Engine modules do not establish that an HTTP route exists.

## Existing backend reads now rendered in full

`GET /api/v1/data/health` returns an array of source records with the existing source fields plus `as_of`, `newest_date`, `age_hours`, `freshness_score`, `completeness`, `consistency`, `hard_failures`, and `checks[{id,passed,detail}]`. The three component scores are fractions in [0,1]; the composite source `score` is in [0,100]. Each check and hard failure is inspectable independently of source listing and mapping coverage. Missing canonical tables return the backend's 503, which remains visible.

`GET /api/v1/data/reconciliation` additionally renders `window_start`, `window_end`, and `platforms[{platform,platform_conversions,store_attributed_orders,over_attribution,over_attribution_reason,session_click_ratio}]`. The conversion/order ratio is not revenue excess. A null denominator is displayed as unavailable with its supplied reason, never zero. The optional extension preserves compatibility with earlier minimal contract examples.

Naive canonical logical timestamps are interpreted in the configured workspace timezone, Asia/Kolkata; timestamps with an offset retain their instant and are displayed in Asia/Kolkata. Invalid timestamps show unavailable instead of crashing a view. Offset-bearing timestamps are recommended for future services.

## Workspace objective and policy history

Navigate to `/executions?section=settings`.

`GET /api/v1/objective`:

```json
{"workspace_id":"demo","objective":"PROFIT","revision":"objective-1","supported_objectives":["PROFIT","GROWTH"],"can_change":true,"note":"Backend configuration and permissions."}
```

`PUT /api/v1/objective` receives `{workspace_id,revision,objective,reason}`. The revision and workspace are captured when review opens, not silently replaced while the dialog is open. A reason of at least ten characters is required. The response uses the GET shape with the same workspace, requested objective and a new revision. A stale revision must return 409; authorization is enforced by the backend. A wrong-context or unchanged-revision acknowledgement is rejected. Successful updates invalidate all cached reads. This is a default for future proposals: the optimizer editor still explicitly submits the objective shown in its selection. Existing proposals retain their recorded objective until the backend replaces or expires them. This route sends no budget instruction.

The fixture workspace is fixed to PROFIT and cannot change this setting. The server, rather than a frontend permission flag, must implement authorization, audit and invalidation of stale proposals.

`GET /api/v1/policy/history`:

```json
{"status":"AVAILABLE","note":"Backend audit record.","versions":[{"version":"policy-2","at":"2026-10-07T06:00:00Z","actor":"Admin review","reason":"Reviewed constraint update.","changes":[{"field":"reserve_floor","before":"4000","after":"5000"}]}]}
```

Version IDs must be unique, max 500. `NOT_AVAILABLE` must have an empty `versions` list. The UI renders history supplied by the backend; it does not invent audit versions or mutate guardrail thresholds. Channel-mode mutations remain under the existing `/policy` contract.

## Confidence qualification evidence

`GET /api/v1/learning/qualification` is an independent read on Learning. `{status,note,pools}` has at most 20 separate pools:

```json
{"world":"SIMULATED","model_version":"response-1","evaluated_at":"2026-10-07T06:00:00Z","outcome_definition":"Positive incremental contribution without a breach.","scope":"HELD_OUT","world_ids":["world-4"],"regions":[{"name":"LOW","total":0,"successes":0,"wilson_lower":null,"wilson_upper":null},{"name":"MID","total":10,"successes":7,"wilson_lower":0.397,"wilson_upper":0.892},{"name":"HIGH","total":20,"successes":18,"wilson_lower":0.699,"wilson_upper":0.972}]}
```

`world`: SIMULATED or REAL. `scope`: WARMUP or HELD_OUT. Include all three unique regions LOW [0,.6), MID [.6,.8), HIGH [.8,1]. The server computes 95% Wilson intervals. Counts are nonnegative integers with successes <= total; zero outcomes require null interval endpoints. Nonempty regions require both ordered endpoints in [0,1]. These validations check response plausibility, not the correctness of the calculation, labeling or evaluation. Observed rates are descriptive counts, displayed separately from confidence scores. Pools are never merged in the browser. Bands are informational and cannot authorize execution or replace policy readiness. Fixture pools are empty, not fake passing examples.

## Read-only SQL inspection

Open Copilot → SQL inspection. Proposed `POST /api/v1/copilot/sql` receives `{query,limit:500}`. Query length: 3–4000 characters. The response is `{query,columns,rows,truncated,elapsed_ms,as_of}`. Echo `query` exactly as trimmed; maximum 40 unique columns and 500 rows; all cells are finite numbers, bounded strings, booleans or null. Rows must match column width. Values render as text, including HTML-looking strings. A changed query clears previous results. Closing the tool cancels the pending read. Empty results, truncation and backend refusals are visible; fixtures have no SQL executor.

The server must enforce the plan's sqlglot single-SELECT allowlist, LIMIT and timeout, isolated read-only connection, external-access restriction and role controls. **A browser validation rule cannot enforce database safety.** POST here represents a read-only query, not an execution instruction. The LLM's bounded `select_proposal` tool remains backend-owned; this UI neither invents proposals nor directly changes budgets from chat.

## Connection coverage and failure boundaries

Connection now probes 27 static GET contracts: health, overview, decisions, anomalies, optimizer context, executions, ledger, outcomes, event polling, opportunities, fatigue, sources, source checks, mapping coverage, reconciliation, workspaces, calibration, accuracy, uplift, feedback, models, policy, policy history, objective, shadow log, confidence qualification, and scenario catalog. Probes remain read-only and use the real configured API even in fixture mode.

This is not exhaustive integration certification: entity-bound reads, streaming services, writes, SQL isolation and analytical correctness require actual backend tests. A response matching a schema is labeled ready for that read contract only. A view boundary preserves navigation after a render/lazy-load failure and asks the user to reload and verify submitted actions. Unknown routes offer a return to the Command Center.
