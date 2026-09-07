"""The onboarding page has to still be true when the third partner opens it.

`docs/26` walks an operator and a partner from an invitation to a first send,
names commands, HTTP paths and queries, and quotes the pilot's success
criteria from `docs/14`. Each of those is checked here against the thing it
claims to drive — the same discipline as the runbook, for the page that runs
before there is anything to operate.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.conftest import requires_db
from tests.test_runbook import _cli_subcommands

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "docs" / "26-partner-onboarding.md"
KPIS = ROOT / "docs" / "14-kpis.md"


def _blocks(language: str) -> list[str]:
    return re.findall(rf"```{language}\n(.*?)```", PAGE.read_text(), re.S)


def test_every_command_on_the_page_exists(capsys):
    registered = _cli_subcommands()
    capsys.readouterr()
    named = {m.group(1) for block in _blocks("bash") + _blocks("sh")
             for m in re.finditer(r"runtime\.cli\s+([a-z][a-z-]*)", block)}
    assert named, "the page names no commands"
    missing = named - registered
    assert not missing, f"docs/26 names {sorted(missing)}; the CLI has no such command"


def test_every_http_path_on_the_page_is_a_route():
    """A path in a document a partner follows must be a path the API serves."""
    from fastapi import FastAPI

    from runtime import serverless
    from runtime.api.app import create_app

    class _Db:
        def admin_tx(self):
            raise AssertionError("not used")

    app: FastAPI = create_app(_Db(), install_connectors=False)  # type: ignore[arg-type]
    served = {getattr(r, "path", "") for r in app.routes}
    served |= {getattr(r, "path", "") for r in serverless.unconfigured("x").routes}
    templated = {re.sub(r"\{[^}]+\}", "{}", p) for p in served}

    named = set(re.findall(r"`(?:GET|POST|DELETE)\s+(/[^` ]+)`", PAGE.read_text()))
    assert named, "the page names no HTTP paths"
    for path in named:
        shape = re.sub(r"\{[^}]+\}", "{}", path)
        assert shape in templated, f"docs/26 names {path}, which no route serves"


@requires_db
def test_every_query_on_the_page_runs(db, tenant):
    reads = [q for q in _blocks("sql") if q.strip().lower().startswith("select")]
    assert reads, "the page prints no queries"
    with db.tenant_tx(str(tenant["id"])) as cur:
        for query in reads:
            cur.execute(query)


def _kpi_target(row_label: str) -> str:
    """The 'now' target of a KPI row in docs/14, e.g. '>3%'."""
    for line in KPIS.read_text().splitlines():
        if line.startswith(f"| {row_label} |"):
            return [c.strip() for c in line.strip("|").split("|")][2]
    raise AssertionError(f"docs/14 has no row '{row_label}'")


@pytest.mark.parametrize("label", ["Positive reply rate", "Email bounce rate",
                                   "Average incremental lift versus holdout"])
def test_the_letter_quotes_the_thresholds_docs_14_sets(label):
    """A pilot criterion that drifts from the KPI page is two promises."""
    target = _kpi_target(label)
    assert target in PAGE.read_text(), (
        f"docs/26 does not quote {label}'s target {target!r} from docs/14")


def test_the_letter_quotes_the_pilot_terms_docs_18_decided():
    text = PAGE.read_text()
    assert "€5k" in text and "three months" in text, (
        "the pilot terms (decision B7: always paid, €5k for three months) are not on the page")
    assert "digest" in text, "the letter does not quote the baseline digest"
