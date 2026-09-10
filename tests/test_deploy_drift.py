"""The document says what it paints, and something notices when production does not.

Two defects, one cause: nobody was comparing.

`runtime.surface.DOCUMENT` declared `color-scheme: dark` as a constant. The
brand work inverted the palette to a light one and the constant stayed, so
every page told the browser the opposite of what its own stylesheet paints
(D-99). Nothing compared the two because the answer was written down rather
than derived.

And production served a build from weeks earlier behind thirty-nine green runs
of `deploy-vercel`, because the release job skips without its secrets and no
check anywhere compared the deployed surface with the one on `main` (D-100).
The founder found it by opening the page and recognising an old screen.

Both are fixed the same way — derive rather than remember — so both are tested
the same way: by flipping the thing being derived from and requiring the answer
to follow.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from runtime import surface

ROOT = Path(__file__).resolve().parent.parent
SURFACE = ROOT / "design" / "console.html"
SCRIPT = ROOT / "scripts" / "deploy_drift.py"


def _template() -> str:
    return SURFACE.read_text(encoding="utf-8")


# -- the document follows the palette ------------------------------------


def test_the_shipped_surface_is_light_and_the_document_says_so():
    """The premise first: this palette really is a light one.

    Asserted rather than assumed, because the whole point is that the document
    used to disagree with it. A test that only read the document would pass on
    a document that lies.
    """
    template = _template()
    assert surface.canvas(template) == "#f2f2f3", (
        "the surface's canvas moved; this test's premise needs rereading")
    assert surface.scheme(template) == "light"

    rendered = surface.document(surface.inject(template, {"programs": []}))
    assert '<meta name="color-scheme" content="light">' in rendered
    assert '<meta name="theme-color" content="#f2f2f3">' in rendered
    assert 'content="dark"' not in rendered


@pytest.mark.parametrize("canvas,expected", [
    ("#f2f2f3", "light"), ("#ffffff", "light"), ("#fff", "light"),
    ("#08090a", "dark"), ("#000000", "dark"), ("#000", "dark"),
    # Mid greys, either side of the luminance bound. A surface that lands here
    # is a design problem rather than a scheme problem, and the answer still
    # has to be one of the two.
    ("#8a8a8a", "light"), ("#6f6f6f", "dark"),
])
def test_the_scheme_is_read_off_the_canvas(canvas, expected):
    assert surface.scheme(f"--canvas:{canvas};") == expected


def test_inverting_the_palette_inverts_the_document():
    """The both-directions guard.

    Painting the surface dark again must make the document say dark. The
    constant this replaced could not do that, which is how it came to disagree
    with the palette for weeks.
    """
    dark = _template().replace("--canvas:#f2f2f3", "--canvas:#08090a")
    rendered = surface.document(surface.inject(dark, {"programs": []}))
    assert '<meta name="color-scheme" content="dark">' in rendered
    assert '<meta name="theme-color" content="#08090a">' in rendered


def test_a_surface_with_no_canvas_is_refused_rather_than_guessed():
    """Defaulting here would put the wrong answer back, silently."""
    with pytest.raises(ValueError, match="no --canvas token"):
        surface.scheme("<style>body{}</style>")


# -- the build stamp -----------------------------------------------------


def test_the_stamp_is_the_template_and_not_the_render():
    """The static build and the API must stamp the same code identically.

    The digest is taken before injection. Taken after, a tenant with different
    figures would stamp a different build, and the drift check would report
    drift on every page load.
    """
    template = _template()
    one = surface.document(surface.inject(template, {"programs": []}),
                           build=surface.build_id(template))
    other = surface.document(
        surface.inject(template, {"programs": [{"key": "a"}], "decisions": {}}),
        build=surface.build_id(template))
    stamp = f'<meta name="zolts-build" content="{surface.build_id(template)}">'
    assert stamp in one and stamp in other


def test_editing_the_surface_moves_the_stamp():
    template = _template()
    assert surface.build_id(template) != surface.build_id(template + "<!-- x -->")


def test_both_call_sites_stamp_the_page():
    """A stamp nothing writes is a drift check that always reports drift.

    The two places that assemble this document are the static build and the
    API. Read out of the source rather than exercised, because the API path
    needs a database and this claim is about the call, not the render.
    """
    for path in (ROOT / "scripts" / "build_site.py", ROOT / "runtime" / "api" / "app.py"):
        body = path.read_text(encoding="utf-8")
        assert "build=build_id(" in body, (
            f"{path.name} assembles the console document without stamping it")


def test_the_built_site_carries_the_stamp_this_checkout_makes():
    built = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
    assert f'<meta name="zolts-build" content="{surface.build_id(_template())}">' in built, (
        "site/ is stale; run PYTHONPATH=. python3 scripts/build_site.py")


# -- the drift check itself ----------------------------------------------


def _run(url: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), "--url", url],
                          capture_output=True, text=True, timeout=120,
                          env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"})


def test_a_page_serving_this_build_passes(tmp_path):
    page = tmp_path / "index.html"
    page.write_text(surface.document(surface.inject(_template(), {"programs": []}),
                                     build=surface.build_id(_template())),
                    encoding="utf-8")
    done = _run(page.as_uri())
    assert done.returncode == 0, done.stderr
    assert surface.build_id(_template()) in done.stdout


def test_a_page_serving_another_build_is_drift(tmp_path):
    page = tmp_path / "index.html"
    page.write_text(surface.document(surface.inject(_template(), {"programs": []}),
                                     build="0123456789ab"), encoding="utf-8")
    done = _run(page.as_uri())
    assert done.returncode == 1
    assert "behind `main`" in done.stderr


def test_a_page_with_no_stamp_is_drift_and_names_the_reason(tmp_path):
    """What production actually serves today."""
    page = tmp_path / "index.html"
    page.write_text("<!doctype html><html><head></head><body>old</body></html>",
                    encoding="utf-8")
    done = _run(page.as_uri())
    assert done.returncode == 1
    assert "no build stamp" in done.stderr
    # The message has to carry the remedy: a red build whose cause is a secret
    # nobody set is only actionable if it says which secret.
    assert "VERCEL_TOKEN" in done.stderr


def test_an_address_that_cannot_be_opened_is_not_a_pass(tmp_path):
    """Exit 2, and never 0.

    A check that cannot reach its subject and returns success is a guard that
    survives the thing it guards against — the shape `ambient_check.py` exists
    to catch, and the reason this one distinguishes no-verdict from pass.
    """
    done = _run((tmp_path / "absent.html").as_uri())
    assert done.returncode == 2
    assert "not a pass" in done.stderr


def test_the_workflow_runs_the_drift_check_without_skipping_it():
    """The job must not be gated on a secret, or it repeats the defect.

    The release job skips when its credentials are missing, deliberately. This
    one has nothing to skip on: it opens a public address. If it ever grows an
    `if:` on a secret, thirty-nine green runs happen again.
    """
    import yaml

    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "deploy-vercel.yml").read_text(encoding="utf-8"))
    drift = workflow["jobs"]["drift"]
    assert drift["if"] == "always()", drift.get("if")
    steps = drift["steps"]
    run = [s for s in steps if "deploy_drift.py" in str(s.get("run", ""))]
    assert len(run) == 1, "the drift job does not run the drift check"
    assert "if" not in run[0], "the drift check is conditional, so it can skip into silence"
    assert "secrets." not in str(drift), "the drift check must need no secret"
