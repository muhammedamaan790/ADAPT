---
name: ADAPT
description: A precise advertising decision workspace with visible evidence and guarded budget approval.
colors:
  canvas: "#f3f6fa"
  surface: "#ffffff"
  ink: "#152238"
  muted: "#586a83"
  rule: "#dce4ee"
  accent: "#135cec"
  accent-soft: "#edf3ff"
  success: "#20704b"
  warning: "#87550c"
  danger: "#b03338"
  success-soft: "#edf7f1"
  warning-soft: "#fff6e7"
  danger-soft: "#fdf0f0"
  soft: "#f6f8fa"
  forecast: "#a66a0b"
  focus: "#135cec"
  graphite: "#202833"
  nav-ink: "#edf2f7"
  nav-muted: "#b5bfcb"
  nav-hover: "#303c4c"
  nav-selected: "#135cec"
  nav-selected-ink: "#ffffff"
  primary-hover: "#0c48c7"
  dark-canvas: "#151d28"
  dark-surface: "#1d2837"
  dark-ink: "#edf3ff"
  dark-muted: "#adbed6"
  dark-rule: "#35465b"
  dark-accent: "#8ab4ff"
  dark-accent-soft: "#273c60"
  dark-success: "#8dd1ab"
  dark-warning: "#e7bd7c"
  dark-danger: "#f4a1a5"
  dark-success-soft: "#253b32"
  dark-warning-soft: "#3b3225"
  dark-danger-soft: "#402c32"
  dark-soft: "#263347"
  dark-forecast: "#e7bd7c"
  dark-focus: "#a9c8ff"
typography:
  headline:
    fontFamily: "IBM Plex Sans, sans-serif"
    fontSize: "32px"
    fontWeight: 600
    lineHeight: 1.25
    letterSpacing: "-0.025em"
  section:
    fontFamily: "IBM Plex Sans, sans-serif"
    fontSize: "18px"
    fontWeight: 600
    lineHeight: 1.4
    letterSpacing: "-0.012em"
  title:
    fontFamily: "IBM Plex Sans, sans-serif"
    fontSize: "14px"
    fontWeight: 600
    lineHeight: 1.5
    letterSpacing: "0"
  body:
    fontFamily: "IBM Plex Sans, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.6
    letterSpacing: "normal"
  support:
    fontFamily: "IBM Plex Sans, sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.6
    letterSpacing: "normal"
  label:
    fontFamily: "IBM Plex Sans, sans-serif"
    fontSize: "12px"
    fontWeight: 500
    lineHeight: 1.4
    letterSpacing: "normal"
  metadata:
    fontFamily: "IBM Plex Sans, sans-serif"
    fontSize: "11px"
    fontWeight: 400
    letterSpacing: "normal"
  financial:
    fontFamily: "IBM Plex Sans, sans-serif"
    fontSize: "26px"
    fontWeight: 600
    letterSpacing: "-0.03em"
rounded:
  chip: "4px"
  control: "6px"
  panel: "8px"
  dialog: "14px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "20px"
  panel: "20px"
  dialog: "24px"
  page: "24px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "#ffffff"
    rounded: "{rounded.control}"
    padding: "10px 14px"
    height: "40px"
  button-primary-hover:
    backgroundColor: "{colors.primary-hover}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "10px 14px"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "10px 12px"
    height: "42px"
  panel:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
    padding: "20px"
  badge:
    backgroundColor: "{colors.soft}"
    textColor: "{colors.muted}"
    rounded: "{rounded.chip}"
    padding: "3px 7px"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.muted}"
    rounded: "{rounded.control}"
    padding: "10px 14px"
  button-danger:
    backgroundColor: "{colors.danger}"
    textColor: "#ffffff"
    rounded: "{rounded.control}"
    padding: "10px 14px"
---

# Design System: ADAPT

## Overview

**Creative North Star: "Evidence Studio"**

Evidence Studio uses graphite navigation and cool reading surfaces for sustained comparison. One self-hosted sans family, ruled financial rows and restrained semantic color keep the interface precise while allowing substantial evidence density. The approved two-form Forward Shift symbol is the identity asset; retain its geometry in light, graphite and monochrome applications.

**Key Characteristics:**

- Cool reading ground and graphite navigation.
- Tabular financial values and thin comparison rules.
- Explicit evidence, forecast and execution-state labels.
- Native dialogs, disclosures and keyboard-accessible charts.

This is the implemented replacement world authorized by the frontend redesign request and delegated design selection. Its normative tokens come from the final overriding CSS and self-hosted font imports. The current reference-led home composition and responsive priorities are documented in `adapt/docs/frontend-reference-redesign.md`; the earlier redesign record remains historical.

Review boundary: the final concept comparison reports 0.7226, `drift`; pixel certification is not claimed. The formal CLI plan-receipt gate remains pending because it does not encode the user's delegated selection. No human approval receipt or passed build phase is fabricated. Browser evidence covers desktop and emulated mobile; it is not a physical-device or complete accessibility certification.

The reference-led home places the supplied budget proposal beside an expanded campaign chart, followed by a full-width attention register and source strip. Shared navigation uses saturated blue selection on graphite. Phone users see stacked current/proposed budgets before the attention register and financial metrics. Existing guarded decision workflows remain intact.

## Colors

The palette is cool and quiet, with graphite chrome and restrained blue action color.

### Primary

**Decision Blue** (`accent`) identifies analytical actuals, links and selection. Primary buttons keep the light-theme blue with white text in both themes; `primary-hover` supplies their darker hover state. Dark-theme chart/link accents use the lighter `dark-accent` for legibility.

### Secondary

**Forecast Amber** (`forecast`) distinguishes baseline/forecast material. Plot forecasts are dashed and explicitly labeled, while actuals are solid blue. It is separate from general warning state.

### Neutral

**Cool Canvas**, **White Surface**, **Operating Ink**, **Quiet Slate** and **Comparison Rule** (`canvas`, `surface`, `ink`, `muted`, `rule`) separate page, containers, text and rows. **Graphite Navigation** remains stable across themes. The corresponding `dark-*` tokens govern dark reading surfaces and state colors.

Success, warning and danger use paired foreground and soft background tokens for actual state semantics. A rise in ad spend is neutral unless the product establishes that it is beneficial. Reconciled is a neutral status; missing evidence is an empty state rather than success.

The sidecar's eight-step OKLCH ramps are synthesized palette previews for documentation; they are not implemented application scales. The extracted frontmatter values remain authoritative.

**The Evidence before assertion Rule.** Keep measured, reconciled, forecast, simulated and unavailable states explicit. Color supports these labels; it cannot replace them.

## Typography

**Display and Body Font:** IBM Plex Sans, with sans-serif fallback. Latin weights 400, 500, 600 and 700 are self-hosted. Headings and financial values predominantly use weight 600; monospace is limited to identifiers and code.

### Hierarchy

- **Headline:** the normative headline token, reduced to 26px on phones; the decision-detail headline uses 28px on desktop.
- **Section:** compact 17px headings, reduced to 16px on phones.
- **Title and Body:** 14px; supporting explanatory copy uses 13px.
- **Label and Table:** 12px for compact controls and comparisons, with tabular financial numerals and right-aligned numeric columns.
- **Metadata:** 11px for subordinate dates, comparisons and identifiers. Source provenance has an existing compact 10px treatment; this exception is not a new body-text standard.
- **Financial:** 26px for overview amounts, 25px on phones; model-estimate emphasis uses 32px in the proposal review.

**The One reading voice Rule.** Use IBM Plex Sans for interface text and financial values. Reserve monospace for identifiers and code.

## Layout

The desktop rail is 224px and collapses to 76px; it becomes 204px below 1100px. The content container is capped at 1480px with 28px page insets, reducing to 22px below 1100px, 20px below 900px and 16px below 600px. Desktop chrome uses a 48px top bar and a separate truthful data-mode disclosure strip.

Working panels use the panel spacing token, with 18px for compact desktop overview panels and phone panels. Overview evidence and attention pair in a 1.35:1 grid with a 20px gap. At 1280px and below, six metrics and sources reflow into three columns; below 600px they use two columns. At 900px and below the sidebar is replaced by a native navigation dialog exposing all 11 routes. Navigation groups are Operate, Analyze and Workspace.

Mobile home places the attention register before metrics and charting. The decision review stacks below evidence at 1100px and below, with a compact proposal summary and review shortcut above it. The phone top bar is sticky; section anchors clear it by 76px. Tables scroll within labeled containers rather than causing document overflow.

The desktop first-view correction fits all six source records at 1505 × 1045 with existing type sizes. The decision-loop strip follows below the fold. These viewport-specific observations are evidence, not a promise that every section fits every screen.

## Elevation & Depth

**The Flat comparison surfaces Rule.** Use thin rules and tonal separation for working panels. Reserve physical elevation for the modal layer.

Panels and lineage popovers have no decorative shadow. The native modal retains its structural shadow (`0 15px 60px #0003`) and a simple dimming backdrop (`rgb(20 29 41 / 50%)`) without blur. Do not promote the discarded popover shadow into the new system.

## Shapes

Panels use gently rounded corners through the panel token. Controls, badges and dialogs use their smaller or larger role-specific radii. Borders are thin and continuous. The logo's two geometric forms remain exact SVG geometry rather than an enclosing badge or generated illustration.

## Components

### Buttons

Primary actions have blue fill, white text and a darker hover state. Secondary actions have surface fill and a rule border. Buttons use 12px medium text, 40px minimum desktop height and 44px mobile minimum height. Disabled controls use reduced opacity and a not-allowed cursor. Keyboard focus uses a visible 3px outline with 3px offset. Buttons transition their background over 150ms ease-out; reduced-motion preferences remove transitions and animations while keeping textual feedback.

### Chips

Compact, softly filled state labels use medium text. Neutral, accent, success, warning and danger variants bind to semantic tokens. Place provenance beside or below the heading it qualifies; it is never a decorative eyebrow.

### Cards / Containers

Surface fill, rule border, panel radius and restrained internal padding create comparison containers. Lists rely on rules and aligned values rather than separate floating metric tiles. Section headings can wrap without hiding their qualifiers.

### Inputs / Fields

Fields have visible labels, surface backgrounds, rule borders and control radii. Inputs use 13px text and 42px minimum height; invalid fields use a danger border. Preserve native focus and disabled behavior. Placeholder text is subordinate support, not the field label.

### Navigation

Graphite navigation uses pale readable labels, a darker hover background and a saturated-blue selected row with white text. All routes retain meaningful names and grouped hierarchy. The mobile dialog traps focus, closes on navigation or Escape, and restores focus to its trigger. Native controls have accessible names even when their visible phone treatment is icon-only.

API mode preserves the existing sign-in form and session controls from main. The sign-in page uses the same reading surfaces, labeled native fields and primary action; the top bar exposes the existing signed-in identity and sign-out control. Fixture mode retains its explicitly illustrative workspace.

### Financial comparison and charts

Financial strips use tabular values separated by vertical rules. The campaign plot is mathematically drawn SVG with a solid actual series and dashed amber baseline. Exact values are available through a labeled range input and an HTML data-table disclosure. Pointer and touch input supplement keyboard controls; they are not the sole means of reading data. Plot geometry responds to container width while labels remain legible.

### Approval and lineage

Approval is a native dialog showing the exact proposal, immutable identity and required confirmation. Its wording distinguishes fixture interaction from connected execution. Lineage is a native, viewport-clamped popover with light dismissal and Escape support. Forecasts, likely drivers and accounting decomposition preserve their distinct certainty labels.

### Decision and economics workbenches

The decision inbox and campaign economics comparison use native disclosures with explicit chevrons, ruled rows and visible search/filter labels. They remain optional context around the active proposal; opening either never approves or edits budgets. The inbox pairs decision identity, status, policy blockers and the supplied horizon estimate. Campaign comparison exposes keyboard-sortable numeric columns with `aria-sort`; unknown marginal contribution sorts last in both directions. At narrow widths filters stack and the economics table scrolls internally rather than expanding the document.

Daily budget movement uses paired horizontal bars on a shared zero-based scale, with exact current/proposed values and net allocation change. Current is neutral graphite and proposed is blue; spending more does not imply a successful outcome. Forecast/measured feedback uses a common signed scale with a marked zero, an amber dashed forecast and solid blue measurement. Zero values have no visible bar. Creative review uses ruled two-column evidence groups on desktop and one column on mobile, retaining CTR change, frequency, source-qualified explanations and textual review states. Stable creatives are neutral rather than success-colored. These additions bind to the existing theme tokens and do not introduce a second visual system.

## Do's and Don'ts

### Do:

- Do align monetary comparisons and use tabular numerals.
- Do pair color with text, icons or line style for meaning.
- Do keep data provenance and model qualifiers near the evidence they describe.
- Do preserve visible focus, labels and feedback in both themes.

### Don't:

- Don't replace the approved Forward Shift symbol with generated decoration.
- Don't use decorative gradients, glows or sparkle branding in this analytical workspace.
- Don't present a forecast, probable driver or fixture interaction as an achieved outcome.
- Don't use the discarded Manrope/DM Sans and cream/purple palette for new screens.
