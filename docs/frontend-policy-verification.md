# Frontend objective, policy and scenario verification

Recorded 2026-10-07 for the scoped extension of `adapt/web`. The finish-review disposition is **SHIP for this frontend scope**, with no blocking material finding. This is not certification of live optimizer integration, autonomous execution, backend qualification, or production readiness.

## Scope and incumbent-system preservation

This work extends ADAPT's existing **Evidence Workspace**. It retains the warm neutral canvas, ink navigation, restrained violet actions, text-labelled semantic status, fine dividers, flat grouped evidence panels, locally bundled DM Sans and Manrope, and deliberate review dialogs. The new controls use the existing field, button, badge, disclosure, panel and modal patterns; there is no new visual identity or system-wide design rule.

The documentation pass read `PRODUCT.md`, `DESIGN.md`, `.impeccable/design.json` and the Impeccable document reference. Those incumbent records remain unchanged. Their existing health-only backend description and mobile-route/navigation descriptions are stale relative to the current application; the sidecar's navigation example likewise describes the older surface. They are preserved and reported here because this pass authorizes only this verification record, not a product-context or design-system refresh.

The product invariants still apply: evidence precedes action; forecasts remain distinct from measured outcomes; a failed API does not silently become fixture data; proposal identity and the reviewed revision govern consequential requests. The frontend does not calculate engine mathematics or implement authentication, authorization, or security enforcement.

## Behavior verified in this slice

| Surface | Implemented frontend behavior | Authoritative boundary |
| --- | --- | --- |
| Optimizer | Six named objectives: PROFIT, GROWTH, ACQUISITION, INVENTORY_CLEARANCE, MARGIN_PROTECTION and BALANCED. Only backend-supported capabilities are selectable. Context, valuations and new revisions must match the objective and proposal identity; changing the objective clears the valuation. | The backend owns objective models, scores, feasibility, risk, supersession and execution. A displayed Growth valuation in review evidence is a supplied synthetic API response. |
| Execution & Ledger → Policy & readiness | Channel-specific modes, separate simulation and production readiness pools, explicit missing evidence, and Google test-account exclusion from production qualification. A mode change requires a reviewed reason and acknowledgement; the PUT request binds the channel, mode, policy version and revision. | Server permissions, evidence and revision must be rechecked atomically. Confirmation sends a policy request; it starts no budget execution. Browser contract checks do not establish authorization or qualification. |
| Observe-mode shadow log | Strict forecast-only records, proposal identity links and channel/world filters visible on mobile. Account-world labels describe context, not a measured outcome. | Unexecuted recommendations, forecasts, simulated results and test-account read-backs cannot qualify real outcomes or prove causal effects. |
| Scenario Lab | Fresh capability catalog before a load request; canonical later scenarios disclose missing modules and cannot load substitute examples. | The backend must recheck availability and execution state atomically, then supply the loaded scenario, clock and results. A catalog read or acknowledgement alone does not prove integration. |

Stage 1 fixture mode still supports only PROFIT and APPROVE, seven examples (`DEMO_01`, `S1`–`S5`, `S7`), a read-only policy with zero qualification evidence, and an empty unavailable shadow log. Later canonical scenarios are S6 demand surge, S8 saturation, S9 ROAS trap, S10 seasonality, S11 excess inventory and S12 fatigue plus demand surge. These disclosures do not claim the corresponding backend modules are available through HTTP.

## Evidence and review limits

The capture inventory is [manifest.json](../.impeccable/review/policy/manifest.json), alongside 17 PNGs: eight desktop cases, eight mobile cases and one additional desktop dark-theme case. Browser viewports were 1440 × 1000 and 390 × 844; screenshots can extend beyond viewport height. The manifest records widths matching the viewports, local DM Sans, and no recorded JavaScript errors or document overflow.

| Case | Desktop capture | Mobile capture |
| --- | --- | --- |
| Fixture objectives | `desktop-objectives-fixture.png` | `mobile-objectives-fixture.png` |
| Missing qualification evidence in fixture policy | `desktop-policy-missing.png` | `mobile-policy-missing.png` |
| Canonical scenario catalog | `desktop-scenario-catalog.png` | `mobile-scenario-catalog.png` |
| Supplied Growth valuation | `desktop-growth-backend.png` | `mobile-growth-backend.png` |
| Qualified synthetic TikTok mock readiness | `desktop-policy-qualified.png` | `mobile-policy-qualified.png` |
| Reviewed mode-change dialog | `desktop-mode-confirm.png` | `mobile-mode-confirm.png` |
| Google test account | `desktop-google-test.png` | `mobile-google-test.png` |
| Forecast-only shadow log | `desktop-shadow-log.png` | `mobile-shadow-log.png` |

The additional dark-theme capture is `desktop-policy-dark.png`. Ready-state browser responses were intercepted and synthetic. They establish frontend rendering, request handling and contract rejection behavior, not real qualification or measured advertising results.

Initial self-review found hidden mobile shadow filters; the implementation was corrected and one confirmation capture round followed. A fresh finish reviewer examined all 17 captures and scoped source and returned SHIP without a blocking material finding. Review included native labels, modal focus behavior, explicit status text and controls. This was scoped review, not a complete WCAG conformance audit. The reviewer did not independently rerun tests, the detector, or live backend integration. This documentation pass checked the manifest, incumbent records, relevant policy/objective/style and backend-route source, and representative mobile shadow-log and desktop dark-theme captures; it did not rerun application tests.

The design detector was unavailable because its launcher engine/cache location was not writable. No detector pass is claimed.

## Validation reported by the implementation owner

| Check | Result and exact scope |
| --- | --- |
| TypeScript and Vite build | Final build passed. Nonfatal third-party Zod annotation and approximately 508 kB main-chunk warnings remain. |
| Unit tests | Final run passed all 62 tests. |
| Fixture browser tests | Full 23-test suite passed; eight targeted golden/policy confirmations then passed after status-copy edits. |
| API browser tests | The existing 15 API tests passed in the full API run. After assertion and mobile-filter corrections, all seven new objective/policy/scenario API tests passed in the final targeted run. This does not claim a single final all-green 22-test API run. |
| Formatting | Final frontend format check passed. |

The current aggregate suite contains **107 tests = 62 unit + 23 fixture browser + 22 API browser tests**. This is the total suite size, not 107 new tests. Full runs plus targeted confirmations are the validation history; the targeted confirmations are not added again to the suite total. No frontend source changes followed the last final build/tests and captures; subsequent work is documentation. This record does not invent a commit or source hash for the uncommitted frontend slice.

## Backend status and remaining integration

Source inspection of the current backend shows health and Data Hub read HTTP routes. Detection and diagnosis modules exist, but decision/optimizer, policy, shadow-log and scenario-capability HTTP routes required here are not exposed. Backend runtime tooling (`uv`), a running service and canonical data were not provisioned locally. No live canonical integration was performed.

[frontend-policy-api.md](frontend-policy-api.md) is the provisional backend handoff. Its schemas and routes must be reconciled with authoritative OpenAPI before live use. Remaining acceptance work is:

1. Implement the authoritative objective, proposal, valuation, revision, policy, shadow and scenario catalog endpoints. Advertise objectives and scenarios only when their required models and gates exist.
2. Verify matching proposal identities and objectives, atomic supersession and conflict handling against persisted live state. Reconcile timeout/conflict outcomes through backend reads.
3. Enforce current-user permissions, stale policy revisions, revocation, audit history and future candidate gates on the server. Request IDs, credentials and idempotency headers alone do not prove security or semantic deduplication.
4. Qualify separate simulation and real pools from matured stored outcomes, including the candidate region, independent worlds, held-out reliability and health/guardrail checks. Prevent forecasts, simulator truth and Google test-account read-backs from entering real readiness.
5. Recheck scenario availability and unresolved execution state atomically, including catalog changes between read and mutation. Validate canonical scenario output through subsequent reads without fixture substitution.
6. Run live read/mutation/conflict tests and canonical integration once the service and data exist. Preserve visible unavailable-endpoint and contract errors until then.

No engine mathematics, production autonomy, real-outcome qualification, causal proof, authentication or security implementation is delivered by this frontend slice.
