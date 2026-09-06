"""The mapping language.

Every test here is a shape a real CRM actually uses. The language is small on
purpose: anything more expressive is a program, and a program that runs on a
customer's payload inside this runtime is a liability rather than a feature.
"""

from __future__ import annotations

import pytest

from zolts.mapping import MISSING, ConsentRule, MappingError, build, extract, records_at

HUBSPOT = {"id": "701", "properties": {"email": "dana@acme.test", "firstname": "Dana"}}
PIPEDRIVE = {"id": 21, "name": "Dana Cruz",
             "email": [{"value": "old@acme.test", "primary": False},
                       {"value": "dana@acme.test", "primary": True}],
             "org_id": {"value": 11, "name": "Acme"}}
IN_HOUSE = {"pk": 5, "contact": {"mail": " DANA@ACME.TEST ", "flags": ["vip"]},
            "company": {"site": "https://www.acme.test/about?x=1"}}


# -- paths ----------------------------------------------------------------

def test_a_nested_field():
    assert extract(HUBSPOT, "properties.email") == "dana@acme.test"


def test_an_array_by_position():
    assert extract(PIPEDRIVE, "email[0].value") == "old@acme.test"
    assert extract(PIPEDRIVE, "email[-1].value") == "dana@acme.test"


def test_the_element_flagged_primary():
    """Pipedrive marks one address primary. Taking the first would take the
    old one."""
    assert extract(PIPEDRIVE, "email[primary].value") == "dana@acme.test"


def test_a_flag_nobody_set_falls_back_to_the_first():
    """Several CRMs mark none, and the first is then the only defensible
    choice."""
    payload = {"email": [{"value": "a@x.test"}, {"value": "b@x.test"}]}
    assert extract(payload, "email[primary].value") == "a@x.test"


def test_a_nested_object_reference():
    assert extract(PIPEDRIVE, "org_id.value") == 11


def test_a_missing_field_is_missing_not_an_error():
    """Reading somebody else's system, an absent field is the normal case. A
    resolver that threw would make every optional field required."""
    assert extract(HUBSPOT, "properties.nothing.here") is MISSING
    assert extract(HUBSPOT, "email[3].value") is MISSING
    assert extract(None, "anything") is MISSING


def test_an_unknown_transform_is_an_authoring_error_and_says_so():
    with pytest.raises(MappingError, match="unknown transform"):
        extract(HUBSPOT, "properties.email | shout")


# -- transforms -----------------------------------------------------------

def test_a_transform_pipeline():
    assert extract(IN_HOUSE, "contact.mail | trim | lower") == "dana@acme.test"


@pytest.mark.parametrize("raw,expected", [
    ("https://www.acme.test/about?x=1", "acme.test"),
    ("acme.test", "acme.test"),
    ("dana@acme.test", "acme.test"),
    ("", MISSING),
])
def test_a_domain_from_however_the_crm_stored_it(raw, expected):
    assert extract({"w": raw}, "w | domain") == expected


def test_join_builds_a_name_from_parts():
    assert extract({"n": ["Dana", "Cruz"]}, "n | join") == "Dana Cruz"


# -- records --------------------------------------------------------------

def test_records_are_found_where_the_mapping_says():
    """Every API wraps its results differently. Guessing is a source of silent
    empty syncs."""
    assert len(records_at({"results": [1, 2, 3]}, "results")) == 3
    assert len(records_at({"data": {"items": [1, 2]}}, "data.items")) == 2
    assert len(records_at([1, 2], None)) == 2
    assert records_at({"results": []}, "results") == []
    assert records_at({}, "nothing") == []


def test_build_drops_what_the_source_did_not_have():
    built = build(HUBSPOT, {"external_id": "id", "email": "properties.email",
                            "title": "properties.jobtitle"})
    assert built == {"external_id": "701", "email": "dana@acme.test"}
    assert "title" not in built, "an absent field must not become an empty string"


# -- consent: the rule that decides whether a mapping is safe -------------

def test_a_value_map_translates_the_crms_own_vocabulary():
    rule = ConsentRule(field="status",
                       values={"active": "allowed", "unsubscribed": "opted_out"})
    assert rule.read({"status": "active"}) == "allowed"
    assert rule.read({"status": "UNSUBSCRIBED"}) == "opted_out"


def test_a_value_the_map_does_not_cover_is_unknown_not_allowed():
    """The whole point. A CRM state nobody mapped is not permission."""
    rule = ConsentRule(field="status", values={"active": "allowed"})
    assert rule.read({"status": "pending_review"}) == "unknown"


def test_an_absent_consent_field_is_unknown():
    rule = ConsentRule(field="status", values={"active": "allowed"})
    assert rule.read({}) == "unknown"


def test_a_bare_boolean_is_not_guessed_at():
    """This test replaces one that asserted the opposite.

    Reading an unmapped boolean as an opt-out flag was wrong twice over. It
    granted `allowed` for a state the document never named, which is the shape
    `validate` refuses when written honestly as `default: allowed`. And it
    inverted every positively-phrased field: `email_ok: true` means this person
    consented, and the flag reading suppressed them while mailing everyone who
    had refused.
    """
    rule = ConsentRule(field="email_ok")
    assert rule.read({"email_ok": True}) == "unknown"
    assert rule.read({"email_ok": False}) == "unknown"


def test_a_boolean_field_is_mapped_like_any_other():
    """Nothing is lost: the document says which way the boolean points."""
    rule = ConsentRule(field="unsubscribed",
                       values={"true": "opted_out", "false": "allowed"})
    assert rule.read({"unsubscribed": True}) == "opted_out"
    assert rule.read({"unsubscribed": "false"}) == "allowed"

    positive = ConsentRule(field="email_ok",
                           values={"true": "allowed", "false": "opted_out"})
    assert positive.read({"email_ok": True}) == "allowed"
    assert positive.read({"email_ok": False}) == "opted_out"


def test_a_default_can_be_declared_but_never_silently():
    """A tenant who knows their export only contains contactable people can
    say so. They have to say it."""
    rule = ConsentRule(field="status", values={}, default="allowed")
    assert rule.read({"status": "whatever"}) == "allowed"
    assert ConsentRule(field="status").default == "unknown"


# -- what a document may not say -----------------------------------------

def test_a_malformed_path_is_refused_at_publish_not_mid_crawl():
    """`extract` walks only as far as the record takes it, so a bad third
    segment in a document whose second segment is usually absent surfaces on
    the one record that has it — halfway through a customer's sync."""
    from zolts.mapping import check_path

    with pytest.raises(MappingError, match="not a valid path segment"):
        check_path("contacts.email", "emails[[0]].value")
    with pytest.raises(MappingError, match="empty path"):
        check_path("contacts.email", "")


def test_a_base_url_pointing_at_this_runtime_is_refused():
    """A customer-authored base URL is a request this runtime makes with its
    own network position. 169.254.169.254 is the cloud metadata endpoint."""
    from zolts.mapping import check_base_url

    for hostile in ("http://169.254.169.254/latest/meta-data",
                    "http://localhost:8000/v1",
                    "https://[::1]/api",
                    "https://user:pw@127.0.0.1/api"):
        with pytest.raises(MappingError, match="loopback or link-local"):
            check_base_url(hostile)

    with pytest.raises(MappingError, match="must be http or https"):
        check_base_url("file:///etc/passwd")


def test_a_private_address_behind_a_vpn_is_the_use_case_not_the_threat():
    from zolts.mapping import check_base_url

    check_base_url("https://crm.acme.internal/api/v2")
    check_base_url("http://10.0.0.5/api")
    check_base_url("https://192.168.1.20:8443/crm")
