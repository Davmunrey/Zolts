# 28 · Operator experience: the road to enviable

**Conclusion:** the console now answers *what do I do now* (ADR-060) and lets a person act on eight of eleven screens (ADR-061 to ADR-063). That makes it usable. It does not make it enviable. Enviable is three tests, and the product passes none of them yet:

| Test | What it means | Passes today |
|---|---|---|
| **An operator does their day inside it** | Not alongside it with a CRM tab, a spreadsheet and a Slack thread open | Nearly: nothing reloads and the console updates live (OX-6), a batch takes one reason (OX-7), the palette reaches every screen, programme and contact (OX-8). The phone cannot approve yet (OX-9) |
| **It shows a person something no other tool can, and proves it** | Per contact: the signal, the policy decision with its rule and pack digest, the step, the evidence behind each claim, the cost, the outcome. Every row of that exists in the database; no screen joins them | No |
| **A DPO and a CFO can sign off from a screen, not a deck** | The incrementality report is signed and frozen (ADR-043); a subject access request is `docs/11` COMP-2, *not built* | Half |

The consequence of getting this wrong is not a worse demo. It is that the moat `docs/01` names — governance, jurisdictional policy, incrementality — stays invisible at the point of use, and the product is compared on the one axis where it is weakest: how pleasant it is to send email from.

## Three levels, in the order they are built

Ordered by leverage per unit of operator attention, not by size. Each row has a binary exit criterion; a row without one is not on this list. Sizes are the same scale as `docs/24`.

### Level 2 first: depth. The features nobody else has, built on data that already exists

| ID | What | Why it makes the product enviable | Builds on | Exit criterion | Size |
|---|---|---|---|---|---|
| **OX-1** | **Why this person.** One screen per contact: every signal that touched them, every enrolment, every policy decision with rule key and pack digest, every proposal with its evidence and dropped claims, every touch with cost and provider, every outcome, every audit entry naming them. Newest first, one timeline | The governance moat, at the point of use. A CRO sees *why* an email went; a DPO sees the legal basis and the rule that allowed it; a subject access request is this screen exported (the first half of COMP-2). No competitor in `docs/01` can render this because none records it | `policy_decision.subject_id`, `touch.person_id` (ADR-056), `proposal.evidence`, `enrollment`, `outcome`, `audit_log.subject` | The browser check opens a contact who received a gated send and reads the rule key, the pack digest and a dropped claim off the screen. `GET /v1/people/{id}/timeline` answers the same rows | M |
| **OX-2** | **What would this programme do today.** Before Activate: how many accounts would enrol now, how many contacts each step would reach in week one, how many would be blocked by policy and under which rule, what it would cost in credits. Computed by the same functions that will run it | Activation is the scariest click in the product and the one with no preview. A number beside the button turns a guess into a decision. It is also the demo moment: change the audience, watch the count move | `audience.includes`, `admission.check`, `policy.evaluate` under the active pack, `billing.CREDITS`, the holdout split | The preview for a programme equals what activating it enrols in the same second, checked by a test that does both. A programme whose audience is unanswerable says so rather than showing zero | M |
| **OX-3** | **Which signals earn their keep.** Per signal type: fired, matched a programme, enrolled, reached, converted, within the metric window. Descriptive, not attributed: SIG-2 stays blocked on attribution and this does not pretend otherwise | The operator's first question after a month is "which of these is worth paying for". The catalogue prices signals by tier; nothing shows what each one produced | `signal`, `enrollment.context`, `outcome`, `zolts.signals` | Every signal type in the catalogue has a row, including the ones that fired zero times, and the funnel never counts a conversion outside the programme's declared window | S |
| **OX-4** | **Which copy works.** Reply and positive-reply rate per step and per variant, with the sample size beside the rate and no rate shown under the minimum `zolts.experiment` accepts | Every sequencing tool shows open rates. None shows a rate against a holdout with the sample size that makes it believable | `enrollment.variant`, `touch`, `outcome`, `MIN_CONVERSIONS_PER_ARM` | A step with fewer conversions than the floor shows the count and no rate; a test builds one at the floor and requires the rate to appear there | S |
| **OX-5** | **The report a CFO opens.** The frozen incrementality report as a first-class screen with a shareable, signed export: the digest on the page, the baseline it rests on, own-spend basis, cost per incremental meeting against the ceiling. Verified offline by `scripts/verify_report.py` | The reason the buyer moves from Marketing to Finance (`docs/01`). It exists as a panel inside a programme; nobody sends a panel to a CFO | ADR-043, `runtime/reporting.py`, `GET /v1/reports/{id}` | An exported report verifies with the public key on a machine that has never seen the database | M — sized S before it was found that nothing signed the document and no verifier existed |

### Level 1 next: the day. Usability that compounds

| ID | What | Why | Builds on | Exit criterion | Size |
|---|---|---|---|---|---|
| **OX-6** | **Live, no reload.** The console polls `/v1/console` and patches what changed; every action updates in place and never `location.reload()`. The worklist count in the rail moves while you watch | Every action today dumps the operator back through a full page load. Persisting the view (ADR-060) hid the cost; it did not remove it | ETag on `/v1/console`, the existing view model | No `window.location.reload()` remains in the surface; a test greps for it. The browser check approves a draft and sees the rail count fall without a navigation | M |
| **OX-7** | **Bulk, with reasons.** Select many: approve drafts, complete tasks, revive or retire dead actions. One reason for the batch, recorded on every row, and a refusal names which rows it refused | An operator with forty drafts does not click forty times; they open a spreadsheet instead, and the product loses the day | `zolts.reasons`, the per-row endpoints | A batch of three where one row is stale returns two done and one named refusal, and the two are recorded with the batch's reason | S |
| **OX-8** | **A palette that reaches everything, and no dead commands.** Every screen, every contact, every programme, every action from ⌘K. *New program from blueprint* is wired or removed: its handler was `function(){}`, and it is removed (ADR-071) | A command that does nothing is D-38 inside the palette. Keyboard-first is what an operator who lives in a tool expects | `#open-pal`, the blueprints list | A test fails on any palette command whose handler is empty; the browser check reaches the Outbox from the palette | S |
| **OX-9** | **The phone approves.** At 390px an operator can read a draft and approve or reject it, and can mark a task done | The person who unblocks the queue at 11pm is on a phone | ADR for VER-2 (the console at 390px is measured) | The browser check at 390px approves a draft and reads it back from the database | S |

### Level 3 last: trust. The polish that makes it feel finished

| ID | What | Why | Exit criterion | Size |
|---|---|---|---|---|
| **OX-10** | **Every number defines itself.** Hover or tap any figure: what it counts, over what window, from which table. Read from one registry the tests also read | A number without a definition is a number two people read differently in the same meeting | Every KPI tile and every `dl` value on every screen has a definition; a test enumerates them | S |
| **OX-11** | **First run inside the console.** A new tenant is walked from connect CRM → choose blueprint → review the programme → activate → first signal, on the screen, with each step's status read from the database | `quickstart` is a CLI. The founder could not find where to configure the product (this document exists because of that conversation) | A fresh tenant sees the guide; a tenant with a live programme does not | M |
| **OX-12** | **States that say something.** Every empty, loading and error state names what the runtime is doing or what failed, never a blank panel | An empty screen that says nothing looks like a screen that failed | A test renders every view with an empty model and requires a sentence | S |
| **OX-13** | **Keyboard and screen reader.** Full keyboard navigation of lists, ARIA on every control, contrast measured | Accessibility is table stakes for enterprise procurement and it is cheaper now than after the surface grows | An automated pass reports zero critical findings in the browser check | S |

## What this document is not

It is not a promise of dates. `docs/13` owns the calendar. Each row here ships as its own pull request with tests, a mutation guard where a rule is involved, an ADR where a decision constrains future work, and a line in `docs/24` when delivered. A row that ships without its exit criterion met is not shipped.

It is not a redesign. The design system in `DESIGN.md` and the brand in `docs/27` stand. Every screen here uses the components that exist.
