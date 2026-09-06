"""Producing the research dossier, and deciding when it is worth paying for.

`docs/12` prices this at 20 credits — the most expensive action in the list,
and the last one nothing could execute. Three rules follow from the price, and
the second is the one that decides whether the feature is affordable.

**Evidence first, model second.** The dossier is assembled by retrieval from
what this runtime already knows: the account, its contacts, its signals, and
what has already been tried on it. An account nothing is known about produces
a refusal rather than a paragraph, before a model is called at all — twenty
credits for a confident description of a company nobody has data on is the
exact purchase this layer exists to prevent.

**A current dossier is served, not rewritten.** A dossier is current for the
facts it saw, so `built_through` records the newest signal it read. A request
for an account with nothing new since is answered from storage and bills
nothing. Charging per request instead would make the console's own account
page the most expensive screen in the product.

**A refusal is stored.** The next caller learns why without paying to find
out again, and an operator can see that the runtime declined rather than
failed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from runtime.db import one
from zolts.provenance import Evidence, Source

# What one dossier costs, per `docs/12`.
CREDIT_KIND = "agent.dossier"

# How far back the evidence reaches. `docs/06` gives most signals a half-life
# well inside this, so a signal older than the window is one that has decayed
# to nothing and would only pad the prompt.
WINDOW = timedelta(days=180)


@dataclass(frozen=True)
class Built:
    """What one request produced, and whether it cost anything."""
    account_id: str
    state: str
    credits: float = 0.0
    reused: bool = False
    reason: str | None = None
    dossier_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"account": self.account_id, "state": self.state,
                "credits": self.credits, "reused": self.reused,
                "reason": self.reason, "id": self.dossier_id}


def evidence_for(cur, account: dict[str, Any]) -> tuple[list[Evidence], datetime | None, int]:
    """What the runtime knows about this account, as citable statements.

    Every item names its own subject. A citation reading "raised 9,000,000"
    supports no sentence on its own, because the verifier asks one source to
    cover a whole sentence and a sentence names who did the thing — the defect
    that made the copywriter's first real messages unsupportable.
    """
    name = account.get("name") or "the account"
    items: list[Evidence] = [Evidence(Source.CRM, f"{name} is the account.", "crm.account")]
    for column, phrase, ref in (
            ("domain", "{n}'s website is {v}.", "crm.domain"),
            ("employee_band", "{n} has {v} employees.", "crm.employee_band"),
            ("country", "{n} is based in {v}.", "crm.country"),
            ("industry_code", "{n} operates in {v}.", "crm.industry")):
        value = account.get(column)
        if value:
            items.append(Evidence(Source.CRM, phrase.format(n=name, v=value), ref))

    cur.execute(
        "select p.full_name, m.title, m.seniority, m.buying_role from membership m"
        " join person p on p.id = m.person_id"
        " where m.account_id = %s and m.ended_at is null"
        " order by m.buying_role nulls last limit 12", (str(account["id"]),))
    for row in cur.fetchall():
        who = row["full_name"]
        if not who:
            continue
        role = row["title"] or row["buying_role"] or "a contact"
        items.append(Evidence(Source.CRM, f"{who} is {role} at {name}.",
                              f"crm.contact.{who.split()[0].lower()}"))

    cur.execute(
        "select id, type, payload, observed_at from signal"
        " where entity_id = %s and observed_at >= %s"
        " order by observed_at desc limit 20",
        (str(account["id"]), datetime.now(timezone.utc) - WINDOW))
    newest: datetime | None = None
    signals = cur.fetchall()
    for row in signals:
        rendered = ", ".join(f"{k} {v}" for k, v in (row["payload"] or {}).items())
        when = row["observed_at"].date().isoformat()
        items.append(Evidence(
            Source.RETRIEVED,
            f"{name}: {row['type']} on {when}" + (f" with {rendered}." if rendered else "."),
            f"signal.{str(row['id'])[:8]}"))
        newest = max(newest, row["observed_at"]) if newest else row["observed_at"]

    # What has already been tried. A brief that opens with an approach somebody
    # used a fortnight ago is worse than no brief, and this is the only place
    # the runtime can know it.
    cur.execute(
        "select count(*) as sent, max(t.sent_at) as last_sent from touch t"
        " join enrollment e on e.id = t.enrollment_id"
        " where e.entity_id = %s and t.status = 'sent'", (str(account["id"]),))
    reached = cur.fetchone()
    if reached and reached["sent"]:
        when = reached["last_sent"].date().isoformat() if reached["last_sent"] else "an unknown date"
        items.append(Evidence(
            Source.CRM,
            f"{name} has been sent {reached['sent']} message(s), most recently on {when}.",
            "history.touches"))
    return items, newest, len(signals)


def current(cur, account_id: str, newest_signal: datetime | None) -> dict[str, Any] | None:
    """The stored dossier, if nothing has happened since it was written.

    Freshness is a comparison rather than a clock. A dossier written a month
    ago on an account that has done nothing since is still the answer; one
    written this morning on an account that announced a funding round at noon
    is not.
    """
    cur.execute("select * from dossier where account_id = %s"
                " order by seq desc limit 1", (account_id,))
    stored = one(cur)
    if stored is None or stored["state"] == "refused":
        # A refusal is reported but never reused as an answer: the condition
        # that caused it — no evidence, no budget — is one that changes.
        return None
    if newest_signal is None:
        return stored
    seen = stored["built_through"]
    return stored if seen is not None and seen >= newest_signal else None


def _store(cur, tenant_id: str, account_id: str, row: dict[str, Any],
           built_through: datetime | None, signals_seen: int,
           credits: float) -> dict[str, Any]:
    cur.execute(
        "insert into dossier (tenant_id, account_id, state, body, evidence, dropped,"
        " model, prompt_version, cost_micros, billed_credits, built_through, signals_seen)"
        " values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning *",
        (tenant_id, account_id, row["state"], row["body"], json.dumps(row["evidence"]),
         json.dumps(row["dropped"]), row["model"], row["prompt_version"],
         row["cost_micros"], credits, built_through, signals_seen))
    return one(cur)


def build(cur, tenant: dict[str, Any], account: dict[str, Any], *,
          client: Any = None, guard: Any = None,
          consumed_usd: float | None = None, limit_usd: float | None = None,
          force: bool = False) -> Built:
    """Write, or reuse, the dossier for one account."""
    from runtime import metering
    from runtime.agents import researcher

    tenant_id, account_id = str(tenant["id"]), str(account["id"])
    items, newest, seen = evidence_for(cur, account)

    if not force:
        stored = current(cur, account_id, newest)
        if stored is not None:
            return Built(account_id=account_id, state=stored["state"], reused=True,
                         dossier_id=str(stored["id"]),
                         reason="nothing new has happened on this account since it was written")

    if client is None or guard is None:
        # Fail closed, as everywhere else in this layer: a deployment with no
        # model configured must not write an empty document and charge for it.
        return Built(account_id=account_id, state="refused",
                     reason="no model client is configured; a dossier needs one")

    # Before the model call, not after. Twenty credits is the largest single
    # purchase in the price list, and a tenant at their ceiling finding out
    # afterwards has already been charged.
    budget = metering.allowance(cur, tenant, cost=CREDIT_KIND)
    if not budget.allowed:
        return Built(account_id=account_id, state="refused",
                     reason=f"budget: {budget.reason}")

    written = researcher.research(client=client, guard=guard, evidence=items,
                                  consumed_usd=consumed_usd, limit_usd=limit_usd)
    credits = 0.0
    if written.state != "refused":
        # Refused costs nothing: no document was produced. This is the same
        # rule as an enrichment miss (decision 21) and for the same reason —
        # the customer pays for what they received.
        credits = float(metering.meter(cur, tenant, kind=CREDIT_KIND,
                                       cost_micros=written.cost_micros))
    stored = _store(cur, tenant_id, account_id, written.as_row(), newest, seen, credits)
    return Built(account_id=account_id, state=written.state, credits=credits,
                 reason=written.refused, dossier_id=str(stored["id"]))


def latest(cur, account_id: str) -> dict[str, Any] | None:
    """The stored dossier for one account, current or not."""
    cur.execute("select * from dossier where account_id = %s"
                " order by seq desc limit 1", (account_id,))
    return one(cur)


def coverage(cur) -> dict[str, Any]:
    """How much of the book has been researched, and how much of that is stale.

    Reported together on purpose: a tenant with a dossier on every account and
    every one of them written before this quarter's news has coverage of 100%
    and knows nothing.
    """
    cur.execute("select count(*) as n from account")
    accounts = int(cur.fetchone()["n"])
    cur.execute(
        "select d.state, count(*) as n,"
        "  count(*) filter (where exists ("
        "    select 1 from signal s where s.entity_id = d.account_id"
        "      and (d.built_through is null or s.observed_at > d.built_through))) as stale"
        "  from dossier d"
        "  join (select account_id, max(seq) as newest from dossier group by account_id) l"
        "    on l.account_id = d.account_id and l.newest = d.seq"
        " group by d.state")
    by_state = {r["state"]: {"count": int(r["n"]), "stale": int(r["stale"])}
                for r in cur.fetchall()}
    written = sum(v["count"] for v in by_state.values())
    return {"accounts": accounts, "withDossier": written,
            "stale": sum(v["stale"] for v in by_state.values()),
            "byState": by_state}
