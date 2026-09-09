"""Everything a program must satisfy before a version of it exists.

Four checks decide whether a program is admissible, and until now all four
ran in exactly one place: the `POST /v1/programs` handler. Every other route
to a stored program skipped them —

    runtime/cli.py:60             `zolts bootstrap`
    runtime/onboarding.py:238     self-service signup
    scripts/seed_demo.py:152      the demo tenant
    scripts/smoke_runtime.py:94   the smoke run

— and the middle one is the one that matters, because signup publishes into a
real tenant with no operator watching. A program declaring no holdout, an
audience by `segment_ref` that nothing resolves, and an enrichment field the
price list does not carry was accepted through that path and activated. The
API refuses the same document with three separate 422s.

So the checks moved to the choke point every path already goes through:
`runtime.repo.programs.publish`. A guard reachable by one of five callers is
not a guard, it is a habit of the caller that happens to have it.

The imports are inside the function on purpose. `runtime.engine.enroll`
imports `runtime.repo.programs` (enroll.py:15), so a module-level import here
would close a cycle the moment the repo imports this module. Nothing in this
module's body touches the engine, which is what keeps `from runtime.engine
import admission` cheap and safe at the top of the repo.
"""

from __future__ import annotations

from typing import Any


class NotAdmissible(ValueError):
    """A program the runtime refuses to store, and the reason why."""


def check(spec: dict[str, Any], program_key: str) -> None:
    """Raise `NotAdmissible` if this spec must not become a version.

    The four failures, each of which is a program that looks armed and does
    something other than what it says:

    * **No holdout.** Product invariant 4. Without one there is no measured
      lift, only a number nobody can attribute.
    * **An audience the runtime cannot evaluate.** Decision 31: a
      `segment_ref` nothing resolves, an audience that writes, one that
      selects no subject, or none at all.
    * **An enrichment field with no price.** Decision 33. A field the price
      list does not carry cannot be billed, so a runtime that bought it would
      pay for its customers.
    * **A primary metric the runtime cannot measure.** D-51: the schema
      requires one, the console displayed it, and every program was measured
      on the same three outcome types with no window. A name nothing can
      count is refused here rather than silently measured as something else.
    * **A guardrail metric the holdout cannot exhibit.** D-76. A guardrail is
      answered by the holdout the same way the primary metric is, so it has to
      be something the control arm can also do. It cannot unsubscribe from an
      email it was never sent — and a comparison whose control arm is
      structurally zero reports *not resolvable* forever while reading like a
      measurement still gathering data. Refused here, with the reason, rather
      than measured to nothing.
    * **A policy override the pack cannot be reconciled with.** An override
      that cannot be read as stricter is refused rather than ignored;
      ignoring is the worst of the three, because it lets an operator believe
      they are protected by a rule nothing applies.
    """
    from runtime.engine import audience, enrich_step, enroll
    from zolts import metrics, policy

    try:
        enroll.holdout_pct(spec, program_key)
        audience.check(spec, program_key)
        enrich_step.check(spec, program_key)
        experiment = spec.get("experiment") or {}
        primary = metrics.resolve(experiment.get("primary_metric"))
        _guardrails(experiment, primary)
        overrides = (spec.get("policy") or {}).get("overrides") or {}
        for rule in policy.PACK_V1.values():
            policy.tighten(rule, overrides)
    except (enroll.HoldoutMissing, audience.AudienceError,
            enrich_step.EnrichmentNotPriced, metrics.MetricError,
            policy.OverrideNotUnderstood) as exc:
        # One type at the boundary, the original message intact. Callers map
        # it to a 422; the point of the single type is that a caller cannot
        # catch three of the four and let the fourth through.
        raise NotAdmissible(str(exc)) from exc


def _guardrails(experiment: dict[str, Any], primary: Any) -> None:
    """Every declared guardrail resolves, differs from the primary, and is unique.

    `metrics.resolve_guardrail` refuses the two names that cannot work at all —
    one the runtime cannot count, and one only a contacted person can produce.
    Two more failures need the primary metric in hand, so they live here:

    * **A guardrail that is the primary metric.** The same comparison twice,
      reported as two findings. It cannot disagree with itself, so it can never
      fire, which is a guard that passes when you break what it guards.
    * **The same guardrail declared twice.** Harmless to measure and confusing
      to read: two rows, one number, and a reader who counts breaches gets two.
    """
    from zolts import metrics

    seen: set[str] = set()
    for name in experiment.get("guardrail_metrics") or []:
        metrics.resolve_guardrail(name)
        if name == primary.name:
            raise metrics.MetricError(
                f"'{name}' is already this programme's primary metric, so it cannot "
                f"also guard it. A guardrail asks whether winning the primary metric "
                f"cost something else; declaring the same name asks whether it cost "
                f"itself")
        if name in seen:
            raise metrics.MetricError(f"guardrail metric '{name}' is declared twice")
        seen.add(name)
