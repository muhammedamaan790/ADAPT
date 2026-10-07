# Remaining frontend verification

Verified surface scope: Opportunity Map, Outcomes, Learning, Data Hub (sources, lineage and CSV import), Workspace Copilot, and decision comparison, sensitivity, timeline, replay availability and snapshot export in `adapt/web`. This ordinary extension preserves ADAPT's existing **Evidence Workspace** identity. It does not establish production backend integration, engine correctness or passage of a backend delivery gate.

## Design preservation

The documentation pass compared `PRODUCT.md`, the incumbent `DESIGN.md` and `.impeccable/design.json`, the final stylesheet, the new page and component implementations, and `docs/frontend-insights-api.md`. The document workflow's extraction guidance was used to compare the implementation with the existing system; no new visual world or normative token specification was introduced.

The extension retains the warm neutral canvas, ink navigation rail, restrained violet actions and selected states, locally bundled Manrope and DM Sans, tabular financial values, fine rules, gently curved controls and flat bordered panels. Status and availability colors have readable labels. Ordinary report rows use dividers rather than decorative elevation. Copilot uses the existing modal pattern, and the dark Learning capture uses the incumbent dark-theme variables.

| Surface | Evidence-first presentation retained |
| --- | --- |
| Opportunity Map | Ranked candidates lead to feasibility, provenance and linked decision evidence. Scores are explicitly not probabilities. Response points have exact table values and a caption distinguishing connecting lines from a fitted model. |
| Outcomes | Decision-time forecast, example measured result, prediction error, measurement context and calibration eligibility remain distinct. Class and verdict filters have visible labels. |
| Learning | Before/after calibration transitions and eligibility records sit beside descriptive prediction error. Held-out strategy evaluation and missing model artifacts have explicit unavailable states. |
| Data Hub | Source status, mapping coverage, attribution reconciliation and metric lineage remain inspectable. CSV mapping, validation and acknowledgement precede submission; fixture staging is labelled local preview. |
| Decision insights | Strategy estimates, sensitivity intervals, loss-risk labels, confidence interpretation and revision confirmation remain separate from realized outcomes and execution. Timeline and snapshot actions explain the archived-artifact requirement. |
| Copilot | Read-only assistance, template provenance, question/answer rows and evidence links remain visible within the existing dialog hierarchy. |

Responsive additions stack the evidence graph and CSV mappings at narrow widths, collapse the two-column Learning group, wrap comparison actions and retain readable labels. The current mobile shell uses its existing labelled two-column primary navigation and a **More pages** expansion for the four new routes. The incumbent `DESIGN.md` still describes horizontal mobile primary navigation, and its sidecar navigation example still represents three routes. Its breakpoint inventory also omits later workbench queries. Those documentation discrepancies are recorded without refreshing the design artifacts or repairing earlier route/mobile-navigation drift. The previously documented allocation-table/mobile-row discrepancy remains outside this pass.

`DESIGN.md` and `.impeccable/design.json` were preserved. Their SHA-256 values checked during this pass are:

| Artifact | SHA-256 |
| --- | --- |
| `DESIGN.md` | `1B00C482920A72A9ECD7E0DE36E630333BD863BDEF05CA6FFBC7C45C2B08245D` |
| `.impeccable/design.json` | `B3403D12BEC7ECBAEB6F8D2EDE53EE3E2964C5F61D72750542A52C9F672360BC` |

## Rendered evidence and scoped disposition

All fifteen supplied images listed by `.impeccable/review/remaining/capture.json` were visually inspected during documentation. The manifest reports no capture errors. The evidence set comprises:

| View | Desktop capture | Mobile capture |
| --- | --- | --- |
| Opportunity Map | `desktop-opportunities.png` | `mobile-opportunities.png` |
| Sources and lineage | `desktop-sources.png` | `mobile-sources.png` |
| CSV mapping and validation | `desktop-csv.png` | `mobile-csv.png` |
| Comparison and sensitivity | `desktop-comparison.png` | `mobile-comparison.png` |
| Outcomes | `desktop-outcomes.png` | `mobile-outcomes.png` |
| Learning | `desktop-learning.png` | `mobile-learning.png` |
| Copilot dialog | `desktop-copilot.png` | `mobile-copilot.png` |

The fifteenth capture is `desktop-learning-dark.png`. Comparison and Copilot images are component/dialog captures, so they do not independently demonstrate the whole surrounding route. These images show illustrative frontend state; they are not advertising results or engine evaluation evidence. Numeric fixture corrections are recaptured at the same paths; screenshot layout review does not validate the underlying probability model.

The finish-review handoff records **SHIP** at the supplied visual and sampled-code scope. The final captures were refreshed at the same fifteen paths with no capture errors after the fixture numerical correction. The completed follow-up also records **SHIP**: P10/loss-risk consistency was resolved in the final desktop/mobile comparison captures, bounded staging-cache/count guards and the selected-proposal replay-hash guard were confirmed, and no visible regressions were reported at that narrow scope. This disposition applies to the remaining frontend surfaces and the evidence supplied for them. It is not a fresh whole-product defect audit, an accessibility certification, a review of every possible state, or approval of backend mathematics and enforcement. Timeline/replay availability and exports were sampled in code and behavioral verification; the manifest does not contain dedicated rendered captures of every such state.

Review PNGs and the capture manifest are development-only artifacts under the ignored `.impeccable/review/` directory. They are not shipping raster assets. This extension uses bundled fonts, code-rendered SVG charts and SVG icons; the public asset inventory contains the existing SVG favicon. No new shipping photography, generated raster imagery or remote font dependency is introduced.

## Validation record

Commands run from `adapt/web`. The implementation handoff confirms the following final results after the numerical correction and replay-hash guard:

| Check | Result at documentation handoff |
| --- | --- |
| `npm run build` | Passed TypeScript and Vite production build |
| `npm test` | 37 unit tests passed |
| `npm run test:e2e` | 14 fixture browser journeys passed |
| `npm run test:e2e:api` | 9 API browser checks passed, including the added replay-hash guard |
| Rendered evidence | Fifteen supplied desktop/mobile/dark/component captures inspected; capture manifest contains no errors |

The documentation pass did not rerun these suites. Fixture journeys and controlled API-response tests validate frontend behavior under those conditions; they do not establish that the live decision-loop backend or advertising-platform execution is connected.

## Integration and product boundaries

`docs/frontend-insights-api.md` is the detailed contract and ownership handoff. The current HTTP backend exposes health only. Insights endpoints are proposed view-model contracts requiring agreement with backend OpenAPI, envelopes, authentication, permissions, pagination and mutation semantics. API errors remain explicit and never silently select fixture data. The UI remains in PROFIT / human approval mode.

CSV parsing and validation are browser checks with a 2 MB/5,000-row limit, distinct required mappings, range checks and duplicate checks. Backend freshness, canonical identity, authorization and promotion remain authoritative. Fixture staging stores preview metadata only; it does not ingest canonical data, store parsed records in localStorage or change fixture metrics, inventory or decisions. The proposed JSON ingest envelope is not an implemented multipart backend service. Mapping-confirmation retries reuse a staged ID in memory for unchanged submissions, but refreshes and uncertain upload responses still require backend status inspection and semantic deduplication.

Fixture Copilot answers are deterministic templates over visible records, labelled TEMPLATE; no LLM, SQL tool or budget action runs in this frontend. The provisional API adapter accepts only a complete validated answer followed by a done event, validates internal evidence routes and supports cancellation. This transport validation cannot prove answer grounding or backend read-only permissions.

Recorded comparison and fatigue examples are illustrations, not evaluated strategy performance. Alternative fixture allocations produce distinct non-executable drafts pending backend valuation. Response charts join supplied points and do not fit or value a model. Missing creative predictions remain NOT_ESTIMABLE. Learning's MAE is descriptive over eligible optimization fixture outcomes; safety and inconclusive records do not calibrate the response curves. Once-applied calibration transitions are displayed without importing hidden simulator truth. No held-out benchmark or trained-model artifact is claimed.

Fixture replay reports UNAVAILABLE and performs no archived-engine hash verification. API replay status/hash consistency and the expected hash's agreement with the inspected decision are frontend contract guards, not proof that the backend reproduced a run. A JSON snapshot is an inspectable frontend export; it lacks an archived environment and model artifacts and cannot prove reproducibility.

Dedicated data-health/lineage adapters, canonical ingest and promotion, workspace creation/switching, model promotion/rollback, archived replay artifacts, richer report metadata, backend Copilot tools, and actual held-out strategy evaluation remain follow-up integration work. Existing decision-loop contract and lifecycle ownership stay with the backend.
