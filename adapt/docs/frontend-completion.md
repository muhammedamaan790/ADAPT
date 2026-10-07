# ADAPT frontend completion pass

This pass extends Evidence Studio on `frontend-redesign`. It preserves the graphite Forward Shift identity, all 11 routes, API sign-in, exact-proposal approval and fixture/simulation boundaries. The operating workflow remains evidence → anomaly → diagnosis → candidate → allocation → approval → execution → outcome → feedback.

## Implemented

- **Decision inbox:** expandable queue with title/ID/class search, status filtering, selected identity, supplied horizon estimate and policy blockers. Operational and not-estimable decisions do not display invented financial valuations.
- **Budget movement:** paired current/proposed daily budgets on one scale, exact amounts, unit deltas and net allocation change. Tables and phone summaries remain available. Increasing spend is not colored as success.
- **Campaign economics:** read-only search/channel comparison of current budget, ROAS, margin, marginal CAA, inventory gate and allowed range. Numeric/alphabetical sorting exposes `aria-sort`; unknown marginal CAA sorts last in either direction. Comparison never edits budgets.
- **Creative evidence:** review/stable filtering, CTR-change and frequency measures, source-qualified explanations and a neutral stable state. Ruled desktop columns stack on phones; no fake asset thumbnails.
- **Outcome comparison:** forecast and measured ΔCAA share a signed scale and marked zero. Dashed amber forecast differs from solid blue measurement; exact values and calibration context remain visible. Mixed signs, losses and zeros are supported.
- **Chart hardening:** additional decomposition terms fit within a wider, keyboard-focusable internal scroll region. Full labels and values remain in accessible chart text/titles.
- **Route loading:** core screens now load on demand. The main production JS chunk dropped from 517.51 KB to 464.35 KB, about 10%; this is not a field performance claim.

Shared components: `web/src/components/{DecisionInbox,CampaignEconomics,CreativeSignals,FinancialComparison}.tsx` and `evidence-workbench.css`. No new dependency or backend endpoint. `DESIGN.md` documents the added patterns.

## Two-cycle review

UI/UX Pro Max guidance informed sorted comparisons, numeric alignment, progressive disclosure and accessible chart data. Impeccable's polish workflow informed the rendered review. This was a primary, single-context assessment, not an independent dual-agent critique or formal CLI-gate certification. Questions were skipped under the user's routine-design autonomy and focused-pass request.

Cycle 1 exposed an inherited rule hiding phone filters, decomposition overflow, scroll-region keyboard access, ambiguous select naming, missing disclosure indicators and an overly dark forecast outline. Cycle 2 confirmed their fixes on desktop and phone without restarting the visual direction. A later signed-value test received one narrow functional correction: a zero forecast must not retain a visible two-pixel border. No third design refinement cycle.

## Verification

- TypeScript/Vite production build and Prettier check pass.
- 69 unit tests pass.
- 33 fixture browser tests pass across the initial suite and targeted reruns, including evidence → approval → verified execution → matured outcome → feedback, new inbox/search/sort/filter checks and a phone-filter regression.
- 29 mocked API browser tests pass across the initial suite and a targeted rerun, including identity conflicts, errors, unsafe model links, untrusted SQL cells and mixed-sign/zero chart cases.
- Five changed states were rendered at 1440 × 1000 and 390 × 844; outcomes were also inspected in dark theme. Ten visible-state axe scans found zero WCAG 2 A/AA and 2.1 AA tagged violations. No runtime JS errors. Eight 320 px checks of the four changed routes found no document overflow, with disclosures open.
- Focused source review found no raw-HTML rendering or `eval`. New links retain a fixed internal route and encode IDs. Zod validation, cookie sessions and in-memory CSRF remain intact. This is not a penetration test or Codex Security scan.

Screenshots/checks: ignored `.artifacts/frontend-completion/cycle-{1,2}/`. Durable browser tests: `web/tests/{e2e,api-browser}/evidence-workbench.spec.ts`.

## Limits

API browser tests mock HTTP contracts; they do not certify a production backend, live ad accounts or actual profit lift. Chrome phone emulation and axe do not certify physical-device behavior or complete accessibility compliance.

Current contracts supply campaign budget economics, creative fatigue and SKU shortfall projections, but no complete joined SKU profit-and-loss table or creative asset thumbnails. Missing scoring/models keep their unavailable states. The frontend does not invent costs, measured lift, connections or execution success.

Optional Build Web Apps/Data Visualization, Context7, 21st.dev, Superpowers and Codex Security capabilities in the brief were unavailable. Existing typed React, local design guidance and actual Chrome checks supplied the applicable implementation and verification.
