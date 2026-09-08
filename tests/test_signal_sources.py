"""What the signal engine ships with, and what it does not.

`docs/24` listed Signals under *delivered and verified against real
infrastructure* — nine definitions and a runtime that "goes and looks". The
engine is real: refresh clocks, decay, dedupe, per-account-day billing and the
latency split are all executed and tested. What is not real is any **source**.
Every shipped definition names a connector — `jobs_feed`, `press_feed`,
`product_events` — that no shipped code registers, and the only implementation
of the protocol is a fake that a test registers for itself (D-67).

The code is honest about this and the documents were not, which is the whole of
the defect. `watch.once` resolves the source *before* asking whether anything is
due, precisely so a deployment with no sources reports that on the first pass
rather than looking like a quiet one. Its error message was better than the
backlog's claim.

These tests hold the documents to the code. When a real source ships, they fail,
and the row in `docs/24` gets corrected in the same change — which is the only
way that sentence stays true.
"""

from __future__ import annotations

import pathlib

import yaml

from runtime import connectors
from runtime.connectors import signalsource

DEFINITIONS = sorted(pathlib.Path("examples/signals").glob("*.yaml"))


def _declared() -> dict[str, str]:
    out = {}
    for path in DEFINITIONS:
        document = yaml.safe_load(path.read_text())
        key = (document.get("metadata") or {}).get("key")
        connector = ((document.get("spec") or {}).get("source") or {}).get("connector")
        out[key] = connector
    return out


def test_every_definition_names_the_connector_it_wants():
    """The definitions are the specification. They are complete."""
    declared = _declared()
    assert len(declared) == 9, f"expected nine definitions, found {len(declared)}"
    for key, connector in declared.items():
        assert connector, f"{key} declares no source connector"


def test_no_signal_source_ships_and_the_backlog_says_so():
    """The two halves of the correction, asserted together.

    If someone registers a real source in `install_default_connectors`, this
    fails — and the fix is to update `docs/24`, not to delete the assertion.
    """
    connectors.install_default_connectors()
    registered = set(signalsource.sources())
    backlog = pathlib.Path("docs/24-backlog.md").read_text()

    if registered:
        assert "No source ships" not in backlog, (
            f"signal sources are registered now ({sorted(registered)}), and docs/24 "
            f"still says none ships. Correct the document")
    else:
        assert "**No source ships.**" in backlog, (
            "no signal source is registered and docs/24 does not say so, which is "
            "the defect D-67 records")


def test_watch_reports_a_missing_source_rather_than_raising():
    """The behaviour that makes the gap operable instead of fatal, and the
    reason the code was never the problem here."""
    for connector in set(_declared().values()):
        try:
            signalsource.get_source(connector)
        except LookupError as exc:
            assert connector in str(exc), (
                "the error must name the connector an operator has to register")
            assert "registered:" in str(exc), (
                "and say what is registered, so the answer is in the message")
        else:  # pragma: no cover - only once a real source ships
            pass
