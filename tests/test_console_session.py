"""Opening the operator surface as an operator.

`/console` authenticated by the `x-api-key` header, which a browser cannot send
when somebody types the URL. It answered 401 to the person it was built for,
and the quickstart's own instruction was to `curl` it — which renders the page
and can click nothing on it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from runtime.api import session as console_session
from runtime.api.app import create_app
from runtime.provision import issue_api_key, revoke_api_key
from tests.conftest import SECRET, requires_db


@pytest.fixture
def client(db):
    return TestClient(create_app(db, secret_key=SECRET), raise_server_exceptions=False)


@pytest.fixture
def key(db, tenant):
    return issue_api_key(db, str(tenant["id"]), "first", [])


# -- the door ------------------------------------------------------------

@requires_db
def test_a_browser_gets_a_sign_in_page_not_a_json_401(client):
    response = client.get("/console", headers={"accept": "text/html"})
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("text/html")
    assert 'id="f"' in response.text
    assert "content-security-policy" in response.headers


@requires_db
def test_the_sign_in_page_declares_the_hash_of_the_script_it_serves(client):
    """A hash written by hand is a hash that drifts, and the failure is a page
    whose script silently never runs."""
    import base64
    import hashlib
    import re

    response = client.get("/console")
    script = re.search(r"<script>(.*?)</script>", response.text, re.S).group(1)
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    assert f"'sha256-{digest}'" in response.headers["content-security-policy"]


# -- the session ---------------------------------------------------------

@requires_db
def test_a_key_opens_a_session_and_the_console_renders(client, key):
    opened = client.post("/v1/console/session", json={"api_key": key.token})
    assert opened.status_code == 201
    assert console_session.COOKIE in client.cookies
    assert console_session.CSRF_COOKIE in client.cookies

    assert client.get("/console").status_code == 200
    assert client.get("/v1/programs").status_code == 200


@requires_db
def test_the_cookie_is_not_the_api_key(client, key):
    """A key is a long-lived bearer credential shown once. In a cookie it is in
    browser storage, in history, and on every request to this origin forever."""
    client.post("/v1/console/session", json={"api_key": key.token})
    assert client.cookies[console_session.COOKIE] != key.token
    assert not client.cookies[console_session.COOKIE].startswith("zk_")


@requires_db
def test_the_session_token_is_never_stored(db, client, key, tenant):
    import hashlib

    client.post("/v1/console/session", json={"api_key": key.token})
    cookie = client.cookies[console_session.COOKIE]
    with db.tenant_tx(str(tenant["id"])) as cur:
        cur.execute("select token_hash from console_session")
        stored = cur.fetchone()["token_hash"]
    assert stored == hashlib.sha256(cookie.encode()).hexdigest()


@requires_db
def test_an_invalid_key_opens_nothing(client):
    response = client.post("/v1/console/session", json={"api_key": "zk_" + "x" * 40})
    assert response.status_code == 401
    assert console_session.COOKIE not in client.cookies


# -- CSRF ----------------------------------------------------------------

@requires_db
def test_a_cookie_write_without_the_csrf_token_is_refused(client, key):
    """A cookie is ambient — the browser attaches it to whatever the page asks
    for — so a state-changing request must also carry something nothing
    cross-origin can read."""
    client.post("/v1/console/session", json={"api_key": key.token})
    refused = client.post("/v1/accounts", json={"name": "A", "domain": "a.example"})
    assert refused.status_code == 403
    assert "CSRF" in refused.json()["detail"]


@requires_db
def test_a_cookie_write_with_the_csrf_token_is_allowed(client, key):
    csrf = client.post("/v1/console/session",
                       json={"api_key": key.token}).json()["csrf"]
    allowed = client.post("/v1/accounts", json={"name": "A", "domain": "a.example"},
                          headers={console_session.CSRF_HEADER: csrf})
    assert allowed.status_code == 201


@requires_db
def test_a_forged_csrf_token_is_refused(client, key):
    client.post("/v1/console/session", json={"api_key": key.token})
    forged = client.post("/v1/accounts", json={"name": "A", "domain": "a.example"},
                         headers={console_session.CSRF_HEADER: "not-the-token"})
    assert forged.status_code == 403


@requires_db
def test_the_key_header_is_exempt_from_csrf(client, key):
    """Nothing ambient sent it: something built the request and attached the
    credential."""
    response = client.post("/v1/accounts", json={"name": "B", "domain": "b.example"},
                           headers={"x-api-key": key.token})
    assert response.status_code == 201


@requires_db
def test_reads_do_not_need_the_csrf_token(client, key):
    client.post("/v1/console/session", json={"api_key": key.token})
    assert client.get("/v1/programs").status_code == 200


# -- ending a session ----------------------------------------------------

@requires_db
def test_revoking_the_key_ends_the_sessions_it_opened(db, client, key, tenant):
    """Otherwise revocation stops the credential and leaves the browser holding
    a working door."""
    client.post("/v1/console/session", json={"api_key": key.token})
    assert client.get("/v1/programs").status_code == 200

    revoke_api_key(db, str(tenant["id"]), key.key_id)
    ended = client.get("/v1/programs")
    assert ended.status_code == 401
    assert "session has ended" in ended.json()["detail"]


@requires_db
def test_signing_out_ends_the_session(client, key):
    csrf = client.post("/v1/console/session",
                       json={"api_key": key.token}).json()["csrf"]
    out = client.delete("/v1/console/session",
                        headers={console_session.CSRF_HEADER: csrf})
    assert out.status_code == 204
    assert client.get("/console").status_code == 401


@requires_db
def test_an_expired_session_is_refused(db, client, key, tenant):
    client.post("/v1/console/session", json={"api_key": key.token})
    with db.tenant_tx(str(tenant["id"])) as cur:
        cur.execute("update console_session set expires_at = now() - interval '1 minute'")
    assert client.get("/v1/programs").status_code == 401


@requires_db
def test_a_session_carries_only_the_scopes_of_the_key_that_opened_it(db, client, tenant):
    """Signing in must not widen what a key could do."""
    narrow = issue_api_key(db, str(tenant["id"]), "read only", ["read"])
    csrf = client.post("/v1/console/session",
                       json={"api_key": narrow.token}).json()["csrf"]
    refused = client.post("/v1/keys", json={"name": "escalated"},
                          headers={console_session.CSRF_HEADER: csrf})
    assert refused.status_code == 403
    assert "scope" in refused.json()["detail"]


@requires_db
def test_sessions_are_tenant_scoped(db, client, key, other_tenant):
    client.post("/v1/console/session", json={"api_key": key.token})
    with db.tenant_tx(str(other_tenant["id"])) as cur:
        cur.execute("select count(*) as n from console_session")
        assert cur.fetchone()["n"] == 0


# -- what the console renders --------------------------------------------

@requires_db
def test_a_draft_appears_in_the_program_list(db, client, key, tenant):
    """Signup publishes starter programs as drafts on purpose. Rendering only
    live programs meant a partner who had just signed up opened the console,
    saw nothing at all, and had no way to activate the one thing they held.
    The surface was built against a fixture in which everything was live."""
    from runtime.api import console

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        cur.execute(
            "insert into program (tenant_id, key, version, spec, spec_hash, status)"
            " values (%s, 'starter', 1, %s, 'h', 'draft')",
            (tid, '{"experiment": {"holdout_pct": 10}}'))
        cur.execute("select * from tenant where id = %s", (tid,))
        view = console.build(cur, cur.fetchone())

    keys = [p["key"] for p in view["programs"]]
    assert "starter" in keys
    assert view["programs"][0]["status"] == "draft"
    # And it is not counted twice.
    assert "starter" not in [p["key"] for p in view["plannedPrograms"]]


@requires_db
def test_every_program_carries_an_id_the_console_can_act_on(db, client, key, tenant):
    """Without one there is no button: the console could render a program and
    not address one."""
    from runtime.api import console

    tid = str(tenant["id"])
    with db.tenant_tx(tid) as cur:
        cur.execute(
            "insert into program (tenant_id, key, version, spec, spec_hash, status)"
            " values (%s, 'addressable', 1, '{}', 'h', 'draft')", (tid,))
        cur.execute("select * from tenant where id = %s", (tid,))
        view = console.build(cur, cur.fetchone())

    assert all(p.get("id") for p in view["programs"])


@requires_db
def test_the_served_console_declares_itself_live(client, key):
    """The static build inlines a fixture and keeps connect-src at 'none', so
    its buttons must stay inert. One flag separates them."""
    client.post("/v1/console/session", json={"api_key": key.token})
    assert '"live":true' in client.get("/console").text


def test_the_static_build_does_not():
    from pathlib import Path

    built = Path(__file__).resolve().parent.parent / "site" / "index.html"
    if not built.is_file():
        import pytest as _p
        _p.skip("site/ is not built")
    assert '"live":true' not in built.read_text()
