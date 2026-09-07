"""What `docs/11` tells a buyer about the model provider has to be what ships.

ADR-011 and `docs/23` said *a tenant can route through their own gateway*.
`ModelClient` reads one `ZOLTS_MODEL_BASE_URL` from the environment: the
endpoint is per deployment, and no tenant has ever had a place to store one
(D-42). A security document promising a control that does not exist is the
kind of sentence a diligence reviewer asks to see — so the three claims the
disclosure rests on are read here against the code, on every push.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DISCLOSURE = ROOT / "docs" / "11-compliance-and-governance.md"
ARCHITECTURE = ROOT / "docs" / "02-architecture.md"
SECURITY = ROOT / "docs" / "23-security-register.md"
CLIENT = ROOT / "runtime" / "agents" / "client.py"

VARIABLE = "ZOLTS_MODEL_BASE_URL"


def test_the_agent_layer_is_off_unless_a_deployment_turns_it_on(monkeypatch):
    """The disclosure's first sentence. Nothing leaves for a model provider
    until somebody sets the variable, and the default must stay that way."""
    from runtime.config import Settings

    monkeypatch.setenv("ZOLTS_DATABASE_URL", "postgresql://x")
    monkeypatch.setenv("ZOLTS_SECRET_KEY", "k")
    monkeypatch.delenv("ZOLTS_AGENTS", raising=False)
    assert Settings.from_env().agents_enabled is False
    assert "off by default" in DISCLOSURE.read_text()


def test_the_variable_the_document_names_is_the_one_the_code_reads(monkeypatch):
    assert VARIABLE in DISCLOSURE.read_text(), "docs/11 no longer names the endpoint control"
    assert VARIABLE in CLIENT.read_text(), "the client no longer reads the variable docs/11 names"

    from runtime.agents.client import ModelClient

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only")
    monkeypatch.setenv(VARIABLE, "https://gateway.example/v1")
    assert ModelClient().base_url == "https://gateway.example/v1"
    monkeypatch.delenv(VARIABLE)
    assert ModelClient().base_url is None


def test_no_document_promises_per_tenant_routing_the_code_does_not_have():
    """The sentence that was D-42, kept out of both documents until the
    control exists (decision 36)."""
    adr = ARCHITECTURE.read_text()
    block = adr[adr.index("**ADR-011 ·"):adr.index("**ADR-012 ·")]
    for document, text in (("ADR-011", block), ("docs/23", SECURITY.read_text())):
        assert not re.search(r"a tenant can route", text), (
            f"{document} promises per-tenant gateway routing again; the endpoint is one "
            f"variable per deployment")
    assert "per deployment" in DISCLOSURE.read_text()


def test_the_disclosure_lists_every_agent_that_calls_the_model():
    """Three agents call the model. A fourth one added without a row here is
    data leaving the runtime that the disclosure does not mention."""
    agents = {p.stem for p in (ROOT / "runtime" / "agents").glob("*.py")
              if p.stem not in {"__init__", "client", "spend"}}
    text = DISCLOSURE.read_text()
    section = text[text.index("## What a model provider sees"):]
    section = section[:section.index("## Security and certifications")]
    for agent in agents:
        assert agent.capitalize() in section, (
            f"runtime/agents/{agent}.py calls the model and docs/11 has no row for it")
