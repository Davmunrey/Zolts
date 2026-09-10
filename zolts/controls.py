"""Which declared controls the runtime enforces, and which it does not.

A program is a contract a customer writes. Most of its fields describe what the
program *is* — an audience, a sequence, a metric. A handful are **controls**:
the customer states a limit and expects the runtime to hold it. Money, contact
pressure, capacity.

A control the runtime does not read is the worst member of this repository's
commonest defect family, because it fails in the flattering direction. A field
nothing reads that describes a program is inert. A *limit* nothing reads is a
promise kept only by luck, and the customer has no way to tell the two apart:
they wrote a ceiling, nothing exceeded it this week, and the system looks
correct.

`spec.budget` was exactly that (D-63). Five fields; one honoured, and only for
one of the eight priced actions. `docs/07` named per-program, per-tenant and
daily ceilings as the control for spend leakage and only the per-tenant ceiling
existed. Nothing failed, because the tenant ceiling does fire and defers actions
with `budget: ...` — so an operator watching it work would reasonably conclude
the program's own block was what worked.

This module exists so that can never again be true silently. Every control the
schema offers is named here with its status. `HONOURED` entries say where the
enforcement lives, so the claim can be checked. `NOT_HONOURED` entries say what
the customer loses, so nobody has to guess whether it matters. A test fails when
the schema grows a control this file does not name, which is the only way a
registry like this stays true.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Control:
    """One limit a program or a blueprint may declare."""
    path: str
    what: str
    #: Where the runtime enforces it, or None when it does not.
    enforced_by: str | None
    #: Where the runtime *measures and prints* it without holding it. A ceiling
    #: that appears on a report is not a ceiling the engine keeps, and treating
    #: the two as one is the exact confusion this module exists to prevent. A
    #: reported control stays in `NOT_HONOURED`; it simply stops being invisible
    #: (decision 45).
    reported_by: str | None = None
    #: What a customer loses by declaring it today. Empty when it is enforced.
    consequence: str = ""
    #: Which document declares it. A program's `spec.policy` and a blueprint's
    #: are different schemas that happen to share a name, so the two are never
    #: matched against each other.
    surface: str = "program"

    @property
    def honoured(self) -> bool:
        return self.enforced_by is not None

    @property
    def reported(self) -> bool:
        """Measured and shown, but not held. Never a substitute for honoured."""
        return self.reported_by is not None


CONTROLS: tuple[Control, ...] = (
    # -- honoured -------------------------------------------------------
    Control(
        "spec.policy.overrides.max_touches_per_person_per_week",
        "contact pressure, per person per week",
        enforced_by="runtime/engine/gate.py, read into policy.ActionContext"),
    Control(
        "spec.enrich.account.max_cost_per_account",
        "ceiling the waterfall may spend resolving one account",
        enforced_by="runtime/engine/enrich_step.py, passed as the waterfall cap"),
    Control(
        "spec.enrich.person.max_cost_per_contact",
        "ceiling the waterfall may spend resolving one contact",
        enforced_by="runtime/engine/enrich_step.py, passed as the waterfall cap"),
    Control(
        "spec.experiment.holdout_pct",
        "share held back, so lift can be measured at all",
        enforced_by="zolts/experiment.py; mandatory or waived in writing"),

    # -- honoured, but narrower than it reads ---------------------------
    Control(
        "spec.budget.monthly_credits",
        "ceiling on what this program may spend in a billing period",
        enforced_by="runtime/engine/generate.py, as the SpendGuard ceiling",
        consequence=(
            "Narrower than the name promises, and this is the trap. It bounds "
            "**model generation only** — one of the eight priced actions. Sends, "
            "enrichment, signal checks and dossiers on this program are bounded by "
            "the tenant's credit ceiling and by nothing program-specific. A customer "
            "running two programs cannot cap one of them")),

    # -- not honoured ---------------------------------------------------
    Control(
        "spec.budget.on_exceed",
        "what the runtime does when the ceiling is reached",
        enforced_by=None,
        consequence=(
            "Nothing at runtime branches on it, so `pause_and_alert`, `throttle` and "
            "`continue_and_alert` are the same program. `docs/07` names "
            "`on_exceed: pause_and_alert` as the control for spend leakage. It was "
            "also a console dial an operator could set (ADR-029), which is a control "
            "that reports success and does nothing — removed from the tunable set "
            "until it is real")),
    Control(
        "spec.budget.max_cost_per_account",
        "ceiling on what this program may spend resolving one account",
        enforced_by=None,
        consequence=(
            "Read by one validation rule and enforced by nothing. The cap of this "
            "name that *is* enforced is `spec.enrich.account.max_cost_per_account`, "
            "a different field in a different block — so a customer who sets the "
            "budget one and watches the enrich one fire has every reason to believe "
            "theirs is working")),
    Control(
        "spec.budget.max_cost_per_person",
        "ceiling on what this program may spend on one person",
        enforced_by=None,
        consequence="Spend on a person is bounded only by the tenant's credit ceiling"),
    Control(
        "spec.budget.max_cost_per_meeting",
        "ceiling on acquisition cost per meeting booked",
        enforced_by=None,
        reported_by="zolts/report.py, on the frozen incrementality report",
        consequence=(
            "Reported and never acted on (decision 45). The signed report carries the "
            "cost per *incremental* meeting, the declared ceiling, and whether the "
            "period finished above it — so a partner quoting the ceiling in a letter "
            "now quotes a number the document holds. Nothing stops or throttles on a "
            "breach: a programme is above its cost per meeting every day until the "
            "first one lands, so a stop here would kill programmes that are working. "
            "The breach is a whole reporting period, and the operator decides")),
    Control(
        "spec.route.tiers.capacity_per_week",
        "how many accounts a tier may take in a week",
        enforced_by="runtime/engine/enroll.py, in resolve_tier at the routing choke point",
        consequence=""),
    Control(
        "spec.route.tiers.capacity_per_week_per_rep",
        "how many accounts one representative may take in a week",
        enforced_by=None,
        consequence=(
            "A human's queue can be filled past what they can work — and unlike every "
            "other entry here, this one cannot be enforced by writing code against the "
            "schema as it stands. **There is no representative anywhere in the data "
            "model.** No `user` or `seat` table exists; identity is a tenant and an API "
            "key, and the review queue records `approved_by` as `key:<uuid>`, a "
            "credential rather than a person. `account` and `enrollment` carry no owner "
            "column; only `opportunity` does, filled from the CRM's owner long after "
            "routing. So the count has nothing to count against. It shares a root cause "
            "with `spec.route.strategy` — `owner_of_record` and `territory_round_robin` "
            "route to somebody the system cannot name — and both wait on decision 50")),
    Control(
        "spec.plays.*.steps.sla_hours",
        "how long a step may wait before it is late",
        enforced_by=None,
        reported_by="runtime/repo/tasks.py, as the deadline on the work item itself",
        consequence=(
            "Measured and reported, never acted on: an overdue task is listed and "
            "nothing escalates it, because escalation needs somebody to escalate to "
            "and no representative exists in the data model (decision 50). It was "
            "measured by nothing at all until D-84, and could not have been: the work "
            "item it timed had no completion path, so a deadline against it would "
            "have reported every task as breached for ever (D-83)")),
    Control(
        "spec.enrich.*.accuracy_sla",
        "the measured accuracy a provider must reach before it may be asked",
        enforced_by="runtime/enrichment.py, passed into zolts.waterfall.optimise",
        consequence=""),
    Control(
        "spec.route.strategy",
        "who receives the enrolled account",
        enforced_by=None,
        consequence=(
            "Read by nothing at all, so `score_desc`, `territory_round_robin` and "
            "`owner_of_record` are the same programme: every enrolment is routed by "
            "the tier predicates alone and no account is assigned to anybody. It was "
            "offered as a console dial and escaped the guard that refuses one, because "
            "that guard compared the dial list against this file and this file did not "
            "name it (D-80). Taken out of the dial list on the same terms as "
            "`budget.on_exceed`: it returns when a rep exists on an enrolment for it "
            "to assign to"),
    ),
    Control(
        "spec.enrich.*.skip_if_known",
        "do not buy a field the record already has",
        enforced_by=None,
        consequence=(
            "The command-line sweep selects only records missing the field, so the "
            "waste it prevents does not arise on that path. It is unread rather than "
            "violated — but a second caller would not inherit that protection")),
    Control(
        "spec.experiment.guardrail_metrics",
        "metrics that must not degrade while the experiment runs",
        enforced_by=None,
        reported_by="zolts/report.py, on the frozen incrementality report",
        consequence=(
            "Measured against the same holdout as the primary metric and never acted "
            "on: a degraded guardrail qualifies the verdict paragraph and pauses "
            "nothing, exactly like the cost ceiling above (decision 45). What is now "
            "enforced is admissibility, not the limit — a guardrail naming something "
            "the holdout cannot exhibit is refused at publication rather than measured "
            "to a permanent *not resolvable* (D-76)")),

    # -- declared by a blueprint, carried into a program's policy overlay,
    #    and read by nothing (D-64) ---------------------------------------
    Control(
        "spec.policy.special_category_inference",
        "whether the runtime may infer special category data about a person",
        enforced_by=None,
        surface="blueprint",
        consequence=(
            "The only value the schema allows is `forbidden`, so a blueprint can "
            "state the prohibition and nothing checks it. `zolts/catalog.py` carries "
            "the whole policy block into the overlay, so the declaration travels the "
            "full distance to a live program and is then consulted by nobody — the "
            "gate reads one key from the merged policy and this is not it. Article 9 "
            "data is the category where a wrong inference is a regulator's letter "
            "rather than a bad send")),
    Control(
        "spec.policy.copy_approval_required",
        "whether generated copy needs a person's approval before it may send",
        enforced_by=None,
        surface="blueprint",
        consequence=(
            "The auto-send gate is decided per play by `auto_send_requires.eval_score` "
            "(`zolts/dsl.py` refuses a play that auto-sends without one). A blueprint "
            "that says approval is required for its whole archetype does not tighten "
            "that, so an archetype meant to be human-reviewed is not")),
    Control(
        "spec.policy.discount_authority",
        "what commercial latitude a generated message may offer",
        enforced_by=None,
        surface="blueprint",
        consequence=(
            "Nothing reads it and nothing constrains the copywriter against it, so a "
            "generated message may offer terms the archetype does not permit. "
            "Provenance (ADR-012) requires a claim to quote a source; it says nothing "
            "about an offer")),
)

HONOURED = tuple(c for c in CONTROLS if c.honoured)
NOT_HONOURED = tuple(c for c in CONTROLS if not c.honoured)
#: Held by nobody, but measured and shown. A separate tuple rather than a third
#: value of `honoured`, so no caller can widen "the runtime enforces this" by
#: accident: every reported control is still an unenforced one.
REPORTED = tuple(c for c in CONTROLS if c.reported)
BY_PATH = {c.path: c for c in CONTROLS}


def _matches(path: str, declared: str) -> bool:
    """Whether a concrete dotted path matches a registry path with `*` segments.

    Paths are shaped by `declared_paths`, which does not index list elements: a
    field inside a list of tiers is `spec.route.tiers.capacity_per_week`, not
    `spec.route.tiers.0.capacity_per_week`. Writing the patterns as though lists
    were indexed produced a registry that matched nothing and reported every
    program clean — the same defect it exists to catch, one level up. The test
    that resolves every path against the schema is what keeps that honest.
    """
    want, got = path.split("."), declared.split(".")
    if len(want) != len(got):
        return False
    return all(w == "*" or w == g for w, g in zip(want, got))


def declared_paths(spec: dict) -> list[str]:
    """Every dotted path present in a program spec, one per leaf and branch."""
    out: list[str] = []

    def walk(node, prefix: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                here = f"{prefix}.{key}"
                out.append(here)
                walk(value, here)
        elif isinstance(node, list):
            for item in node:
                walk(item, prefix)

    walk(spec, "spec")
    return out


def unenforced(spec: dict, surface: str = "program") -> list[Control]:
    """The controls this document declares that the runtime does not hold.

    Publishing a program says which of its own limits are decoration. Silence
    here was the defect; a list is the correction.

    `surface` keeps a program's `spec.policy` and a blueprint's apart. They are
    different schemas wearing the same name, and matching one against the other
    would report a limit the document could not have declared.
    """
    declared = declared_paths(spec)
    return [c for c in NOT_HONOURED
            if c.surface == surface and any(_matches(c.path, d) for d in declared)]
