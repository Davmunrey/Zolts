"""A report signed against one version of the form still verifies against the next.

`docs/10` sells the frozen report on two checks. Hash the JSON you hold and
compare it with the letter — that never depended on this code. Rebuild it
through `zolts.report.from_mapping` and recompute both the figures and the
hash — that is the stronger claim, and it broke silently the first time
`canonical()` grew a key (D-72). A report frozen in one month, rebuilt the
next, produced a digest its own letter did not carry.

The fixture below is the artefact, not a recomputation of it: a body and a
digest captured from the code as it stood at `46ff70d`, before the form was
versioned at all. A test that recomputes both sides proves only that the code
agrees with itself.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date

import pytest

from zolts.report import (KEYS_ADDED_IN, SCHEMA_VERSION, Comparison,
                          IncrementalityReport, from_mapping)

# Frozen at 46ff70d. Twenty-three keys, no `schema_version`, no acquisition
# cost: the shape every report written before this month carries.
VERSION_ONE_BODY: dict = {
    "average_opportunity_micros": None,
    "baseline": None,
    "converted_by_type": {},
    "credits_by_kind": {"email.send": 40.0},
    "credits_total": 40.0,
    "decisions": {},
    "holdout_pct": 10.0,
    "incremental_pipeline_micros": None,
    "metric_window_days": 90,
    "opportunities": {
        "control_converted": 10, "control_enrolled": 1000, "control_rate": 0.01,
        "incremental_conversions": 20, "lift": 0.02,
        "minimum_detectable_effect": 0.012466, "treatment_converted": 30,
        "treatment_enrolled": 1000, "treatment_rate": 0.03, "verdict": "significant"},
    "opportunities_with_amount": 0,
    "period_end": "2026-06-01",
    "period_start": "2026-05-01",
    "pipeline_withheld_because": "the CRM holds no opportunity with an amount",
    "primary": {
        "control_converted": 40, "control_enrolled": 1000, "control_rate": 0.04,
        "incremental_conversions": 50, "lift": 0.05,
        "minimum_detectable_effect": 0.024552, "treatment_converted": 90,
        "treatment_enrolled": 1000, "treatment_rate": 0.09, "verdict": "significant"},
    "primary_metric": "any_conversion_90d",
    "program_key": "flagship",
    "program_version": "2.1.0",
    "spec_hash": "abc",
    "touches_sent": 0,
    "unread_conversions": 0,
    "unread_share": 0.0,
    "verdict": "significant",
}

# What the partner's letter quotes.
VERSION_ONE_DIGEST = "35246e3491ab6a72f86b2ddd1cd93d988ed003bc7e90746de1e5b8d5d39a0748"


def _today(**overrides) -> IncrementalityReport:
    """The same programme and the same arms, composed by today's code."""
    fields = dict(
        program_key="flagship", program_version="2.1.0", spec_hash="abc",
        period_start=date(2026, 5, 1), period_end=date(2026, 6, 1), holdout_pct=10.0,
        primary=Comparison(1000, 1000, 90, 40),
        opportunities=Comparison(1000, 1000, 30, 10),
        credits_by_kind={"email.send": 40.0})
    fields.update(overrides)
    return IncrementalityReport(**fields)


def test_a_report_frozen_before_the_form_was_versioned_still_verifies():
    """The defect, stated as the partner's own check. Without the version the
    rebuild emitted six keys the August body never had, and the digest a
    counterparty computed was not the digest in the letter they were holding.
    """
    rebuilt = from_mapping(VERSION_ONE_BODY)
    assert rebuilt.schema_version == 1, "a body with no version key is version 1"
    assert rebuilt.digest() == VERSION_ONE_DIGEST


def test_the_fixture_is_the_artefact_and_not_a_recomputation():
    """Otherwise the test above passes for whatever the code emits today."""
    blob = json.dumps(VERSION_ONE_BODY, sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(blob.encode()).hexdigest() == VERSION_ONE_DIGEST, (
        "the pinned digest is not the hash of the pinned body")
    assert "schema_version" not in VERSION_ONE_BODY
    assert len(VERSION_ONE_BODY) == 23


def test_hashing_the_json_you_hold_never_needed_this_code_at_all():
    """The weaker check, stated so the stronger one is not confused with it.
    Integrity survived the defect; re-derivation did not."""
    blob = json.dumps(VERSION_ONE_BODY, sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(blob.encode()).hexdigest() == VERSION_ONE_DIGEST


def test_todays_report_declares_its_version_and_round_trips():
    report = _today()
    body = report.canonical()
    assert body["schema_version"] == SCHEMA_VERSION
    assert from_mapping(body).digest() == report.digest()
    assert from_mapping(body).render_markdown() == report.render_markdown()


def test_the_same_arms_hash_differently_under_different_versions():
    """A version is part of the claim, not decoration. Two documents with the
    same numbers and different shapes are two documents."""
    assert _today(schema_version=1).digest() != _today().digest()
    assert _today(schema_version=1).digest() == VERSION_ONE_DIGEST, (
        "today's code, asked for version 1, must reproduce the August letter")


@pytest.mark.parametrize("version", sorted(KEYS_ADDED_IN))
def test_every_key_a_version_added_is_absent_from_the_one_before(version):
    """The registry has to describe the shapes, or a rebuild of an older body
    silently emits a key that body never had — which is the whole defect."""
    older = _today(schema_version=version - 1).canonical()
    newer = _today(schema_version=version).canonical()
    for key in KEYS_ADDED_IN[version]:
        assert key not in older, f"{key} is claimed for v{version} and is in v{version - 1}"
        assert key in newer, f"{key} is claimed for v{version} and is not in it"
    assert set(newer) - set(older) == set(KEYS_ADDED_IN[version]), (
        f"v{version} adds keys the registry does not name")


def test_the_current_version_is_the_highest_one_the_registry_knows():
    """A version bumped without its key set recorded is a shape nothing can
    reproduce."""
    assert SCHEMA_VERSION == max(KEYS_ADDED_IN), (
        "SCHEMA_VERSION and KEYS_ADDED_IN disagree about what the current shape is")


def test_an_older_document_re_renders_as_the_document_that_was_signed():
    """`rendered` is stored verbatim, so this is belt and braces — but a reader
    who re-renders an August report must not be shown a section August never
    had, above a figure August never computed."""
    august = from_mapping(VERSION_ONE_BODY).render_markdown()
    assert "## Cost per meeting" not in august
    assert "## Cost per meeting" in _today().render_markdown()


# -- what the documents claim ---------------------------------------------

def test_the_documents_name_both_checks_and_the_version_that_makes_one_work():
    """`docs/10` sold one check as though it were two, and ADR-043 said adding
    a figure was *a new field and a new digest* — true of the rows and false of
    the verification. A document that overstates what a partner can check is
    the expensive kind to be wrong about."""
    from pathlib import Path

    docs = Path(__file__).resolve().parents[1] / "docs"
    measurement = (docs / "10-measurement-and-incrementality.md").read_text()
    assert "schema_version" in measurement
    assert "A body with no version key is version 1." in measurement

    architecture = (docs / "02-architecture.md").read_text()
    assert "The canonical form is versioned" in architecture
    assert "Every change to the key set is a new version" in architecture
    assert "D-72" in architecture
