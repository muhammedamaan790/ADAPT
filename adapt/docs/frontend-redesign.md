# Frontend redesign rationale
Date: 2026-10-08. Branch: frontend-redesign.
User delegated the design choice: "research and analyse and choose whatever is the best".
## Product and workflow
The growth manager's job is to distinguish an actual efficiency problem from expected budget changes, review evidence and inventory limits, compare current/proposed allocation, verify policy checks, approve an immutable proposal and inspect execution/feedback. Financial estimates and causal diagnoses have different certainty; neither may masquerade as achieved results.
Home must connect a flagged change to a reviewable decision. It is a working analytical surface, not a marketing landing page. Preserve all 11 routes and frontend/API boundaries.
## ui-ux-pro-max research
Searched product/design-system, style, typography, chart, accessibility and React guidance. The global generator twice offered Enterprise Gateway landing-page structure: rejected hero/contact-sales treatment. Verified Data-Dense Dashboard style supports compact tables, restrained chrome and clear financial hierarchy. Verified Financial/Analytics palette profiles informed blue interactions and amber forecasts, combined with graphite neutrals. Typography research recommended IBM Plex Sans for financial readability; selected one self-hosted family, 400/500/600/700, Latin subset. Monospace remains for identifiers/code only.
Forecast guidance: actual solid, baseline dashed, both labelled; no uncertainty bands invented on the overview. Native range input and HTML chart-data table support keyboard/touch and precise comparison.
## Primary guidance
Prioritized content directs attention to the most promising choices; this informed the action register before detailed charting on mobile. [NN/g prioritization](https://www.nngroup.com/articles/prioritize-good-content-bubbles-to-the-top/)
Numeric comparison tables retain right alignment and tabular numerals. [GOV.UK table guidance](https://design-system.service.gov.uk/components/table/)
WCAG 2.2 AA target-size minimum is 24px with exceptions; this implementation intentionally targets 44px for primary mobile buttons, navigation and chart scrubber. Do not label every 44px recommendation an AA requirement. [W3C target size](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum)
## Direction exploration
Impeccable roll db6d492e assigned candidate 7. Own grounded candidate forms:
1. Financial operating memo: audience high, clarity high; direct but text-heavy.
2. Swiss information system: audience medium, clarity high; strong wayfinding, little evidence emphasis.
3. Media-buying run sheet: audience high, clarity high; risks campaign-only scope.
4. Portfolio tearsheet: audience high, clarity high; risks returns before constraints.
5. Market briefing: audience medium, clarity medium; editorial hierarchy can bury actions.
6. Retail assortment plan: audience high, clarity medium; inventory lens too dominant.
7. Evidence journal: audience high, clarity high; chosen. Borrow disciplined evidence/action pairing, not paper texture or illustration.
Challengers assessed on audience identification / product clarity: split-flap concourse medium/high (donate stable entity rows); garden map low/low (donate clear semantic color roles, decline organic lobes); software manual medium/medium (donate progressive disclosure); bitmap specimen low/low (donate typographic consistency, decline pixel costume); airline timetable medium/high (donate compact tabular comparison); video feed low/low (donate one primary action, decline one-at-a-time content).
## Three compositions
A evidence workbench: financial strip, trend left, attention right, sources below. Chosen for showing the problem and next action together.
B proposal-led: budget bars dominate before diagnosis. Declined because it prematurely presents a settled action.
C analysis-led: broad trend above attention. Declined because the task is buried beneath analysis.
All three live in .impeccable/mocks with prompts and delegated choice metadata. Generated wording/dates/extra controls are not authoritative; real fixture/API fields and the approved graphite Forward Shift symbol prevail.
## Interaction and responsive decisions
Operate / Analyze / Workspace grouped navigation; all routes findable without desktop More menu. On <=900px native focus-trapped navigation dialog replaces the rail. On <=600px the top bar stays reachable, and touch targets enlarge. Home moves the attention register ahead of metrics and chart on mobile. At <=1100px decision rail stacks; a supplied-estimate summary and review anchor prevent long-scroll concealment. Approval still requires exact-proposal confirmation.
No glow, gradients, sparkle branding, fabricated live connections or decorative metric cards. Quiet borders provide separation; states retain explicit text. Reconciled data is neutral; unavailable evidence uses Inbox, not a success tick.
## Validation boundaries
Screenshots and browser checks use Chromium desktop/emulated mobile; this is not physical-device testing. Automated axe checks cover default visible route states and cannot certify all WCAG requirements. API-browser regressions mock contracts; they are not a production backend integration certification.
## Tool gate exception
The Impeccable CLI's interactive plan-receipt gate does not represent the user's explicit delegation and remains pending. No human approval receipt is forged. Continue authorized implementation under higher-priority session instructions; record actual review and documentation evidence separately. Comp-diff readings are retained honestly rather than declared a pixel-fidelity pass.

## Final verification
Production build and formatting checks pass. The regression run covered 69 unit tests, 29 fixture browser tests and 28 API-contract browser tests (126 distinct checks). After the final reviewer correction batch, the build, all 69 unit tests and eight relevant browser tests were rerun successfully.

The final renders cover all 11 routes at 1505 × 1045 desktop and 390 × 844 mobile, with no document overflow or JavaScript runtime errors. An additional 55 route/width combinations at 320, 375, 768, 900 and 1280 pixels also have no document overflow. Touch chart scrubbing changes the selected observation; the mobile review shortcut clears the sticky header. Automated axe scans of 22 default route/viewport states report zero violations under WCAG 2 A/AA and 2.1 AA tags. These are bounded checks, not accessibility certification or physical-device testing.

The independent visual critique drove mobile attention priority, a proposal review shortcut, neutral reconciliation labels, clearer empty states and a consolidated backend recovery message. The isolated technical assessor hit its usage limit after producing the detector result; the primary completed that technical audit, explicitly recorded as a degraded single-context assessment. The detector ran once and returned no findings.

The finish review requested three corrections: desktop source visibility, Opportunity Map provenance placement, and documentation of the implemented visual system. All six source records now occupy y973–1040 in the 1045-pixel desktop viewport without reducing type size. The decision-loop strip follows below the fold. The final comp-diff score is 0.7226 with verdict `drift`; changes required by actual data, the approved logo and real interactions are documented translations, and pixel certification is not claimed. The CLI plan-receipt gate remains pending separately from rendered review.

Local review evidence is retained in `.impeccable/review/` and `.artifacts/redesign/`, which are development artifacts excluded from the application bundle. The existing approximately 515 KB entry-chunk build warning remains; no field performance measurement or production backend certification is claimed.

The independent finish reviewer scored all three named corrections resolved and returned `ship` for that fix list, with no regressions observed in the inspected recaptures. This verdict does not certify the entire surface or pixel fidelity. The required documenter completed its extraction but failed two file-write attempts; the primary safely applied and validated the token-bearing documents using the fallback instructions. This handoff substitution is disclosed rather than represented as a successful independent write.

The final `build-phase finish --disposition ship` command was attempted and refused because `plates`, `hero`, `sections`, `motion` and `responsive` are not formally closed. The phase remains `plates`, under the previously recorded plan-receipt exception. The frontend implementation, browser verification, scoped review verdict and actual documentation are complete; formal CLI closure remains unfinished. No approval receipt or gate pass is fabricated.
