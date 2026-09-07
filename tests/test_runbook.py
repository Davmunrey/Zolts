"""The runbook has to still be true tomorrow.

`docs/25` tells an operator which command to run at three in the morning. A
renamed subcommand or a dropped column turns that page into a document that
sends somebody down the wrong path while the outbox is stalled — which is
worse than no page, because it is trusted.

Every command and every query on that page is checked here against the code
and the schema it claims to drive.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.conftest import requires_db

RUNBOOK = Path(__file__).resolve().parent.parent / "docs" / "25-runbook.md"


def _blocks(language: str) -> list[str]:
    return re.findall(rf"```{language}\n(.*?)```", RUNBOOK.read_text(), re.S)


def _commands() -> set[str]:
    """Every `runtime.cli <subcommand>` the runbook tells someone to run."""
    found = set()
    for block in _blocks("bash"):
        for match in re.finditer(r"runtime\.cli\s+([a-z][a-z-]*)", block):
            found.add(match.group(1))
    return found


def _cli_subcommands() -> set[str]:
    """Read the subcommands off the parser rather than out of its help text.

    Parsing `--help` catches the words too: it accepted a runbook command that
    happened to match a stray noun in the usage line. The parser knows exactly
    what it registered.
    """
    import argparse

    from runtime import cli

    registered: set[str] = set()
    original = argparse._SubParsersAction.add_parser

    def recording(self, name, **kwargs):
        registered.add(name)
        return original(self, name, **kwargs)

    argparse._SubParsersAction.add_parser = recording          # type: ignore[assignment]
    try:
        with pytest.raises(SystemExit):
            cli.main(["--help"])
    finally:
        argparse._SubParsersAction.add_parser = original        # type: ignore[assignment]
    return registered


def test_the_runbook_names_commands_that_exist(capsys):
    """The failure this catches is a rename: the code moves on and the page
    keeps naming the old command, which fails at the worst moment.

    It caught one on its first run — the page said `crm-connect`, and the
    command is `connect`.
    """
    registered = _cli_subcommands()
    capsys.readouterr()
    assert registered, "no subcommands were captured; the parser changed shape"
    named = _commands()
    assert named, "the runbook names no commands at all"
    missing = named - registered
    assert not missing, (
        f"docs/25 tells an operator to run {sorted(missing)}, and the CLI has no such "
        f"command. Known: {sorted(registered)}")


@requires_db
def test_every_query_the_runbook_prints_actually_runs(db, tenant):
    """A column that was renamed leaves a query that errors in front of
    somebody who is already having a bad night."""
    reads = [q for q in _blocks("sql") if q.strip().lower().startswith("select")]
    assert reads, "the runbook prints no queries"
    with db.tenant_tx(tenant["id"]) as cur:
        for query in reads:
            cur.execute(query)   # raises if a column or table is gone


def test_the_runbook_does_not_promise_production_experience():
    """The one claim this repository cannot make. It is stated on the page,
    and it has to stay stated until it stops being true."""
    text = RUNBOOK.read_text()
    assert "there is no production" in text
