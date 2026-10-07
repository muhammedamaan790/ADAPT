# Next frontend verification

Verified slice: Anomalies, Optimizer, Execution & Ledger, and Backend Connection in `adapt/web/src`, completed on 7 October 2026. This extends the existing operating dashboard with labelled fixture workbenches and provisional API adapters. It does not establish backend integration or passage of the Stage 2 gate.

## Design evidence

The documentation pass compared final `styles.css`, the existing `DESIGN.md` and `.impeccable/design.json`, `PRODUCT.md`, and the API handoff. All ten supplied captures in `.impeccable/review/next/` were visually inspected: the four routes on desktop and mobile, the dark Optimizer, and the recovery dialog. `capture.json` lists these ten captures with no capture errors.

The extension retains the warm neutral canvas, ink navigation rail, restrained violet actions and selection, locally bundled Manrope and DM Sans, flat bordered evidence panels, tabular amounts, and text-labelled semantic states. Anomalies puts observed data, gates, lineage and the probable diagnosis before investigation actions. Optimizer places budgets beside their bounds and distinguishes input checks from valuation. Execution separates prior settings, requested settings, latest read-back, request state and simulation sync. Connection displays read endpoint errors explicitly. Fixture provenance remains visible across the captured routes; the recovery dialog names the decision hash and explains that restoring settings cannot undo spend or exposure.

The design artifacts were preserved rather than regenerated. Their SHA-256 values before and after this documentation pass are:

| Artifact | SHA-256 |
|---|---|
| `DESIGN.md` | `1B00C482920A72A9ECD7E0DE36E630333BD863BDEF05CA6FFBC7C45C2B08245D` |
| `.impeccable/design.json` | `B3403D12BEC7ECBAEB6F8D2EDE53EE3E2964C5F61D72750542A52C9F672360BC` |

The incumbent documentation has drift: it describes horizontal mobile primary navigation, while the final seven-route shell uses a labelled two-column grid. It also describes horizontally scrolling mobile allocation tables, while the existing decision allocation presentation uses mobile rows. The sidecar navigation example still represents three routes and horizontal mobile links, and its breakpoint list does not include the new workbench-specific 1100px and 800px queries. These discrepancies are recorded here; this pass does not refresh normative design tokens or repair existing documentation.

## Scoped review disposition

The review handoff records **ship**, applying only to these four resolved scored findings:

1. Mobile anomaly filters are collapsed initially so the selected signal remains in the initial 844px viewport; filters remain available through their labelled control.
2. Responsive anomaly chart axes remain readable, with a browser assertion that their font size is at least 9px.
3. The new 3px selected and notice side stripes were removed to retain the established border treatment.
4. Valuation labels distinguish fixture recorded valuation from API-mode **Backend valuation**.

This disposition is not a fresh whole-product defect audit, an accessibility certification, or an approval of backend behavior. The documentation pass performed no further implementation or defect hunt.

## Validation results

The implementation and review handoff reports the following final results. Commands run from `adapt/web`:

| Check | Result |
|---|---|
| `npm run build` | Passed TypeScript and Vite production build |
| `npm test` | 21 unit tests passed |
| `npm run test:e2e` | 9 fixture browser tests passed |
| `npm run test:e2e:api` | 5 API browser tests passed, including the final Backend valuation label check |
| Rendered evidence | Ten supplied desktop/mobile/dark/dialog captures inspected |

Test counts are the completed implementation/review runs supplied to this documentation pass; they were not rerun solely to create this file. Browser fixture and controlled API-response checks demonstrate frontend behavior under those test conditions, not a working decision-loop backend or real advertising-platform execution.

## Backend and fixture limits

`docs/frontend-api.md` remains the contract and ownership handoff. The backend foundation currently exposes health only; decision-loop and workbench adapters remain provisional until C6 aligns them with OpenAPI. `/optimizer/context` is a proposed frontend read endpoint. Event polling also requires agreement with the specified SSE interface. Authentication, mutation authorization and backend policy enforcement require their own integration validation.

Fixtures are illustrative UI examples, not engine output, seed-42 simulator evidence, measured advertising results or causal proof. Probable anomaly drivers remain distinct from causal estimates. Browser allocation checks validate inputs; the backend owns model profit, bootstrap risk, inventory projections and executable policy gates. Only the exact recorded fixture allocation has its recorded valuation. Arbitrary fixture edits return `NOT_ESTIMABLE`; a revision is a separate non-executable draft with forecasts withheld until backend valuation exists.

Unknown execution read-back remains unverified. Recovery fixture actions do not call advertising platforms. Settings restoration changes future settings and cannot reverse incurred spend or exposure; reconciliation records a compensated resolution and does not itself restore budgets. Accepted-partial and blocked manager workflows, fresh platform reads, semantic idempotency and real recovery remain backend follow-up work.

Connection readiness validates schemas for seven GET responses and reports degraded health separately. It does not validate mutations, auth sessions, live spend or engine mathematics. API errors never silently substitute fixtures. The captured Connection error state is frontend evidence of explicit failure reporting, not proof of a backend outage or backend endpoint readiness.
