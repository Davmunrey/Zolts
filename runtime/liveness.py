"""Whether this deployment is doing its job, as opposed to being up.

`/health` answers "can I reach the database". That question is nearly always
yes, including on the morning when the worker died at 3am, the outbox has been
growing for six hours and a paying partner's campaign has sent nothing. Both
states report `"status": "ok"`, which makes the endpoint an alibi rather than a
signal.

These are the failures that are silent by construction — nothing raises, no
request 500s, and the first person to notice is the customer:

* The outbox stops draining. Work is queued and nothing claims it.
* A connector fails every call. Each individual failure is retried and logged;
  the pattern is what matters and nothing looks at the pattern.
* Actions pile up dead. They have exhausted their attempts and stopped, and
  nobody was told they stopped.
* A tenant has programs running and no live connection to run them through.
* A circuit breaker paused a sending domain. The campaign stops, nothing
  raises, and the asset that stopped takes months to replace.

Each is expressed as a threshold with a number beside it, because "the outbox
is deep" is not actionable and "412 actions have been pending for over 30
minutes" is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from runtime.db import Database

# An action due half an hour ago that nothing has claimed means no worker is
# claiming. Below that it is a slow tick or a burst, and paging on it teaches
# people to ignore the page.
STALE_MINUTES = 30
# One dead action is a bad address. A wall of them is a broken integration.
DEAD_THRESHOLD = 10


@dataclass
class Signal:
    name: str
    ok: bool
    detail: str
    value: int = 0


@dataclass
class Liveness:
    signals: list[Signal] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str, value: int = 0) -> None:
        self.signals.append(Signal(name, ok, detail, value))

    @property
    def failing(self) -> list[Signal]:
        return [s for s in self.signals if not s.ok]

    def as_dict(self) -> dict[str, Any]:
        return {"draining": not self.failing,
                "signals": [{"name": s.name, "ok": s.ok, "detail": s.detail,
                             "value": s.value} for s in self.signals]}


def check(db: Database) -> Liveness:
    """Read across every tenant. This is an operator's question, not a tenant's."""
    live = Liveness()
    with db.admin_tx() as cur:
        _outbox(cur, live)
        _dead(cur, live)
        _connections(cur, live)
        _programs_without_a_channel(cur, live)
        _burned_domains(cur, live)
    return live


def _burned_domains(cur, live: Liveness) -> None:
    """A domain a breaker paused is the most expensive silent state here.

    Nothing raises when it happens, no request fails, and the campaign simply
    stops. A domain takes months to build a reputation and cannot be bought,
    so an operator finding out on the weekly review has found out too late.
    """
    cur.execute("select name, paused_reason, paused_at from sending_domain"
                " where paused order by paused_at desc nulls last")
    paused = cur.fetchall()
    if paused:
        named = ", ".join(f"{r['name']} ({r['paused_reason']})" for r in paused)
        live.add("sending domains", False,
                 f"paused by a cut-off: {named}. Every program sending from "
                 "them has stopped, and nothing else reports that it has",
                 value=len(paused))
    else:
        live.add("sending domains", True, "no domain is paused by a cut-off")


def _outbox(cur, live: Liveness) -> None:
    cur.execute(
        "select count(*) as n,"
        " coalesce(extract(epoch from now() - min(run_after)) / 60, 0)::int as oldest"
        " from action where state = 'pending' and run_after < now()")
    row = cur.fetchone()
    count, oldest = row["n"], row["oldest"]
    if count and oldest >= STALE_MINUTES:
        live.add("outbox draining", False,
                 f"{count} actions due, the oldest {oldest} minutes ago. Nothing has "
                 "claimed them, which means no worker is running or every worker is "
                 "failing before it claims", value=count)
    else:
        live.add("outbox draining", True,
                 f"{count} due, oldest {oldest} minutes" if count else "nothing due",
                 value=count)


def _dead(cur, live: Liveness) -> None:
    cur.execute("select count(*) as n from action where state = 'dead'"
                " and updated_at > now() - interval '24 hours'")
    count = cur.fetchone()["n"]
    if count >= DEAD_THRESHOLD:
        live.add("actions completing", False,
                 f"{count} actions exhausted their attempts in the last 24 hours. One "
                 "is a bad address; this many is an integration that stopped working",
                 value=count)
    else:
        live.add("actions completing", True,
                 f"{count} dead in the last 24 hours", value=count)


def _connections(cur, live: Liveness) -> None:
    cur.execute("select provider, count(*) as n from connection where status = 'error'"
                " group by provider order by provider")
    broken = cur.fetchall()
    if broken:
        named = ", ".join(f"{r['provider']} ({r['n']})" for r in broken)
        live.add("connections healthy", False,
                 f"connections in error: {named}. Every send through them fails, and "
                 "each failure looks like an ordinary retry on its own",
                 value=sum(r["n"] for r in broken))
    else:
        live.add("connections healthy", True, "no connection is in error")


def _programs_without_a_channel(cur, live: Liveness) -> None:
    """A tenant running programs with nothing to run them through.

    This is the shape of an onboarding that stopped halfway: programs activated,
    connector never connected. It sends nothing and reports nothing.
    """
    # A program's running state is 'live'. Written as 'active' here first, which
    # no program can ever be, so this signal could not fire — the check existed
    # and checked nothing. A test caught it; nothing in production would have.
    cur.execute(
        "select count(distinct p.tenant_id) as n from program p"
        " where p.status = 'live'"
        "   and not exists (select 1 from connection c"
        "                   where c.tenant_id = p.tenant_id and c.status = 'active')")
    count = cur.fetchone()["n"]
    if count:
        live.add("tenants can send", False,
                 f"{count} tenants have live programs and no working connection. Their "
                 "programs enroll, plan and then send nothing", value=count)
    else:
        live.add("tenants can send", True, "every tenant with live programs can send")


def render(live: Liveness) -> str:
    lines = [f"{'OK  ' if s.ok else 'FAIL'}  {s.name}: {s.detail}" for s in live.signals]
    lines.append("")
    lines.append("DRAINING" if not live.failing
                 else f"NOT DRAINING: {len(live.failing)} failing")
    return "\n".join(lines)
