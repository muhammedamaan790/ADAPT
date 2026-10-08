# ADAPT UI Registry

This registry complements the project-level `DESIGN.md`. It records the reusable implementation patterns that new frontend work should match. All colour comes from `src/design.css` tokens; never hard-code a hex value in a component sheet.

### Application Shell and Navigation

File: `src/App.tsx`, `src/design.css`
Last updated: 2026-10-08

| Property | Class / token |
| --- | --- |
| Page background | `body` on `--c-paper`; dark theme adds the 6% lime top wash |
| Header | `.site-header`: sticky, `paper/0.75` with a 12px backdrop blur, `line/0.5` bottom hairline, 64px row |
| Identity | `.wordmark`: Forward Shift symbol (`.wordmark-mark`, ink) plus Fraunces `.wordmark-text` |
| Routes | `.route-strip`: 48px row with all 8 routes in three `.route-group`s split by a hairline |
| Route states | muted → ink on hover (0.3s); active is ink/500 with a 2px sage underline that scales in |
| Header controls | `.nav-pill` (Copilot), `.mode-pill` (data mode), `.nav-round` (theme toggle, menu), `.avatar` |
| Content | `main.shell-wide`: 76rem max, 2rem / 1.5rem / 1rem gutters |
| Footer | `.site-footer`: surface/0.4 band, brand blurb plus three link columns, data-mode base line |

**Pattern notes:** The theme class lives on `<html>` (set before paint by the inline script in `index.html`) and is mirrored on `.app` for legacy selectors. At 900px and below, the route strip is replaced by the labelled menu trigger and the native `Modal` navigation dialog (all 8 routes as bordered rows; the active route is a lime tile). Footer link names must not repeat route labels, because tests locate routes by exact name.

### Page Heading

File: `src/design.css` (`.page-heading`)
Last updated: 2026-10-08

| Property | Class / token |
| --- | --- |
| Eyebrow | `::before` on the heading's first `div`, text from `--eyebrow` (set on `<main>` per route group) |
| Title | 2.75rem / 700 / −0.035em, 2.125rem on phones |
| Lead | 1.0625rem muted, max 38rem |

**Pattern notes:** Keep the `.page-heading > div > h1 + p` structure. The eyebrow is a pseudo-element on the wrapper, not the heading, so heading accessible names stay exact.

### Semantic Status Badge

File: `src/components/ui.tsx`, `src/design.css`
Last updated: 2026-10-08

| Property | Class / token |
| --- | --- |
| Shape | `.badge`: pill, 1px `line` border, 0.6875rem / 500 |
| Neutral | transparent fill, muted text |
| Accent | `sage/0.35` border on `sage-soft/0.3` |
| Success / warning / danger | `*-soft` fill, `*-text` colour, leading 6px dot |

**Pattern notes:** Every status includes readable text; the dot is supplementary. Completed, approved and healthy states are success. Failed, blocked and conflict states are danger. Pending, partial and executing states are warning.

### Cards, Tiles and Quick Answers

File: `src/design.css`
Last updated: 2026-10-08

| Property | Class / token |
| --- | --- |
| Card | `.panel`, `.metrics`, `.workbench-stats`: surface, `line/0.7`, 1.6rem radius, 1.75rem padding |
| Card hover | 0.5s: border `sage/0.4`, shadow `--shadow-hover` |
| Verdict surface | `.review-card`, lineage popover, `.modal`: raised fill, `--shadow-verdict` |
| Tile | `.scenario-option`, `.investigation-row`, `.decision-switcher a`: 1rem radius, hover lifts 2px with a `lime/0.6` border |
| Selected tile | lime fill, lime-ink text, badges re-tinted to lime-ink |
| Quick answer | `.brief`, `.command-brief`, `.reserve-callout`, `.lab-intro`: `sage-soft/0.6` on `sage/0.25` |
| Figures | Fraunces 400 for metric values, estimates and display numbers; mono for exact small values |

### Controls

File: `src/design.css`
Last updated: 2026-10-08

| Property | Class / token |
| --- | --- |
| Primary | `.button.primary`: sage pill, `--shadow-button`, `brightness(1.25)` hover, `1.1` active |
| Secondary | `.button.secondary`: ghost pill; hover gives an ink/0.2 border and a surface fill |
| Tertiary | `.button.ghost` / `.tertiary`: text only, faint ink wash on hover |
| Danger | `.button.danger`: stop fill, white text |
| Segmented | `.report-tabs`: pill track; `[aria-pressed='true']` is a sage pill |
| Section links | `.section-nav a`: bordered pills, trailing arrow nudges 2px |
| Fields | 0.75rem radius, line border; focus gives a sage border and a 1px inset ring |
| Slider | 6px line track, 22px lime thumb with a 3px ink keyline, scales 1.12 on hover |

**Pattern notes:** Do not set `display` on shared control classes in `design.css`; layout sheets hide some buttons per breakpoint (for example `.mobile-filter-toggle`).

### Motion

File: `src/hooks/reveal.ts`, `src/design.css`
Last updated: 2026-10-08

| Property | Class / token |
| --- | --- |
| Easing | `--ease`: `cubic-bezier(.22,.61,.36,1)` |
| Scroll reveal | `[data-reveal='pending']`: opacity 0, translateY(16px); `in` settles over 0.7s with a 70ms stagger |
| Reduced motion | reveal skipped; all transitions 0.001ms |

**Pattern notes:** To make a new block reveal, add its selector to `BLOCKS` in `reveal.ts`. Nested matches inherit their ancestor's motion and are not observed separately.

### Ask ADAPT Agent Panel

File: `src/components/AskAdapt.tsx`, `src/design.css`
Last updated: 2026-10-08

| Property | Class / token |
| --- | --- |
| Container | `.ask-panel`: 2rem radius, `line` border, surface → `lime/0.1` vertical wash (0.05 in dark) |
| Header | `.ask-eyebrow` pill with `.ask-dot`; `.ask-greeting` Fraunces 1.875rem (1.5rem on phones) |
| Suggestions | `.ask-chip`: popular-search pills (`sage/0.35` on `sage-soft/0.3`), lift and arrow nudge on hover |
| Messages | `.ask-user`: ink bubble, right; `.ask-bot`: raised bubble with hairline, left; both rise in over 0.5s |
| Evidence | `.ask-source` pills to app routes; `.ask-checked` success line; `badge-warning` for unmatched figures |
| Progress | `.ask-step` list; the live step has a pulsing lime dot and ellipsis |
| Composer | `.ask-form` pill with sage focus ring; `.ask-send` round sage button (Stop while answering) |

**Pattern notes:** Model output is rendered as text: only `**bold**`, `- ` and `1. ` lists are interpreted, so no markup can be injected. The thread is kept in `sessionStorage` (`adapt.ask.thread`, last 40 messages) so it survives navigation, and the last 16 turns are sent as history. Enter sends; Shift+Enter starts a new line.

