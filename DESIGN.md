---
version: alpha
name: Zolts-design-system
description: "An evidence-grade dark product surface for a GTM execution runtime. Anchors on a near-black canvas (#08090a) with a four-step surface ladder and hairline borders, in the tradition of dense technical software. Its defining rule is chromatic abstinence: Zolts ships NO brand accent colour. Buttons are inverse white-on-black, the wordmark is ink, focus rings are ink. Colour is reserved exclusively for measurement semantics — verified lift, experimental control, policy denial, review, and live latency — because in a product whose thesis is that measurement is the only truth, spending colour on decoration spends the one signal the interface has. Numerals are always tabular mono: a P&L that jitters between rows is a P&L nobody trusts."

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
  ink-inverse: "#08090a"
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
  ease-out: cubic-bezier(0.22, 1, 0.36, 1)
  ease-in-out: cubic-bezier(0.65, 0, 0.35, 1)
  spring-gentle: linear(0, 0.35, 0.79, 0.96, 1.02, 1.01, 1)
  stagger-row: 24ms

components:
  button-primary:
    backgroundColor: "{colors.inverse-canvas}"
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

**Zolts has no brand accent colour.** Not lavender, not cyan, not a gradient. The wordmark is ink, the primary button is inverse white, the focus ring is ink at 40% opacity.

Colour appears in exactly five roles, all of them semantic:

| Token | Means | Never used for |
|---|---|---|
| `{colors.data-lift}` | Verified incremental lift against a holdout | Generic success, "on" states, brand |
| `{colors.data-control}` | The experimental control group | Disabled states, secondary text |
| `{colors.data-deny}` | A policy engine denial | Errors in general, destructive buttons |
| `{colors.data-review}` | Held for human review | Warnings in general |
| `{colors.data-live}` | Live signal latency, under SLA | Links, info, decoration |

The reasoning is not aesthetic. The product's entire thesis is that measurement is the only truth in GTM. An interface that spends green on a "Save" button has spent the signal it needs when a number is genuinely, verifiably up. Scarcity is what makes `{colors.data-lift}` legible across a dense screen at a glance.

This is also positioning. The category's most visible product renders warm cream, claymation illustration and five saturated card colours. Reading as its opposite — near-black, hairline, tabular, unornamented — communicates *audited* before a word is read.

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

This is a professional tool used for hours. Rows are 32–36px, not 52px. Sidebar items are 28px. The default table is comfortable at 13px, not 16px.

Density is not the absence of space — it is space spent on separation between *groups* rather than padding inside every element.

## Motion

Motion exists to explain a state change. If a viewer cannot say what a transition told them, it should not ship.

| Situation | Duration | Easing |
|---|---|---|
| Hover, focus, colour shift | `{motion.duration-instant}` 90ms | `{motion.ease-out}` |
| Popover, tooltip, chip appear | `{motion.duration-fast}` 150ms | `{motion.ease-out}` |
| Panel, drawer, tab content | `{motion.duration-base}` 220ms | `{motion.ease-out}` |
| Number counting to a new value | `{motion.duration-slow}` 380ms | `{motion.ease-out}` |
| Drawer and sheet gestures | — | `{motion.spring-gentle}` |

Rules:

- **Enter with movement, exit without.** Entering elements translate 4–8px and fade in; exiting elements fade only. An exit that animates position makes the interface feel slow, because the user has already decided.
- **Stagger lists at `{motion.stagger-row}` 24ms**, capped at eight rows. Beyond eight it stops reading as choreography and starts reading as lag.
- **Never animate `width`, `height`, `top` or `left`.** `transform` and `opacity` only.
- **Numbers count up; they never crossfade.** A metric changing from 1.4× to 2.1× interpolates through the values, because the movement is the information.
- **Honour `prefers-reduced-motion`**: all durations collapse to 0ms and transforms are removed. Never merely shortened.
- The only continuous animation permitted is the live-latency pulse, and it is a 2px dot at `{colors.data-live}`, nothing more.

## Components

**`button-primary`** — Inverse white. There is exactly one on any screen.
- `{colors.inverse-canvas}` background, `{colors.ink-inverse}` text, `{rounded.md}`, padding 7px 13px. Hover shifts to `{colors.inverse-hover}`; active scales to `0.985`.

**`button-secondary`** — `{colors.surface-2}` with a 1px `{colors.hairline}` border. Everything that is not the single primary action.

**`button-ghost`** — Transparent, `{colors.ink-subtle}` text, background lifts to `{colors.surface-2}` on hover. Toolbar and row-level actions.

**`panel`** — `{colors.surface-1}`, 1px `{colors.hairline}`, `{rounded.lg}`, padding 20px. The universal container.

**`metric-tile`** — A panel whose value is `{typography.metric}` mono-tabular, with an `{typography.eyebrow}` label above and a delta below. The delta is the only element permitted `{colors.data-lift}`, and only when measured against a control.

**`decision-chip`** — Renders a policy decision. `allow` uses `{colors.ink-subtle}` on `{colors.surface-3}`; `deny` uses `{colors.data-deny}` on `{colors.data-deny-dim}`; `review` uses `{colors.data-review}` on `{colors.data-review-dim}`. **Always adjacent to its rule key in mono** — a denial the operator cannot trace is a denial they will work around.

**`code-block`** — Recessed to `{colors.canvas}`, `{typography.mono}`. Used for program YAML and execution traces. Recessed, not raised: code is the substrate, not an object on top of the page.

**`data-row`** — 34px, transparent, hairline bottom rule. Hover lifts to `{colors.surface-2}` at 90ms. Selection is a 2px `{colors.ink}` left border, never a fill.

## Accessibility

- Body text holds ≥ 7:1 against its surface; `{colors.ink-subtle}` holds ≥ 4.5:1 and is never used below 12px.
- **No status is conveyed by colour alone.** Every `decision-chip` carries a text label; every lift figure carries a sign; every control row carries the word "control".
- Focus is a 2px `{colors.hairline-focus}` ring at 2px offset, on every interactive element, never removed on mouse input.
- Touch targets ≥ 40px on coarse pointers even though density targets 34px on fine pointers.

## Do's and Don'ts

### Do

- Reserve colour for the five measurement semantics. Everything else is the ink and surface ladder.
- Set every numeral in mono with tabular figures.
- Put the rule key beside every policy decision.
- Show the control group next to the treatment, always, even when the lift is negative.
- Use the surface ladder for hierarchy before reaching for a border.
- Let the primary button be the only white element in a view.

### Don't

- Don't introduce a brand accent. The absence is the brand.
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
3. If a new colour is proposed, identify which of the five measurement semantics it serves. If none, it is not approved.
4. Default body to `{typography.body-sm}` 13px in product surfaces, `{typography.body}` 14px in marketing.
5. Every new metric ships with its control comparison in the same commit.

## Known Gaps

- The display, text and mono families are specified as a superfamily. The shipped substitute is **IBM Plex Sans** (display and text, weights 450-600) with **IBM Plex Mono** (all numerals, tabular). Plex was drawn as an engineering typeface rather than a product-marketing one, which is the register this system wants; it is also not one of the two faces every AI-generated interface currently reaches for.
- Light mode is undefined and intentionally so.
- Data-visualisation palettes beyond the five semantics (multi-series charts) are unspecified and should extend the ladder in luminance, not hue.
