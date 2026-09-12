"""First run inside the console.

`quickstart` is a CLI, and the founder could not find where to configure the
product (`docs/28`, OX-11: that document exists because of that conversation).
A new tenant is walked from the blueprint through a connected CRM, a reviewed
and activated programme, to the first signal, on the screen, with each step's
state read from the database rather than from a checklist somebody ticks.

**The guide leaves when a programme goes live.** A tenant with a live programme
is past first run whatever else is missing; what is missing then is work, and
the worklist (`zolts.attention`) is where work is ranked. A guide that stays
becomes a banner nobody reads.

**A connection in error is not connected.** The runtime writes `error` on a
credential a provider refused; a guide that counted it as connected would tell
the operator the one thing the liveness check is shouting about is fine.

**Reviewed means activated.** Nothing records that a person read a programme,
and a step that ticked itself on a page view would be a step that lies. The
review step is done by the act that proves it, and until then it says what
reading a draft shows: what activating it would do today, before the click.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

CONNECTED = "active"
STEP_KEYS = ("blueprint", "connect", "review", "activate", "signal")

CONNECT_HOW = ("Store a credential with `zolts connect --tenant <slug> --provider <name>`, "
               "the secret on stdin, or POST /v1/crm/mappings for a mapped CRM. The console "
               "takes no credential: a secret pasted into a browser is a secret in a "
               "browser's history.")
REVIEW_HOW = ("Open a draft on Programs. The panel says what activating it would do "
              "today - who would enrol, who is held out, what week one costs - before "
              "the click.")
ACTIVATE_HOW = ("Activate is on the programme's panel, under the preview. It queues "
                "nothing until the policy gate has run.")
SIGNAL_HOW = ("A live programme's trigger puts its signal types on the watch list; a "
              "customer can also POST /v1/signals.")


@dataclass(frozen=True)
class Step:
    key: str
    title: str
    done: bool
    status: str          # what the database says, in a sentence
    how: str             # what to do next; empty once done
    view: str | None     # the screen the act lives on, when it has one


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def guide(*, blueprint: str, connections: Sequence[Mapping[str, Any]],
          program_statuses: Sequence[str],
          first_signal: tuple[str, str] | None) -> dict[str, Any]:
    """The five steps, each read from rows, and whether to show them at all."""
    statuses = list(program_statuses)
    drafts = sum(1 for s in statuses if s == "draft")
    live = sum(1 for s in statuses if s == "live")
    active = [c for c in connections if c.get("status") == CONNECTED]
    errored = [c for c in connections if c.get("status") == "error"]

    if statuses:
        chosen = (f"{blueprint}, chosen at signup; "
                  f"{_plural(len(statuses), 'starter programme', 'starter programmes')} published")
    else:
        chosen = f"{blueprint}, chosen at signup; no starter programme ships for it yet"

    if active:
        connected = ", ".join(sorted({str(c.get('provider')) for c in active})) + " connected"
    elif errored:
        c = errored[0]
        connected = f"{c.get('provider')} in error: {c.get('last_error') or 'the provider refused it'}"
    else:
        connected = "not connected"

    steps = [
        Step("blueprint", "Choose a blueprint", True, chosen, "", "programs"),
        Step("connect", "Connect a CRM", bool(active), connected,
             "" if active else CONNECT_HOW, None),
        Step("review", "Read a programme", live > 0,
             "read: a programme is live" if live else _plural(drafts, "draft to read", "drafts to read"),
             "" if live else REVIEW_HOW, "programs"),
        Step("activate", "Activate it", live > 0,
             _plural(live, "programme live", "programmes live") if live else "none live",
             "" if live else ACTIVATE_HOW, "programs"),
        Step("signal", "The first signal", first_signal is not None,
             f"arrived: {first_signal[0]} at {first_signal[1][:16].replace('T', ' ')}"
             if first_signal else "none yet",
             "" if first_signal else SIGNAL_HOW, "signals"),
    ]
    show = not any(s == "live" for s in statuses)
    nxt = next((s.key for s in steps if not s.done), None)
    return {"show": show, "steps": [asdict(s) for s in steps],
            "done": sum(1 for s in steps if s.done), "of": len(steps), "next": nxt}
