# README asset notes

These assets document the application at the revision in `provenance.json`. They do not change the application or make claims about production performance.

The heroes reuse the approved Forward Shift symbol from `adapt/web/src/App.tsx`. Graphite/cyan is the requested motion palette; real product screenshots retain the application's paper/ink/lime palette. Light and dark heroes use independent readable foreground colors.

## Reproduce the motion

Use a separate documentation environment with Pillow. For MP4, also install `imageio-ffmpeg`. Application dependency files are intentionally untouched.

```sh
python render_assets.py --font-dir /path/to/fonts
```

Run from this directory. The font directory must contain `segoeui.ttf` and `segoeuib.ttf`; Windows defaults to its installed font directory. Fonts are not distributed here. Pass `--fixture path/to/snapshot.json` to use another compatible snapshot; the renderer asserts the demonstrated values to prevent accidental changes to the story.

The render uses the checked-in `story-data.json`, a snapshot extracted from the real frontend fixture module and the outcome produced by the real fixture approval/advance flow. Motion interpolation is explanatory artwork rather than a capture of every intermediate UI frame. The renderer removes its temporary frame directory after MP4 encoding.

Screenshots were captured with Playwright and installed Chrome at a 1440px viewport. Crops focus on actual product sections; no displayed values were replaced. The outcome's measurement disclosure was opened through the UI. Mobile app capture at 390px showed no horizontal overflow or browser exceptions.

## Content audit

- **Keep:** fixture/API separation, truthful simulation labels, approval controls and backend contracts.
- **Fix:** the outdated frontend-only description, missing engine setup/bootstrap/authentication, old routes and workstation-specific paths.
- **Remove:** long completion inventories, unsupported backend-pending claims and repetitive feature prose.
- **Add:** actual evidence-to-feedback journey, accurate source-linked algorithms, architecture, real screenshots, accessible motion alternatives and limitations.

## Review receipt

- **Judge:** the top tells the problem and full decision story; the live fixture walkthrough has exact actions. No invented benchmark appears.
- **Engineer:** current source modules back the formulas, routes and system boundaries. A clean lock install passed; source-help checks validate entrypoints. Full data bootstrap and live services remain explicitly untested in this pass.
- **Product reader:** the proposal explains why a high-ROAS receiver can be blocked, why money can remain unallocated and how outcomes affect later estimates.
- **Visual review:** restrained identity, consistent data scales, labelled provenance, readable desktop figures and a larger stacked mobile story. One batch corrected screenshot clipping and diagram density; a final batch exposed the calibration disclosure and eased stage transitions.
- **Compatibility:** GFM was rendered by GitHub's Markdown API. Preview checks cover 1200px/390px, both themes, working disclosures, image loads, Mermaid syntax, theme selection and reduced-motion/static alternatives. The native unpublished branch page has not been reviewed on GitHub itself.

PNG/GIF/MP4 files are finished documentation deliverables. `story-data.json`, `provenance.json` and `render_assets.py` are the minimal retained provenance and regeneration source; browser automation, preview HTML and frame/contact-sheet files are outside the repository.
