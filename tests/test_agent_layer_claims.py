"""What `docs/08` says the agent layer is, checked against the agent layer.

`docs/08` describes six agents and a model architecture in the present tense.
**Three of the six run**, and four of the mechanisms beneath them do not exist:
there is no per-task model router, no minimiser, no prompt caching, no golden
set and no outward MCP server (D-93). The page reads as a description of what
ships, and it is the page a technical buyer opens after the compliance one.

This is ADR-054's mechanism applied to a third document. Each claim carries a
status and this file holds the probe behind it, checked in both directions: a
claim marked *not built* whose mechanism appears in the runtime fails here, so
the day one ships the document is corrected rather than quietly overtaken.

The router is the interesting one. It is not a free win — the Qualifier's job
is deciding whether somebody asked to be left alone, a cheaper model that
misreads *take me off your list* is exactly D-16, and the golden set that would
measure it is itself one of the missing pieces. Decision 56 registers it with a
default of *no change until there is something to measure against*, which is
why this file asserts the frontier model is still what every agent starts on.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "08-ai-agent-layer.md"
AGENTS = ROOT / "runtime" / "agents"

# The agents `docs/08` names, and the module each built one is. A name mapped
# to None is one the document must mark as not built.
ROLES = {
    "Researcher": "researcher.py",
    "Copywriter": "copywriter.py",
    "Qualifier": "triage.py",
    "Strategist": None,
    "Ops": None,
    "Analyst": None,
}

# A mechanism the document describes, and the pattern that would prove it real.
NOT_BUILT = {
    "prompt caching": r"cache_control|prompt_cache|ephemeral",
    "golden set": r"golden[_ ]set|regression_set",
    "outward MCP server": r"FastMCP|mcp\.tool\(|def mcp_server",
    "PII minimiser": r"def minimise|def minimize|tokenise_pii|strip_pii",
}


def _appears(pattern: str) -> list[str]:
    """Files under `runtime/` and `zolts/` matching, tests excluded: a test
    naming a mechanism is not the mechanism."""
    result = subprocess.run(
        ["grep", "-rEl", pattern, str(ROOT / "runtime"), str(ROOT / "zolts"),
         "--include=*.py"], capture_output=True, text=True)
    return [line for line in result.stdout.split() if line]


# ── the premise ───────────────────────────────────────────────────────────

def test_the_roles_table_is_still_where_this_file_reads_it():
    text = DOC.read_text(encoding="utf-8")
    for role in ROLES:
        assert f"**{role}**" in text, f"docs/08 no longer names the {role}"


# ── the agents ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("role,module", sorted(ROLES.items()))
def test_every_agent_the_document_names_is_built_or_says_it_is_not(role, module):
    text = DOC.read_text(encoding="utf-8")
    row = next(line for line in text.splitlines() if line.startswith(f"| **{role}**"))
    if module is None:
        assert "Not built" in row, (
            f"docs/08 presents the {role} as shipped and no module implements it")
        return
    assert (AGENTS / module).exists(), f"{module} is gone and docs/08 still claims the {role}"
    assert "Built" in row and "Not built" not in row, (
        f"docs/08 marks the {role} not built and `{module}` exists")


def test_no_agent_shipped_while_the_document_still_calls_it_designed():
    """The direction that matters. A Strategist landing in `runtime/agents/`
    while this table says *not built* is the same defect pointing the other
    way, and it is how the page drifted in the first place."""
    built = {p.name for p in AGENTS.glob("*.py")} - {"__init__.py", "client.py", "spend.py"}
    expected = {module for module in ROLES.values() if module}
    assert built == expected, (
        f"the agent layer holds {sorted(built)} and docs/08 accounts for "
        f"{sorted(expected)}")


# ── the mechanisms ────────────────────────────────────────────────────────

@pytest.mark.parametrize("mechanism,pattern", sorted(NOT_BUILT.items()))
def test_nothing_marked_not_built_has_quietly_shipped(mechanism, pattern):
    where = _appears(pattern)
    assert not where, (
        f"docs/08 says the {mechanism} is not built and {where} matches "
        f"{pattern!r}; the document is stale")


# The words the document must still use for each missing mechanism, so a
# reader looking for it finds the sentence saying it is not there.
NAMED_AS = {
    "prompt caching": "prompt caching",
    "golden set": "golden set",
    "outward MCP server": "mcp server",
    "PII minimiser": "minimiser",
}


def test_the_document_says_each_missing_mechanism_is_missing():
    """A mechanism dropped from the page rather than marked absent is worse
    than one described wrongly: the reader concludes it was never designed."""
    text = DOC.read_text(encoding="utf-8").lower()
    for mechanism, phrase in NAMED_AS.items():
        assert phrase in text, f"docs/08 no longer mentions the {mechanism} at all"
    assert "not built" in text


# ── the router, which is a decision rather than an omission ───────────────

def test_every_agent_starts_on_the_frontier_model():
    """Decision 56's default, asserted where it lives. The Qualifier classifies
    a reply on the same model that writes the copy, and that stays true until a
    golden set can measure the cheaper one on the classification that matters."""
    import inspect

    from runtime.agents import client

    assert client.DEFAULT_MODEL and client.CHEAP_MODEL
    assert client.DEFAULT_MODEL != client.CHEAP_MODEL
    default = inspect.signature(client.ModelClient.__init__).parameters["model"].default
    assert default == client.DEFAULT_MODEL, (
        f"a client built with no model asks for {default!r}, and decision 56 "
        f"says every agent starts on {client.DEFAULT_MODEL!r}")


def test_the_only_downgrade_is_a_spend_refusal_with_an_alternative():
    """What the router is not. The cheaper model is reached when a tenant is
    near a ceiling, not when a task is cheap — a different trigger, and the
    document says so rather than calling it a router."""
    for module in ("copywriter.py", "researcher.py"):
        source = (AGENTS / module).read_text(encoding="utf-8")
        assert "cheapest_alternative" in source, (
            f"{module} no longer takes the lever a refusal offers")
    assert "not built" in DOC.read_text(encoding="utf-8").lower()


@pytest.mark.parametrize("module", ["copywriter.py", "researcher.py", "triage.py"])
def test_no_agent_imports_a_model_it_never_uses(module):
    """`CHEAP_MODEL` was imported by the copywriter and used nowhere: an unused
    import for a router that does not exist, which is one of the two things
    that made this page read as though one did."""
    import ast

    tree = ast.parse((AGENTS / module).read_text(encoding="utf-8"))
    imported = {alias.asname or alias.name
                for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                # `from __future__ import annotations` is a compiler
                # directive, not a name anything refers to.
                and node.module != "__future__"
                for alias in node.names}
    used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    used |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    unused = {name for name in imported if name not in used}
    assert not unused, f"{module} imports {sorted(unused)} and uses none of them"
