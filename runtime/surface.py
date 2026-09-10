"""Assembling the console document and deriving its content security policy.

Shared by the static build and by the API, which serves the same surface with
live data. Two copies of this would drift, and the copy that drifts is the one
that ships a policy the page violates — the failure this file already had once,
when a hash-locked `style-src` refused every runtime style and the evidence
bars rendered at zero width.

Pure string functions. The callers own the file system.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from typing import Any

PLACEHOLDER = '/*__FIXTURE__*/ {"programs":[],"decisions":{},"jurisdictions":[],"plannedPrograms":[]}'

TITLE = "Zolts — Program Console"
DESCRIPTION = (
    "The Zolts operator surface: a dense program list with evidence, per-play "
    "P&L and policy trace, where a lift that cannot be resolved is not reported."
)

FAVICON = (
    "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 18 18'%3E"
    "%3Crect width='18' height='18' rx='4' fill='%237d4bf5'/%3E"
    "%3Cpath d='M4 4h10L4 14h10' stroke='white' stroke-width='2' "
    "stroke-linecap='square' fill='none'/%3E%3C/svg%3E"
)

DOCUMENT = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="description" content="{description}">
<meta name="color-scheme" content="{scheme}">
<meta name="theme-color" content="{canvas}">
<meta name="zolts-build" content="{build}">
<meta property="og:type" content="website">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<link rel="icon" href="data:image/svg+xml,{favicon}">
{head}
</head>
<body>
{body}
</body>
</html>
"""

_HEAD_PARTS = re.compile(r"<title>.*?</title>|<link\b[^>]*>|<style>.*?</style>", re.S)
_CANVAS = re.compile(r"--canvas:\s*(#[0-9a-fA-F]{3,8})")
_INLINE = re.compile(r"<style>(.*?)</style>|<script>(.*?)</script>", re.S)


def sha256_csp(payload: str) -> str:
    digest = hashlib.sha256(payload.encode("utf-8")).digest()
    return "'sha256-" + base64.b64encode(digest).decode("ascii") + "'"


def inject(source: str, data: dict[str, Any]) -> str:
    """Replace the surface's data placeholder.

    The surface ships with its data inlined rather than fetched: it keeps
    `connect-src` at 'none', avoids a round trip, and makes the page a single
    self-contained file. Injecting before hashing means the policy covers the
    data as well as the code.
    """
    if PLACEHOLDER not in source:
        raise ValueError("design/console.html no longer carries the fixture placeholder")
    return source.replace(PLACEHOLDER, json.dumps(data, separators=(",", ":"), default=str), 1)


def build_id(template: str) -> str:
    """Which build of the surface this is, from the surface itself.

    A digest of the *template*, taken before any tenant's data is injected, so
    the static build and the API serving live figures stamp the same twelve
    characters for the same code. It is what `scripts/deploy_drift.py` compares
    a deployed page against: production served a build from weeks earlier
    behind thirty-nine green runs, because nothing anywhere compared the two
    (D-100).
    """
    return hashlib.sha256(template.encode("utf-8")).hexdigest()[:12]


def canvas(source: str) -> str:
    """The background the stylesheet actually paints."""
    found = _CANVAS.search(source)
    if not found:
        raise ValueError(
            "the surface declares no --canvas token, so the document cannot "
            "say which colour scheme it is; a wrong answer here paints the "
            "browser's own canvas against the page")
    return found.group(1)


def scheme(source: str) -> str:
    """`light` or `dark`, read off the surface rather than remembered.

    The document used to declare `dark` as a constant. The brand work inverted
    the palette to a light one and the constant stayed, so the browser was told
    the opposite of what the stylesheet paints: form controls render in the
    wrong scheme, and the canvas behind the page and the mobile chrome around
    it are painted near-black around a light surface (D-99).

    Derived, so it cannot disagree again. Relative luminance, sRGB weights.
    """
    hex_value = canvas(source).lstrip("#")
    if len(hex_value) == 3:
        hex_value = "".join(c * 2 for c in hex_value)
    red, green, blue = (int(hex_value[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return "light" if 0.2126 * red + 0.7152 * green + 0.0722 * blue > 0.5 else "dark"


def document(source: str, *, title: str = TITLE, description: str = DESCRIPTION,
             build: str = "") -> str:
    """Wrap the surface in a real document.

    `design/console.html` is authored for a runtime that supplies the shell. It
    puts `<title>`, `<link>` and `<style>` ahead of its markup; those belong in
    the head and everything after belongs in the body.
    """
    head_parts = _HEAD_PARTS.findall(source)
    body = source
    for part in head_parts:
        body = body.replace(part, "", 1)
    head = "\n".join(p for p in head_parts if not p.startswith("<title>"))
    return DOCUMENT.format(title=title, description=description, favicon=FAVICON,
                           scheme=scheme(source), canvas=canvas(source), build=build,
                           head=(f"<title>{title}</title>\n" + head).strip(),
                           body=body.strip())


def content_security_policy(rendered: str, *, connect_src: str = "'none'") -> str:
    """Derive the policy from the bytes being served.

    A hash covers a `<style>` element but never a `style=""` attribute, and the
    surface sets transforms from data at runtime. Rather than weaken the whole
    `style-src`, it is split: stylesheet elements stay hash-locked and only
    inline attributes are allowed. `script-src` stays hash-locked either way,
    which is where injection actually matters.

    `connect_src` is 'none' for the static build, which fetches nothing, and
    'self' when the API serves the surface and the page may talk back to it.
    """
    hashes = " ".join(sorted({sha256_csp(style or script)
                              for style, script in _INLINE.findall(rendered)}))
    return (
        "default-src 'self'; "
        f"style-src 'self' 'unsafe-inline' {hashes} https://fonts.googleapis.com; "
        f"style-src-elem 'self' {hashes} https://fonts.googleapis.com; "
        "style-src-attr 'unsafe-inline'; "
        "font-src https://fonts.gstatic.com; "
        f"script-src 'self' {hashes}; "
        "img-src 'self' data:; "
        f"connect-src {connect_src}; "
        "object-src 'none'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
    )
