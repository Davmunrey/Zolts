---
version: alpha
name: Zolts-design-system
description: "An evidence-grade dark product surface for a GTM execution runtime. Anchors on a near-black canvas (#08090a) with a four-step surface ladder and hairline borders, in the tradition of dense technical software. A single violet accent (#7d4bf5) carries brand and interaction — mark, primary action, focus ring, active navigation — and is confined to chrome. Five separate colours carry measurement semantics: verified lift, experimental control, policy denial, review, and live latency. The accent sits in violet precisely because no data series would ever occupy that hue, so brand and evidence never collide on a dense screen. Numerals are always tabular mono: a P&L that jitters between rows is a P&L nobody trusts."

colors:
  canvas: "#08090a"
  surface-1: "#0e1011"
  surface-2: "#141618"
  surface-3: "#1a1d1f"
  surface-raised: "#1f2325"
  hairline: "#1f2226"
  hairline-strong: "#2b2f34"
  hairline-focus: "#4a5058"
  ink: "#f2f4f5"
  ink-muted: "#c2c8cc"
  ink-subtle: "#8b9297"
  ink-tertiary: "#5c6367"
  ink-inverse: "#ffffff"
  accent: "#7d4bf5"
  accent-hover: "#9670f7"
  accent-pressed: "#6a3ce0"
  accent-dim: "#1e1435"
  accent-ring: "#8f66f7"
  inverse-canvas: "#ffffff"
  inverse-hover: "#e3e6e8"
  data-lift: "#3ecf7e"
  data-lift-dim: "#153d28"
  data-control: "#7d8590"
  data-control-dim: "#22262a"
  data-deny: "#f4645a"
  data-deny-dim: "#3d1a18"
  data-review: "#d9a441"
  data-review-dim: "#3a2c11"
  data-live: "#3bc4d4"
  data-live-dim: "#0f3238"

typography:
  display-xl:
    fontFamily: Zolts Display
    fontSize: 60px
    fontWeight: 560
    lineHeight: 1.04
    letterSpacing: -2.2px
  display-lg:
    fontFamily: Zolts Display
    fontSize: 40px
    fontWeight: 560
    lineHeight: 1.08
    letterSpacing: -1.3px
  headline:
    fontFamily: Zolts Display
    fontSize: 24px
    fontWeight: 550
    lineHeight: 1.20
    letterSpacing: -0.6px
  title:
    fontFamily: Zolts Display
    fontSize: 17px
    fontWeight: 550
    lineHeight: 1.30
    letterSpacing: -0.3px
  body:
    fontFamily: Zolts Text
    fontSize: 14px
    fontWeight: 400
    lineHeight: 1.55
    letterSpacing: -0.06px
  body-sm:
    fontFamily: Zolts Text
    fontSize: 13px
    fontWeight: 400
    lineHeight: 1.50
    letterSpacing: 0
  caption:
    fontFamily: Zolts Text
    fontSize: 12px
    fontWeight: 450
    lineHeight: 1.35
    letterSpacing: 0
  eyebrow:
    fontFamily: Zolts Text
    fontSize: 11px
    fontWeight: 550
    lineHeight: 1.30
    letterSpacing: 0.5px
  button:
    fontFamily: Zolts Text
    fontSize: 13px
    fontWeight: 500
    lineHeight: 1.20
    letterSpacing: -0.05px
  metric-xl:
    fontFamily: Zolts Mono
    fontSize: 34px
    fontWeight: 450
    lineHeight: 1.05
    letterSpacing: -1.2px
  metric:
    fontFamily: Zolts Mono
    fontSize: 19px
    fontWeight: 450
    lineHeight: 1.15
    letterSpacing: -0.5px
  mono:
    fontFamily: Zolts Mono
    fontSize: 12.5px
    fontWeight: 400
    lineHeight: 1.60
    letterSpacing: 0

rounded:
  xs: 3px
  sm: 5px
  md: 7px
  lg: 10px
  xl: 14px
  pill: 9999px
  full: 9999px

spacing:
  xxs: 4px
  xs: 8px
  sm: 12px
  md: 16px
  lg: 24px
  xl: 32px
  xxl: 48px
  section: 80px

motion:
  duration-instant: 90ms
  duration-fast: 150ms
  duration-base: 220ms
  duration-slow: 380ms
  ease-out: cubic-bezier(0.23, 1, 0.32, 1)
  ease-in-out: cubic-bezier(0.77, 0, 0.175, 1)
  press-scale: 0.97

components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.ink-inverse}"
    typography: "{typography.button}"
    rounded: "{rounded.md}"
    padding: 7px 13px
  button-secondary:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.ink}"
    typography: "{typography.button}"
    rounded: "{rounded.md}"
    padding: 7px 13px
  button-ghost:
    backgroundColor: transparent
    textColor: "{colors.ink-subtle}"
    typography: "{typography.button}"
    rounded: "{rounded.md}"
    padding: 7px 10px
  panel:
    backgroundColor: "{colors.surface-1}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.lg}"
    padding: 20px
  metric-tile:
    backgroundColor: "{colors.surface-1}"
    textColor: "{colors.ink}"
    typography: "{typography.metric}"
    rounded: "{rounded.lg}"
    padding: 18px
  data-row:
    backgroundColor: transparent
    textColor: "{colors.ink-muted}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.xs}"
    padding: 10px 12px
  status-pill:
    backgroundColor: "{colors.surface-3}"
    textColor: "{colors.ink-muted}"
    typography: "{typography.caption}"
    rounded: "{rounded.pill}"
    padding: 2px 8px
  decision-chip:
    backgroundColor: "{colors.data-deny-dim}"
    textColor: "{colors.data-deny}"
    typography: "{typography.caption}"
    rounded: "{rounded.sm}"
    padding: 2px 7px
  code-block:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.ink-muted}"
    typography: "{typography.mono}"
    rounded: "{rounded.md}"
    padding: 16px
  sidebar-item:
    backgroundColor: transparent
    textColor: "{colors.ink-subtle}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.sm}"
    padding: 6px 10px
  sidebar-item-active:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.ink}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.sm}"
    padding: 6px 10px
---

# Zolts Design System

## The one rule that generates the rest

**The accent is violet, and it never enters a data region.**

Zolts ships a brand accent — `{colors.accent}` `#7d4bf5` — on the mark, the primary action, the focus ring, active navigation and link emphasis. Alongside it, five colours carry measurement semantics and nothing else:

| Token | Means | Never used for |
|---|---|---|
| `{colors.data-lift}` | Verified incremental lift against a holdout | Generic success, "on" states, brand |
| `{colors.data-control}` | The experimental control group | Disabled states, secondary text |
| `{colors.data-deny}` | A policy engine denial | Errors in general, destructive buttons |
| `{colors.data-review}` | Held for human review | Warnings in general |
| `{colors.data-live}` | Live signal latency, under SLA | Links, info, decoration |

**Why violet specifically.** Green, red, amber, cyan and neutral grey are spoken for by the five semantics. An accent in any of those hues would collide with evidence on a dense screen — a violet accent cannot be mistaken for a lift figure, a denial or a control row, because nothing in a chart legend is violet unless someone chose it. The hue is not a preference; it is the only band left once measurement has taken its five.

**Where the accent is forbidden.** Inside any data region: metric values, deltas, chart marks, decision chips, table cells, sparklines. The accent lives in chrome. This boundary is the discipline that keeps the screen scannable — without it, the decision to ship an accent degrades into colour everywhere, which is the failure mode it exists to avoid.

This is also positioning. The category's most visible product renders warm cream, claymation illustration and five saturated card colours across every surface. A near-black ground with one violet and five reserved data hues reads as *instrument* against that, and communicates audited before a word is read.

## Foundations

### Surface ladder

Depth comes from a four-step surface ladder plus hairlines. **No drop shadows on dark, no atmospheric gradients, no spotlight cards.**

| Token | Value | Use |
|---|---|---|
| `{colors.canvas}` | `#08090a` | App background, code blocks (recessed) |
| `{colors.surface-1}` | `#0e1011` | Panels, cards, tiles |
| `{colors.surface-2}` | `#141618` | Hover, active nav, secondary buttons |
| `{colors.surface-3}` | `#1a1d1f` | Status pills, nested elements |
| `{colors.surface-raised}` | `#1f2325` | Popovers, command palette, drawers |

Never skip a level. A popover over a panel goes `surface-1 → surface-raised`, not `surface-1 → surface-3`.

One exception to the no-shadow rule: elements that genuinely float above the plane — command palette, popover, drawer — carry `0 16px 40px -12px rgba(0,0,0,0.7)` plus a 1px `{colors.hairline-strong}` border and a `inset 0 1px 0 rgba(255,255,255,0.05)` top edge highlight. That inset hairline is what makes a dark panel read as lifted rather than as a hole.

### Type

Two families plus one mono. Display and Text are the same superfamily at different optical sizes; Mono is where the product's credibility lives.

**Every numeral in the product is mono and tabular.** `font-variant-numeric: tabular-nums`. A pipeline figure that shifts horizontally as it updates is a figure the CFO stops trusting, and this product is sold to that CFO.

Display weights sit at 550–560, never 700. Negative tracking is applied aggressively above 24px and drops to zero at body size.

### Density

This is a professional tool used for hours. Rows are 32–36px, not 52px. Rail items are 26px. The default table is comfortable at 13px, not 16px.

Density is not the absence of space — it is space spent on separation between *groups* rather than padding inside every element.

## Layout: the list is the page

A product surface is scanned and operated, not read top to bottom, and the fastest way to make one read as a generic template is to compose it from widgets. Four rules keep it from happening.

**No metric-tile row.** A row of large numbers across the top is the single most recognisable signature of a generated dashboard, and it puts the least actionable information in the most valuable position. Totals belong in a status strip at the foot of the list, where they summarise what is above them.

**Not everything is a card.** Content sits directly on `{colors.canvas}` separated by hairlines. Border, fill, radius and shadow each say "separate object" and are spent by role: the command palette floats, so it is lifted; a table is not an object on the page, it *is* the page.

**No section heading with a subtitle.** `<h2>Title</h2><p>Explanatory sentence</p>` on every block is documentation furniture. Product surfaces use an uppercase label at `{typography.eyebrow}` and nothing else; the context comes from the bar above.

**Three panes: rail, list, detail.** Navigation on the left, the working set in the middle at full bleed, properties on the right as a label/value list. The detail pane is a property list, never a stack of small cards.

## Keyboard

A tool people live in is driven from the keyboard, and the affordances have to be visible or they do not exist.

- `⌘K` opens the command palette: jump to any object, run any command. It is the only element permitted a drop shadow.
- `J` / `K` move the selection; arrow keys do the same for anyone who does not know the convention.
- `Enter` opens, `Esc` closes.
- Shortcut hints sit permanently at the foot of the rail, set in `kbd` at 10px.
- Selection is a 2px `{colors.accent}` left rail plus a surface lift — never a filled row, which would compete with the data.

## Motion

Motion exists to explain a state change. If a viewer cannot say what a transition told them, it should not ship.

### The first question is whether to animate at all

Frequency decides, not taste. An animation the operator sees a hundred times a day is a hundred small delays.

| How often it is seen | Decision |
|---|---|
| 100+ times a day — the command palette, selection movement, keyboard shortcuts | **No animation. Ever.** |
| Tens of times a day — hover, filter toggles | Reduce to 90ms, colour only |
| Occasional — a drawer, a confirmation, a published version | Standard animation |
| Rare — onboarding, a first successful program | Delight is affordable |

**Keyboard-initiated actions are never animated.** `⌘K` opens the palette instantly, with no fade and no scale. This is the single rule most often broken by interfaces that feel slow despite being fast: the animation is charged to the user on every repetition, and the palette is the most repeated action in the product.

### Durations and curves

| Situation | Duration | Curve |
|---|---|---|
| Hover, focus, colour shift | 90ms | `{motion.ease-out}` |
| Press feedback | 160ms | `{motion.ease-out}` |
| Popover, chip appear | 150ms | `{motion.ease-out}` |
| Panel, drawer, tab content | 220ms | `{motion.ease-out}` |
| A value moving to a new position | 380ms | `{motion.ease-out}` |
| Something moving across the screen | 300ms | `{motion.ease-in-out}` |

Custom curves only. `cubic-bezier(.23, 1, .32, 1)` for ease-out and `cubic-bezier(.77, 0, .175, 1)` for ease-in-out; the built-in CSS keywords are too weak to read as intentional.

**Never `ease-in` on UI.** It delays the first movement — the exact moment the user is watching hardest — so a 300ms `ease-in` dropdown *feels* slower than a 300ms `ease-out` one.

### Rules

- **Enter with movement, exit without.** Entering elements translate 4–8px and fade; exiting elements fade only. The user has already decided, so an animated exit only costs them time.
- **Only `transform` and `opacity`.** Both skip layout and paint and run on the GPU. A progress bar animates `transform: scaleX()` with `transform-origin: left`, never `width`.
- **Every pressable element scales to `0.97` on `:active`.** Without it the surface never confirms it heard the click.
- **Never animate from `scale(0)`.** Nothing in the real world appears from nothing; start at `0.95` with opacity.
- **Transitions, not keyframes, for anything triggered rapidly.** Transitions retarget from their current position; keyframes restart from zero.
- **Gate every hover behind `@media (hover: hover) and (pointer: fine)`.** Touch devices fire hover on tap and the state sticks after the finger lifts.
- **`prefers-reduced-motion` means fewer and gentler, not none.** Colour and opacity transitions aid comprehension and stay. Movement is what causes sickness, so only movement is removed.
- The only continuous animation permitted is the live-latency pulse, and it is a 2px dot at `{colors.data-live}`.

## Components

**`button-primary`** — Violet accent. There is exactly one on any screen.
- `{colors.accent}` background, white text, `{rounded.md}`, padding 7px 13px. Hover shifts to `{colors.accent-hover}`, pressed to `{colors.accent-pressed}`; active scales to `0.985`.
- One primary per view still holds. An accent does not license three of them.

**`button-secondary`** — `{colors.surface-2}` with a 1px `{colors.hairline}` border. Everything that is not the single primary action.

**`button-ghost`** — Transparent, `{colors.ink-subtle}` text, background lifts to `{colors.surface-2}` on hover. Toolbar and row-level actions.

**`panel`** — `{colors.surface-1}`, 1px `{colors.hairline}`, `{rounded.lg}`, padding 20px. The universal container.

**`metric-tile`** — A panel whose value is `{typography.metric}` mono-tabular, with an `{typography.eyebrow}` label above and a delta below. The delta is the only element permitted `{colors.data-lift}`, and only when measured against a control.

**`decision-chip`** — Renders a policy decision. `allow` uses `{colors.ink-subtle}` on `{colors.surface-3}`; `deny` uses `{colors.data-deny}` on `{colors.data-deny-dim}`; `review` uses `{colors.data-review}` on `{colors.data-review-dim}`. **Always adjacent to its rule key in mono** — a denial the operator cannot trace is a denial they will work around.

**`code-block`** — Recessed to `{colors.canvas}`, `{typography.mono}`. Used for program YAML and execution traces. Recessed, not raised: code is the substrate, not an object on top of the page.

**`data-row`** — 34px, transparent, hairline bottom rule. Hover lifts to `{colors.surface-2}` at 90ms. Selection is a 2px `{colors.ink}` left border, never a fill.

## Interaction contracts

Visual language is the easy half. The half that decides whether a keyboard or screen-reader user can operate the product at all is a set of contracts, and every one of them is invisible when correct. These follow the Radix Primitives model; the vanilla implementation in `design/console.html` is the reference.

### Lists

A selectable list is a `listbox` whose **options are not interactive widgets**. `role="option"` on a `<button>` is invalid — an option cannot itself be a control, and making every row focusable produces one tab stop per row, which turns a 200-row list into a keyboard trap.

The correct shape is **active descendant**: the container is the single tab stop, holds `role="listbox"` and `tabindex="0"`, and names the current option through `aria-activedescendant`. Rows are plain elements carrying `role="option"`, a stable `id` and `aria-selected`. Arrow keys and `J`/`K` move the pointer; focus never leaves the container.

### The command palette

It is a `dialog` containing a `combobox`, and both halves have obligations.

| Contract | Why it is not optional |
|---|---|
| `aria-labelledby` and `aria-describedby` on the dialog | A dialog with no accessible name is announced as "dialog", which tells the user nothing. Both targets are visually hidden. |
| Input carries `role="combobox"`, `aria-expanded`, `aria-controls`, `aria-autocomplete="list"` | Without them the results are an unannounced div; the user types into a box and hears nothing change. |
| Input's `aria-activedescendant` points at the highlighted result | This is what makes arrow keys legible to a screen reader while focus stays in the input. |
| Focus trapped in the input, `Tab` intercepted | Focus escaping to the page behind an open modal is the most common overlay bug there is. |
| Focus returned to the trigger on close | Losing focus to `<body>` strands a keyboard user at the top of the document. |
| `inert` and `aria-hidden` on the background | Otherwise the content behind the modal stays reachable and readable. |
| Scroll lock on `<body>` | The page scrolling behind an open dialog breaks the sense that it is modal. |
| `data-state="open" \| "closed"` on the dialog | State belongs in an attribute, not an ad-hoc class, so styling and testing read the same source. |

None of these change a single pixel. That is the point: they are the difference between a surface that looks operable and one that is.

## Accessibility

- Body text holds ≥ 7:1 against its surface; `{colors.ink-subtle}` holds ≥ 4.5:1 and is never used below 12px.
- **No status is conveyed by colour alone.** Every `decision-chip` carries a text label; every lift figure carries a sign; every control row carries the word "control".
- Focus is a 2px `{colors.accent-ring}` ring at 2px offset, on every interactive element, never removed on mouse input. The accent earns its keep here: a focus ring must be unmistakable, and violet against near-black is the most legible ring in this palette.
- Touch targets ≥ 40px on coarse pointers even though density targets 34px on fine pointers.

## Do's and Don'ts

### Do

- Reserve colour for the five measurement semantics. Everything else is the ink and surface ladder.
- Set every numeral in mono with tabular figures.
- Put the rule key beside every policy decision.
- Show the control group next to the treatment, always, even when the lift is negative.
- Use the surface ladder for hierarchy before reaching for a border.
- Let the primary button be the only accent-filled element in a view.
- Use `{colors.accent-dim}` for the accent's own tinted surfaces (active nav, selected row), never a semantic dim.

### Don't

- Don't put the accent inside a data region: no violet metrics, deltas, chart marks or decision chips.
- Don't introduce a second accent hue. One violet, five semantics, and nothing else.
- Don't ship a light-mode product surface. Marketing pages may differ; the product does not.
- Don't use `{colors.data-lift}` for a successful save, a healthy status or an enabled toggle.
- Don't add gradients, glows or spotlight cards.
- Don't pill-round buttons. Pills are for status only.
- Don't animate a number's replacement with a crossfade.
- Don't render a metric without its control comparison or its confidence interval.
- Don't use pure `#000000`.

## Responsive Behaviour

| Name | Width | Key changes |
|---|---|---|
| Desktop-XL | 1440px | Sidebar 224px, three-column detail |
| Desktop | 1280px | Detail panel collapses to a drawer |
| Tablet | 1024px | Sidebar collapses to icons; metric grid 4-up → 2-up |
| Mobile | 640px | Single column; tables become stacked cards, never horizontal scroll for critical data |

The P&L table is the exception: below 1024px it keeps its own `overflow-x: auto` container rather than restructuring, because column alignment is what makes it readable.

## Iteration Guide

1. Name the component by its `components:` token before changing it.
2. Decide the surface level first, then the border, then never the shadow.
3. If a new colour is proposed, identify which of the five measurement semantics it serves, or show that it is the existing accent. If neither, it is not approved.
4. Default body to `{typography.body-sm}` 13px in product surfaces, `{typography.body}` 14px in marketing.
5. Every new metric ships with its control comparison in the same commit.

## Known Gaps

- The display, text and mono families are specified as a superfamily. The shipped substitute is **IBM Plex Sans** (display and text, weights 450-600) with **IBM Plex Mono** (all numerals, tabular). Plex was drawn as an engineering typeface rather than a product-marketing one, which is the register this system wants; it is also not one of the two faces every AI-generated interface currently reaches for.
- Light mode is undefined and intentionally so.
- Data-visualisation palettes beyond the five semantics (multi-series charts) are unspecified and should extend the ladder in luminance, not hue — and must avoid the accent's violet band so a series is never mistaken for chrome.
- This system originally shipped with no brand accent at all, reserving colour entirely for measurement. That was overridden by decision O1 in the decision register: an accent buys the thirty-second first impression in a side-by-side comparison, at the cost of the argument that colour in this product always means something measured. The forbidden-in-data-regions rule is what preserves as much of that argument as an accent allows.
