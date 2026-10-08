---
name: ADAPT
description: A precise advertising decision workspace with visible evidence and guarded budget approval.
colors:
  # RGB triplets, consumed as rgb(var(--c-name) / alpha). Light theme is :root; dark theme is :root.dark.
  paper: "250 250 248"
  surface: "255 255 255"
  raised: "255 255 255"
  inset: "244 244 240"
  ink: "20 21 24"
  muted: "92 94 100"
  faint: "143 145 152"
  line: "228 228 224"
  sage: "24 25 27"
  sage-soft: "232 245 202"
  sage-ink: "250 250 248"
  lime: "198 242 78"
  lime-ink: "20 22 15"
  warn: "173 122 40"
  stop: "190 78 58"
  go-text: "62 96 18"
  warn-text: "133 88 18"
  stop-text: "168 60 42"
  dark-paper: "11 11 13"
  dark-surface: "20 20 24"
  dark-raised: "28 29 34"
  dark-inset: "26 27 31"
  dark-ink: "240 240 237"
  dark-muted: "165 166 172"
  dark-faint: "118 119 126"
  dark-line: "44 45 51"
  dark-sage: "198 242 78"
  dark-sage-soft: "34 40 16"
  dark-sage-ink: "18 20 12"
  dark-warn: "216 169 78"
  dark-stop: "208 122 99"
  dark-stop-text: "222 140 118"
typography:
  display:
    fontFamily: "Instrument Sans Variable, system-ui, sans-serif"
    fontSize: "2.75rem"
    fontWeight: 700
    lineHeight: 1.02
    letterSpacing: "-0.035em"
  title:
    fontFamily: "Fraunces Variable, Georgia, serif"
    fontSize: "1.25rem"
    fontWeight: 400
    lineHeight: 1.25
    letterSpacing: "-0.02em"
  figure:
    fontFamily: "Fraunces Variable, Georgia, serif"
    fontSize: "2rem"
    fontWeight: 400
    letterSpacing: "-0.02em"
  body:
    fontFamily: "Instrument Sans Variable, system-ui, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.625
    letterSpacing: "-0.006em"
  lead:
    fontFamily: "Instrument Sans Variable, system-ui, sans-serif"
    fontSize: "1.0625rem"
    fontWeight: 400
    lineHeight: 1.6
  eyebrow:
    fontFamily: "Instrument Sans Variable, system-ui, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 500
    letterSpacing: "0.16em"
    textTransform: "uppercase"
  data:
    fontFamily: "JetBrains Mono Variable, ui-monospace, monospace"
    fontSize: "0.75rem"
    fontWeight: 400
rounded:
  pill: "999px"
  field: "0.75rem"
  tile: "1rem"
  card: "1.6rem"
spacing:
  shell: "76rem"
  gutter: "2rem"
  gutter-tablet: "1.5rem"
  gutter-phone: "1rem"
  card: "1.75rem"
  card-phone: "1.25rem"
motion:
  ease: "cubic-bezier(0.22, 0.61, 0.36, 1)"
  reveal: "opacity 0 → 1, translateY(16px) → 0, 0.7s, 70ms stagger (max 350ms)"
  card-hover: "0.5s"
  control: "0.3s"
components:
  button-primary:
    backgroundColor: "{colors.sage}"
    textColor: "{colors.sage-ink}"
    rounded: "{rounded.pill}"
    padding: "0.625rem 1.25rem"
    shadow: "0 8px 24px -10px rgba(20, 21, 24, 0.5)"
    hover: "brightness(1.25)"
  button-secondary:
    backgroundColor: "transparent"
    border: "1px {colors.line}"
    textColor: "{colors.ink}"
    rounded: "{rounded.pill}"
    hover: "border ink/20, surface fill"
  button-danger:
    backgroundColor: "{colors.stop}"
    textColor: "#ffffff"
    rounded: "{rounded.pill}"
  card:
    backgroundColor: "{colors.surface}"
    border: "1px {colors.line}/0.7"
    rounded: "{rounded.card}"
    padding: "{spacing.card}"
    hover: "border sage/0.4, shadow 0 8px 40px -16px rgba(32, 29, 43, 0.16)"
  tile:
    backgroundColor: "{colors.surface}"
    border: "1px {colors.line}"
    rounded: "{rounded.tile}"
    hover: "translateY(-2px), border lime/0.6, shadow 0 10px 40px -18px"
    selected: "lime fill, lime-ink text"
  badge:
    border: "1px {colors.line}"
    rounded: "{rounded.pill}"
    padding: "0.1875rem 0.625rem"
  input:
    backgroundColor: "{colors.surface}"
    border: "1px {colors.line}"
    rounded: "{rounded.field}"
    focus: "sage border, 1px inset sage ring"
---

# Design System: ADAPT

## Overview

**Paper, ink and one signal colour.** ADAPT reads like a calm financial brief: warm off-white paper (near-black in the dark theme), hairline cards, a bold sans for page titles, a serif for card titles and figures, and a single lime accent reserved for the thing that matters right now: the selected item, the primary action in the dark theme, the proposed budget, the live slider thumb.

**Key characteristics**

- Sticky, blurred header with the ADAPT symbol, a Fraunces wordmark and every route one click away.
- Large tight-tracked page titles under a small uppercase section eyebrow.
- Hairline cards with a slow, quiet hover (border darkens, soft shadow appears). No glow, no gradients on controls.
- Pill buttons, pill badges, pill segmented tabs.
- Content rises into place as it scrolls into view.
- Tabular, right-aligned financial values. Big figures in Fraunces, small exact values in JetBrains Mono.

The implementation lives in `adapt/web/src/design.css`, loaded last. Earlier sheets (`styles.css`, `components/*.css`) own layout only and consume the semantic variables below.

## Colors

Tokens are RGB triplets (`--c-paper`, `--c-ink`, …) so any surface can take an alpha. The layout sheets use semantic names mapped onto them: `--canvas`, `--surface`, `--soft` (inset), `--ink`, `--muted`, `--faint`, `--rule`, `--accent`, `--accent-soft`, `--success`, `--warning`, `--danger` and their `-soft` pairs, `--forecast`, `--focus`. Both themes switch from the `dark` class on `<html>`. An inline script in `index.html` applies the saved choice (`localStorage['adapt.theme']`, else the system preference) before first paint.

- **Sage** is the action and emphasis colour: ink in the light theme, lime in the dark. Eyebrows, primary buttons, chart actuals, the active route underline and selected segmented tabs use it.
- **Lime** is the signal: selected tiles, the slider thumb, the current decision-loop step, healthy-source dots. In the light theme it is only ever a fill, never text.
- **Warn** (amber) marks forecasts and baselines, which are always dashed and labelled. It is separate from the warning state.
- **Stop** marks blocking and failed states.
- **Status text** uses the `*-text` variants, which hold at least 4.5:1 on paper and surface. Status badges add a dot so colour is never the only signal.
- **Dark theme background:** paper with a faint lime radial wash from the top (6% opacity, fixed). This is the only gradient in the system.

**The evidence-before-assertion rule.** Keep measured, reconciled, forecast, simulated and unavailable states explicit. Colour supports these labels; it never replaces them.

## Typography

Three self-hosted variable families: **Instrument Sans** (interface and page titles), **Fraunces** with optical sizing (card titles, the wordmark, large figures), **JetBrains Mono** (chart axes, exact small values, identifiers, formulas).

- **Page title:** 2.75rem / 700 / −0.035em, 2.125rem on phones. Preceded by the route group (Operate, Analyze, Workspace) as a sage eyebrow.
- **Lead:** 1.0625rem muted, max 38rem.
- **Card title:** Fraunces 1.25rem / 400.
- **Figures:** Fraunces 2rem for metric values, 2.5rem for the proposal estimate.
- **Eyebrows and table headers:** 0.6875–0.75rem, uppercase, 0.12–0.16em tracking.
- **Body:** 0.875rem, line-height 1.625.

## Layout

- The content shell is 76rem with 2rem gutters (1.5rem ≤ 900px, 1rem ≤ 600px).
- The header is 64px. On desktop, a 48px route strip below it lists all 11 routes in three hairline-separated groups.
- At 900px and below, the strip is replaced by the menu button and a native navigation dialog with the same 11 routes.
- `--nav-h` keeps anchors and the sticky decision review clear of the header.
- The footer repeats the identity, deep-links into key views (names distinct from the route labels) and shows the data mode.

## Elevation and depth

Cards are flat at rest. Hover adds `0 8px 40px -16px rgba(32,29,43,.16)` and a sage/0.4 border over 0.5s. The decision review card, the lineage popover and dialogs are "verdict" surfaces: raised fill and `0 24px 60px -24px rgb(20 21 24 / .22)`. Dialog backdrops dim with a 6px blur.

## Motion

- **Easing:** everything uses `cubic-bezier(.22,.61,.36,1)`.
- **Scroll reveal:** `useReveal` (`src/hooks/reveal.ts`) marks page blocks `data-reveal="pending"` and flips them to `in` as they enter the viewport, with a 70ms stagger. State lives in a data attribute so React re-renders never reset it.
- **Hover durations:** controls 0.3s, cards 0.5s.
- **Arrows:** `→` glyphs nudge 2px right on hover.
- **Reduced motion:** reveal and transitions are disabled entirely under `prefers-reduced-motion`.

## Components

- **Buttons:** pills. Primary is sage fill with a soft drop shadow and `brightness(1.25)` on hover. Secondary is a ghost: line border, gaining a surface fill and ink/20 border on hover. Tertiary is text-only. Danger is a stop fill. Disabled is 50% opacity.
- **Badges:** pills with a hairline border. Accent uses the sage-soft tint. Success, warning and danger add a leading dot.
- **Cards** (`.panel`, `.metrics`, `.workbench-stats`): 1.6rem radius, line/0.7 border, card hover.
- **Tiles** (`.scenario-option`, `.investigation-row`, `.decision-switcher a`): 1rem radius. On hover they lift 2px with a lime/0.6 border. The selected tile is a lime fill with lime-ink text.
- **Quick-answer boxes** (`.brief`, `.command-brief`, `.reserve-callout`, `.lab-intro`): sage-soft/0.6 fill, sage/0.25 border, uppercase sage label.
- **Segmented tabs** (`.report-tabs`): a pill track; the selected item is a sage pill.
- **Section links** (`.section-nav`): bordered pills with a trailing arrow.
- **Fields:** 0.75rem radius, line border. Focus shows a sage border plus a 1px inset ring.
- **Slider:** 6px line track with a 22px lime thumb and a 3px ink keyline, growing 12% on hover.
- **Tables:** uppercase faint headers, hairline rows, a faint row hover, tabular numerals.
- **Charts:** solid sage actual, dashed warn baseline, mono axis labels. Exact values stay available through the range input and the data-table disclosure.
- **Identity:** the approved two-form Forward Shift symbol, monochrome ink, beside the Fraunces wordmark. Its forms ease apart on hover.

## Do's and don'ts

### Do

- Align monetary comparisons and use tabular numerals.
- Pair colour with text, dots, icons or line style.
- Keep provenance and model qualifiers next to the evidence they describe.
- Keep visible focus, labels and feedback in both themes.

### Don't

- Don't replace or recolour the Forward Shift symbol.
- Don't add glows, neon shadows, gradient buttons or sparkle decoration. The dark-theme top wash is the only gradient.
- Don't use lime for text on paper.
- Don't present a forecast, probable driver or fixture interaction as an achieved outcome.
