"""Buying a missing field, and learning which provider to ask first.

`zolts/waterfall.py` proved the ordering arithmetic from the day the reference
core existed, and nothing in the runtime imported it — the fourth time this
repository has found a complete specification with no caller. Six of the eight
actions `docs/12` prices could not be executed, three of them enrichment, and
they are the bulk of the consumption the pricing model assumes.

So these tests are not about the optimiser, which was already right. They are
about whether a field is actually bought, in the order it says, at the price
the document names, with a basis a DPO can read.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from runtime import enrichment
from runtime.connectors import dataprovider
from runtime.connectors.dataprovider import Lookup, cohort_of, unresolved
from runtime.connectors.fake import FakeDataProvider
from tests.conftest import requires_app_role, SECRET, requires_db
from tests.test_runtime_engine import _account_with_contact


@pytest.fixture(autouse=True)
def _clean_registry():
    """Providers register globally; a leak between tests is a false pass."""
    saved = dict(dataprovider._SOURCES)
    dataprovider._SOURCES.clear()
    yield
    dataprovider._SOURCES.clear()
    dataprovider._SOURCES.update(saved)


def _register(cur, key, *, fields=("email",), cost_micros=30_000, accuracy=0.9,
              hit_rate=0.3, billed_on_miss=False, hits=True, error_times=0):
    """Register a provider in the database and in the process."""
    source = FakeDataProvider(key=key, fields=tuple(fields), hits=hits,
                              cost_micros=cost_micros, error_times=error_times)
    dataprovider.register_provider(source)
    cur.execute(
        "insert into data_provider (tenant_id, key, fields, unit_cost_micros,"
        " accuracy, default_hit_rate, billed_on_miss) values"
        " (zolts_internal.current_tenant(), %s,%s,%s,%s,%s,%s) returning *",
        (key, list(fields), cost_micros, accuracy, hit_rate, billed_on_miss))
    cur.fetchone()
    return source


def _person(cur, tid, *, email=None):
    from runtime.repo import entities

    return entities.upsert_person(
        cur, tid, email=email, country="ES", full_name="Dana Cruz",
        consent_state={"email": {"basis": "legitimate_interest"}})


def _tenant_row(cur, tid):
    cur.execute("select * from tenant where id = %s", (tid,))
    return cur.fetchone()


# -- the cohort, and what is worth paying for ----------------------------

def test_a_cohort_is_coarse_on_purpose():
    """A matrix keyed finely enough to be interesting never accumulates enough
    calls per cell to mean anything."""
    assert cohort_of({"country": "ES", "employee_band": "51-200"}) == "es_51-200"
    assert cohort_of({"country": "ES"}) == "es_xx"
    assert cohort_of(None) == "xx_xx"
    assert cohort_of({}) == "xx_xx"


def test_a_field_already_present_is_not_bought_again():
    """The cheapest provider is the one you do not call."""
    assert unresolved({"email": None}, "email")
    assert not unresolved({"email": "dana@acme.com"}, "email")
    assert unresolved({"employee_band": "51-200"}, "firmographics"), (
        "firmographics needs both the band and the industry")
    assert not unresolved({"employee_band": "51-200", "industry_code": "62.01"},
                          "firmographics")
    with pytest.raises(ValueError, match="not a priced field"):
        unresolved({}, "favourite_colour")


# -- the order the waterfall chooses -------------------------------------

@requires_db
def test_the_cheap_provider_is_asked_first_and_the_dear_one_is_not_asked(db, tenant):
    """Reordering changes cost and never coverage, so the whole saving is cost
    reduction — which is simultaneously the value proposition and the margin."""
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        cheap = _register(cur, "cheap", cost_micros=10_000, hit_rate=0.6)
        dear = _register(cur, "premium", cost_micros=200_000, hit_rate=0.9)
        person = _person(cur, tid)
        result = enrichment.resolve(
            cur, _tenant_row(cur, tid), field_name="email", entity=dict(person),
            legal_basis="legitimate_interest")

    assert result.hit
    assert result.provider == "cheap"
    assert result.attempts == ("cheap",)
    assert dear.calls == [], "the premium provider was paid for a field already found"


@requires_db
def test_a_miss_falls_through_to_the_next_provider(db, tenant):
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        _register(cur, "cheap", cost_micros=10_000, hits=False)
        _register(cur, "premium", cost_micros=200_000, hits=True)
        person = _person(cur, tid)
        result = enrichment.resolve(
            cur, _tenant_row(cur, tid), field_name="email", entity=dict(person),
            legal_basis="legitimate_interest")

    assert result.hit and result.provider == "premium"
    assert result.attempts == ("cheap", "premium")
    # Both were paid; the tenant is charged once, for the field they got.
    assert result.cost_micros == 210_000
    assert result.credits == 8.0


@requires_db
def test_a_provider_error_is_not_counted_as_a_miss(db, tenant):
    """An error is the absence of an answer. Counting it as a miss teaches the
    optimiser that a broken integration is a bad provider."""
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        _register(cur, "broken", cost_micros=10_000, error_times=5)
        _register(cur, "working", cost_micros=20_000)
        person = _person(cur, tid)
        result = enrichment.resolve(
            cur, _tenant_row(cur, tid), field_name="email", entity=dict(person),
            legal_basis="legitimate_interest")
        recorded = enrichment.matrix(cur, "email")

    assert result.hit and result.provider == "working"
    assert result.attempts == ("broken:error", "working")
    assert {r["provider"] for r in recorded} == {"working"}, (
        "a provider that could not be asked was scored as if it had answered")


# -- what the tenant pays ------------------------------------------------

@requires_db
def test_a_resolved_field_costs_what_the_document_says(db, tenant):
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        _register(cur, "p", fields=("email", "phone", "firmographics"))
        person = _person(cur, tid)
        email = enrichment.resolve(cur, _tenant_row(cur, tid), field_name="email",
                                   entity=dict(person), legal_basis="legitimate_interest")
        # Materialised before the call: `_tenant_row` runs its own query on
        # this cursor, and evaluating it as an argument would discard the
        # result set this fetch is waiting on.
        cur.execute("select * from person where id = %s", (person["id"],))
        refreshed = dict(cur.fetchone())
        phone = enrichment.resolve(cur, _tenant_row(cur, tid), field_name="phone",
                                   entity=refreshed, legal_basis="legitimate_interest")
        cur.execute("select kind, sum(billed_credits) as c from cost_event"
                    " group by kind order by kind")
        billed = {r["kind"]: float(r["c"]) for r in cur.fetchall()}

    assert email.credits == 8.0 and phone.credits == 25.0
    assert billed == {"enrich.email": 8.0, "enrich.phone": 25.0}


@requires_db
def test_a_miss_costs_us_and_not_the_tenant(db, tenant):
    """We pay every provider we ask; the customer pays for a field they got.

    The expensive half of the choice, and deliberate: it makes the waterfall's
    efficiency our margin rather than the customer's problem, which is what
    docs/07 claims the optimiser is for.
    """
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        _register(cur, "a", cost_micros=10_000, hits=False)
        _register(cur, "b", cost_micros=40_000, hits=False)
        person = _person(cur, tid)
        result = enrichment.resolve(
            cur, _tenant_row(cur, tid), field_name="email", entity=dict(person),
            legal_basis="legitimate_interest")
        cur.execute("select count(*) as n from cost_event")
        billed = cur.fetchone()["n"]

    assert not result.hit
    assert result.cost_micros == 50_000, "we paid both providers"
    assert result.credits == 0.0
    assert billed == 0, "a miss was charged to the tenant"


@requires_db
def test_a_tenant_out_of_credits_buys_nothing(db, tenant):
    """A lookup we cannot bill for is one we should not pay a provider for."""
    from runtime import metering

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        provider = _register(cur, "p")
        metering.meter(cur, _tenant_row(cur, tid), kind="email.send", units=15_000)
        person = _person(cur, tid)
        result = enrichment.resolve(
            cur, _tenant_row(cur, tid), field_name="email", entity=dict(person),
            legal_basis="legitimate_interest")

    assert not result.hit
    assert "budget" in result.reason
    assert provider.calls == [], "a provider was paid for a tenant who cannot be billed"


# -- not buying the same nothing twice -----------------------------------

@requires_db
def test_a_complete_miss_is_not_retried_the_next_morning(db, tenant):
    """Asking everyone again for a number that does not exist is the most
    reliable way to spend a data budget on nothing."""
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        provider = _register(cur, "p", hits=False)
        person = _person(cur, tid)
        first = enrichment.resolve(cur, _tenant_row(cur, tid), field_name="email",
                                   entity=dict(person), legal_basis="legitimate_interest")
        second = enrichment.resolve(cur, _tenant_row(cur, tid), field_name="email",
                                    entity=dict(person), legal_basis="legitimate_interest")

    assert not first.hit and not second.hit
    assert len(provider.calls) == 1
    assert "still unresolved" in second.reason


@requires_db
def test_the_cooldown_expires(db, tenant):
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        provider = _register(cur, "p", hits=False)
        person = _person(cur, tid)
        enrichment.resolve(cur, _tenant_row(cur, tid), field_name="email",
                           entity=dict(person), legal_basis="legitimate_interest")
        later = datetime.now(timezone.utc) + enrichment.MISS_COOLDOWN + timedelta(days=1)
        enrichment.resolve(cur, _tenant_row(cur, tid), field_name="email",
                           entity=dict(person), legal_basis="legitimate_interest",
                           now=later)

    assert len(provider.calls) == 2


# -- the matrix that cannot be bought ------------------------------------

@requires_db
def test_a_declared_hit_rate_gives_way_to_a_measured_one(db, tenant):
    """The optimiser gets better at a customer's own segments by running,
    which is the whole argument for running it early."""
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        # Registered as the better provider, and in this cohort it is not.
        _register(cur, "optimistic", cost_micros=10_000, hit_rate=0.95, hits=False)
        _register(cur, "honest", cost_micros=12_000, hit_rate=0.40, hits=True)
        cohort = "es_xx"
        for n in range(enrichment.MIN_OBSERVATIONS + 5):
            cur.execute(
                "insert into enrichment_attempt (tenant_id, provider_key, field,"
                " cohort, entity_type, entity_id, hit) values"
                " (zolts_internal.current_tenant(),'optimistic','email',%s,'person',"
                " gen_random_uuid(), false)", (cohort,))
        priced = enrichment._providers(cur, tid, "email", cohort)

    rates = {p.key: p.hit_rates[cohort] for _, p in priced}
    assert rates["optimistic"] == 0.0, "a measured zero was ignored for a declared 95%"
    assert rates["honest"] == 0.40, "a provider below the sample floor lost its default"


@requires_db
def test_the_matrix_reports_the_sample_beside_the_rate(db, tenant):
    """A cell with four observations and one with four thousand look identical
    as percentages, and only one is worth acting on."""
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        _register(cur, "p")
        person = _person(cur, tid)
        enrichment.resolve(cur, _tenant_row(cur, tid), field_name="email",
                           entity=dict(person), legal_basis="legitimate_interest")
        rows = enrichment.matrix(cur)

    assert len(rows) == 1
    assert rows[0]["calls"] == 1
    assert rows[0]["trusted"] is False, "one call was presented as a trustworthy rate"


# -- what a DPO asks -----------------------------------------------------

@requires_db
def test_enrichment_without_a_legal_basis_is_refused(db, tenant):
    """A resolved value with no basis recorded beside it is one nobody can
    defend, and the basis cannot be reconstructed afterwards."""
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        _register(cur, "p")
        person = _person(cur, tid)
        with pytest.raises(enrichment.EnrichmentError, match="legal basis"):
            enrichment.resolve(cur, _tenant_row(cur, tid), field_name="email",
                               entity=dict(person), legal_basis="")


@requires_db
def test_every_resolved_value_says_where_it_came_from(db, tenant):
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        _register(cur, "p", fields=("email", "phone"))
        person = _person(cur, tid)
        enrichment.resolve(cur, _tenant_row(cur, tid), field_name="email",
                           entity=dict(person), legal_basis="legitimate_interest")
        cur.execute("select * from person where id = %s", (person["id"],))
        refreshed = dict(cur.fetchone())
        enrichment.resolve(cur, _tenant_row(cur, tid), field_name="phone",
                           entity=refreshed, legal_basis="legitimate_interest")
        trail = enrichment.provenance(cur, "person", str(person["id"]))

    assert {t["field"] for t in trail} == {"email", "phone"}
    for entry in trail:
        assert entry["provider"] == "p"
        assert entry["legalBasis"] == "legitimate_interest"
        assert entry["confidence"] > 0


# -- the value actually lands --------------------------------------------

@requires_db
def test_a_bought_phone_is_a_column_and_not_an_attribute(db, tenant):
    """National do-not-call registries are matched on the number. A phone in
    `attributes` is a phone that cannot be suppressed."""
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        _register(cur, "p", fields=("phone",))
        person = _person(cur, tid)
        enrichment.resolve(cur, _tenant_row(cur, tid), field_name="phone",
                           entity=dict(person), legal_basis="legitimate_interest")
        cur.execute("select phone, phone_status from person where id = %s",
                    (person["id"],))
        row = cur.fetchone()

    assert row["phone"].startswith("+34")
    assert row["phone_status"] == "verified"


@requires_db
def test_a_later_provider_never_overwrites_a_value_somebody_may_have_acted_on(db, tenant):
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        _register(cur, "p")
        person = _person(cur, tid, email="known@acme.com")
        result = enrichment.resolve(
            cur, _tenant_row(cur, tid), field_name="email", entity=dict(person),
            legal_basis="legitimate_interest")
        cur.execute("select email from person where id = %s", (person["id"],))
        after = cur.fetchone()["email"]

    assert result.reason == "already resolved"
    assert after == "known@acme.com"


@requires_db
def test_firmographics_land_on_the_account(db, tenant):
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        _register(cur, "p", fields=("firmographics",))
        account, _ = _account_with_contact(cur, tid)
        cur.execute("update account set employee_band = null, industry_code = null,"
                    " country = null where id = %s", (account["id"],))
        cur.execute("select * from account where id = %s", (account["id"],))
        stripped = dict(cur.fetchone())
        result = enrichment.resolve(
            cur, _tenant_row(cur, tid), field_name="firmographics",
            entity=stripped, legal_basis="legitimate_interest")
        cur.execute("select employee_band, industry_code from account where id = %s",
                    (account["id"],))
        row = cur.fetchone()

    assert result.hit and result.credits == 4.0
    assert row["employee_band"] == "51-200"
    assert row["industry_code"] == "62.01"


@requires_db
def test_nothing_is_bought_when_no_provider_is_registered(db, tenant):
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        person = _person(cur, tid)
        result = enrichment.resolve(
            cur, _tenant_row(cur, tid), field_name="email", entity=dict(person),
            legal_basis="legitimate_interest")

    assert not result.hit
    assert "no provider is registered" in result.reason


@requires_app_role
def test_attempts_are_tenant_scoped(db, tenant, other_tenant, app_role_is_restricted):
    """One tenant's measured matrix must not order another tenant's waterfall:
    the segments are theirs and so are the provider contracts."""
    tid, other = str(tenant["id"]), str(other_tenant["id"])
    with db.tenant_tx(other) as cur:
        _register(cur, "p", hits=False)
        person = _person(cur, other)
        enrichment.resolve(cur, _tenant_row(cur, other), field_name="email",
                           entity=dict(person), legal_basis="legitimate_interest")

    with db.tenant_tx(tid) as cur:
        assert enrichment.matrix(cur) == []
        cur.execute("select count(*) as n from data_provider")
        assert cur.fetchone()["n"] == 0


# -- a provider described by a document, not written as a connector ------

DOCUMENT = {
    "apiVersion": "zolts/v1", "kind": "DataProvider",
    "metadata": {"provider": "declared"},
    "spec": {
        "transport": {"base_url": "https://api.example.com/v2",
                      "auth": {"kind": "header", "name": "x-api-key"}},
        "unit_cost_micros": 28_000,
        "lookups": {"email": {"path": "/find", "query": {"d": "account.domain"},
                              "response": {"email": "data.work_email"},
                              "confidence": "data.confidence"}},
    },
}


def _doc(**spec_overrides):
    import copy

    document = copy.deepcopy(DOCUMENT)
    document["spec"].update(spec_overrides)
    return document


def test_a_provider_document_is_refused_before_it_can_spend_money():
    """Checked on the way in rather than on the first lookup, because the
    first lookup happens against a real endpoint with a real credential."""
    from runtime.connectors.declarative_provider import ProviderDocumentError, validate

    with pytest.raises(ProviderDocumentError, match="kind"):
        validate({"kind": "CrmMapping", "spec": {}})
    with pytest.raises(ProviderDocumentError, match="base_url"):
        validate({"kind": "DataProvider", "spec": {"transport": {}}})
    with pytest.raises(ProviderDocumentError, match="no field to resolve"):
        validate({"kind": "DataProvider",
                  "spec": {"transport": {"base_url": "https://x.example"}}})
    with pytest.raises(ProviderDocumentError, match="every call is a miss"):
        validate(_doc(lookups={"email": {"path": "/f", "query": {"d": "x"}}}))
    with pytest.raises(ProviderDocumentError, match="method"):
        validate(_doc(lookups={"email": {"path": "/f", "method": "DELETE",
                                         "response": {"email": "e"}}}))


def test_a_provider_document_cannot_point_at_this_runtime():
    """The same guard the CRM mappings use. A document naming 169.254.169.254
    would read this runtime's own cloud credentials."""
    from zolts.mapping import MappingError
    from runtime.connectors.declarative_provider import validate

    for hostile in ("http://169.254.169.254/latest", "http://127.0.0.1:8000",
                    "http://localhost/api"):
        with pytest.raises(MappingError):
            validate(_doc(transport={"base_url": hostile}))


def test_a_miss_on_a_provider_that_bills_for_misses_reports_what_it_cost():
    """Reporting zero understates our own COGS and teaches the optimiser that
    a provider is cheaper than it is — which is the provider it then puts first."""
    import httpx

    from runtime.connectors.declarative_provider import DeclarativeDataProvider

    empty = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"data": {}})))
    for bills, expected in ((False, 0), (True, 28_000)):
        source = DeclarativeDataProvider(document=_doc(billed_on_miss=bills),
                                         client=empty)
        found = source.resolve(Lookup("email", {}, {"domain": "acme.example"}),
                               "key", {})
        assert not found.hit
        assert found.cost_micros == expected


def test_a_transport_fault_raises_and_a_404_does_not():
    """An error is the absence of an answer; a 404 is the provider saying it
    has nothing. Only one of them belongs in the hit-rate matrix."""
    import httpx

    from runtime.connectors.dataprovider import ProviderError
    from runtime.connectors.declarative_provider import DeclarativeDataProvider

    missing = DeclarativeDataProvider(document=_doc(), client=httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(404, json={}))))
    assert missing.resolve(Lookup("email", {}, {"domain": "a.example"}), "k", {}).hit is False

    broken = DeclarativeDataProvider(document=_doc(), client=httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(401, json={}))))
    with pytest.raises(ProviderError):
        broken.resolve(Lookup("email", {}, {"domain": "a.example"}), "bad", {})


def test_nothing_is_asked_when_there_is_nothing_to_ask_with():
    """A call with no facts cannot hit and would still be charged for."""
    import httpx

    from runtime.connectors.declarative_provider import DeclarativeDataProvider

    calls = []

    def record(request):
        calls.append(request)
        return httpx.Response(200, json={"data": {"work_email": "x@y.example"}})

    source = DeclarativeDataProvider(document=_doc(),
                                     client=httpx.Client(transport=httpx.MockTransport(record)))
    found = source.resolve(Lookup("email", {}, None), "key", {})
    assert not found.hit
    assert found.cost_micros == 0
    assert calls == []


@requires_db
def test_a_declared_provider_needs_no_connector_to_be_written(db, tenant):
    """The registration is the whole integration. Without this the CLI could
    store a provider the runtime had no way to call."""
    from runtime.connectors.declarative_provider import DeclarativeDataProvider

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        import json as _json

        cur.execute(
            "insert into data_provider (tenant_id, key, fields, unit_cost_micros,"
            " config) values (zolts_internal.current_tenant(),'declared',"
            " '{email}', 28000, %s)", (_json.dumps({"mapping": DOCUMENT}),))
        installed = enrichment.install_declared(cur)

    assert installed == ["declared"]
    assert "declared" in dataprovider.providers()
    assert isinstance(dataprovider.get_provider("declared"), DeclarativeDataProvider)


# -- what a real run found -----------------------------------------------

@requires_db
def test_the_provider_credential_actually_reaches_the_provider(db, tenant):
    """`data_provider.connection_id` existed and the first version of this
    module passed None regardless, so every lookup was unauthenticated. A real
    run found it in three 401s."""
    from runtime.provision import store_connection

    tid = str(tenant["id"])
    store_connection(db, tid, provider="keyed", secret="the-secret", secret_key=SECRET)
    with db.tenant_tx(tid) as cur:
        source = _register(cur, "keyed")
        cur.execute("select id from connection where provider = 'keyed'")
        connection = cur.fetchone()["id"]
        cur.execute("update data_provider set connection_id = %s where key = 'keyed'",
                    (connection,))
        person = _person(cur, tid)
        enrichment.resolve(cur, _tenant_row(cur, tid), field_name="email",
                           entity=dict(person), legal_basis="legitimate_interest",
                           secret_key=SECRET)

    assert source.calls, "the provider was never asked"
    assert source.seen_credentials == ["the-secret"], (
        "the provider was asked unauthenticated")


@requires_db
def test_a_value_belonging_to_another_person_is_refused_rather_than_merged(db, tenant):
    """Three people at one company, and the provider knows one address.

    Writing it would merge two identities, which is worse than not having it.
    A real run hit this on its second row and lost the whole batch to a
    constraint violation.
    """
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        _register(cur, "p")
        dataprovider.get_provider("p").values = {"email": "shared@acme.example"}
        first = _person(cur, tid)
        second = _person(cur, tid)

        one_result = enrichment.resolve(
            cur, _tenant_row(cur, tid), field_name="email", entity=dict(first),
            legal_basis="legitimate_interest")
        two_result = enrichment.resolve(
            cur, _tenant_row(cur, tid), field_name="email", entity=dict(second),
            legal_basis="legitimate_interest")

        # The transaction survived, which is the half that a savepoint buys.
        cur.execute("select count(*) as n from person where email = 'shared@acme.example'")
        holders = cur.fetchone()["n"]
        billed = enrichment.matrix(cur, "email")

    assert one_result.hit
    assert not two_result.hit
    assert "merge two identities" in two_result.reason
    assert holders == 1, "one address was written onto two people"
    # The provider answered both times and the matrix says so; only one was billed.
    assert billed[0]["calls"] == 2 and billed[0]["hitRate"] == 1.0
    assert two_result.credits == 0.0


@requires_db
def test_an_unusable_answer_is_not_bought_again_tomorrow(db, tenant):
    """The cooldown covers "answered but unattributable" as well as "nobody had
    it". Both cost the same to repeat."""
    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        source = _register(cur, "p")
        source.values = {"email": "shared@acme.example"}
        first, second = _person(cur, tid), _person(cur, tid)
        enrichment.resolve(cur, _tenant_row(cur, tid), field_name="email",
                           entity=dict(first), legal_basis="legitimate_interest")
        enrichment.resolve(cur, _tenant_row(cur, tid), field_name="email",
                           entity=dict(second), legal_basis="legitimate_interest")
        calls_after_collision = len(source.calls)
        again = enrichment.resolve(cur, _tenant_row(cur, tid), field_name="email",
                                   entity=dict(second), legal_basis="legitimate_interest")

    assert len(source.calls) == calls_after_collision, "the same nothing was bought twice"
    assert "still unresolved" in again.reason
