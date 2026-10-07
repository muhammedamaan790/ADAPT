---
name: ADAPT
description: An evidence-led operating workspace for inspecting and approving D2C budget decisions.
colors:
  canvas: "#f5f5f1"
  surface: "#fff"
  ink: "#202522"
  muted: "#626860"
  rule: "#e2e5de"
  accent: "#6550b9"
  accent-soft: "#efebfa"
  success: "#23704c"
  warning: "#8a5813"
  danger: "#ae3939"
  success-soft: "#eef6f0"
  warning-soft: "#faf2e5"
  danger-soft: "#faeeee"
  soft: "#f8f9f5"
  primary-hover: "#53409e"
  focus: "#a48ee6"
  selection: "#d9cff2"
  nav-text: "#bcc4b7"
  nav-hover: "#2d342d"
  nav-active: "#d4c8f2"
  nav-active-text: "#282032"
  dark-canvas: "#181c1a"
  dark-surface: "#222723"
  dark-ink: "#edf0e9"
  dark-muted: "#b0b8ad"
  dark-rule: "#3a423c"
  dark-accent: "#b7a0f1"
  dark-accent-soft: "#342c4c"
  dark-success: "#88cda5"
  dark-warning: "#e0b97d"
  dark-danger: "#ee9b9b"
  dark-success-soft: "#263d2f"
  dark-warning-soft: "#403423"
  dark-danger-soft: "#402a2a"
  dark-soft: "#2a302b"
typography:
  headline:
    fontFamily: "Manrope Variable, sans-serif"
    fontSize: "30px"
    fontWeight: 700
    lineHeight: 1.25
    letterSpacing: "-.025em"
  title:
    fontFamily: "Manrope Variable, sans-serif"
    fontSize: "17px"
    fontWeight: 700
    lineHeight: 1.4
    letterSpacing: "-.025em"
  subtitle:
    fontFamily: "Manrope Variable, sans-serif"
    fontSize: "14px"
    fontWeight: 700
    lineHeight: 1.5
    letterSpacing: "-.025em"
  body:
    fontFamily: "DM Sans Variable, sans-serif"
    fontSize: "14px"
    fontWeight: 400
  label:
    fontFamily: "DM Sans Variable, sans-serif"
    fontSize: "11px"
    fontWeight: 600
    lineHeight: 1.4
  badge:
    fontFamily: "DM Sans Variable, sans-serif"
    fontSize: "9px"
    fontWeight: 600
    lineHeight: 1.5
    letterSpacing: ".2px"
rounded:
  chip: "4px"
  control: "6px"
  panel-mobile: "7px"
  inset: "8px"
  panel: "9px"
  dialog: "12px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "24px"
  xxl: "32px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.surface}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "10px 13px"
  button-primary-hover:
    backgroundColor: "{colors.primary-hover}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "10px 13px"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.muted}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "10px 13px"
  button-danger:
    backgroundColor: "{colors.danger}"
    textColor: "{colors.surface}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "10px 13px"
  panel:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
    padding: "22px"
  field:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "10px 12px"
  badge-success:
    backgroundColor: "{colors.success-soft}"
    textColor: "{colors.success}"
    typography: "{typography.badge}"
    rounded: "{rounded.chip}"
    padding: "3px 7px"
  badge-warning:
    backgroundColor: "{colors.warning-soft}"
    textColor: "{colors.warning}"
    typography: "{typography.badge}"
    rounded: "{rounded.chip}"
    padding: "3px 7px"
  badge-danger:
    backgroundColor: "{colors.danger-soft}"
    textColor: "{colors.danger}"
    typography: "{typography.badge}"
    rounded: "{rounded.chip}"
    padding: "3px 7px"
  dialog:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.dialog}"
    padding: "26px"
    width: "calc(100% - 32px)"
---

# Design System: ADAPT

## Overview

**Creative North Star: "The Evidence Workspace"**

ADAPT's operate mode feels calm, precise and inspectable. A warm off-white workspace and an ink navigation rail frame compact financial information. Restrained violet identifies primary actions, selected states and model forecasts; status colors communicate health, review and blocked states alongside explicit text.

Fine rules, aligned numbers and grouped white surfaces keep the evidence readable. Approval remains a deliberate interaction connected to the proposal's evidence, policy checks and decision hash. Illustrative fixtures remain visibly labelled, and forecasts remain distinct from measured outcomes.

**Key Characteristics:**

- Warm neutral workspace with an ink navigation rail.
- Restrained violet emphasis and text-labelled semantic status.
- Compact typography, tabular financial numerals and fine dividers.
- Evidence before explicit approval, with visible fixture provenance.
- Locally bundled fonts, responsive navigation and reduced-motion support.

## Colors

The palette uses warm neutrals for work surfaces, violet for model/action emphasis and separate semantic colors for operational states. Frontmatter captures the existing stylesheet; dark-prefixed values are the corresponding dark-theme overrides.

### Primary

- **Restrained Violet** (`accent`): primary action fills, forecasts, selected controls and links. **Violet Wash** (`accent-soft`) groups the brief and selected scenarios without saturating the page.
- **Deep Action Violet** (`primary-hover`): primary button hover. **Focus Lavender** (`focus`) marks keyboard focus; **Selection Lavender** (`selection`) marks selected text.

### Secondary

- **Healthy Green** (`success`, `success-soft`): healthy sources, passed checks and verified execution.
- **Review Amber** (`warning`, `warning-soft`): review conditions, inventory warnings and fixture notices.
- **Blocked Red** (`danger`, `danger-soft`): failed checks, blocked tracking, adverse states and errors.

### Neutral

- **Warm Canvas** (`canvas`) and **White Surface** (`surface`): page ground and grouped panels.
- **Operating Ink** (`ink`): primary text and navigation background. **Quiet Slate** (`muted`) carries supporting context; **Fine Rule** (`rule`) separates rows; **Inset Neutral** (`soft`) distinguishes minor groups.
- Navigation has separate quiet text, hover surface and lavender selected-state tokens. The rail retains its ink background in both themes.

**The Status Text Rule.** Pair every semantic state color with a readable label or explanation; color alone cannot establish the state.

Dark mode remaps workspace variables. Primary and destructive button fills, their white text and primary hover remain fixed literal colors in the current code; not all controls use the dark accent overrides.

## Typography

**Heading Font:** Manrope Variable with a sans-serif fallback.
**Body Font:** DM Sans Variable with a sans-serif fallback.

Both fonts are bundled locally through Fontsource. Manrope gives headings and financial values a firm hierarchy; DM Sans keeps dense labels readable. Supporting interface copy often uses 9–12px rather than the root body size. Paragraphs use a line height of 1.6.

### Hierarchy

- **Headline:** the page heading role in frontmatter. Decision headings use 28px; at the mobile breakpoint page headings use 25px and decision headings 24px.
- **Title:** section headings use the title role; mobile section titles use 15px.
- **Subtitle:** smaller grouped headings use the subtitle role.
- **Body:** the root size and family; component copy adjusts size for its density.
- **Label:** compact semibold action text. Badges have their own smaller role.
- **Financial values:** Manrope with tabular numerals. Standard metrics use 25px, forecasts 36px and the scenario day 54px. These are component sizes rather than a universal display scale.

**The Comparable Numbers Rule.** Use tabular numerals for financial metrics, review facts, chart labels and tables; align amounts consistently within their group.

## Layout

Desktop uses a fixed ink sidebar (224px) and matching workspace offset. The optional compact sidebar uses 76px. Main content is centered at a maximum width of 1290px with padding of 30px 36px 36px. At widths of at least 1600px, the maximum becomes 1400px and shell gutters become 48px.

Command Center uses six-column metric/source groups and a two-column overview. Decisions and the lab use flexible main content plus a review/control aside (300px), separated by a 22px gap. The decision aside is sticky at a 20px top offset. Related rows use rules inside grouped panels.

At widths up to 1200px, the sidebar becomes 196px, main gutters become 24px, metrics/sources become three columns, the overview becomes one column and the decision/lab aside becomes 275px. At widths up to 900px, decision/lab layouts become one column: the decision review precedes the main content and internally uses two columns.

At widths up to 700px, the sidebar becomes a full-width static header with horizontal primary navigation and no workspace offset. Main padding becomes 23px 18px; metrics/sources use two columns. The decision main precedes its single-column review and lab controls stack. Wide allocation tables retain horizontal scrolling. Mobile primary navigation preserves route labels.

The spacing vocabulary is recorded in frontmatter. Fitted component padding also includes 13px, 18px, 20px and 22px; preserve those observed values when extending the same component. The retained 32px design scale step appears in dialog viewport spacing, rather than standard panel padding.

## Elevation & Depth

The workspace is flat at rest: background tones, thin borders and dividers establish depth. Panels have no shadow. Shadows separate floating lineage details and approval dialogs from underlying evidence. The dialog backdrop dims and lightly blurs the workspace.

### Shadow Vocabulary

- **Lineage popover:** `0 8px 24px #1112`.
- **Approval dialog:** `0 15px 60px #0003`.
- **Dialog backdrop:** `#1118` with a 2px blur.

**The Flat Workspace Rule.** Keep ordinary panels and information rows flat; reserve elevation for floating details and modal decisions.

## Shapes

Gently curved controls and grouped surfaces soften dense information without making every row a card. Controls use the control radius; panels use the panel radius, reduced to the mobile panel radius at 700px. Briefs and lineage popovers use the inset radius. Small badges use the chip radius; dialogs use the largest radius.

Borders are generally a single fine rule. Circular avatars, health dots and loop nodes are functional markers. Ordinary content surfaces remain rectangular, without decorative imagery.

## Components

### Buttons

Compact semibold actions with gently curved outlines. Primary and destructive fills use fixed colors. Secondary actions use a surface fill with a fine border; ghost actions use a transparent fill and muted text. Hover changes the background without movement. Disabled buttons reduce opacity to 0.5 and use a not-allowed cursor.

Buttons use a 150ms ease background transition. Keyboard focus uses a 3px lavender outline with a 3px offset. Icon buttons are smaller square controls rather than a separate primary action style.

### Chips

Status badges use soft semantic fills, explicit short text and a small radius. These are informational labels. Forecast/model badges use violet; success, review and blocked badges use their semantic pairs.

### Cards / Containers

Surface fills, a fine border and no shadow. Standard panels use the frontmatter panel padding; decision main panels use 24px and review cards 20px. At 1200px panels use 20px padding, and at 700px they use 18px. Internal rows use dividers so comparisons stay close together.

### Inputs / Fields

Fields use a surface fill, fine border, control radius, padding of 10px 12px and a minimum height of 38px. Compact labels sit above the field. Keyboard focus uses the shared outline; invalid inputs use a danger border. Textareas resize vertically. Approval checkboxes use action violet.

### Navigation

The ink rail uses compact labelled links, a quiet hover fill and a lavender selected surface with dark text. At 700px it becomes horizontal navigation in the header. Decision section links use a text row and fine bottom rule; preserve the distinction between primary routes and in-page evidence sections.

### Decision Review and Approval

The review groups forecast, policy checks and action together. Approval presents the allocation, explicit acknowledgement and decision hash before confirmation. Fixture notices remain visible at route level. Keep probable explanations, model forecasts and measured feedback distinct in copy and labels.

### Charts and Progress

Charts use fine grid lines, violet emphasis, restrained supporting series and textual numeric labels. Accessible summaries expose numeric data. Loading uses a 1.2s linear rotating indicator; control transitions are brief and functional. Reduced motion removes animations, transitions and smooth scrolling.

## Do's and Don'ts

### Do:

- **Do** pair health, review and blocked colors with readable state labels.
- **Do** use local DM Sans and Manrope fonts and tabular numerals for comparisons.
- **Do** keep evidence, policy checks and the decision hash available before explicit approval.
- **Do** visibly label illustrative fixtures and distinguish forecasts from measured outcomes.
- **Do** preserve horizontal primary navigation on mobile and respect reduced-motion preferences.

### Don't:

- **Don't** use semantic color as the only explanation of a state.
- **Don't** add shadows to ordinary panels or turn every information row into a separate card.
- **Don't** present fixture values as engine output, evaluation results or measured simulator evidence.
- **Don't** hide a failed API connection by silently showing fixtures.
- **Don't** add remote font or image requests to the locally bundled visual system.
