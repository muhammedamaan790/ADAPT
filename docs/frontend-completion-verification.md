# Frontend completion verification

Recorded 7 October 2026 for `adapt/web`, against the ADAPT v2.4.3 plan. This is an ordinary extension of the committed Evidence Workspace. `DESIGN.md`, `.impeccable/design.json` and `PRODUCT.md` were read and preserved; this record does not refresh their context or replace the design system.

The final [review verdict](../.impeccable/review/completion/verdict.md) is **Ship for the reviewed frontend scope**. It resolves the two material findings in the [initial review](../.impeccable/review/completion/reviewer.md). The verdict is a targeted follow-up, not an independent general browser acceptance pass or backend certification.

## Implemented completion surfaces

| Surface | Component and behavior | Integration boundary |
|---|---|---|
| Workspace objective | `WorkspaceSettings` on `/executions?section=settings` shows the workspace, revision, current/supported objectives and permission note. Review captures the workspace/revision and selected objective; confirmation requires a reason of at least ten characters. Changed context disables confirmation. Acknowledgements must match the workspace and objective and advance the revision; successful actions invalidate cached reads. | Proposed GET/PUT `/objective`. Fixture mode is fixed to PROFIT. Backend authorization, audit and stale-proposal invalidation remain required. This request sends no budget instruction. |
| Policy version history | The same settings surface displays supplied actors, reasons, timestamps and before/after changes, with independent loading, retry and empty states. | Proposed GET `/policy/history`; no fabricated fixture versions or browser threshold rule engine. |
| Canonical source health | `SourceChecks` independently renders `as_of`, `newest_date`, `age_hours`, fractional freshness/completeness/consistency, hard failures and individual check details. Source listing and mapping coverage remain separate. | Existing GET `/data/health`; canonical tables are required and the backend's 503 remains visible. Composite source scores use 0–100 while component fractions use 0–1. |
| Platform reconciliation | `DataHub` displays the reporting window and platform conversions, store-attributed orders, conversion/order ratio, its unavailable reason and session/click ratio. The table has a named, explicitly focusable scroll region. | Existing GET `/data/reconciliation`. A missing denominator remains unavailable; conversion/order ratio is not revenue excess. |
| Confidence-region evidence | `ConfidenceBands` on Learning presents each SIMULATED/REAL and WARMUP/HELD_OUT pool separately, with model/time/world provenance, outcome definition, all three confidence regions, successes/outcomes, observed rates and supplied 95% Wilson intervals. Zero outcomes show “Not estimable.” Mobile cells wrap within the panel. | Proposed GET `/learning/qualification`. Schema checks reject implausible counts and interval shapes, but do not establish calculation correctness, calibration or evaluation quality. Bands are informational and authorize no execution. Fixture pools are empty. |
| Copilot SQL inspection | `SqlInspector` submits the trimmed query and a 500-row limit, binds the response to that query, validates bounded scalar results, renders cells as text and clears old results after query edits. Closing cancels the pending request. Loading, refusal, empty and truncated states are explicit. Results are a named region with `tabIndex={0}` and shared focus styling. | Proposed POST `/copilot/sql`, representing a read. Fixture SQL is disabled. Server allowlisting, single-SELECT parsing, timeout, LIMIT enforcement, read-only isolation, external-access restrictions and role controls are not browser guarantees. |
| Connection | Backend Connection probes health plus 26 static contracts through GET, including the new objective/history/qualification/source-check reads. It uses the configured API even in fixture mode and reports readiness per response contract. | 27 GET probes are not exhaustive certification of entity reads, streams, writes, SQL safety or analytical correctness. |
| Time and recovery | `dateTime` interprets offset-free canonical logical timestamps in Asia/Kolkata, retains offset-bearing instants and safely labels invalid timestamps. `ViewBoundary` preserves the shell around routed failures; unknown routes link home. The lazy Copilot subtree now has a boundary with a native-modal fallback offering Close assistant, Reload workspace and the shared labelled close control. | Reload guidance asks users to verify submitted actions before retrying. The failure test covers a rejected lazy Copilot import, rather than every possible runtime failure. |

Exact proposed shapes and backend responsibilities are in [the integration handoff](frontend-completion-api.md); broader frontend coverage is in [the completion matrix](frontend-completion-matrix.md).

## Validation evidence and timing

The following execution results were supplied by the parent implementation agent. This documentation pass read source, tests, review records and local PNGs; it did not independently run a browser or these suites.

| Check | Recorded result | Timing and limit |
|---|---|---|
| Production build | Passed: TypeScript and Vite | Final build after both reviewer corrections. Existing nonfatal Rollup/Zod annotation warnings and a main-bundle warning around 513 KB remain. No dependency change. |
| Unit suite | 69 passed | Contracts, state behavior and formatting include completion evidence validation, acknowledgement/query binding and timezone handling. |
| Fixture browser suite | Full 25 passed before review corrections; targeted 3 completion tests passed afterward in 9.4 seconds | The targeted run adds one Copilot import-failure test, closes its recovery dialog, navigates to Decision Center and checks the actual approval control. Also covers unavailable settings/confidence, disabled fixture SQL and unknown-route navigation. There are 26 unique fixture tests covered across these runs. |
| Intercepted API browser suite | Full 28 passed before review corrections; targeted 6 completion tests passed afterward in 16.2 seconds | Objective review/success/conflict, canonical health/reconciliation, confidence counts/intervals, SQL text rendering/query clearing/refusal, and the explicit SQL region's programmatic focus are covered. The full API suite was not rerun after the two focused corrections. |
| Formatting and diff checks | `npm run format:check` and `git diff --check` passed | Final implementation checks reported by the parent. |

The suite inventory is **123 unique tests: 69 unit + 26 fixture + 28 intercepted API**. Targeted reruns are not additional unique tests. This count describes coverage across the recorded runs; it does not describe one final all-suite invocation.

The SQL test focuses the named results region and asserts it is focused. Source confirms the shared `:focus-visible` outline. Neither that assertion nor the supplied PNGs establishes a keyboard-visible ring or keyboard scrolling across a maximum-width/maximum-height result; a live wide/long-result keyboard check remains outside the recorded evidence.

## Visual evidence

The [capture report](../.impeccable/review/completion/capture-report.json) lists **20 current PNGs**: the original 18 plus desktop/mobile Copilot recovery captures. Captures cover 1440px desktop and 390px mobile contexts. Settings, confidence and SQL include light/dark; objective review, source/reconciliation details and recovery are light-only in this set. The report has an empty error list, records document width equal to each configured viewport, and reports DM Sans Variable as the body font. Thus no page errors or document overflow were recorded during these captures; that observation does not cover all routes or failure states.

Captures mix full pages and element crops. Report `geometry.width` is surrounding **document width**, not PNG width. Dialog crops are 510px desktop/358px mobile; confidence crops are 1144px desktop/354px mobile. Crops support component layout review, but cannot alone prove modal placement, backdrop, surrounding context or viewport-height scrolling. The initial reviewer inspected all 18 originals; the follow-up reviewer inspected the two recovery images and four refreshed SQL images. This documentation pass independently inspected desktop API settings, mobile confidence, mobile Copilot recovery and desktop dark SQL as representative artifacts.

These renders retain the inherited warm-white/ink/violet palette, flat grouped surfaces, fine rules, locally bundled Manrope/DM Sans, tabular numerical evidence and shared lavender focus treatment. No design-system change was required. Mobile confidence remains contained; reconciliation intentionally scrolls additional columns. Synthetic intercepted HTTP examples are labelled as such and are not live backend evidence. No fresh computed contrast audit, context launcher pass or detector pass is recorded; launcher/detector capability was unavailable earlier.

## Backend and release limits

Local HTTP source exposes `/api/v1/health` and the Data Hub source, health, mapping-coverage and reconciliation reads. The parent reports newer `origin/main` at `efcd2fb` includes pipeline, decision/snapshot/replay, policy and execution-saga engine modules; those modules do not add the proposed HTTP routes or prove the complete world → ingest → engine → API → UI path. Objective/history/qualification/SQL and the other provisional adapters still depend on backend implementation.

Recorded browser API cases use synthetic interception. No live backend integration, held-out model qualification, SQL security engine proof, authorization/login implementation, LLM tool orchestration or actual ad-account execution is established here. Backend CI was green for an earlier head; the new push/check and merge were pending at the documentation handoff. Frontend build/test status and the narrow Ship verdict do not certify that backend head or a public deployment.

## Preserved context drift

- `PRODUCT.md` still describes the FastAPI application as exposing health only. Existing Data Hub HTTP reads make that statement stale. Its capability section also describes an earlier “next frontend slice,” rather than the current broader coverage in the completion matrix.
- `DESIGN.md` Layout, Navigation and Do's describe horizontal mobile primary navigation. Current later stylesheet rules use a two-column labelled navigation grid with an expandable More pages group at 700px and below.
- `.impeccable/design.json` retains a horizontal mobile navigation snippet and the same horizontal-navigation narrative. Its recorded breakpoint summary also omits additional component breakpoints now present in the stylesheet.

These are pre-existing documentation drift, not a new visual direction. All three context files remain preserved as requested; this scoped verification record documents the mismatch without silently regenerating them.
