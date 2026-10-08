# Design system: Construction Grid

Recorded from the built frontend in `web/`. Source of truth for values is
`web/app/globals.css`; this file explains the system so new work stays inside it.

## Idea

Steam prices are steps, so everything is built from cells on one visible grid. The
display lettering, the charts and the page layout share a single unit. The look is
graph paper, a blue pen and one highlighter. Energy comes from scale and motion (large
uppercase type, cell-by-cell assembly, pinned scroll scenes), in the spirit of
landonorris.com, with Steam4Caster's own palette and letterforms.

It deliberately refuses the usual dark navy price-tracker dashboard with a smooth line
chart.

## The cell

- `.sheet` is a size container; `.sheet-inner` defines `--cell` as the container width
  divided by `--cols`: 32 columns under 640px, 40 up to 999px, 48 from 1000px.
- `.gridded` draws the hairline grid at `--cell`, with a heavier line every 8 cells.
- Horizontal page inset is `--gutter` (2 cells, 3 on wide screens) via `.wrap`.
- Section spacing and major gaps are multiples of `--cell`. Type sizes are not: they use
  `rem` and container-query clamps.

## Colour

| Token | Value | Use |
| --- | --- | --- |
| `--paper` | `#fbfcfe` | Ground |
| `--ink` | `#0c0e14` | Text, rules, filled cells |
| `--ink-2`, `--ink-3` | `#353b50`, `#565d75` | Secondary and tertiary text (both pass 4.5:1 on paper) |
| `--pen` | `#1b3cff` | Construction lines, links, the WAIT state, the blueprint field |
| `--hi` | `#ffe600` | Highlighter. Filled planes only, never text |
| `--red` | `#c8230d` | Errors only |
| `--grid`, `--grid-major` | pen at 8.5% and 20% | Grid hairlines |

Rules:

- Yellow means "marked": money a sale took off, a filled probability cell, the BUY
  state, the primary action, the active nav item. Text on yellow is always ink.
- Blue is structure: grid lines, the hatch for estimates, WAIT, and one full-bleed
  `.blueprint` section where the palette inverts (white on pen, tokens re-pointed).
- Colour commits at section scale: paper, then a blueprint field, then paper, closing
  on a full highlighter field (`.closer`). No gradients, no shadows except the chart
  readout's soft drop.

## Type

- **Cell type** (`CellType`): display words built from unit cells on a 5×7 glyph grid
  with diagonal corner cuts (`web/lib/cells.ts`). Used for the hero, verdict words
  (BUY / WAIT / NEUTRAL), big numbers and section closers. It assembles cell by cell
  and rebuilds when its text changes. Always paired with screen-reader text.
- **Archivo** (variable, width axis): everything else. Headings are `.display`:
  weight 800, stretch 122%, uppercase, tight tracking. `.h-xl`, `.h-lg`, `.h-md`, `.h-sm`.
- **Martian Mono** (`.data`): measurements only. Axis ticks, units, timestamps, small
  data labels. Never for headings or body.
- Numbers use `.num` (Archivo 800, tabular).
- No eyebrow labels above headings.

## Shape

- Corners are square. The one diagonal is the corner cut: buttons clip their
  bottom-right corner, the slider thumb and cell glyphs use the same cut, and every
  action carries the diagonal `Slash` mark.
- Regions are ruled, not carded: 1px ink rules (`.module`, `.rule-list`, `.rows`,
  `.kv`). No rounded cards, no nested cards.
- Hatching (blue 45° lines) always means "estimate" or "not yet": the forecast box, the
  WAIT state, loading skeletons, the lowest-price bar.

## Components

| Component | Notes |
| --- | --- |
| `.btn` (`Button`, `ButtonLink`) | Ink by default; `.btn-hi` yellow primary; `.btn-line` outlined; `.btn-sm`. Hover wipes a colour plane in from the left |
| `.input`, `.select`, `.check`, `.slider` | Square, 1px ink border; focus adds a pen inset and a yellow ring |
| `.nav` | Sticky top bar, wordmark, uppercase links with a yellow underline plane for hover and current; scrolls horizontally on small screens |
| `.chip`, `.tag` | Small mono labels; `.tag[data-tone]` encodes BUY (yellow), WAIT (hatch), FAILED (red) |
| `.kv` | Ruled grid of label/value facts |
| `.rows` / `.row` | Ruled list; link rows wipe yellow on hover |
| `.verdict` | The call. Background encodes the state: yellow (BUY), hatch (WAIT), plain (NEUTRAL) |
| `.empty`, `.skeleton`, `.error`, `.notice`, `.banner` | States |

## Charts

All charts are drawn from cells or on the grid, and are interactive by pointer and
keyboard.

- **PriceChart**: step line in ink. Each sale is a yellow plane between the regular
  price and the sale price. The lowest recorded price is a dashed pen line. Right of
  "today", the estimated next sale is a hatched pen box (time window by price range)
  with a median line. Hover or arrow keys read a step; drag zooms; double-click resets;
  1Y / 2Y / All presets.
- **Waffle**: 100 cells per horizon; filled cells are percentage points of chance.
- **TierChart**: discount depth as stacked cells; hover or focus a tier to read its
  share and implied price range.
- **CalibrationChart**: predicted against observed, with the pen diagonal as perfect
  calibration and squares sized by count.

## Motion

GSAP with ScrollTrigger (`web/lib/motion.ts`). House ease is `expo.out`; wipes use
`expo.inOut`.

- Cell type assembles in about a second, cells landing in scattered order.
- **Signature moment (`HeroMorph`)**: scrolling from the hero to the price scene, every
  cell of the headline breaks loose, flies across an open stretch of grid and re-stacks
  as columns under the price line, so the letters literally become the price history.
  The real chart's draw-on sweep then replaces the cells column by column. It is drawn
  on one fixed canvas, scrubbed by scroll and fully reversible. Keep it the only effect
  of this size on the page.
- The landing's price scene is pinned and scrubbed: the price log draws left to right
  while three copy beats swap.
- Section headlines wipe in with a clip-path; waffles and tier columns fill when they
  enter view; the chart draws on.
- On the hero, the pointer acts as a highlighter over the grid cells.
- Everything respects `prefers-reduced-motion`: no pin, no assembly, content shown.

## Voice in the interface

Plain and direct. Estimates are always called estimates. Buttons name their action.
Errors say what went wrong and what to do. Demo data is labelled as illustrative.
