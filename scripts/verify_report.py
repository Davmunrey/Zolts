#!/usr/bin/env python3
"""Verify an exported incrementality report on a machine that has never seen
the database.

    python3 scripts/verify_report.py report.json --public-key <base64>
    python3 scripts/verify_report.py report.json --keys-url https://zolts.example/.well-known/zolts-signing-keys.json
    python3 scripts/verify_report.py report.json

The first two pin the instance's key; the third uses the key the document
carries and says so, which proves the document was not altered after it was
signed and nothing about who signed it. Exit 0 when every check passes,
1 when one fails, 2 when the document cannot be read.

Imports `zolts` alone: no runtime, no database, no network unless `--keys-url`
asks for one.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from zolts import reportsig  # noqa: E402


def _key_from_url(url: str, key_id: str | None) -> str | None:
    with urllib.request.urlopen(url, timeout=10) as response:
        published = json.loads(response.read().decode("utf-8"))
    for entry in published.get("keys", []):
        if entry.get("key_id") == key_id:
            return str(entry.get("public_key"))
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("document", help="the exported report, JSON")
    parser.add_argument("--public-key", help="the instance's public key, base64")
    parser.add_argument("--keys-url", help="where the instance publishes its keys")
    args = parser.parse_args(argv)

    try:
        doc = json.loads(Path(args.document).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"cannot read {args.document}: {exc}", file=sys.stderr)
        return 2

    public_key = args.public_key
    if public_key is None and args.keys_url:
        wanted = (doc.get("signature") or {}).get("key_id")
        public_key = _key_from_url(args.keys_url, wanted)
        if public_key is None:
            print(f"{args.keys_url} publishes no key {wanted}", file=sys.stderr)
            return 1

    verdict = reportsig.verify(doc, public_key)
    program = doc.get("program") or {}
    period = doc.get("period") or {}
    print(f"{program.get('key')} v{program.get('version')}  "
          f"{period.get('start')} → {period.get('end')}  verdict: {doc.get('verdict')}")
    print(f"digest     {doc.get('digest')}")
    for line in verdict.lines():
        print(line)
    return 0 if verdict.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
