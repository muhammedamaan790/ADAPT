# ADAPT UI Registry

This registry complements the project-level `DESIGN.md`. It records the reusable implementation patterns that new frontend work should match.

### Application Shell and Responsive Navigation

File: `src/App.tsx`, `src/styles.css`
Last updated: 2026-10-07

| Property | Class / token |
| --- | --- |
| Workspace background | `.app` with `--canvas` |
| Navigation background | `.sidebar` with `--nav` (`#11191d`) |
| Navigation border | `1px solid --nav-rule` |
| Navigation radius | `7px` links, `8px` workspace switcher |
| Text - primary | `--ink`; near-white inside the rail |
| Text - secondary | `--muted`; `--nav-text` inside the rail |
| Spacing | 244px desktop rail; 34px workspace gutters; 10px/11px navigation controls |
| Hover state | Slate lift (`#19262c`) with bright text |
| Active state | Deep cobalt surface (`#21385f`) with a 3px inset marker |
| Shadow | None at rest; tinted soft shadow only on the open mobile drawer |
| Accent usage | Restrained cobalt for selected routes and primary actions |

**Pattern notes:** Keep the desktop rail fixed and the workspace header sticky. Group routes as Workspace, Operations and Intelligence. At 860px and below, replace the rail with the labelled menu trigger and off-canvas drawer. The drawer contains every route, closes on route change, backdrop click, close button, or Escape, and locks body scrolling while open.

### Semantic Status Badge

File: `src/components/ui.tsx`, `src/styles.css`
Last updated: 2026-10-07

| Property | Class / token |
| --- | --- |
| Background | `.badge` uses `--soft`; semantic variants use `--*-soft` |
| Border | Transparent 1px border |
| Border radius | `4px` |
| Text - primary | Semantic `--success`, `--warning`, `--danger`, or `--accent` |
| Text - secondary | `--muted` for neutral states |
| Text style | 9px, 600 weight, 0.2px tracking |
| Spacing | 3px 7px |
| Shadow | None |
| Accent usage | Cobalt for active/informational states only |

**Pattern notes:** Every status must include readable text. Completed, approved, healthy, ready, online, available, applied, and feasible states are green. Failed, blocked, rejected, conflict, and unresolved critical states are red. Pending, open, partial, acknowledged, executing, and compensating states are amber. Normal metadata remains neutral.

### Loading and Empty States

File: `src/components/ui.tsx`, `src/styles.css`
Last updated: 2026-10-07

| Property | Class / token |
| --- | --- |
| Background | Transparent loading region; empty-state icon uses `--accent-soft` |
| Border | None |
| Border radius | 4px skeleton lines; 9px empty icon tile |
| Text - primary | `--ink` headings |
| Text - secondary | `--muted` labels and explanations |
| Spacing | 14px loading gap; 42px 24px empty-state padding |
| Motion | 1.4s skeleton opacity/scale pulse, disabled for reduced motion |
| Shadow | None |
| Accent usage | Restrained cobalt on the empty-state icon only |

**Pattern notes:** Loading placeholders should echo content density with short aligned skeleton lines instead of a generic spinner. Empty states use a neutral inbox icon and actionable, factual copy; do not imply success unless the empty condition is genuinely positive.

### Panels, Data Bands, and Forms

File: `src/styles.css`
Last updated: 2026-10-07

| Property | Class / token |
| --- | --- |
| Standard panel | `.panel`: surface, 1px rule, 11px radius, 22px padding |
| Metric band | `.metrics`: bordered six-column band; two columns on phones |
| Workbench stats | `.workbench-stats`: three-column bordered band; two columns on phones |
| Fact group | `.fact-grid`: inset neutral cells with shared perimeter, not floating cards |
| Fields | 42px minimum height, 7px radius, `--rule-strong` border |
| Focus | Cobalt border plus a subtle 3px cobalt ring |
| Tables | Tinted header, tabular numbers, restrained row hover, horizontal scroll on narrow viewports |
| Tabs | `.report-tabs` and `.section-nav` use one segmented surface with compact selected cells |

**Pattern notes:** Keep ordinary surfaces flat and use borders, inset fills, and alignment for hierarchy. Shadows are reserved for modals, drawers, and floating provenance details. Use one parent border for comparable metrics instead of turning every value into an unrelated card.
