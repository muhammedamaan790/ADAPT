# Reference-led visual redesign

The user rejected the previous completion pass because it added useful comparison features without visibly transforming the home screen. The three supplied dashboard images now determine the visual direction. This pass rebuilds the home composition and strengthens the shared shell; it retains earlier functional improvements.

## Visible changes

- Command Center presents an actual supplied budget proposal beside a substantially larger campaign chart.
- Campaign rows show locally served provider marks, current/proposed daily amounts, comparable budget bars, signed changes and unallocated reserve.
- The proposal exposes its supplied horizon estimate and a review action that navigates to the exact decision and focuses the guarded approval section. Home itself never executes a mutation.
- A full-width attention register replaces the former narrow attention list, aligning signal, evidence, estimated impact and action.
- A compact morning brief, financial strip, six-source health register and state-bound decision loop complete the page.
- The shared shell has stronger graphite navigation, saturated blue active/primary controls, larger typography, clearer workspace identity and tighter panel corners. Both themes bind to the same updated tokens.
- Phone budgets become readable stacked current/proposed comparisons. They appear before the attention register, financial strip and chart; a wide table is not required to read the recommendation.
- The chart retains actual supplied observations and forecasts, visible keyboard controls, exact data disclosure and touch interaction. Axis labels avoid the final-date collision. No reference-image metrics, dates, curves or successful executions are copied into the data.

## Review and verification

Two visual refinement cycles used actual Chromium renders. The first inspected all 11 routes at desktop and phone dimensions and checked accessibility on home, decisions, opportunities and optimizer. The second focused on home density, mobile budget composition, chart readability and review navigation, rather than reopening unchanged routes. Final light/dark home renders and a 320 px boundary check were retained.

All 22 first-cycle route/viewport checks reported no document overflow or runtime errors. Eight first-cycle and two final-cycle axe scans reported no WCAG 2 A/AA or 2.1 AA tagged violations. This is bounded automated inspection, not complete accessibility or physical-device certification.

Nine distinct fixture browser regressions cover daily chart keyboard access, lineage, mobile navigation, the full approval/execution/outcome/feedback journey, tracking/empty states, rejection, mobile layouts and the two new home entry tests. Six mocked API tests cover unavailable/malformed data, stale approval and revision identity, absent endpoints and the new partial-home failure state. Production build and formatting pass. API checks do not certify a live production backend.

The first test run identified an incorrectly overridden link role in the attention register; preserving list semantics on a wrapper restored keyboard/link discovery and the existing journey. A new image test initially counted both responsive representations; it now checks the visible desktop table's four provider images. These were fixed without changing the supplied decision values or approval contract.

Local evidence: `.artifacts/frontend-completion/reference-1/` and `reference-2/` (ignored development screenshots/checks). Shared visual rules: `web/src/components/command-workspace.css`. Proposal rendering: `web/src/components/HomeProposal.tsx`. Durable new browser regressions: `web/tests/{e2e,api-browser}/home-proposal.spec.ts`.
