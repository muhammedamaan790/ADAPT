# Frontend completion and integration boundary

The requested frontend work is reviewed against the final ADAPT v2.4.3 plan, rather than the superseded original 24-hour proposal. This matrix describes concrete interface coverage; it is not a claim that backend stages have passed. “Implemented” in the plan requires the actual world → ingest → engine → API → UI path, including analytical and failure tests. That condition is not met by intercepted HTTP responses or frontend fixtures.

| Plan surface | Frontend coverage | Remaining dependency |
|---|---|---|
| Command Center | Attention queue, financial metrics, provenance, formulas, brief, trend/baseline, health, loop | Actual overview/metrics/brief service |
| Decision Center | Why/evidence, decomposition, policy checks, alternative rejection, reserve, human approve/modify/reject, execution/outcome | Decision/evidence/approval services and ground-truth tests |
| Sensitivity and comparisons | Conservative/aggressive revisions, strategy report, confidence breakdown, forecast availability | Engine-valued comparisons, actual uncertainty; browser does not fit models |
| Timeline and replay | Captured timeline, manifest, identity binding, archive export, verification states | Archived environment, replay runner and HTTP endpoints |
| Scenario Lab | Stage 1 fixture catalog, clock/reset/events; backend capability checks, later scenarios gated | Actual world controls, built later-stage scenarios; fixture load is not engine execution |
| Head-to-Head | Validated paired reports, seed filters, comparison, import/export | Held-out evaluation runner and real reports |
| Anomalies | Queue, filters, drilldown, bands, source gates, causal estimability and reasoned status changes | Actual detection/diagnosis endpoints |
| Optimizer | Budget editor, constraints, six named objectives, capability restriction, valuation/hash binding, drafts | Optimization context/valuation/run endpoints and engine guardrails |
| Execution ledger | Per-leg states, verification, retry, restore, reconcile, audit identifiers | Platform read-backs, saga and ledger services |
| Policy and settings | Separate real/sim readiness, allowed modes, reviewed revisions, objective defaults, audit history | Policy qualification/authorization/history/objective endpoints |
| Opportunity Map | Scoring/feasibility, evidence graph, curves and table, fatigue and creative score | Models, opportunity/curve/fatigue endpoints |
| Outcomes | Class/verdict filters, predicted/measured outcome comparison and export | Measured outcomes and causal estimator |
| Learning | Calibration/feedback, accuracy, uplift, model registry/promotion/rollback, confidence-region intervals | Learning/model/qualification services, held-out evidence |
| Data Hub | Source checks, reconciliation/window/platform ratios, lineage, CSV preview/mapping/confirmation, workspaces | Existing source reads need canonical state; ingest/workspace/lineage services pending |
| Copilot | Evidence templates, completed SSE answers, cancellation, citations, read-only SQL result viewer | LLM tool runtime, SQL isolation, bounded existing-proposal selection and streaming endpoints |
| App resilience | Desktop/mobile, themes, keyboard paths, explicit loading/error/empty states, view error recovery, unknown-route handling | Actual live acceptance checks with available backend services |

Frontend-owned screens and their provisional response adapters are present. Advanced analytical behavior, platform execution, auth/security, held-out experiments, database isolation and LLM tool orchestration remain backend work. Policy threshold editing is not a browser-owned rule engine: the frontend inspects constraints and audit history, and submits reviewed channel/objective requests only.

## Judge demonstration boundary

Use the labelled fixture mode to show the interface journey without presenting illustrative values as results. Switch to API mode for actual integration; missing services produce errors, with no fixture substitution. The Backend Connection page reports read-contract coverage rather than a green whole-system indicator. Before claiming the autonomous engine is implemented, run the backend's canonical build and end-to-end stage acceptance tests and complete live UI checks.

## Release checks

Build and formatting, unit contracts/state behavior, fixture browser journey, intercepted API success/refusal/conflict cases, and desktop/mobile/dark visual inspection belong to frontend validation. Backend CI, public deployment and actual ad/API execution are separate checks. Validation counts and inspected artifacts are recorded in `frontend-completion-verification.md` after the final review.
