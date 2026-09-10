"""What a generated message may offer, and the ceiling nothing read.

`blueprints/ecommerce-dtc.yaml` declares `discount_authority: {max_pct: 15}`.
The name appeared in the blueprint schema, that file, `zolts/controls.py` and
a test asserting it was unenforced — and nowhere under `runtime/`.
`runtime/agents/copywriter.py` had no reference to a blueprint at all (D-85).

Provenance already asks whether a claim is *true*: every statement must map to
a retrieved span, a CRM field or the proof library. This asks a different
question — whether we are *allowed to say it* — and no amount of evidence makes
an unauthorised offer permissible. A message can cite its source perfectly and
still commit the seller to terms the archetype forbids, which is a commercial
exposure rather than a factual one: an offer binds.
"""

from __future__ import annotations

import pytest

from zolts import offers
from zolts.blueprint import load_blueprints
from zolts.offers import Authority

FIFTEEN = Authority(max_pct=15)


# -- what counts as an offer --------------------------------------------------

@pytest.mark.parametrize("message", [
    "Get 20% off your next order.",
    "Take 20 per cent off before Friday.",
    "Would you like 20% off?",
    "A 15,5% discount is yours.",
    "Here is a 20% discount on the annual plan.",
])
def test_an_offer_above_the_ceiling_needs_a_person(message):
    assert offers.exceeding(message, FIFTEEN), message


@pytest.mark.parametrize("message", [
    "20% of our customers renew early.",
    "We grew 30% last year.",
    "Teams like yours cut onboarding by 40%.",
])
def test_a_number_with_no_offer_in_it_passes(message):
    """The check must not fire on a fact. A percentage is a claim about the
    world until a word turns it into a commitment, and demanding review for
    every figure would empty this check's credibility — an operator who is
    asked to approve arithmetic learns to approve everything."""
    assert offers.exceeding(message, FIFTEEN) == [], message


def test_a_saving_that_is_not_money_is_not_an_offer():
    """`save 3 hours a week` has the cue and no money in it."""
    assert offers.exceeding("Save 3 hours a week on reporting.", FIFTEEN) == []


@pytest.mark.parametrize("message", [
    "Get 15% off your next order.",
    "Here is 10% off.",
    "Save 7.5% on the annual plan.",
])
def test_an_offer_within_the_ceiling_sends(message):
    assert offers.exceeding(message, FIFTEEN) == [], message


def test_the_boundary_is_permitted_and_not_refused():
    """Exactly the declared ceiling is what the archetype allows. Refusing it
    would make `max_pct: 15` mean fourteen."""
    assert offers.exceeding("Take 15% off.", FIFTEEN) == []
    assert offers.exceeding("Take 15.01% off.", FIFTEEN)


# -- the decimal separator ----------------------------------------------------

@pytest.mark.parametrize("text,value", [
    ("15,5", 15.5), ("15.5", 15.5), ("1.234,56", 1234.56), ("1,234.56", 1234.56),
    ("1,234", 1234.0), ("500", 500.0), ("20", 20.0),
])
def test_a_number_reads_the_same_on_both_sides_of_europe(text, value):
    """`1,5` is one and a half in half of Europe and fifteen in the other half,
    and reading it as fifteen is the direction that sends the offer out. A
    separator leaving one or two digits behind it is the decimal one; anything
    else is grouping, because a group is three digits."""
    assert offers._number(text) == pytest.approx(value)


def test_the_separator_rule_changes_a_verdict():
    """Not arithmetic for its own sake: `15,5%` is over a ceiling of fifteen
    and `15.5` read as `155` or `15` would answer differently."""
    assert offers.exceeding("Take 15,5% off.", FIFTEEN)
    assert offers.exceeding("Take 1,5% off.", FIFTEEN) == []


# -- a currency amount against a percentage ceiling ---------------------------

@pytest.mark.parametrize("message", [
    "Take €500 off the annual plan.",
    "Here is $50 off.",
    "A £25 discount is yours.",
    "We will refund 200 EUR.",
])
def test_a_currency_offer_cannot_be_judged_and_so_needs_a_person(message):
    """`€500 off` cannot be compared with `max_pct: 15` without an order value
    nobody supplied. Passing what cannot be judged is the flattering
    direction, and this repository's whole register is that direction's
    consequences. Adding `max_amount` makes it comparable; that is registered
    as the alternative (decision 52) rather than assumed."""
    over = offers.exceeding(message, FIFTEEN)
    assert over and over[0].kind == offers.AMOUNT, message


def test_the_reason_says_what_the_reviewer_has_to_decide():
    over = offers.exceeding("Get 20% off.", FIFTEEN)
    assert offers.reason(over, FIFTEEN) == "offers 20% against an authority of 15%"
    money = offers.exceeding("Get €500 off.", FIFTEEN)
    assert "cannot judge" in offers.reason(money, FIFTEEN)


# -- silence is not a ceiling of zero -----------------------------------------

def test_a_blueprint_that_declares_nothing_permits_everything():
    """Reading an absent authority as zero would send every message mentioning
    a discount to a person, on an archetype that never asked for that."""
    assert offers.Authority.from_policy({}) is None
    assert offers.Authority.from_policy(None) is None
    assert offers.exceeding("Get 90% off!", None) == []


def test_an_authority_declaring_no_ceiling_permits_everything():
    empty = offers.Authority.from_policy({"discount_authority": {}})
    assert empty is not None and not empty.declares_anything
    assert offers.exceeding("Get 90% off!", empty) == []


def test_the_shipped_blueprint_is_the_one_that_declares_a_ceiling():
    """The premise the wiring rests on, asserted rather than remembered: if the
    archetype stops declaring one, these tests are about nothing."""
    declaring = {b.key: offers.Authority.from_policy(b.policy)
                 for b in load_blueprints()
                 if (offers.Authority.from_policy(b.policy) or Authority()).declares_anything}
    assert declaring, "no shipped blueprint declares a discount authority any more"
    assert declaring["ecommerce-dtc"].max_pct == 15


# -- an offer inside a question is still an offer -----------------------------

def test_an_offer_in_a_question_is_not_missed():
    """`provenance.split_claims` returns every sentence, which is why this
    module splits with it rather than filtering to assertive claims first: a
    question is not a claim and *would you like 20% off?* is still an offer."""
    assert offers.exceeding("Would you like 20% off?", FIFTEEN)


def test_every_offer_in_a_message_is_reported_not_only_the_first():
    over = offers.exceeding(
        "Take 20% off today. And 30% off next month.", FIFTEEN)
    assert [o.value for o in over] == [20.0, 30.0]


# -- the gate that stops the send ---------------------------------------------

# The offer is *evidenced*, which is the whole point. An offer with no
# provenance is already stopped, one layer earlier, by the check that a
# quantified claim must cite a source — so a fixture without this proof line
# would pass for provenance's reason and prove nothing about this check. The
# case that matters is a message that cites its source perfectly and still
# commits the seller to terms the archetype forbids.
OFFER_PROOF = "Q4 promotion: 25% off the annual plan, and 10% off the monthly one."
OVER_DRAFT = ("Hi Dana. Northwind opened four RevOps roles last quarter. Take 25% off "
              "the annual plan if you decide this week. Reply unsubscribe and I will stop.")
WITHIN_DRAFT = OVER_DRAFT.replace("25% off the annual", "10% off the monthly")


@pytest.fixture(scope="module")
def guard():
    from runtime.agents.spend import SpendGuard

    g = SpendGuard()
    yield g
    g.close()


def _draft(text, authority, guard):
    from runtime.agents import copywriter
    from tests.test_agents import OPT_OUT, StubClient
    from zolts.provenance import Evidence, Source

    evidence = [
        Evidence(Source.RETRIEVED, "Northwind opened four RevOps roles last quarter.",
                 "li#1"),
        Evidence(Source.PROOF, OFFER_PROOF, "proof.q4"),
    ]
    return copywriter.draft(
        client=StubClient(text=text), guard=guard, evidence=evidence, tier="t2",
        opt_out=OPT_OUT, policy_allows=True, tenant_enabled=True,
        consumed_usd=1.0, limit_usd=40.0, threshold=0.0, authority=authority)


@pytest.mark.skipif(__import__("shutil").which("trazum-mcp") is None,
                    reason="trazum-mcp is not installed")
def test_an_over_generous_message_never_auto_sends(guard):
    """The consequence, as the seller meets it. Unlike a wrong fact, which
    provenance strips before anybody reads it, an offer binds: a message
    promising 25% under a 15% authority is a commitment the company made."""
    within = _draft(WITHIN_DRAFT, FIFTEEN, guard)
    assert within.gate.auto_send, (
        f"the premise: this message is otherwise fit to send, so the only thing "
        f"that changes below is the size of the offer. Stopped by "
        f"{within.gate.reason!r} instead")

    over = _draft(OVER_DRAFT, FIFTEEN, guard)
    assert not over.gate.auto_send
    assert "discount authority" in over.gate.reason
    assert "25%" in over.gate.reason and "15%" in over.gate.reason
    assert over.state == "needs_human"


@pytest.mark.skipif(__import__("shutil").which("trazum-mcp") is None,
                    reason="trazum-mcp is not installed")
def test_the_offer_is_not_deleted_from_the_message(guard):
    """An unsupported claim is dropped; an unauthorised offer is not. Deleting
    it and sending the rest is exactly the case `zolts/provenance.py` sends to
    a person instead — and a reviewer needs to see what they are deciding
    about."""
    over = _draft(OVER_DRAFT, FIFTEEN, guard)
    assert "25%" in over.text, "the reviewer has to see the offer to judge it"
    content = over.as_content()
    assert any("25%" in sentence for sentence in content["unauthorised_offers"])


@pytest.mark.skipif(__import__("shutil").which("trazum-mcp") is None,
                    reason="trazum-mcp is not installed")
def test_a_tenant_whose_archetype_sets_no_ceiling_is_unaffected(guard):
    """Most blueprints declare nothing. Reading that as a ceiling would stop
    their messages on a rule nobody wrote."""
    over = _draft(OVER_DRAFT, None, guard)
    assert over.gate.auto_send
    assert over.as_content()["unauthorised_offers"] == []


# -- the wiring, from the tenant's archetype to the gate ----------------------

@pytest.mark.skipif(__import__("shutil").which("trazum-mcp") is None,
                    reason="trazum-mcp is not installed")
@pytest.mark.db
def test_the_authority_reaches_generation_from_the_tenants_blueprint(db, guard):
    """The half a unit test cannot reach.

    Everything above calls `copywriter.draft` with an authority in hand. That
    proves the check works and says nothing about whether anything supplies
    one — the third defect shape in `docs/22`: a guard correct, tested, and not
    on the path. A mutant passing `authority=None` from `generate.run` survived
    every other test in this file until this one existed.
    """
    import uuid

    from runtime.engine import generate
    from runtime.provision import create_tenant

    shop = create_tenant(db, name="Shop", slug=f"shop-{uuid.uuid4().hex[:8]}",
                         region="eu", blueprint_id="ecommerce-dtc")
    sales = create_tenant(db, name="Sales", slug=f"sales-{uuid.uuid4().hex[:8]}",
                          region="eu", blueprint_id="b2b-saas-sales-led")

    with db.tenant_tx(str(shop["id"])) as cur:
        cur.execute("select blueprint_id from tenant where id = %s", (str(shop["id"]),))
        assert cur.fetchone()["blueprint_id"] == "ecommerce-dtc", (
            "the premise: this tenant's archetype is the one declaring a ceiling")
        assert generate._authority(cur, str(shop["id"])) == Authority(max_pct=15.0), (
            "the blueprint's ceiling has to reach the generation path, or the "
            "check this file tests is correct and unreachable")

    with db.tenant_tx(str(sales["id"])) as cur:
        assert generate._authority(cur, str(sales["id"])) is None, (
            "an archetype declaring no ceiling supplies none, rather than zero")


def test_the_generation_path_passes_the_authority_it_resolved():
    """The call site, not the helper.

    The test above proves `_authority` reads the blueprint. It passed with
    `generate.run` handing `authority=None` to the copywriter, because a
    helper nothing calls is still a correct helper — the same defect one level
    in. This reads the source and fails unless the value the copywriter gets
    is the one the helper produced.
    """
    import ast
    from pathlib import Path

    tree = ast.parse((Path(__file__).resolve().parent.parent
                      / "runtime" / "engine" / "generate.py").read_text())
    run = next(n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "run")
    call = next(n for n in ast.walk(run)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute) and n.func.attr == "draft")
    passed = {kw.arg: kw.value for kw in call.keywords}
    assert "authority" in passed, "the copywriter is drafting with no authority at all"
    value = passed["authority"]
    assert isinstance(value, ast.Call) and getattr(value.func, "id", None) == "_authority", (
        "the authority passed to the copywriter is not the one read from the "
        f"tenant's blueprint: {ast.dump(value)}")
