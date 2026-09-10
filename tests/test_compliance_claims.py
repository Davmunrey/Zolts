"""What `docs/11` claims the product does, checked against the product.

`docs/11` is the page a buyer's data protection officer reads. Its GDPR table
was written in the present tense under the heading *Product implementation*, for
eleven obligations, and five of them had no implementation anywhere in the
runtime: no privacy notice, no legitimate-interest assessment, no subject-request
path, no retention job, no records of processing, no sub-processor register. The
AI Act section claimed a hard engine rule that is a setting nothing reads, and
the governance section described five roles in a product with no identity model
at all (D-89).

This is the shape D-21 and D-42 already cost this repository twice: a document
promising a control a reviewer then asks to see. Both were fixed the same way —
correct the document to what ships, and write the guard that fails in *both*
directions, because a correction nothing enforces is a sentence somebody
restores next quarter.

So each row of the table names a status and this file holds the probe behind it.
A row marked *Not built* must stay unimplemented or the document is stale; a row
marked *Built* must stay implemented or the document is a promise again.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "11-compliance-and-governance.md"
SEARCHED = ("runtime", "zolts")

STATUSES = ("Built", "Partly built", "Not built")

# The obligation, and the pattern whose presence in the runtime decides it.
# A row is here or the completeness test fails: an obligation nobody wrote a
# probe for is an obligation nobody checked, which is how the table came to say
# eleven things and mean five.
PROBES: dict[str, str] = {
    "Legal basis": r"required_basis",
    "Minimisation": r"EnrichmentNotPriced",
    "Transparency · provenance": r"provenance",
    "Accuracy": r"enrichment_attempt",
    "International transfers": r"ZOLTS_MODEL_BASE_URL",
    "Security": r"rotate|sealed|seal\(",
    "Transparency · privacy notice": r"privacy_notice",
    "Legitimate interest assessment": r"interest_assessment|lia_record|lia_template",
    "Data subject rights": r"subject_request|\bdsar\b|erasure",
    "Retention": r"retention_sweep|purge_expired|retain_until",
    "Records of processing": r"records_of_processing|\bropa\b",
    "Sub-processors": r"sub_processor|subprocessor",
}


def _rows() -> list[tuple[str, str, str]]:
    text = DOC.read_text(encoding="utf-8")
    head = text.index("## GDPR:")
    body = text[head:text.index("\n## ", head + 1)]
    rows = re.findall(r"^\| ([^|]+?) \| \*\*([^*]+)\*\* \| ([^|]+?) \|$", body, re.M)
    return [(o.strip(), s.strip(), w.strip()) for o, s, w in rows]


def _appears(pattern: str) -> list[str]:
    """Files under `runtime/` and `zolts/` matching the pattern, tests excluded.

    A test naming a mechanism is not the mechanism. `grep` rather than an
    import because half of these would be a table, a migration or a command
    line rather than a symbol.
    """
    found: list[str] = []
    for root in SEARCHED:
        result = subprocess.run(
            ["grep", "-rEil", pattern, str(ROOT / root),
             "--include=*.py", "--include=*.sql"],
            capture_output=True, text=True)
        found += [line for line in result.stdout.split() if line]
    return found


# ── the premise ───────────────────────────────────────────────────────────

def test_the_gdpr_table_is_still_where_this_file_reads_it():
    """A regex matching nothing leaves every test below iterating an empty
    list. Six defects in `docs/22` have been that shape, so the premise is
    asserted before any verdict rests on it."""
    rows = _rows()
    assert len(rows) >= 10, f"docs/11 no longer holds a status-bearing GDPR table: {rows}"
    assert {"Legal basis", "Retention", "Data subject rights"} <= {r[0] for r in rows}


def test_every_row_carries_one_of_the_three_statuses():
    for obligation, status, _ in _rows():
        assert status in STATUSES, (
            f"'{obligation}' is marked '{status}', which is not one of {STATUSES}")


def test_every_row_has_a_probe_and_every_probe_has_a_row():
    """A row with no probe is a claim nobody checked, and a probe with no row
    is a check nobody reads. Both directions, because the defect was the first
    one eleven times over."""
    documented = {r[0] for r in _rows()}
    assert documented == set(PROBES), (
        f"rows without a probe: {sorted(documented - set(PROBES))}; "
        f"probes without a row: {sorted(set(PROBES) - documented)}")


# ── both directions ───────────────────────────────────────────────────────

def test_nothing_marked_built_has_gone_missing():
    for obligation, status, _ in _rows():
        if status == "Not built":
            continue
        assert _appears(PROBES[obligation]), (
            f"docs/11 marks '{obligation}' {status} and nothing under "
            f"{'/, '.join(SEARCHED)}/ matches {PROBES[obligation]!r}")


def test_nothing_marked_not_built_has_quietly_shipped():
    """The direction that matters more. The day a subject-request path or a
    retention job lands, this fails and the document is corrected — rather than
    the page going on saying *not built* about a control the product has, which
    is the same defect pointing the other way."""
    for obligation, status, _ in _rows():
        if status != "Not built":
            continue
        where = _appears(PROBES[obligation])
        assert not where, (
            f"docs/11 says '{obligation}' is not built and {where} matches "
            f"{PROBES[obligation]!r}; the document is stale")


def test_the_document_counts_the_open_rows_rather_than_remembering_them():
    """The count is the finding, so the prose states a number this file reads
    back. A sentence saying *five* over a table of six is the register's own
    recurring defect, and `docs/22` has it twice already."""
    not_built = [o for o, s, _ in _rows() if s == "Not built"]
    stated = re.search(r"\*\*(\d+) of them had no\s+implementation at all\*\*",
                       DOC.read_text(encoding="utf-8"))
    assert stated, "docs/11 no longer says how many rows had nothing behind them"
    assert int(stated.group(1)) == len(not_built), (
        f"docs/11 says {stated.group(1)} obligations had no implementation and "
        f"the table marks {len(not_built)}: {not_built}")


# ── the claims outside the table ──────────────────────────────────────────

def test_the_ai_disclosure_check_is_switched_on_by_the_pack():
    """This test used to assert the opposite, and that is the point of it.

    While `requires_ai_disclosure` was a keyword no caller set, it pinned the
    default off and required `docs/11` to say *not switched on* (D-90). Pack v2
    wired it, so the guard turned red and the page was corrected in the same
    commit — which is the behaviour ADR-054 asks of every row: a control that
    ships corrects the document rather than quietly overtaking it.

    What it holds now is the other direction. The flag must still default off,
    because a `GateResult` that did not answer means the pack's own silence and
    not a marker demanded of every message in every market; and something must
    still be setting it, or the check is switched off again.
    """
    from runtime.engine import generate
    from runtime.agents import copywriter

    for function in (generate.run, copywriter.draft):
        default = function.__kwdefaults__ or {}
        assert default.get("requires_ai_disclosure") is False, (
            f"{function.__module__}.{function.__name__} now demands the marker "
            f"by default, which is a rule no pack published")

    text = DOC.read_text(encoding="utf-8")
    assert "built, from the pack" in text, "docs/11 no longer says the check is wired"
    assert "not switched on" not in text, "docs/11 still describes the check as off"
    assert _appears(r"ai_disclosure"), "nothing in the runtime reads the pack's answer"


def test_no_document_claims_a_role_model_this_product_does_not_have():
    """There is no `user`, `seat` or `role` table in any migration (D-82), so
    the governance section is a design. It says so until one exists."""
    sql = "\n".join(p.read_text(encoding="utf-8")
                    for p in sorted((ROOT / "runtime" / "migrations").glob("*.sql")))
    tables = set(re.findall(r"create table (?:if not exists )?(\w+)", sql))
    identity = tables & {"user", "app_user", "seat", "role", "membership_role"}
    text = DOC.read_text(encoding="utf-8")
    if identity:
        pytest.fail(f"{sorted(identity)} exists now; docs/11 still calls the roles a design")
    assert "None of this exists yet" in text, (
        "docs/11 presents its RBAC roles as shipped and no identity model exists")
    assert "MFA is not on this list" in text, (
        "docs/11 promises MFA on day one and there is nobody to authenticate")
