"""Reading a record out of a payload nobody at this company has seen.

A partner may run a CRM that is not on the market — their own, built in-house,
with field names nobody can guess. No connector written here can read it, so
the connector cannot be the unit of work. The mapping is.

This is product invariant 5 applied to integration: *program logic is versioned
configuration, never ad-hoc code*. Connecting a CRM becomes authoring a
document, and the document is validated, stored and versioned like any other.

The path language is deliberately small. Four forms cover every CRM shape this
repository has met:

    properties.email          nested objects
    emails[0].value           an array by position
    emails[primary].address   the array element flagged primary, else the first
    name | trim | lower       a transform pipeline

Six transforms, and every one of them returns a string or MISSING, because
every canonical field is a string. A transform producing anything else has
nowhere to put it.

Anything more expressive is a program, and a program that runs on a customer's
payload inside this runtime is a liability rather than a feature.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema
import yaml

SCHEMA_PATH = (Path(__file__).resolve().parent.parent / "examples" / "schema"
               / "zolts-crm-mapping.schema.json")

MISSING = object()

_SEGMENT = re.compile(r"^([^\[\]]+)(?:\[([^\]]*)\])?$")

# Every transform is total: it takes whatever it is given and returns something
# or MISSING. A transform that raises would make a malformed record from one
# customer stop a sync for all of them.
TRANSFORMS: dict[str, Any] = {
    "trim": lambda v: v.strip() if isinstance(v, str) else v,
    "lower": lambda v: v.lower() if isinstance(v, str) else v,
    "upper": lambda v: v.upper() if isinstance(v, str) else v,
    "join": lambda v: " ".join(str(x) for x in v if x) if isinstance(v, (list, tuple)) else v,
    "str": lambda v: None if v is None else str(v),
    "domain": lambda v: _domain(v),
}


def _domain(value: Any) -> Any:
    """A hostname from a URL or an email. CRMs store 'website' every way."""
    if not isinstance(value, str) or not value.strip():
        return MISSING
    text = value.strip().lower()
    if "@" in text:
        return text.rsplit("@", 1)[-1] or MISSING
    text = re.sub(r"^[a-z]+://", "", text)
    text = text.split("/")[0].split("?")[0]
    return text.removeprefix("www.") or MISSING


class MappingError(ValueError):
    pass


def _index(items: list[Any], selector: str) -> Any:
    """An array element, by position or by a truthy flag."""
    if not items:
        return MISSING
    if selector.lstrip("-").isdigit():
        position = int(selector)
        return items[position] if -len(items) <= position < len(items) else MISSING
    # A flag name: the element where it is truthy, and the first otherwise.
    # Pipedrive marks one address primary; several CRMs mark none, and the
    # first is then the only defensible choice.
    flagged = next((i for i in items
                    if isinstance(i, dict) and str(i.get(selector, "")).lower()
                    not in {"", "false", "0", "none"}), None)
    return flagged if flagged is not None else items[0]


def extract(payload: Any, path: str) -> Any:
    """Resolve one path against one record. Returns MISSING, never raises.

    A missing field is the normal case when reading somebody else's system, and
    a resolver that threw would make every optional field a required one.
    """
    if not path:
        return MISSING
    steps = [s.strip() for s in path.split("|")]
    current: Any = payload
    for segment in steps[0].split("."):
        match = _SEGMENT.match(segment.strip())
        if match is None:
            raise MappingError(f"'{segment}' is not a valid path segment in '{path}'")
        key, selector = match.group(1), match.group(2)
        if isinstance(current, dict):
            current = current.get(key, MISSING)
        else:
            return MISSING
        if current is MISSING:
            return MISSING
        if selector is not None:
            if not isinstance(current, (list, tuple)):
                return MISSING
            current = _index(list(current), selector)
            if current is MISSING:
                return MISSING

    for name in steps[1:]:
        transform = TRANSFORMS.get(name)
        if transform is None:
            raise MappingError(f"unknown transform '{name}'; known: "
                               f"{', '.join(sorted(TRANSFORMS))}")
        current = transform(current)
        if current is MISSING:
            return MISSING
    return current


def check_path(where: str, path: str) -> None:
    """Reject a path at publish time rather than in the middle of a crawl.

    `extract` cannot do this on its own: it walks only as far as the record
    takes it, so a malformed third segment in a document whose second segment
    is absent is never reached until the one record that has it arrives.
    """
    steps = [s.strip() for s in path.split("|")]
    if not steps[0]:
        raise MappingError(f"{where} has an empty path")
    for segment in steps[0].split("."):
        if _SEGMENT.match(segment.strip()) is None:
            raise MappingError(f"{where}: '{segment.strip()}' is not a valid path "
                               f"segment in '{path}'")
    for name in steps[1:]:
        if name not in TRANSFORMS:
            raise MappingError(f"{where} uses unknown transform '{name}'; "
                               f"known: {', '.join(sorted(TRANSFORMS))}")


@dataclass(frozen=True)
class TranslationRule:
    """One field of the CRM's vocabulary, read into one of ours.

    Two things are translated this way, and both share the rule that an
    unmapped value takes the default rather than a guess: whether somebody may
    be contacted, and whether a deal is still live. A mapping with no consent
    rule is not a mapping that permits contact — it is a mapping whose source
    cannot answer the question, and every one of its contacts is unknown until
    a basis is established elsewhere. A mapping with no deal-status rule is the
    same shape, with `open` as the default, because open is the answer that
    leaves an account alone.
    """
    field: str
    values: dict[str, str] = field(default_factory=dict)
    default: str = "unknown"

    def read(self, record: Any) -> str:
        raw = extract(record, self.field)
        if raw is MISSING or raw is None:
            return self.default
        key = str(raw).strip().lower()
        if key in self.values:
            return self.values[key]
        # An unmapped value is the default and nothing else. Guessing at a
        # boolean here would grant `allowed` for a state the document never
        # named, and would grant it backwards for a field phrased positively:
        # `email_ok: true` means this person consented, and reading a bare
        # boolean as an opt-out flag would suppress them and mail everyone
        # who refused. A boolean field is mapped like any other:
        # `values: {"true": allowed, "false": opted_out}`.
        return self.default


def build(record: Any, fields: dict[str, str]) -> dict[str, Any]:
    """Apply a field map to one record, dropping what the source did not have."""
    built: dict[str, Any] = {}
    for name, path in fields.items():
        value = extract(record, path)
        if value is not MISSING and value is not None and value != "":
            built[name] = value
    return built


def records_at(payload: Any, path: str | None) -> list[Any]:
    """The list of records inside a response envelope.

    Every API wraps its results differently — `results`, `data`, `items`, or
    nothing at all. Naming the path is one line of configuration; guessing it
    is a source of silent empty syncs.
    """
    if path:
        found = extract(payload, path)
        if found is MISSING:
            return []
        return list(found) if isinstance(found, (list, tuple)) else [found]
    if isinstance(payload, list):
        return list(payload)
    return [payload] if isinstance(payload, dict) else []


# -- authoring ------------------------------------------------------------


# A partner's own CRM legitimately sits on a private address behind a VPN, so
# RFC1918 is the use case rather than the threat. Loopback and link-local are
# neither: nothing a partner runs lives there, and 169.254.169.254 is the cloud
# metadata endpoint, which would hand this runtime's own instance credentials
# to whoever authored the document.
_BLOCKED_HOSTS = re.compile(
    r"^(localhost|127\.|0\.0\.0\.0|\[?::1\]?|169\.254\.|\[?fe80:|\[?fd00:)", re.I)


def check_base_url(url: str) -> None:
    """Refuse a base URL that points at the runtime instead of at a CRM."""
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise MappingError(f"transport.base_url must be http or https, not "
                           f"'{parts.scheme or url}'")
    if not parts.hostname:
        raise MappingError(f"transport.base_url names no host: '{url}'")
    if _BLOCKED_HOSTS.match(parts.netloc.split("@")[-1]) or _BLOCKED_HOSTS.match(parts.hostname):
        raise MappingError(
            f"transport.base_url '{parts.hostname}' is a loopback or link-local "
            "address, which is this runtime rather than your CRM; a system behind "
            "a VPN uses its own private address, and one with no reachable address "
            "uses transport 'push'")


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text())


def validate(document: dict[str, Any]) -> dict[str, Any]:
    """Schema-check a mapping, then check what a schema cannot express.

    A mapping is a customer-authored document that decides who gets contacted.
    It is validated at the door for the same reason a program is: the
    alternative is discovering the mistake in a sent message.
    """
    try:
        jsonschema.Draft202012Validator(load_schema()).validate(document)
    except jsonschema.ValidationError as exc:
        # One exception type leaves the module. A caller handling a customer's
        # document should not have to know which library rejected it.
        where = ".".join(str(part) for part in exc.absolute_path) or "(root)"
        raise MappingError(f"{where}: {exc.message}") from exc
    spec = document["spec"]

    for section in ("accounts", "contacts"):
        for name, path in spec[section].items():
            if name == "consent":
                continue
            check_path(f"{section}.{name}", str(path))
    if spec["transport"]["kind"] == "http":
        check_base_url(spec["transport"]["base_url"])
    consent_field = (spec["contacts"].get("consent") or {}).get("field")
    if consent_field:
        check_path("contacts.consent.field", str(consent_field))

    consent = spec["contacts"].get("consent")
    if consent and consent.get("default") == "allowed" and not consent.get("values"):
        # Not forbidden, but it means "treat everyone as contactable", and it
        # must be a sentence somebody wrote rather than a shape they fell into.
        raise MappingError(
            "contacts.consent declares default 'allowed' with no value map, which "
            "marks every contact contactable regardless of what the CRM says; "
            "map the states explicitly or omit the block and let consent be unknown")
    return document


def load(path: str | Path) -> dict[str, Any]:
    """Load and validate a mapping file."""
    document = yaml.safe_load(Path(path).read_text())
    if not isinstance(document, dict):
        raise MappingError(f"{path}: expected a mapping at the document root")
    return validate(document)


# The name this rule had when consent was the only thing it translated.
ConsentRule = TranslationRule
