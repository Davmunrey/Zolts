"""What a signal is worth, and how long it is worth looking for.

`docs/06` is a catalogue of thirty-odd signals with half-lives, tiers and
action SLAs, and until now nothing read it. A program's trigger named a signal
key and the runtime took the strength, the decay and the legal basis from
whoever happened to be calling `ingest` — which for a pushed signal is the
customer, and for a detected one would have been nobody.

Three properties are required of every definition, and `docs/06` says why: a
signal missing its cost, its decay or its legal basis can be neither budgeted
nor audited. Cost is the one that is deliberately *not* here. `docs/12` prices
a check per account per day rather than per signal, so a definition naming its
own price would contradict the price list, and the price list wins.

Pure, like everything else in `zolts/`: parsing a duration and deciding whether
a detection is too old are arithmetic. Going and looking is the runtime's job.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import jsonschema
import yaml

SCHEMA_PATH = (Path(__file__).resolve().parent.parent / "examples" / "schema"
               / "zolts-signal.schema.json")
CATALOGUE_DIR = Path(__file__).resolve().parent.parent / "examples" / "signals"

_DURATION = re.compile(r"^(\d+)([hd])$")


class SignalDefinitionError(ValueError):
    pass


def duration(spec: str) -> timedelta:
    """`6h` or `30d`. Two units and no more.

    Minutes would invite a refresh interval that costs more in checks than the
    signal is worth, and weeks are ambiguous enough that somebody would have to
    look up whether this one means seven days.
    """
    match = _DURATION.match(str(spec).strip())
    if not match:
        raise SignalDefinitionError(
            f"'{spec}' is not a duration; use hours or days, like 6h or 30d")
    amount, unit = int(match.group(1)), match.group(2)
    if amount <= 0:
        raise SignalDefinitionError(f"'{spec}' is not a positive duration")
    return timedelta(hours=amount) if unit == "h" else timedelta(days=amount)


@dataclass(frozen=True)
class SignalDefinition:
    """One entry of the catalogue, as the runtime needs it."""
    key: str
    entity: str
    connector: str
    refresh: timedelta
    freshness_sla: timedelta
    half_life_h: int
    base_strength: float
    legal_basis: str
    dedupe_window: timedelta
    tier: str | None = None
    name: str | None = None
    config: dict[str, Any] | None = None

    def is_fresh(self, observed_at: datetime, now: datetime) -> bool:
        """Whether a detection is still worth acting on.

        `docs/06` calls the freshness SLA the maximum acceptable data age, and
        the tier's action SLA is what sets it. Acting on a three-day-old
        pricing-page visit with a 48-hour half-life is acting on nothing, and
        it spends a touch on somebody whose moment has passed.
        """
        return (now - observed_at) <= self.freshness_sla

    def due(self, last_checked: datetime | None, now: datetime) -> bool:
        """Whether this account is due another look."""
        return last_checked is None or (now - last_checked) >= self.refresh


def _schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate(document: dict[str, Any]) -> dict[str, Any]:
    try:
        jsonschema.Draft202012Validator(_schema()).validate(document)
    except jsonschema.ValidationError as exc:
        # Wrapped, so a caller does not have to import jsonschema to catch the
        # one error this module raises.
        where = "/".join(str(p) for p in exc.path) or "the document"
        raise SignalDefinitionError(f"{where}: {exc.message}") from exc
    return document


def parse(document: dict[str, Any]) -> SignalDefinition:
    validate(document)
    metadata, spec = document["metadata"], document["spec"]
    source = spec["source"]
    return SignalDefinition(
        key=metadata["key"], entity=spec["entity"],
        connector=source["connector"],
        refresh=duration(source.get("refresh", "24h")),
        freshness_sla=duration(spec["freshness_sla"]),
        half_life_h=int(spec["half_life_h"]),
        base_strength=float(spec["base_strength"]),
        legal_basis=spec["legal_basis"],
        dedupe_window=duration(spec.get("dedupe_window", "14d")),
        tier=metadata.get("tier"), name=metadata.get("name"),
        config=source.get("config"))


def load(path: str | Path) -> SignalDefinition:
    return parse(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def catalogue(directory: str | Path | None = None) -> dict[str, SignalDefinition]:
    """Every definition, by key.

    A missing directory raises rather than returning nothing. An empty
    catalogue and an absent one look identical to a caller and mean opposite
    things: one is a deployment with no signals defined, the other is a
    deployment that shipped without its own data.
    """
    where = Path(directory or CATALOGUE_DIR)
    if not where.is_dir():
        raise SignalDefinitionError(
            f"no signal catalogue at {where}; a deployment without one detects "
            f"nothing and would report that as normal")
    found: dict[str, SignalDefinition] = {}
    for path in sorted(where.glob("*.yaml")):
        definition = load(path)
        if definition.key in found:
            raise SignalDefinitionError(
                f"two definitions claim '{definition.key}'; a program's trigger "
                f"would silently get whichever loaded last")
        found[definition.key] = definition
    return found
