# Management frontend verification

Verified scope: workspace creation and switching, model registry inspection and requests, Head-to-Head report inspection, and archived decision evidence in `adapt/web`. This is an ordinary frontend extension of ADAPT's existing **Evidence Workspace** system. The supplied finish review records **SHIP** for the reviewed frontend scope after the dark workspace placeholder correction. Backend management integration, engine mathematics and actual replay/evaluation correctness remain unverified.

## Delivered surfaces

| Surface | Entry point and delivered behavior | Evidence boundary |
| --- | --- | --- |
| Workspaces | The shell workspace link opens `/data?section=workspaces`. Read the active workspace, create a named workspace without activating it, and explicitly confirm a switch before an application reload to Command Center. | Fixture workspaces isolate local demo state. API creation and activation require a validated backend response; frontend switching guards are advisory. |
| Model registry & controls | Learning displays registry families/versions, artifact identities, recorded prior champion, nullable candidate/champion/baseline metrics, backend checks and allowed requests. Promotion/rollback dialogs require a reason and reviewed-artifact acknowledgement. | Fixture mode has no trained artifact or simulated registry changes. Promotion is an optional operator request only when backend policy allows it; automatic promotion stays backend-owned. |
| Head-to-Head evaluation | Scenario Lab's Head-to-Head view reads a precomputed backend report or a locally uploaded JSON artifact, filters paired seeds, displays selected-seed totals and exports the displayed report. | It does not run a simulator. Upload validation checks the contract, while the visible notice keeps provenance/results unverified. The oracle is a benchmark only. |
| Archived evidence & environment | The decision replay timeline displays the selected decision's manifest, snapshot/environment identities, artifact availability and supplied processing steps, with links for present artifacts. | Captured lifecycle events and supplied processing traces remain distinct. Artifact availability does not prove replay. Existing hash-bound replay verification remains a separate action. |

The detailed provisional paths, payloads and backend responsibilities are in [frontend-management-api.md](frontend-management-api.md). The implementation sources are `src/components/Workspaces.tsx`, `ModelManagement.tsx`, `Evaluation.tsx`, `ReplayArchive.tsx`, and `src/api/management.ts` / `management-contracts.ts`, relative to `adapt/web`.

## Incumbent system comparison

This documentation pass read `PRODUCT.md`, `DESIGN.md`, `.impeccable/design.json`, the management components and contracts, the API handoff, the relevant test sources, and the capture checks. Impeccable's document reference was used to compare the extension against the existing system. It did not authorize a replacement visual world, design-token refresh or new `DESIGN.md`.

The extension retains operate mode: a warm canvas, ink navigation, grouped flat white panels, fine borders/dividers, restrained violet actions, locally bundled DM Sans and Manrope, tabular numbers, gently curved controls and explicit status/availability text. Artifact details use disclosures and lists; comparison metrics use scrollable tables. Missing values remain labelled unavailable rather than receiving invented zeroes. Workspace and registry confirmations reuse the incumbent labelled native dialog. The workspace creation form stacks on mobile, and long names/identities can wrap. No new shipping raster assets or remote font requests are introduced. Review images are QA evidence only.

The current mobile shell keeps its incumbent labelled navigation and **More pages** expansion for Opportunity Map, Outcomes, Learning and Data Hub. Workspace management lives inside Data Hub, model controls inside Learning, evaluation inside Scenario Lab and archive evidence inside Decision Center; no standalone management primary routes were added.

Pre-existing documentation drift is recorded without repair:

- `PRODUCT.md` still describes the earlier stage/slice and says the backend HTTP app exposes health only. The management API handoff reflects the later health and Data Hub backend reads.
- `DESIGN.md` describes horizontal mobile primary navigation, while the incumbent application already uses a two-column mobile primary layout and More pages expansion.
- The sidecar navigation snippet still illustrates three routes, and its breakpoint inventory omits later workbench queries. The earlier allocation-table/mobile-row discrepancy remains outside this pass.
- Earlier frontend verification documents contain historical health-only and follow-up-scope statements. They are prior verification records, not proof of the current management integration state.

`PRODUCT.md`, `DESIGN.md` and `.impeccable/design.json` were preserved. Their SHA-256 values at this documentation pass are:

| Artifact | SHA-256 |
| --- | --- |
| `PRODUCT.md` | `974CDBC40334D7A4F1B3E883AD7765A8FB0929CC9D9775E23457953C2BF674A4` |
| `DESIGN.md` | `1B00C482920A72A9ECD7E0DE36E630333BD863BDEF05CA6FFBC7C45C2B08245D` |
| `.impeccable/design.json` | `B3403D12BEC7ECBAEB6F8D2EDE53EE3E2964C5F61D72750542A52C9F672360BC` |

## Behavioral verification and truth boundaries

### Workspace isolation and switching

The tests cover explicit creation, separate scenario/clock/calibration state, restoration of previous decisions after switching back, duplicate-name rejection, storage failure, corrupted selection data and retention of a loaded tab's own context when another tab changes the future default. The legacy default workspace keeps its storage key; other workspace IDs use separate state keys. CSV preview metadata is also scoped to the workspace. Creation leaves the current context unchanged.

The UI disables switches while mutations are active or execution-state reads are loading, failed or blocking. The adapter blocks unresolved execution states, UNKNOWN/CONFLICT external legs and pending/failed simulator mirrors. A browser journey confirms an unknown execution blocks a switch. An intercepted API response with the wrong active workspace is rejected, the dialog retains the error and the current route is preserved. A valid activation is the precondition for the application's reload; local context is never inferred from an ambiguous acknowledgement.

These checks do not establish authenticated backend isolation or atomic race handling. The backend must recheck permissions, unresolved executions and concurrent operations when activating, then bind all queries/mutations to the authenticated workspace. Loaded fixture tabs maintain their own in-memory context; two tabs editing the same fixture workspace have no conflict-resolution protocol. API import acknowledgement history is session-only, requiring canonical backend status after refresh.

### Model gates and version identity

Contract tests reject a promotable model without its candidate role, artifact or nonempty passing gate set, and reject rollback permission without a champion and recorded prior version. Detail responses must match the requested family/version. Nullable metrics display Unavailable. Fixture mode keeps registry controls unavailable and invents no training or promotion results.

Intercepted API browser tests verify that promotion binds the reviewed version, registry revision, artifact hash and reason; POSTs carry request/idempotency headers. Confirmation stays disabled until acknowledgement and a reason of at least ten characters are supplied. A stale-registry conflict stays visible in the dialog. Rollback requests the recorded identity and does not locally change the displayed registry role; reads are invalidated for backend refresh. Acknowledgements are checked against the family and expected promoted/restored version.

These are transport, identity and presentation guards. They do not validate a model, derive statistical gates or provide semantic idempotency. Backend policy must recompute baseline performance, champion non-inferiority, coverage, replayability and revision preconditions. Approved decision snapshots retain their original model artifacts.

### Evaluation pairs, ratios and oracle boundary

The report schema requires unique seeds and exactly one safe-static, safe-contribution, adapt and oracle row per seed; duplicate, unexpected or missing pairs are rejected. Header metadata is strict. Finite values, horizon, budget/reserve coherence and nonnegative spend/counts are checked; negative realized CAA is allowed. Availability must agree with report presence. Uploaded artifacts have a 1 MB limit and stay component-local.

Fixture browser tests cover invalid upload rejection, valid report inspection, seed filtering, export and clearing. The table calculates total CAA divided by total spend over the selected seeds; zero spend displays Unavailable. The API browser check reads a report even when the overview is unavailable and confirms no write launches an evaluation. The oracle row is visibly marked Benchmark only.

Paired rows and a reported common-random-numbers flag do not independently verify equal starting worlds, fair execution, causal uplift or statistical significance. The UI infers no confidence intervals. Synthetic evaluation payloads reside in tests/capture scripts and are never bundled as production benchmark output.

### Archive identity, availability and replay proof

Archive responses must match the selected decision ID, decision hash and snapshot ID. Tests reject another decision's hash, missing/invalid artifact references and inconsistent archive structure. Artifact and step identities are unique; step references must resolve. AVAILABLE requires nonempty environment fingerprint, code version and dependency-lock identity. Only present artifacts with safe internal links render inspection actions.

The fixture browser journey exposes captured UI evidence while environment identities and full processing steps remain unavailable. The component distinguishes absent full engine trace from captured lifecycle history and explicitly states that availability is not replay verification. Frontend validation does not fetch/hash artifact bytes or reproduce the engine. Hidden world ground truth remains outside decision/Copilot presentation; oracle aggregates belong in the evaluation report only.

## Rendered evidence and finish disposition

The supplied capture set is recorded in `.impeccable/review/management/capture-checks.json`. The implementation owner inspected all nineteen images; a fresh finish reviewer independently inspected the complete set. This documentation pass read the capture manifest and records those review outcomes without claiming a new browser run or a separate image audit.

| State | Desktop capture | Mobile capture |
| --- | --- | --- |
| Workspace list/create | `desktop-workspaces.png` | `mobile-workspaces.png` |
| Workspace switch confirmation | `desktop-workspace-confirm.png` | `mobile-workspace-confirm.png` |
| Evaluation unavailable | `desktop-evaluation-empty.png` | `mobile-evaluation-empty.png` |
| Precomputed evaluation report | `desktop-evaluation-report.png` | `mobile-evaluation-report.png` |
| Model artifacts unavailable | `desktop-models-missing.png` | `mobile-models-missing.png` |
| Model artifact and gates supplied | `desktop-models-ready.png` | `mobile-models-ready.png` |
| Registry request confirmation | `desktop-model-confirm.png` | `mobile-model-confirm.png` |
| Archived environment unavailable | `desktop-archive-missing.png` | `mobile-archive-missing.png` |
| Archive and processing trace supplied | `desktop-archive-ready.png` | `mobile-archive-ready.png` |

The nineteenth image is `desktop-workspaces-dark.png`. Desktop captures use a 1440px viewport and mobile captures 390px. The manifest contains no JavaScript errors and records document width equal to viewport width for all nineteen captures. It records the local DM Sans family and incumbent panel padding: 22px for desktop management panels, 24px for the desktop archive's enclosing decision panel, and 18px on mobile. Intentional table scrolling is distinct from document overflow.

The fresh reviewer found one P2 issue: dark workspace input placeholder contrast. The scoped fix sets `.workspace-create input::placeholder` to `var(--muted)` with `opacity: 1`. The reviewer independently confirmed the correction at **7.45:1** contrast and returned **SHIP**, with no other material finding in that review. This is a disposition for the supplied management states and sampled code, not a whole-product audit, complete dark-theme certification or backend approval.

PNG files and the manifest are under the ignored `.impeccable/review/management/` directory. Supplied ready-state models, reports and archives use explicit synthetic capture/test responses. Their rendered appearance is UI evidence, not trained-artifact authenticity, measured business results or reproducibility proof.

## Validation record and accessibility limits

The implementation handoff reports these results after the scoped contrast fix. Suites were not rerun during documentation.

| Check | Result at documentation handoff |
| --- | --- |
| Production build | Final TypeScript and Vite build passed after the scoped contrast fix |
| Unit tests | 46 passed |
| Fixture browser journeys | 19 passed |
| Intercepted API browser checks | 15 passed |
| Combined automated tests | 80 passed across the full frontend suites; not 80 management-only tests |
| Rendered review | Nineteen desktop/mobile/dark captures inspected by owner and fresh reviewer; scoped disposition SHIP |
| Capture checks | No recorded JavaScript errors or document overflow at sampled widths |
| Final formatting check | Passed; all matched frontend files use Prettier formatting |
| Impeccable detector | Not run: launcher engine unavailable and cache not writable |

The implementation owner confirmed no functional source changes since the 80-test passing run. The build emits nonfatal third-party Zod annotation warnings and a main chunk warning at approximately 501 kB. These do not fail the build; loading/performance remains a separate measurement task. Detector unavailability must not be represented as a detector pass.

Source inspection confirms labelled form controls, semantic headings/lists/tables, report captions, native disclosures, status/alert roles for loading/errors and selected success notices, shared focus styling and reduced-motion support. Confirmation dialogs use native `dialog`, a labelled title, cancellation handling and restoration of the previous focus on unmount. More pages exposes expansion/control attributes. Semantic colors have visible supporting text. The reviewed placeholder contrast correction is a specific measured fix.

No complete automated accessibility audit, assistive-technology run, all-palette contrast matrix, keyboard traversal of every state, zoom/reflow matrix or coverage of every viewport/theme was supplied. The existing dense typography and disabled-control styling inherit the incumbent system. Screenshot geometry and the sampled journeys support the stated scope, not a WCAG conformance claim.

## Backend handoff and pending work

The implementation owner inspected latest `origin/main` backend commit `fe9a9fe`, which adds Data Hub sources, health, mapping and reconciliation reads. The branch was safely fast-forwarded to that commit while preserving the frontend edits, so those backend paths are now present locally. Source inspection found those shapes consistent with the existing frontend adapters. That comparison was not a live integration test, and it does not establish management endpoint availability.

Workspace, model detail/mutation, evaluation report and archive paths remain provisional view-model contracts requiring backend implementation and OpenAPI agreement. API failures remain explicit and never silently substitute fixture data. Credentials, request IDs and idempotency headers are frontend transport behavior; authentication/authorization, atomic context isolation, semantic deduplication, canonical import status and registry concurrency enforcement belong to the backend.

The frontend continues PROFIT / human approval behavior and preserves backend ownership of mathematics, policy, hashes and lifecycle. Nothing in this verification proves a backend delivery gate, real advertising execution, trained-model quality, simulator fairness or archived engine reproduction. Remaining integration work is authoritative management endpoint implementation/OpenAPI alignment and live contract checks when those services are available.
