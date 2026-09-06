#!/usr/bin/env python3
"""Validate GTM programs, blueprints and CRM mappings against their schemas.

Usage: python3 scripts/validate.py
Requires: pyyaml, jsonschema
"""
import glob
import json
import sys
from pathlib import Path

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parent.parent

TARGETS = (
    ("examples/schema/zolts-program.schema.json", "examples/programs/*.yaml"),
    ("examples/schema/zolts-blueprint.schema.json", "blueprints/*.yaml"),
)


def _validate(schema_path: str, pattern: str) -> int:
    validator = jsonschema.Draft202012Validator(json.load(open(schema_path)))
    failed = 0
    for path in sorted(glob.glob(pattern)):
        errors = sorted(validator.iter_errors(yaml.safe_load(open(path))), key=lambda e: list(e.path))
        print(("OK   " if not errors else "FAIL "), path)
        for err in errors:
            failed = 1
            print("     ", list(err.path), err.message)
    return failed


def validate_mappings() -> int:
    """CRM mappings are customer-authored documents that decide who gets
    contacted. They are checked here for the same reason programs are."""
    sys.path.insert(0, str(ROOT))
    from zolts.mapping import MappingError, load

    failed = 0
    for path in sorted((ROOT / "examples" / "crm").glob("*.yaml")):
        name = path.relative_to(ROOT)
        try:
            document = load(path)
        except MappingError as exc:
            failed = 1
            print("FAIL ", name)
            print("     ", exc)
        else:
            consent = document["spec"].get("contacts", {}).get("consent")
            note = "" if consent else "   (declares no consent field: opt-out state is unknown)"
            print("OK   ", name, note)
    return failed


def validate_providers() -> int:
    """A provider document decides which endpoint gets a credential and where
    the data budget goes. It is checked here for the same reason a CRM mapping
    is."""
    sys.path.insert(0, str(ROOT))
    from runtime.connectors.declarative_provider import validate

    failed = 0
    for path in sorted((ROOT / "examples" / "providers").glob("*.yaml")):
        name = path.relative_to(ROOT)
        try:
            document = validate(yaml.safe_load(open(path)))
        except Exception as exc:  # noqa: BLE001 — a bad document is a bad document
            failed = 1
            print("FAIL ", name)
            print("     ", exc)
        else:
            fields = ", ".join(sorted(document["spec"]["lookups"]))
            print("OK   ", name, f"  (resolves: {fields})")
    return failed


def main() -> int:
    schemas = max(_validate(schema, pattern) for schema, pattern in TARGETS)
    return max(schemas, validate_mappings(), validate_providers())


if __name__ == "__main__":
    sys.exit(main())
