"""What a channel is, beyond the connector that talks to it.

Adding a second channel is not adding a connector. The connector contract has
existed since the first one — `Request` in, `Result` out — and it is not where
the assumptions live. Three things the worker did are email's, not every
channel's, and each was written as though there were only ever going to be one:

**What a touch costs.** Every dispatch was priced at `email.send`. `task` and
`crm` both have a registered provider, so a HubSpot task written for a
salesperson was billed to the customer as an email they never sent.

**What limits how many go out.** Every dispatch allocated a seat from the
mailbox fleet. A CRM task consumed no sending reputation and was blocked
anyway once the mailboxes hit their daily cap.

**What a step naming an unbuilt channel does.** `linkedin` and `ads` were
dispatchable with no provider behind them, so the shipped flagship program's
LinkedIn steps queued, failed as a permanent error, and were cancelled one by
one while the sequence carried on. Loud in a row nobody reads is silent.

A channel defined here is one this runtime can execute correctly. Everything
else is a step for a person — which is what the planner already did for an
unknown channel, and the right default: a play that names a channel we cannot
send on should reach somebody, not disappear.

The price of a channel is not decided here. `docs/12` prices actions, this
maps a channel onto one of those prices, and a channel whose action the price
list does not carry is dispatched and not separately billed — the step that
created it already costs 0.2 credits. Inventing a price here would contradict
the document, and the document wins.
"""

from __future__ import annotations

from dataclasses import dataclass


class ChannelNotDefined(LookupError):
    """This runtime cannot execute this channel correctly, so it will not try."""


@dataclass(frozen=True)
class Channel:
    """One channel the runtime can dispatch on its own."""
    key: str

    # The action in `docs/12`'s price list that one touch on this channel is.
    # None means the price list does not carry it: the touch is dispatched and
    # not separately billed, because `program.step` already charged for the
    # orchestration that produced it. It is deliberately not zero — zero is a
    # price somebody chose, and None is a price nobody has been asked for.
    credit_kind: str | None

    # What bounds the volume. Only sending has a fleet: a warmed mailbox has a
    # daily cap and a reputation to lose, and neither is true of a row written
    # into somebody's CRM.
    capacity: str | None = None

    # Whether the contact needs a resolvable address of this kind before the
    # runtime will attempt the touch.
    address: str | None = None

    @property
    def uses_mailbox_fleet(self) -> bool:
        return self.capacity == "mailbox_fleet"


CHANNELS: dict[str, Channel] = {
    "email": Channel("email", credit_kind="email.send", capacity="mailbox_fleet",
                     address="email"),
    # A task written for a person to act on. `docs/12` prices no such action,
    # and the step that created it is already billed, so this adds nothing to
    # the invoice. It is still a dispatch: the runtime performs it.
    "task": Channel("task", credit_kind=None),
    # Writing back to the customer's own CRM. Same reasoning, and the same
    # reason it must not be metered as a send: a sync that bills per record
    # would make an integration the most expensive thing a customer connects.
    "crm": Channel("crm", credit_kind=None),
}


def channel_for(key: str) -> Channel:
    if key not in CHANNELS:
        known = ", ".join(sorted(CHANNELS))
        raise ChannelNotDefined(
            f"no channel definition for '{key}'; this runtime can dispatch: {known}. "
            f"A channel needs a price in docs/12 (or a stated reason it carries "
            f"none) and a capacity model before it can send on its own")
    return CHANNELS[key]


def dispatchable() -> frozenset[str]:
    """The channels the runtime performs itself.

    Read by the planner, so the set that decides "queue this as a dispatch"
    and the set that decides "here is what it costs and what limits it" cannot
    disagree. Two literals would, and the one that drifted would be the one
    that spends money.
    """
    return frozenset(CHANNELS)
