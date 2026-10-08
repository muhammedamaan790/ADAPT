# ADAPT story map

Route: `/product` (public, outside `AuthGate` and the workspace shell). Code: `adapt/web/src/landing/`.
All figures are one illustrative scenario and are labelled as such on the page.

## Story spine: OBSERVE → DIAGNOSE → DECIDE → ACT → LEARN

| # | Reference moment | ADAPT moment | Loop step | Visual | Motion |
| --- | --- | --- | --- | --- | --- |
| 1 | Hero + live product UI | "Know what changed. Know why. Know what to fund next." | — | Command Center window: KPI row, channel allocation, live cycle feed | Feed rows enter on a timer; sparklines draw once; one anomaly row lands last |
| 2 | Proof strip | What ADAPT reads: ad platforms, orders, SKU costs, inventory, outcomes | — | Single hairline band of source names with a health dot | Static. A rest beat before the story |
| 3 | Pinned story, state 1 | **Signal**: Meta Prospecting ROAS 4.12 → 2.91 (−29.4%) | Observe | ROAS line, 14 days | Line draws, figure interpolates, anomaly marker lands, "Something changed." |
| 4 | Pinned story, state 2 | **Diagnosis**: ROAS bridge CPM / CTR / CVR / AOV | Diagnose | Waterfall grows *out of* the ROAS line's last point | Bars build left to right; CVR isolates, the rest recede to 35% |
| 5 | Pinned story, state 3 | **Business context**: Classic Oversized Tee, margin 36% → 28%, cover 6.2 days vs 14-day restock, M/L sold out | Diagnose | The CVR bar collapses into a chip; the SKU ledger slides in beside it | Chip persists; SKU rows stagger; cover meter fills to its threshold |
| 6 | Pinned story, state 4 | **Decision**: shift ₹18,000/day Meta Prospecting → Google PMax, +₹41,200/week, 91% confidence, 4/4 policy checks | Decide | Evidence collapses into a three-line stack; the decision card rises over it | Card enters with one lift; figures interpolate |
| 7 | Pinned story, state 5 (signature) | **Budget flow**: 48/27/18/7 → 40/35/18/7, ₹0 → +₹41,200/week | Act | Allocation bars; an ₹18K token travels from Meta to Google | Token travels; the bars change in step with it; the counter is tied to the token's progress |
| 8 | Feature band | **Creative intelligence**: six creatives over 21 days | Observe/Diagnose | Original typographic ad thumbnails in four lanes | Scroll-scrubbed day counter; cards re-lane with layout animation as fatigue crosses thresholds |
| 9 | Feature band | **Profitability**: Campaign A ROAS 4.6 / margin 11% vs B 3.7 / 42% | Decide | ROAS bars against each campaign's break-even ROAS (1 / margin) | Bars grow; break-even markers drop in; the ranking flips |
| 10 | Feature band | **Outcome feedback**: expected +₹41,200 vs actual +₹37,500/week | Learn | Cumulative contribution: dashed forecast band vs solid actual; loop ring | Actual line draws inside the band; response factor 1.00 → 0.91 |
| 11 | Closing CTA | "Open the workspace" | — | Dark band, loop recap | One reveal |

## Analytics: numbers that must agree

- **ROAS identity:** ROAS = 1000 · CTR · CVR · AOV / CPM, so the ratio of new to old ROAS is
  (1+ΔCTR)(1+ΔCVR)(1+ΔAOV) / (1+ΔCPM).
  With CPM +3.2%, CTR +1.8% and AOV −0.7%, a fall from 4.12 to 2.91 needs **CVR −27.9%**.
  The brief's −17.9% would only produce −19.6%, so we changed CVR to keep the headline intact.
- **Bridge** (log-share allocation of ΔROAS = −1.21): CPM −0.11, CTR +0.06, **CVR −1.14 (94%)**,
  AOV −0.02.
- **Allocation:** total ₹2,25,000/day. An ₹18,000 shift is 8 points: Meta 48→40% (₹1,08,000 incl.
  ₹80,000 Prospecting), Google 27→35% (incl. ₹42,000 PMax), TikTok 18%, Other 7%.
- **Profitability:** contribution per ₹100 spend = ROAS·100·margin − 100. A: −₹49.4. B: +₹55.4.
  Break-even ROAS = 1/margin. A 9.09, B 2.38.
- **Outcome:** 37,500 / 41,200 = 0.91 (−9.0%). The 80% interval ₹29,800–₹52,600 contains it, and
  the response factor for this shift type is recalibrated to 0.91.

## Product vocabulary reused from the workspace

Command Center, Decision Center, Anomalies, Outcomes, Learning; policy checks *Budget ceiling &
reserve*, *Daily change limit*, *Inventory exposure*, *Required data health*; Approve mode; INR
`en-IN` formatting (`src/lib/format.ts`); forecasts dashed, measured values solid.

## Colour

Paper and ink, taken from the workspace palette in `src/design.css`. No accent hue. In the allocation charts Meta is ink and Google is mid grey, so the two channels in the story stay separable by lightness. TikTok and Other are light greys. Red and green appear only for loss and gain, always next to a signed number.

## Motion stack

- `motion` (v14) for everything. `useScroll` on a tall section + CSS `position: sticky` gives the
  pinned, scrubbed sequences without GSAP. GSAP was not needed: no sequence requires timeline
  labels or pin-spacing that sticky + progress cannot express.
- Transforms and opacity only. Bars use `scaleX`/`scaleY`, lines use `pathLength`.
- `prefers-reduced-motion`: pinned stages render as stacked static frames at each chapter's end
  state. No scrubbing, no travelling tokens.
