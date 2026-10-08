# Reference study: Triple Whale Attribution page

## How this was studied

The live page (`https://www.triplewhale.com/attribution`) could not be inspected. Both an automated
Chrome session (Playwright 1.63, `channel: 'chrome'`) and a plain HTTP fetch were refused by the
site's Cloudflare bot protection ("Sorry, you have been blocked" / HTTP 403). We did not try to get
around that protection.

This breakdown therefore comes from the two product-marketing screenshots supplied with the brief
(the Compass "Command Center" iPad framing and the Attribution table + AI Agent column + mobile
agent feed composition), plus the qualities the brief asks us to match. It describes the
**system**: composition, hierarchy and motion character. It does not give pixel measurements of the live
page. Anything marked *inferred* is a pattern we are adopting, not something we measured.

## Global system

| Aspect | What the references show | What we take |
| --- | --- | --- |
| Canvas | Near-white surfaces, one saturated brand blue used as a field or accent, never both at once | Paper and ink only (the workspace palette); one full-bleed dark band for the close |
| Product framing | Real product UI is the hero asset: tables, KPI tiles, sparklines, recommendation rows. Framed by a device bezel or a soft window, cropped off-canvas so it feels larger than the viewport | Product mockups are the illustrations. Crop them deliberately and let them bleed |
| Typography | One neutral grotesque. Large semibold display, small medium labels, numbers set large and plain | Instrument Sans for display and UI, JetBrains Mono for exact values. Big figures in the same sans |
| Hierarchy | Every module answers one question: a label, one big number, a delta, a sparkline | Same module grammar: label → figure → delta → evidence |
| Surfaces | Hairline borders (~1px cool grey), 12–16px radii, near-zero shadow at rest; depth comes from overlap, not drop shadows | Hairlines and overlap. One soft shadow tier for the "stage" window |
| Colour discipline | Green only for positive deltas, blue for brand/selection, red/amber only in alerts | Semantic colour only: ink for identity and action, red for loss, green for gain |
| Highlighted column | The "AI Agent" column is lifted out of the table as its own raised surface with a soft halo. The product's new capability is shown *inside* the old table | Our recommendation is shown inside the data it came from, not on a separate card |
| Notification cards | Agent alerts overlap the main composition, layered at a different depth | Anomaly rows enter the Command Center the same way, at one depth only |
| Mobile | The same story re-composed vertically. The device mockup becomes the frame | Stage collapses to visual-over-copy. Nothing is hidden, only re-stacked |

## Section grammar (inferred from the brief's description of the page)

1. **Hero:** short, very large statement + one CTA pair, product UI immediately below or beside,
   already animating.
2. **Proof strip:** a quiet band between hero and story.
3. **Pinned product story:** the product window stays in place while copy chapters advance;
   the UI inside the window changes state rather than the page swapping images.
4. **Feature bands:** alternating text/visual compositions, each one product capability.
5. **Closing CTA:** a single dark or brand-coloured full-bleed band.

## Motion character (target, not measured)

- **Few moving things at once.** Large compositions move together, small data moves inside them.
- **Scroll-scrubbed, not scroll-triggered,** inside the pinned story: progress maps directly to state
  so scrolling back reverses it exactly.
- **Easing:** long decelerating curves (ease-out, roughly `cubic-bezier(.2,.7,.2,1)`), 500–900ms
  for entrances, 200–300ms for hover.
- **Numbers interpolate;** charts draw along their path; highlighted rows lift with a soft halo.
- **Nav:** transparent over the hero, gains a hairline and blurred fill once scrolled.

## What we must not take

Logo, name, copy, screenshots, illustrations, customer logos, testimonials, iconography, and the
brand blue. Only the craft carries over: pacing, hierarchy, product-as-illustration, and
coordinated motion.
