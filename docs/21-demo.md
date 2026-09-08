# The demo

A buyer does not ask whether the loop is connected. They ask what the number means and how it was computed, and the answer has to hold when they push on it. This is the script for that conversation, and the seeded data behind it.

## Running it

```sh
ZOLTS_DATABASE_URL=… ZOLTS_SECRET_KEY=… python3 scripts/seed_demo.py
```

Roughly 80 seconds. It creates a tenant named **Northwind Traders (demo data)** with a slug beginning `demo-`, seeds 12,000 accounts, and prints the tenant id, an API key and the measurement. `ZOLTS_DEMO_ACCOUNTS` changes the size — and changes what the demo shows, deliberately; see **When it says "not significant"** below.

## What is synthetic and what is not

Say this before the numbers, not after. A buyer who discovers it themselves stops believing the rest.

| Synthetic, and chosen | Not synthetic |
|---|---|
| The 12,000 accounts and their firmographics | Enrollment: the program's own trigger and `amount_usd >= 5000000` filter |
| A 4.0% control reply rate and a 7.5% treatment reply rate | Arm assignment: the real deterministic hash on the program's salt |
| Tier capacity, raised from the shipped 25/200/2,000 | The policy gate: every send carries a decision, allow or deny, with a reason |
| A 34% reply-to-opportunity rate, and deals worth roughly €24,000 | Conversions and deals: recorded through the real inbound webhook and the real CRM sync |
| | The measurement: the same functions that run for a paying customer |

Every one of those choices is printed in the seeder's own output under `assumptions`. The seeder has no code path that writes a lift, a significance flag or a pipeline figure — a test asserts it, by parsing the file and checking it never binds those names.

## The run

A representative result at the default size, from a run of the script rather than from memory:

| | |
|---|---|
| Enrolled | 12,677 of 36,000 accounts — the rest failed the program's own trigger or its audience |
| Left alone because already in a live deal | 40, delivered by the CRM before anything enrolled |
| Holdout | 10%, so 1,280 accounts are never contacted |
| Treatment conversion | 7.71% |
| Control conversion | 4.77% |
| **Measured lift** | **+2.95 points** |
| Minimum detectable effect | 1.76 points |
| Significant | Yes, because 2.95 > 1.76 |
| Opportunities (treatment · control) | 287 · 22 |
| Average deal, from the tenant's own synced deals | €26,483 |
| Incremental pipeline | **not reported** — the *opportunity* comparison is not significant at this size |

The last three rows are new and are the ones worth stopping on. The pipeline figure used to be the measured reply lift multiplied by €24,000, a constant in the runtime — which priced a positive reply as a deal, and priced an ecommerce reorder and an enterprise contract identically (D-49). It now rests on the opportunity arms and on the average of the deals this tenant has actually synced, and a run that has not earned the number does not print it (decision 39).

**The population is smaller than the account list, and that is the audience working.** Twelve thousand accounts leave four thousand enrolled and an arm too small to resolve a three-point effect. The default was raised to 36,000 rather than the rates being raised: the seeded reply rates have not moved since the first version of this script.

## The five beats

**1. A signal became a decision, not an email.** Show the policy decisions. Every action carries an allow or a deny with a rule key and a jurisdiction. There is no path from an agent to a provider that skips it.

**2. Ten percent of the money was deliberately not spent.** The holdout is not a setting, it is the reason the next number exists. Every program declares one; a waiver needs a written justification stored with the program.

**3. The lift is +2.95 points, and here is why we are willing to say so.** The minimum detectable effect at this sample is 1.76 points. Below that the product reports the lift and refuses the conclusion. That is the difference from every dashboard the buyer has been shown: this one can say *no*.

**4. Jurisdiction decides, and it is read out of a pack, not a prompt.** `policy_pack` in the output is printed straight from the shipped rules — Germany and Canada require consent for email, Spain, France, the UK and the US accept legitimate interest, and a country the pack does not cover is refused rather than sent to. A buyer can take that table to their own counsel.


**5. The number we did not print.** The lift is significant and the pipeline figure is still absent, because the *deal* comparison is not — 287 opportunities against 22, an effect the sample cannot resolve yet. Every competing dashboard would multiply the reply lift by an assumed deal size and show eight figures. The euro number appears when the deals earn it, from the customer's own average deal rather than from a constant, and the frozen report carries it with a digest (decision 39, ADR-043).

## When it says "not significant"

Run it with `ZOLTS_DEMO_ACCOUNTS=12000` and the same 3.5-point seeded effect comes back as **+2.27 points against an MDE of 3.15 — not significant**, with `pipeline: null`.

That is not a broken demo. It is the product declining to report €3M of pipeline it cannot support, on data where every competing tool would have drawn the chart. Show it second, after the resolved run. It is the most persuasive thirty seconds available, and it costs one environment variable.

## What a buyer will push on, and the honest answer

| Question | Answer |
|---|---|
| "Are these real customers?" | No. The population is synthetic and labelled as such on the tenant itself |
| "Did you pick the rates to make this work?" | Yes, and they are printed. 4.0% and 7.5%. The measurement of them is not picked |
| "Why is there no pipeline number?" | Because the opportunity comparison is not significant at this size, and a euro figure resting on a reply-rate lift is a reply priced as a deal. Raise the population and it appears; for a real tenant the same rule fills it from their own average deal, and the frozen report carries it (decision 39, ADR-043) |
| "What if my lift is smaller?" | Then the MDE curve says how much volume or holdout you need. Run the 6,000 case |
| "Why is the capacity so high?" | Raised for the demo. Left at the shipped 25/200/2,000 the measured lift goes *negative*, because intent-to-treat counts every treatment enrollment whether or not capacity reached it. Worth showing to a buyer who runs a small team — it is a real finding about their configuration, not a flaw |
