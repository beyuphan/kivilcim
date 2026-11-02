# Design system

The front end is plain CSS and ES modules, with no build step. This page records the decisions so a
change can keep them. Everything lives in `static/css/app.css` and `static/js/`.

## The night look (since 0.9)

One dark scene. `css/app.css` holds the structure and the tokens; `css/night.css` holds the look and is loaded
after it. The map fills the screen; the top bar and the panel are glass (`--glass`, a blurred dark layer with a
thin light edge) floating over it. Everything except heat is cool and dark, so a fire is the brightest, warmest
thing in view: heat uses an amber to crimson ramp (`heatColor` in `map.js`), the only strong accent on the page.
Pins are real elements (`.pin`) with a number and a glow; they pulse only when the person has not asked for
reduced motion. Loading is always shown: the map loader pill with a spinner and a sweep, and a fade between
base layers.

## Principles

- **Colour has one job each.** Blue is what you can act on; green to dark red is the danger level; gray is
  unknown. A level is never colour alone: it always has a word next to it.
- **Say what you see.** The panel opens with a sentence ("In the last 24 hours the satellites saw heat in
  23 zones"), then numbers, then detail. Terms such as zone and detection are explained in place.
- **Say what you do not know.** Weak evidence, old data and low-resolution images are labeled, not hidden.
- **The map is the product.** On a wide screen it fills the space next to a 420 px panel; on a phone the panel
  is a sheet with three heights (peek, half, full) over the map.

## Tokens

Defined on `:root` and redefined for dark mode.

| Group | Tokens |
| --- | --- |
| Text sizes | 12, 13, 14, 16, 18, 22, 28 px |
| Spacing | 4, 8, 12, 16, 20, 24, 32, 40 px |
| Radii | 8, 12, 16, 20 px, pill; outer radius = inner radius + the gap between them |
| Surfaces | `--bg`, `--surface`, `--surface-2`, `--border`, `--border-strong` |
| Text | `--text`, `--text-2`, `--text-3` (all at least 4.5:1 on every surface they sit on) |
| Levels | `--lv-*` (solid), `--lv-*-bg` and `--lv-*-fg` (pill) for low, moderate, high, very high, extreme, unknown |
| Elevation | `--shadow-1` (cards), `--shadow-2` (popovers, map controls) |

Contrast was measured for every text and surface pair in both themes (4.5:1 for text). The one failing
pair found while building (`--text-3` on `--surface-2`, 4.24:1) was darkened to 5.08:1.

## Components

`.card`, `.row` (a zone or a fire), `.pill` (level), `.meter`, `.stat`, `.chip`, `.segmented`, `.btn`
(primary, secondary, ghost, icon), `.disclosure`, `.event-card` with `.thumbs`, `.timeline`, `.compare`,
`.legend`, `.popover`, `.skeleton`, `.notice`.

## Behavior rules

- Interactions that depend on hover are inside `@media (hover: hover)`; touch devices get the pressed state
  (`scale: 0.96`) only.
- `transition` names its properties; nothing uses `transition: all`.
- Motion is off under `prefers-reduced-motion: reduce` (skeleton shimmer, sheet animation, map fly-to,
  spread playback pace).
- Touch targets are at least 44 px; inputs are 16 px so iOS does not zoom the page.
- Content that arrives later is reserved with skeletons of the final size, so layout does not shift.
- Focus is visible everywhere (3 px outline); the search box is an ARIA combobox; the compare line is an ARIA
  slider (arrow keys, Home and End); a live region announces view changes.

## Structure

- `index.html` is the shell: top bar, panel, map. `app.js` routes (`/`, `/?lat=..&lon=..`, `/history`,
  `/history/{id}`) with the History API and loads view modules on first use: `home.js`, `place.js`,
  `history.js`, `event.js`. `compare.js` draws the before/after layers; `map.js`, `search.js`, `sheet.js`,
  `api.js`, `ui.js`, `icons.js` and `tilemath.js` are shared.
- The methodology page uses the same CSS and a small `methodology.js` for the measured results.
- The content security policy allows scripts from this site only; the page's settings travel in a JSON block.
