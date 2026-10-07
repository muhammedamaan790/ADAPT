# Frontend objective, policy and scenario handoff

This extends the v2.4.3 frontend without implementing optimizer mathematics or autonomous execution. Paths are relative to `/api/v1`. These are proposed view-model contracts; the current backend exposes health and Data Hub reads, with detection/diagnosis modules but no HTTP policy or optimizer integration yet. Align these shapes with authoritative OpenAPI before live use.

## Objective controls

`GET /optimizer/context` returns `optimizerContextSchema` with the current proposal's `objective`, `decision_id`, `decision_hash`, `policy_version`, `supported_objectives`, constraints and campaigns. The browser requires the hash and objective to agree with the proposal fetched from `/decisions`. Missing `objective` defaults to PROFIT only for compatibility with Stage 1 responses. Backend capabilities must list selectable objectives.

| Objective | Policy description shown in UI |
| --- | --- |
| PROFIT | Contribution after ads, with downside penalty |
| GROWTH | Net revenue subject to the contribution floor |
| ACQUISITION | New customers subject to contribution tolerance |
| INVENTORY_CLEARANCE | Contribution plus holding-cost benefit on excess stock |
| MARGIN_PROTECTION | Contribution with stronger downside and blended margin floor |
| BALANCED | Contribution with downside penalty and a revenue floor |

Selection applies to a what-if or a new proposal. It does not modify workspace defaults or execute budgets. The browser calculates no alternative objective score, customer acquisition estimate or inventory holding benefit. Stage 1 bundled examples support only PROFIT.

- `POST /optimizer/whatif` takes `AllocationInput`: proposal ID/hash, policy version, selected objective and complete budget legs. `evaluationSchema` must return matching ID/hash/objective. An optional `objective_value:{value,label,unit}` uses `INR`, `CUSTOMERS` or `SCORE`; null means unavailable, not zero. Financial median ΔCAA and bootstrap P(loss) remain financial measures for every objective.
- `POST /optimizer/run` takes `{objective}` and returns a decision for that objective. A mismatched response is rejected.
- `POST /decisions/{id}/modify` takes the allocation input and returns a **new** decision ID, requested objective and `follows` equal to the original ID. Original IDs cannot be reused as revisions. The server must revalue, supersede atomically and preserve the original history.

Changing an objective clears the displayed valuation. Budget edits clear it as before. Browser input checks are explanatory; authoritative feasibility, risk, score and execution checks remain on the backend.

## Execution policy and readiness

`GET /policy` returns `policySchema` from `adapt/web/src/api/policy-contracts.ts`:

```json
{
  "policy_version": "authoritative-policy-version",
  "revision": "current-concurrency-token",
  "note": "Backend policy summary",
  "channels": ["ChannelPolicy objects described below"]
}
```

Each channel includes `channel:Meta|Google|TikTok|Amazon`, `mode:OBSERVE|APPROVE|SIMULATION_AUTONOMOUS|PRODUCTION_AUTONOMOUS`, `execution_mode:MOCK|LIVE`, `test_account`, `serves_ads`, `allowed_modes`, `simulation`, `production`, and `note`. `allowed_modes` is a server permission/capability result for the current user, not a frontend authorization calculation. An empty array disables all mode requests.

Each separate readiness pool contains `eligible`, `executed_decisions`, `measured_outcomes`, `independent_worlds`, nullable `wilson_lower`, `reliability:PASS|FAIL|INCONCLUSIVE|UNAVAILABLE`, `guardrail_violations`, `checks:[{id,label,passed:boolean|null,detail}]` and `note`. Counts concern matured measured outcomes and executed decisions, not proposals or shadow forecasts. Simulation and real pools must never be mixed. The server must use the candidate's qualified confidence region, count outcomes once and describe the scope/worlds in the note.

The frontend rejects inconsistent **eligible** reports: fewer than ten executed decisions, fewer than ten simulation or thirty real measured outcomes, Wilson 95% lower bound below 0.60, non-PASS held-out reliability, violations, missing/unknown/failed checks. Simulation also needs three independent warm-up worlds. Required true check IDs are `TRACKING_HEALTH` and `EXECUTION_HEALTH`; production additionally requires `CHAMPION_MODELS` and `ADMIN_POLICY_REVIEW`. Supply other mandatory policy checks as well; every supplied check must pass for an eligible report.

Simulation mode permission requires MOCK plus qualifying simulation evidence. Production eligibility/permission requires LIVE, a serving account, no test-account flag and qualifying **real** evidence. Google test-account budget mutations/read-backs do not create advertising outcomes. A configured autonomous mode can remain visible after eligibility is lost: the browser does not invent a successful downgrade. The server must stop or downgrade subsequent candidates to review.

The read-only fixture policy provides zero qualification evidence, Approve mode and no allowed mode changes. It does not infer readiness from illustrative UI outcomes.

`PUT /policy` takes:

```json
{
  "policy_version": "reviewed-policy-version",
  "revision": "reviewed-concurrency-token",
  "channel": "TikTok",
  "mode": "SIMULATION_AUTONOMOUS",
  "reason": "Reviewed qualification evidence for this mock channel."
}
```

It returns the complete updated policy. The requested channel's mode must be confirmed. The review dialog requires a reason of at least ten characters and explicit acknowledgement. This request changes future policy behavior; it sends no budget-execution request. The server must authenticate and authorize the operator, recheck evidence, health and revision atomically, audit the change, and return 409 for stale reviews. The existing frontend does not implement login/security. Every future autonomous candidate still needs model risk, movement limits, dependency freshness, entity reservations and the candidate's region qualification checks from the final plan.

Transport includes credentials, request IDs and idempotency keys. Mutations do not automatically retry. Header generation is not proof of semantic deduplication or server authorization. Timeouts/conflicts require refresh and backend state verification.

## Observe-mode shadow log

**Proposed read:** `GET /learning/shadow` returns `shadowSchema` with `status:AVAILABLE|NOT_AVAILABLE`, `note`, `records`. Unavailable logs must be empty. Unique strict records contain `id`, `decision_id`, `decision_hash`, `channel`, `world:SIMULATED|REAL`, ISO UTC `at`, `method:FORECAST_ONLY`, nullable decision `expected`, `guardrail_breaches:string[]`, and `note`.

These are unexecuted recommendations. The world label describes account context, not an observed outcome. There is no measured uplift, counterfactual truth or causal effect field. Extra record fields are rejected, preventing accidentally displaying a measured result in this forecast view. Hidden simulator truth belongs only in benchmark reports with the established benchmark boundary. The UI filters channel/world and links proposal identity; shadow records never qualify real outcomes or feedback.

## Scenario capability catalog

**Proposed read:** `GET /sim/scenarios` returns `scenarioCatalogSchema` with `stage`, `note`, unique `items:[{key,title,description,category,status:AVAILABLE|NOT_BUILT,missing_modules:string[]}]`. An AVAILABLE item cannot have missing modules. Omitted scenarios are unavailable. Maximum thirteen canonical IDs: DEMO_01 and S1–S12.

The frontend fetches fresh capabilities before `POST /sim/scenario/{key}` and requires `{ok:true}` acknowledgement. The server must recheck required modules and unresolved execution state atomically. A catalog read cannot eliminate races. Current loaded scenario/clock/results must come from subsequent backend reads; acknowledgement does not fabricate them.

The demo catalog enables only DEMO_01, S1–S5 and S7. Later scenarios are visible under a disclosure and cannot load substitutes: S6 category demand surge, S8 audience saturation, S9 ROAS trap, S10 seasonality, S11 excess inventory, S12 fatigue plus demand surge. The actual backend catalog owns availability once API mode is active.

## Integration acceptance

1. Serve matching schemas and expose selected objectives only after their models/gates exist.
2. Verify reads and successful/conflicting mutations against live backend state; intercepted browser responses establish frontend behavior only.
3. Test current-user mode permissions, revocation, stale policy revisions, blocked next candidates and audit history on the server.
4. Verify no simulated/forecast/test-account outcome enters real readiness; verify per-region pooling and held-out checks from stored outcomes.
5. Reject unbuilt scenarios atomically, including capability changes between catalog read and write.
6. Preserve missing endpoints/contract errors visibly; API mode never substitutes fixture data.
