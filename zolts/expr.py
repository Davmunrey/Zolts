"""Restricted expression evaluation for DSL `where` and `when` clauses.

Claim under test (docs/04): the DSL has no Turing-complete expressions. An
arbitrary executable DSL blocks static policy analysis and turns the engine
into an unsafe interpreter running customer-authored code on shared
infrastructure.

Implementation: parse with `ast`, walk the tree, reject any node outside the
allowlist. Rejection is by node type, never by pattern matching on source
text — a blocklist of dangerous substrings is trivially bypassed.

Dotted field paths (`payload.limit`, `outcome.type`) are needed by real
programs, so `Attribute` is permitted under three constraints that together
close the sandbox-escape route: the path must root in a plain name, no
segment may start with an underscore, and depth is bounded. Every known
escape (`().__class__.__bases__`, `x.__globals__`) fails at least one.
"""

from __future__ import annotations

import ast
from typing import Any

_ALLOWED_NODES = (
    ast.Expression, ast.BoolOp, ast.UnaryOp, ast.Compare, ast.Name, ast.Load,
    ast.Constant, ast.Tuple, ast.List, ast.Attribute, ast.BinOp,
    ast.And, ast.Or, ast.Not, ast.USub, ast.UAdd,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn,
    # Arithmetic is needed for computed thresholds such as
    # `payload.days_since_purchase >= payload.expected_cycle_days * 0.85`.
    # Pow is deliberately absent: `2 ** 999999999` exhausts memory on a shared
    # worker, so it is a denial-of-service vector rather than a feature.
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod,
)

_MAX_PATH_DEPTH = 3


class _Absent:
    """A field the variable mapping does not provide.

    Every comparison returns False, including `!=`. An expression cannot
    conclude anything about a value it does not have, and the safe conclusion
    is "do not act" — the same default-deny posture the policy engine takes.
    Returning None here instead would raise TypeError inside a comparison and
    take down a running program.
    """

    __slots__ = ()

    def __lt__(self, other): return False
    def __le__(self, other): return False
    def __gt__(self, other): return False
    def __ge__(self, other): return False
    def __eq__(self, other): return False
    def __ne__(self, other): return False
    def __contains__(self, other): return False
    def __hash__(self): return 0
    def __bool__(self): return False
    def __repr__(self): return "<absent>"


ABSENT = _Absent()
_MISSING = object()


class UnsafeExpression(ValueError):
    """Raised when an expression uses a construct outside the allowlist."""


def _path_of(node: ast.Attribute) -> list[str]:
    """Flatten an attribute chain, rejecting anything that is not a field path."""
    segments: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        if current.attr.startswith("_"):
            raise UnsafeExpression(
                f"attribute '{current.attr}' is not permitted: underscore-prefixed "
                "attributes are the sandbox-escape route"
            )
        segments.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        raise UnsafeExpression(
            "a dotted path must root in a plain name, not an expression"
        )
    segments.append(current.id)
    segments.reverse()
    if len(segments) > _MAX_PATH_DEPTH:
        raise UnsafeExpression(
            f"path '{'.'.join(segments)}' exceeds depth {_MAX_PATH_DEPTH}: "
            "expressions read fields, they do not traverse object graphs"
        )
    return segments


def validate(source: str) -> ast.Expression:
    """Parse and statically check an expression. Raises on anything unsafe."""
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError as exc:
        raise UnsafeExpression(f"cannot parse: {exc}") from exc

    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise UnsafeExpression(
                f"{type(node).__name__} is not permitted in a DSL expression"
            )
        if isinstance(node, ast.Attribute):
            _path_of(node)
    return tree


def names(source: str) -> set[str]:
    """Field paths an expression reads, dotted.

    Used to check a clause against the tenant schema before publishing, so a
    typo fails at lint time rather than silently making a clause false at
    3 a.m.
    """
    tree = validate(source)
    found: set[str] = set()
    covered: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            for child in ast.walk(node):
                if child is not node:
                    covered.add(id(child))
            found.add(".".join(_path_of(node)))
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and id(node) not in covered:
            found.add(node.id)
    return found


def resolve(path: str, variables: dict[str, Any]) -> Any:
    """Resolve a dotted path against nested dicts, then against a flat key."""
    if path in variables:
        return variables[path]
    current: Any = variables
    for segment in path.split("."):
        if isinstance(current, dict) and segment in current:
            current = current[segment]
        else:
            return _MISSING
    return current


class _Scope(dict):
    """Exposes dotted paths to `eval` as attribute access on a namespace object."""

    def __init__(self, variables: dict[str, Any], paths: set[str]):
        super().__init__()
        self["__builtins__"] = {}
        roots: dict[str, Any] = {}
        for path in paths:
            value = resolve(path, variables)
            value = ABSENT if value is _MISSING else value
            root, _, rest = path.partition(".")
            if rest:
                roots.setdefault(root, {})[rest] = value
            else:
                self[root] = value
        for root, fields in roots.items():
            if root not in self:
                self[root] = _Namespace(fields)


class _Namespace:
    def __init__(self, fields: dict[str, Any]):
        for key, value in fields.items():
            head, _, tail = key.partition(".")
            if tail:
                setattr(self, head, _Namespace({tail: value}))
            else:
                setattr(self, head, value)

    def __getattr__(self, name: str) -> Any:
        # Any field the fixture did not provide is absent, not an AttributeError.
        return ABSENT


def evaluate(source: str, variables: dict[str, Any]) -> bool:
    """Evaluate a validated expression against a variable mapping.

    An unresolved path evaluates to ABSENT, whose comparisons are all False:
    an absent signal payload field makes a clause false rather than crashing a
    running program. Genuine typos are caught by `names()` at lint time.
    """
    tree = validate(source)
    scope = _Scope(variables, names(source))
    return bool(eval(compile(tree, "<dsl>", "eval"), scope))  # noqa: S307
