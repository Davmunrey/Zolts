# 27 — Brand

**Settles: steel on paper, one accent, five data semantics, and the rule that keeps them apart.**

The runtime is the brand. The identity has one job: make an operator and a CFO
believe the number on screen before they read a word of it. So the surface is a
technical drawing on paper, not a dark dashboard — hairline frames, wide
engineering type, one steel accent, and one solid extruded object, the mark.

| | Was | Is |
|---|---|---|
| Ground | Violet on near-black | Steel on paper |
| Reads as | Every AI GTM tool shipped since 2023 | Instrumentation: a plan you could hand to an auditor |

Every figure below is measured from `design/console.html` by
`tests/test_brand.py`, not quoted from memory. The console is the single source
both `scripts/build_site.py` and `runtime/surface.py` render from, so what the
test measures is what ships.

## 27.1 Chrome — one accent

| Role | Value | On paper |
|---|---|---|
| Paper (ground) | `#F2F2F3` | — |
| Ink (body) | `#1D1F20` | 14.79:1 |
| Steel (the accent, step 500) | `#5980A6` | 3.71:1 |
| Steel reading step (700) | `#2F5171` | 7.40:1 |

**Paragraph-size type in the accent uses 700 or darker; 500 is for chrome, icons
and headline scale.** That is not a preference. Steel 500 measures 3.71:1 on
paper and does not clear AA; 700 measures 7.40:1 and does. The rule is a
consequence of the ground the brand chose, and `test_the_accent_is_chrome_only`
fails if the ramp moves out from under it.

The ramp is generated in OKLCH on one lightness scale with **500 pinned to the
declared steel**. This matters: the design-system canvas the specification was
drawn on generated its own accent ramp whose 500 step is `#749dc4`, and which
does not contain `#5980A6` at any step. The prose is the brand; the generated
ramp was an approximation of it.

## 27.2 Data — five semantics

Inside a chart, table or figure, colour means one thing only. **The accent is
barred from these regions.**

| Semantic | Value | On paper |
|---|---|---|
| Verified lift | `#2F6B4F` | 5.63:1 |
| Holdout / control arm | `#5D5D60` | 5.87:1 |
| Policy denial | `#8A3A34` | 6.86:1 |
| Held for review | `#7A5A1E` | 5.67:1 |
| Live latency | `#2C455D` | 8.87:1 |

Five deep steps, desaturated enough that five on one dense screen read as one
family. Lifecycle — a programme being live, paused or draft — is **not** a
measurement and is drawn in ink. A colour here is a claim about what was
measured, and nothing else is entitled to make one.

### Why the rule is structural, not stylistic

Steel 700 and the live-latency semantic are **4.5 ΔE apart**. Two colours that
close cannot be told apart when they share a region — so they never do. The
separation is enforced by *where* a colour is allowed, not by how far apart the
hues are, and that is the only thing that scales as the palette grows.

### The pair the palette cannot separate on its own

Verified lift and the control arm are **8.1 ΔE apart in normal vision and 2.7
under deuteranopia** — the same colour, for roughly one man in twelve. They are
also the pair a reader most needs to tell apart: they are the two arms of the
experiment the whole product exists to run.

The family resemblance that makes the palette calm is what makes this pair
unreadable. The brand's own grammar answers it: *one solid object per view;
everything else is a line drawing.* **The treatment arm is a fill and the
control arm is hatched**, on the 45° axis the mark is extruded along. Hue still
carries meaning; it is no longer the only thing that does — and the hatch says
"withheld" before the label is read.

`test_the_two_arms_are_not_separated_by_hue_alone` asserts both halves: that the
hazard is real, by simulating deuteranopia and measuring it, and that the
surface answers it with texture rather than a second hue.

## 27.3 Type

| Use | Face |
|---|---|
| Display, section, body | Archivo — 400 body, 600 section, 700 display |
| Identifiers, figures, labels, code | JetBrains Mono |

Numbers are monospaced and tabular, **with the unit and the interval**. A figure
without its confidence interval is a claim, not a measurement — which is the
same rule `zolts/report.py` enforces on the report itself.

Voice: claim, then the condition that makes it true. *"Incremental pipeline is
reported only when both arms carry five observed conversions."* Never
"AI-powered", "autonomous", "10x your pipeline", "magic" — the category's
vocabulary is the thing being positioned against.

## 27.4 The mark

A Z cut as a solid block and extruded on a 45° axis: two rails, one diagonal,
one uniform depth. Built on a 100-unit grid, every vertex on it — the face
occupies 0–89 and the 11-unit extrusion lands exactly on 100.

| Rule | Value |
|---|---|
| Clear space | The extrusion depth, 11% of the mark box, on all four sides |
| Minimum size | 18 px; the extrusion depth never changes with size |
| Tracking on ZOLTS | Fixed at 0.08 em — never tighter, never lowercase |
| Tones | Face 500, extrusion 800, edge 900 |
| Reversed (avatar) | Paper face over a 600 extrusion, no edge line |

Below 28 px on a steel field the extrusion goes flat and the mark ships as a
single paper silhouette. The console uses both variants: the 20 px lockup in the
rail is extruded on paper; the 18 px workspace chip is the flat silhouette on a
steel field.

**Never**: change the extrusion axis, add a gradient, bevel or reflection to the
face, round the corners, or place the wordmark over a photograph.

## 27.5 What this document does not cover

The registration marks and blueprint frames of the specification's document and
marketing surfaces are **not** applied to the console. An operator surface is
read for eight hours a day and corner marks on every panel are ornament at that
duration. Whether the public site adopts them is decision 42; the console's
answer is settled here and is no.
