"""The brand, as a measurement rather than a memory.

`docs/27` states the identity in numbers — one steel accent on paper, five
data semantics, a rule that keeps them apart — and every number it states is
checked here against `design/console.html`, which is the single source both
`scripts/build_site.py` and `runtime/surface.py` render from.

The rule worth testing is not "the colours are the right colours". It is the
one the specification puts first and the one a dense screen breaks silently:
**inside a chart, table or figure, colour means one thing only, and the accent
is barred from those regions.** A surface that breaks it still renders, still
passes every layout check, and quietly tells a CFO that a latency figure is a
verified lift.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CONSOLE = ROOT / "design" / "console.html"
SOURCE = CONSOLE.read_text()

# The specification's own declared values. These are the brand; if one of them
# changes, it changes here and in `docs/27` together or the suite fails.
PAPER = "#f2f2f3"
INK = "#1d1f20"
STEEL = "#5980a6"
SEMANTICS = {
    "lift": "#2f6b4f",      # verified lift
    "control": "#5d5d60",   # holdout / control arm
    "deny": "#8a3a34",      # policy denial
    "review": "#7a5a1e",    # held for review
    "live": "#2c455d",      # live latency
}


def _declared() -> dict[str, str]:
    """Every custom property declared in `:root`, with `var()` chains resolved."""
    block = re.search(r":root\{(.*?)\n\}", SOURCE, re.S)
    assert block, "the console no longer declares a :root token block"
    raw = dict(re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", block.group(1)))
    resolved = {k: v.strip() for k, v in raw.items()}
    for _ in range(4):                       # chains are shallow; this bounds them
        for key, value in list(resolved.items()):
            hit = re.fullmatch(r"var\((--[a-z0-9-]+)\)", value)
            if hit and hit.group(1) in resolved:
                resolved[key] = resolved[hit.group(1)]
    return resolved


TOKENS = _declared()


# ── colour arithmetic, so a contrast claim is computed and not remembered ──

def _channels(colour: str) -> tuple[float, float, float]:
    text = colour.lstrip("#")
    return tuple(int(text[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _luminance(colour: str) -> float:
    r, g, b = (_linear(c) for c in _channels(colour))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def _oklab(colour: str) -> tuple[float, float, float]:
    r, g, b = (_linear(c) for c in _channels(colour))
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l, m, s = l ** (1 / 3), m ** (1 / 3), s ** (1 / 3)
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


def delta_e(a: str, b: str) -> float:
    """OKLab distance, ×100 — the scale the palette checks are stated on."""
    return math.dist([v * 100 for v in _oklab(a)], [v * 100 for v in _oklab(b)])


def _deuteranope(colour: str) -> str:
    """Viénot's dichromat simulation, so the CVD claim is computed too."""
    r, g, b = (_linear(c) for c in _channels(colour))
    L = 17.8824 * r + 43.5161 * g + 4.11935 * b
    M = 3.45565 * r + 27.1554 * g + 3.86714 * b
    S = 0.0299566 * r + 0.184309 * g + 1.46709 * b
    M = 0.494207 * L + 1.24827 * S            # the missing cone, reconstructed
    r2 = 0.080944 * L - 0.130504 * M + 0.116721 * S
    g2 = -0.0102485 * L + 0.0540194 * M - 0.113615 * S
    b2 = -0.000365294 * L - 0.00412163 * M + 0.693513 * S
    def back(v: float) -> int:
        v = max(0.0, min(1.0, v))
        v = 12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055
        return max(0, min(255, round(v * 255)))
    return "#%02x%02x%02x" % (back(r2), back(g2), back(b2))


# ── the declared identity ────────────────────────────────────────────────

def test_the_ground_is_paper_and_the_accent_is_the_declared_steel():
    """`docs/27`: steel #5980A6 on paper #F2F2F3, ink #1D1F20. The canvas's own
    generated ramp put its 500 step at #749dc4 and never contained the declared
    value at all; the ramp here is anchored on it instead."""
    assert TOKENS["--canvas"] == PAPER
    assert TOKENS["--ink"] == INK
    assert TOKENS["--steel-500"] == STEEL
    assert TOKENS["--accent"] == STEEL, "the accent is the steel 500 step, not a near neighbour"


def test_the_five_data_semantics_are_the_declared_ones():
    for name, value in SEMANTICS.items():
        assert TOKENS[f"--{name}"] == value, f"--{name} drifted from the specification"


def test_the_typefaces_are_archivo_and_jetbrains_mono():
    assert TOKENS["--sans"].startswith('"Archivo"')
    assert TOKENS["--mono"].startswith('"JetBrains Mono"')
    assert "family=Archivo" in SOURCE and "family=JetBrains+Mono" in SOURCE, (
        "the faces are declared but never fetched")
    # A face that fails to load must fall back to something, not to nothing.
    for token in ("--sans", "--mono"):
        assert TOKENS[token].count(",") >= 2, f"{token} has no real fallback stack"


# ── the rule the specification puts first ────────────────────────────────

# Selectors that paint a measured value. The accent is barred from all of them.
DATA_SELECTORS = (".kpi .v", ".arm", ".lift", ".cell.good", ".cell.bad",
                  ".cell.warnt", ".meter", ".track i", ".st.warn", ".st.deny")


def test_the_accent_never_enters_a_data_region():
    """The one rule kept from the surface the brand replaces. A steel bar
    beside a green one reads as a third arm."""
    accent_tokens = {"--accent", "--accent-hover", "--accent-dim", "--accent-ring",
                     "--accent-text"} | {f"--steel-{n}" for n in range(100, 1000, 100)}
    offenders = []
    for rule in re.findall(r"([^{}]+)\{([^{}]*)\}", SOURCE):
        selector, body = rule[0].strip(), rule[1]
        if not any(marker in selector for marker in DATA_SELECTORS):
            continue
        for used in re.findall(r"var\((--[a-z0-9-]+)", body):
            if used in accent_tokens:
                offenders.append((selector, used))
    assert not offenders, f"the accent is painting a data region: {offenders}"


def test_every_data_semantic_has_a_caller():
    """A declared colour nothing renders is this repository's signature defect.
    `--live` was declared for eleven months and used nowhere, while the
    runtime's own latency figure wore the verified-lift green. D-56."""
    for name in SEMANTICS:
        uses = SOURCE.count(f"var(--{name})")
        assert uses >= 1, (
            f"--{name} is declared and never rendered; a semantic with no caller "
            f"is a semantic the surface does not actually have")


def test_every_token_the_stylesheet_uses_is_declared():
    """`--ink-secondary` and `--line` were referenced by three rules and
    declared by none, so three declarations were dropped in silence: the event
    list lost its separators and two labels fell back to inherited ink. CSS
    fails quietly, which is why this is a test and not a review. D-56."""
    declared = set(TOKENS) | {"--cols"}      # --cols is set on the element by JS
    used = set(re.findall(r"var\((--[a-z0-9-]+)(?![a-z0-9-])\s*[,)]", SOURCE))
    missing = sorted(used - declared)
    assert not missing, f"used but never declared: {missing}"


# ── the hazard the rule exists to contain ────────────────────────────────

def test_the_two_arms_are_not_separated_by_hue_alone():
    """Verified lift and the control arm are the closest pair in the palette,
    and they are the pair a reader most needs to tell apart. Under deuteranopia
    they are a handful of ΔE apart — the same colour, for a great many readers.

    So the treatment arm is a fill and the control arm is hatched. This test
    asserts both halves: that the hazard is real, and that the surface answers
    it with texture rather than with a second hue.
    """
    normal = delta_e(SEMANTICS["lift"], SEMANTICS["control"])
    deutan = delta_e(_deuteranope(SEMANTICS["lift"]), _deuteranope(SEMANTICS["control"]))
    assert normal < 15, (
        f"lift and control are {normal:.1f} ΔE apart in normal vision; if the "
        f"palette has been re-stepped above 15 this guard can be relaxed")
    assert deutan < 8, f"expected a close pair under deuteranopia, measured {deutan:.1f} ΔE"

    control_track = re.search(r"\.arm\.c \.track i\{([^}]*)\}", SOURCE)
    assert control_track, "the control arm no longer has its own track rule"
    assert "repeating-linear-gradient" in control_track.group(1), (
        "the control arm is painted with a flat colour, so the two arms differ "
        "by hue alone — which is what the measurement above says is not enough")
    assert "45deg" in control_track.group(1), (
        "the hatch runs off the mark's own 45° extrusion axis")


# ── contrast, computed ───────────────────────────────────────────────────

def test_body_ink_and_every_data_semantic_are_legible_on_paper():
    assert contrast(INK, PAPER) >= 7, "body ink no longer clears AAA on paper"
    for name, value in SEMANTICS.items():
        ratio = contrast(value, PAPER)
        assert ratio >= 4.5, f"--{name} measures {ratio:.2f}:1 on paper, below AA"


def test_the_accent_is_chrome_only_and_the_reading_step_is_the_dark_one():
    """`docs/27`: paragraph-size type in the accent uses 700 or darker; 500 is
    for chrome, icons and headline scale. That is not a preference — 500 does
    not clear AA on paper and 700 does, and this is where that is established.
    """
    chrome = contrast(TOKENS["--accent"], PAPER)
    reading = contrast(TOKENS["--accent-text"], PAPER)
    assert chrome < 4.5, (
        f"steel 500 now measures {chrome:.2f}:1 on paper. If the ramp moved, the "
        f"rule in docs/27 that keeps it out of paragraph text needs rewriting")
    assert reading >= 4.5, (
        f"the accent's reading step measures {reading:.2f}:1, below AA on paper")


def test_no_accent_step_collides_with_a_data_semantic():
    """The accent's dark steps and the live-latency semantic are near
    neighbours — which is exactly why the separation is enforced by region
    rather than by hue. Sharing an actual value would make the rule
    unobservable."""
    steel = {k: v for k, v in TOKENS.items() if k.startswith("--steel-")}
    collisions = [(k, name) for k, v in steel.items()
                  for name, value in SEMANTICS.items() if v == value]
    assert not collisions, f"an accent step is a data colour: {collisions}"


# ── the mark ─────────────────────────────────────────────────────────────

def test_the_wordmark_is_uppercase_and_never_set_tighter_than_the_specification():
    rule = re.search(r"\.wordmark\{([^}]*)\}", SOURCE)
    assert rule, "the wordmark has no rule"
    tracking = re.search(r"letter-spacing:\s*(\d*\.?\d+)em", rule.group(1))
    assert tracking, "the wordmark declares no tracking"
    assert float(tracking.group(1)) == 0.08, (
        "tracking on ZOLTS is fixed at 0.08em; never tighter, never lowercase")
    assert ">ZOLTS<" in SOURCE, "the wordmark is not rendered in uppercase"


def test_the_mark_is_extruded_by_a_ninth_of_its_own_box():
    """The face occupies 0-89 of a 100-unit grid and the extrusion is 11 units,
    fixed at every size. Every vertex lands on the grid."""
    face = re.search(r'd="(M0 0H89[^"]*)"', SOURCE)
    assert face, "the mark's face path is gone"
    numbers = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", face.group(1))]
    assert max(numbers) == 89, "the face no longer leaves room for the extrusion"
    assert "var(--steel-500)" in SOURCE and "var(--steel-800)" in SOURCE, (
        "the mark's three tones are face 500, extrusion 800 and a 900 edge")


def test_the_marks_corners_are_never_rounded():
    """One of five things the specification says never to do to the mark."""
    glyph = re.search(r"\.ws \.glyph\{([^}]*)\}", SOURCE)
    assert glyph, "the workspace mark has no rule"
    assert "border-radius" not in glyph.group(1), (
        "the mark's corners are rounded; the specification forbids it")


@pytest.mark.parametrize("claim,expected", [
    ("#5980A6", "--steel-500"), ("#F2F2F3", "--canvas"), ("#1D1F20", "--ink"),
    ("#2F6B4F", "--lift"), ("#5D5D60", "--control"), ("#8A3A34", "--deny"),
    ("#7A5A1E", "--review"), ("#2C455D", "--live"),
])
def test_docs_27_quotes_the_value_the_console_ships(claim, expected):
    """The document is corrected, never the measurement."""
    document = (ROOT / "docs" / "27-brand.md").read_text()
    assert claim in document, f"docs/27 no longer names {claim}"
    assert TOKENS[expected].lower() == claim.lower()
