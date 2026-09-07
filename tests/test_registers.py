"""The registers are documents that count things, and nothing counted them.

`docs/22` opened with a table saying how each defect was found: 16 executed,
3 read. Counting the rows gives 14 and 8. The number was written once and
never re-measurable, which is the exact defect the register exists to
catalogue — committed by the register.

Four rows had also grown a sixth cell under a five-column header, so the
guard each of them names rendered as nothing at all. A markdown table drops
what does not fit and says nothing.

These tests make the documents measurable. The cost of keeping them true is
one edit in the pull request that changes them.
"""

from __future__ import annotations

import collections
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEFECTS = ROOT / "docs" / "22-defect-register.md"
DECISIONS = ROOT / "docs" / "18-decision-register.md"
ARCHITECTURE = ROOT / "docs" / "02-architecture.md"

# `str \| None` inside a cell is an escaped pipe, not a column boundary.
CELL = re.compile(r"(?<!\\)\|")

WORDS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9,
}


def _numeral(word: str) -> int:
    parts = word.lower().split("-")
    return sum(WORDS[part] for part in parts)


def _rows(document: Path) -> list[dict[str, str]]:
    """Every register row, read under the header of its own table."""
    headers: list[str] = []
    out: list[dict[str, str]] = []
    for line in document.read_text().splitlines():
        if line.startswith("| ID |"):
            headers = [c.strip() for c in CELL.split(line.strip().strip("|"))]
        elif line.startswith("| D-"):
            cells = [c.strip() for c in CELL.split(line.strip().strip("|"))]
            assert headers, f"{cells[0]} appears before any table header"
            assert len(cells) == len(headers), (
                f"{cells[0]} has {len(cells)} cells under a {len(headers)}-column "
                f"header ({headers}). Markdown drops the overflow, so whatever "
                f"was written in the extra cell is invisible")
            out.append(dict(zip(headers, cells)))
    return out


def test_every_defect_has_an_id_and_no_id_is_used_twice():
    ids = [row["ID"] for row in _rows(DEFECTS)]
    duplicated = [i for i, n in collections.Counter(ids).items() if n > 1]
    assert not duplicated, f"duplicate defect ids: {duplicated}"
    numbers = sorted(int(i.removeprefix("D-")) for i in ids)
    assert numbers == list(range(1, len(numbers) + 1)), (
        f"the defect numbering has a gap or an extra: {numbers}")


def test_the_finding_method_table_is_a_count_and_not_a_memory():
    """The claim that motivated this file.

    A summary that cannot be re-derived drifts in whichever direction
    flatters the author, and this one drifted five rows towards `executed`.
    """
    rows = _rows(DEFECTS)
    measured = collections.Counter(row["Found"] for row in rows)
    text = DEFECTS.read_text()
    claimed = {method: int(count) for method, count
               in re.findall(r"\| \*\*(\w+)\*\* \| (\d+) \|", text)}
    assert claimed == dict(measured), (
        f"the register claims {claimed} and its rows say {dict(measured)}. "
        f"The document is corrected, never the measurement")


def test_the_prose_total_matches_the_rows():
    text = DEFECTS.read_text()
    stated = re.search(r"of the ([a-z]+(?:-[a-z]+)?) are one defect", text)
    assert stated, "the register no longer states how many defects it holds"
    assert _numeral(stated.group(1)) == len(_rows(DEFECTS))


def test_every_defect_states_how_it_was_found():
    for row in _rows(DEFECTS):
        assert row["Found"] in {"Executed", "Read", "Mutated", "Looked", "CI"}, (
            f"{row['ID']} was found by '{row['Found']}', which is not one of the "
            f"five ways this register counts")


# -- what the documents cite must exist -----------------------------------

def _cited(pattern: str) -> dict[int, set[str]]:
    cited: dict[int, set[str]] = {}
    sources = list((ROOT / "docs").glob("*.md")) + [
        path for path in ROOT.rglob("*.py")
        if "__pycache__" not in str(path) and ".git" not in str(path)]
    for path in sources:
        for match in re.finditer(pattern, path.read_text(errors="ignore")):
            for group in match.groups():
                if group:
                    cited.setdefault(int(group), set()).add(str(path.relative_to(ROOT)))
    return cited


def test_every_adr_cited_anywhere_exists():
    """A dangling `See ADR-034` is a promise the reader cannot check."""
    defined = {int(m.group(1)) for m
               in re.finditer(r"\*\*ADR-(\d{3})\b", ARCHITECTURE.read_text())}
    assert defined, "no ADRs parsed out of docs/02"
    missing = {n: sorted(where) for n, where in _cited(r"ADR-(\d{3})").items()
               if n not in defined}
    assert not missing, f"cited but never written: {missing}"


def test_every_numbered_decision_cited_anywhere_exists():
    """Code comments cite decisions by number. A number nothing defines is
    worse than no citation: it reads as a settled argument."""
    numbered = {int(m.group(1)) for m
                in re.finditer(r"^\|\s*(\d+)\s*\|", DECISIONS.read_text(), re.M)}
    assert numbered, "no numbered decisions parsed out of docs/18"
    missing = {n: sorted(where) for n, where
               in _cited(r"[Dd]ecisions? (\d{1,3})(?: and (\d{1,3}))?").items()
               if n not in numbered}
    assert not missing, f"cited but never registered: {missing}"


@pytest.mark.parametrize("register", [DEFECTS, DECISIONS])
def test_the_registers_are_in_english(register: Path):
    """The language policy is a hiring and diligence decision, not a style
    one. These two documents are the ones a buyer reads first."""
    spanish = re.findall(r"\b(?:para|porque|cuando|desde|hasta|sobre|entre)\b",
                         register.read_text().lower())
    assert not spanish, f"{register.name} contains Spanish: {sorted(set(spanish))}"
