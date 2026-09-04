#!/usr/bin/env python3
"""Valida los programas GTM contra el JSON Schema de Zolts.

Uso: python3 scripts/validate.py [ruta_glob]
Requiere: pyyaml, jsonschema
"""
import glob
import json
import sys

import jsonschema
import yaml

SCHEMA = "examples/schema/zolts-program.schema.json"


def main(pattern: str = "examples/programs/*.yaml") -> int:
    validator = jsonschema.Draft202012Validator(json.load(open(SCHEMA)))
    failed = 0
    for path in sorted(glob.glob(pattern)):
        errors = sorted(validator.iter_errors(yaml.safe_load(open(path))), key=lambda e: list(e.path))
        print(("OK   " if not errors else "FAIL "), path)
        for err in errors:
            failed = 1
            print("     ", list(err.path), err.message)
    return failed


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
