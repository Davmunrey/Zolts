#!/usr/bin/env python3
"""Validate GTM programs and blueprints against their JSON Schemas.

Usage: python3 scripts/validate.py
Requires: pyyaml, jsonschema
"""
import glob
import json
import sys

import jsonschema
import yaml

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


def main() -> int:
    return max(_validate(schema, pattern) for schema, pattern in TARGETS)


if __name__ == "__main__":
    sys.exit(main())
