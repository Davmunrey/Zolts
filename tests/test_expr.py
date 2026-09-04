"""The DSL expression subset must be safe by construction."""

import pytest

from zolts.expr import UnsafeExpression, evaluate, names, validate


def test_comparisons_evaluate():
    assert evaluate("score >= 80", {"score": 88})
    assert not evaluate("score >= 80", {"score": 55})


def test_boolean_composition():
    variables = {"score": 88, "country": "ES"}
    assert evaluate("score >= 80 and country == 'ES'", variables)
    assert not evaluate("score >= 80 and country == 'DE'", variables)


def test_membership():
    assert evaluate("stage in ('series_a', 'series_b')", {"stage": "series_a"})
    assert not evaluate("stage in ('series_a', 'series_b')", {"stage": "seed"})


def test_unknown_name_is_falsy_not_fatal():
    """An absent payload field makes the clause false; it must not crash a run."""
    assert not evaluate("missing_field == 'x'", {})


def test_names_are_extracted_for_lint_time_checking():
    assert names("score >= 80 and country == 'ES'") == {"score", "country"}


@pytest.mark.parametrize("source", [
    "__import__('os').system('id')",
    "open('/etc/passwd').read()",
    "().__class__.__bases__",
    "[x for x in range(10)]",
    "lambda: 1",
    "score.__class__",
    "exec('x=1')",
    "globals()",
    "data['key']",
    "score if x else y",
])
def test_dangerous_constructs_are_rejected(source):
    """Rejection is by AST node type, not by string pattern: a blocklist of
    dangerous substrings is trivially bypassed."""
    with pytest.raises(UnsafeExpression):
        validate(source)


def test_assignment_is_not_parseable_as_an_expression():
    with pytest.raises(UnsafeExpression):
        validate("score = 100")


def test_shipped_program_clauses_all_validate():
    """Every `where`/`when` clause in the examples must be inside the subset."""
    from pathlib import Path

    import yaml

    clauses = []
    for path in Path("examples/programs").glob("*.yaml"):
        spec = yaml.safe_load(path.read_text())["spec"]
        clauses += [e["where"] for e in spec["trigger"]["events"] if "where" in e]
        clauses += [t["when"] for t in spec["route"]["tiers"]]
        clauses += [x["when"] for x in spec["exit"]]
    assert clauses
    for clause in clauses:
        validate(clause)


# --- Dotted field paths: needed by real programs, and the sandbox-escape route
# if left unconstrained.

def test_dotted_path_resolves_from_nested_variables():
    assert evaluate("payload.pct >= 0.8", {"payload": {"pct": 0.9}})
    assert not evaluate("payload.pct >= 0.8", {"payload": {"pct": 0.5}})


def test_dotted_path_resolves_from_a_flat_key():
    assert evaluate("outcome.type == 'meeting'", {"outcome.type": "meeting"})


def test_absent_dotted_field_is_falsy():
    assert not evaluate("payload.missing == 'x'", {"payload": {}})


def test_names_returns_dotted_paths():
    assert names("payload.limit in ('seats',) and score >= 10") == {"payload.limit", "score"}


@pytest.mark.parametrize("source", [
    "score.__class__",
    "score.__class__.__bases__",
    "().__class__.__bases__",
    "func.__globals__",
    "a.b.c.d.e",
])
def test_attribute_escapes_are_rejected(source):
    with pytest.raises(UnsafeExpression):
        validate(source)


# --- Arithmetic: required for computed thresholds, bounded to exclude DoS.

def test_computed_threshold_evaluates():
    """The real clause from 03-ecommerce-dtc."""
    variables = {"payload": {"days_since_purchase": 40, "expected_cycle_days": 45}}
    assert evaluate(
        "payload.days_since_purchase >= payload.expected_cycle_days * 0.85", variables
    )
    variables["payload"]["days_since_purchase"] = 20
    assert not evaluate(
        "payload.days_since_purchase >= payload.expected_cycle_days * 0.85", variables
    )


def test_negative_literals_work():
    """The real clause from 04-local-services-multisite."""
    assert evaluate("payload.pct_change_30d <= -0.35", {"payload": {"pct_change_30d": -0.5}})
    assert not evaluate("payload.pct_change_30d <= -0.35", {"payload": {"pct_change_30d": -0.1}})


def test_exponentiation_is_rejected_as_a_denial_of_service_vector():
    """`2 ** 999999999` exhausts memory on a shared worker. Arithmetic is a
    feature; unbounded exponentiation is an outage."""
    with pytest.raises(UnsafeExpression):
        validate("2 ** 999999999")


def test_assignment_operator_is_rejected():
    """Regression guard: `outcome.type = 'x'` shipped in four example programs.
    A single `=` never parses as a comparison, so the exit rule could never
    fire and opt-outs would never have been recorded."""
    with pytest.raises(UnsafeExpression):
        validate("outcome.type = 'unsubscribe'")
    assert evaluate("outcome.type == 'unsubscribe'", {"outcome": {"type": "unsubscribe"}})
