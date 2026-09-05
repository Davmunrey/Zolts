# 18 — Decision register: questions and default recommendations

## How to use this document

140 decisions. **124 carry a default recommendation: unless you contradict them, they execute as written.** Three of the blocking twelve are now closed (B4, I10, and the design rule in block O); the remaining 🔴 are blocking and only the founder can answer them — they depend on facts (your network, your capital, your risk appetite) that no analysis can substitute.

Usage rule: do not answer everything. Answer the 🔴, strike through the defaults you disagree with, and the rest is settled. An unrecorded decision reopens every six weeks and costs more than a mediocre recorded one.

---

## A · Identity and thesis

| # | Question | Default recommendation | Consequence |
|---|---|---|---|
| A1 🔴 | Is Zolts a product, or the internal platform of a service you sell? | **Pure product.** Services only as discovery, capped at 20% of revenue | Sets the multiple: 3× revenue (services) versus 10-15× (product) |
| A2 | Head-on against Clay, or a layer above it? | **Coexist in phase 1** (Clay table importer), displace in phase 3 | Attacking head-on without a mature product hands the comparison to the leader |
| A3 | Does Zolts decide, or also execute? | **It executes.** | Deciding without executing means a dashboard, at dashboard prices |
| A4 | Single brand or a product suite? | **Single.** One name, one promise | A premature suite fragments the message and the team |
| A5 🔴 | Three-year goal: independent, or strategic acquisition? | — | Independent → invest in self-serve and margin. Acquisition → invest in integrations and data HubSpot/Salesforce lack |
| A6 🔴 | What is the founding "no" — what will you never do, even if it sells? | Proposed: no data resale, no unsupported scraping, no black-box AI SDR | Without a written "no", the first large customer redefines the company |
| A7 | Open-source any part? | **DSL and connector SDK under Apache 2.0; runtime closed** | Distribution and trust without giving away the moat |

## B · ICP and market

| # | Question | Default recommendation | Consequence |
|---|---|---|---|
| B1 🔴 | Initial geography | Depends on where your network is. Proposed: **Iberia plus LATAM** for design speed, **UK/NL/DACH** for ACV | Selling outside your network in phase 0 doubles CAC and cycle |
| B2 | Product language | **English UI by default, Spanish available.** Docs in English | A Spanish-only product closes 80% of the market and the funding round |
| B3 | Target company size | **50-500 employees** | Under 50 lacks budget and pain; over 500 brings a nine-month procurement cycle |
| B4 ✅ | Entry vertical | **DECIDED: B2B SaaS, 50-500 employees.** Agencies remain a design-partner target for template extraction, not an entry segment | Fixes the entry blueprint, the signal set and the phase 1 policy pack |
| B5 | Greenfield or rip-and-replace? | **Target those who already have a stack and suffer it** | The budget already exists; educating a greenfield costs 3× |
| B6 🔴 | Do you have three companies that will sign a paid pilot within 30 days? Names | — | If not, phase 1 starts by finding them, not by building |
| B7 | Free or paid pilot? | **Always paid.** €5k for three months | A free pilot produces polite, useless feedback |
| B8 | Who defines pilot success? | **Written, signed, with a metric and a threshold, before starting** | Without a prior criterion every pilot "goes well" and none converts |
| B9 | Accept customers outside the ICP? | **At most one, if they pay and do not demand roadmap** | Each exception costs roughly six weeks of non-reusable engineering |
| B10 | Agency channel from the start? | **Do not sell through channel before month 9**, but recruit two agencies as design partners | Agencies are your best source of templates and your worst premature channel |
| B11 | Name Clay in the pitch? | **Yes, explicitly** | A known enemy shortens the sale; a new category lengthens it |
| B12 | Real SAM, not a slide TAM? | **Define the ~30-50k European companies with a real GTM team** | An inflated TAM produces an inflated hiring plan |

## C · Product and scope

| # | Question | Default recommendation | Consequence |
|---|---|---|---|
| C1 | Visual Studio in phase 1? | **No.** YAML plus CLI plus a read-only Studio. Bidirectional editor in phase 2 | The visual editor is 40% of frontend effort and validates none of the thesis |
| C2 | Multi-workspace per customer? | **Yes in the schema from day one, not in the UI until asked** | Retrofitting multi-workspace is a brutal data migration |
| C3 | Lightweight own CRM for those without one? | **No.** A minimal "pipeline" object, never a CRM | The trap that has killed dozens of GTM tools |
| C4 | Unified reply inbox? | **Yes, phase 2** | Without an inbox you are background software: nobody opens you daily, and what is not opened gets cancelled |
| C5 | Mobile app? | **No.** Notifications and approvals to Slack | — |
| C6 | Slack/Teams as the approval surface? | **Yes, Slack first** | Email approvals die; tier 1 stalls |
| C7 | Public template marketplace? | **Private until 20 customers** | An empty marketplace signals an empty product |
| C8 | Raw SQL over the customer's warehouse? | **Yes, sandboxed and limited** | It is exactly what the GTM engineer is buying; withholding it insults them |
| C9 | Public API from when? | **Phase 3, versioned** | An early public API freezes decisions that still need to change |
| C10 | MCP server? | **Yes, read plus simulation in phase 2**; externally effective actions require UI approval | A real differentiator in 2026 at low cost |
| C11 | Support agencies managing N customers? | **Yes: org > workspace hierarchy in the schema** | Without it you lose your best future distribution channel |
| C12 | Assisted or self-serve onboarding? | **Assisted through customer 10; self-serve mandatory thereafter** | If customer 15 still needs help, you do not have a product |
| C13 | What if the customer has no warehouse? | **Zolts-managed Postgres as a fallback** | Requiring a warehouse removes roughly 60% of the European mid-market |
| C14 | Clay/Apollo/HubSpot importer? | **Yes, day one** | The single largest reducer of entry friction |

## D · Data and integrations

| # | Question | Default recommendation | Consequence |
|---|---|---|---|
| D1 | Which CRM on day one? | **HubSpot first**, Salesforce second, Attio third | HubSpot dominates the European mid-market, your ICP |
| D2 | Zero-copy or copy? | **Hybrid:** minimal operational copy plus analytical zero-copy | Full copy triggers the governance objection; full zero-copy makes latency unworkable |
| D3 | How many data providers to sign first? | **Three: one cheap for coverage, one premium, one for verification** | With fewer than three, the router has nothing to optimise |
| D4 | Credit resale or bring-your-own API key? | **Both.** BYO unlocks those with existing contracts; resale provides margin | Resale-only loses the sophisticated; BYO-only kills the margin |
| D5 | Accept provider minimum commitments? | **Not until you have volume.** Pay more per unit for flexibility | A minimum signed in month 2 is dead cash in month 6 |
| D6 | Web deanonymisation in-house or partner? | **Partner** | A legal minefield in the EU; not your battle |
| D7 | Third-party intent (Bombora/G2) in phase 1? | **No.** Expensive and low-precision in the European mid-market | Large spend with undemonstrable lift |
| D8 | Store PII or references only? | **Encrypted vault with per-tenant keys; always minimise** | — |
| D9 | Enrich before or after fit scoring? | **After, without exception** | The single largest saving in the system (30-50%) |
| D10 | Per-field cache TTL | Email 180d · phone 365d · firmographics 90d · technographics 45d | Longer TTL is cheaper but degrades accuracy; these are the balance |
| D11 | Share hit-rate statistics across tenants? | **Yes, aggregate metrics only, never values. Declared in the DPA** | Crossing that line is an existential risk |
| D12 | What if a provider demands exclusivity? | **Refuse** | Exclusivity destroys the router thesis |

## E · AI and agents

| # | Question | Default recommendation | Consequence |
|---|---|---|---|
| E1 | Own model or API? | **API.** Fine-tuning only for cheap classification | Training an own model in 2026 burns capital with no advantage |
| E2 | Multi-model router? | **Yes, from phase 2** | Cost and resilience against outages |
| E3 | Can the customer bring their own model or API key? | **Yes, on Enterprise** | A recurring procurement requirement in banking and healthcare |
| E4 | Auto-send from when? | **Never on tier 1.** Tier 2 from phase 3 with eval ≥0.85 | — |
| E5 | Who is liable if the agent writes something false? | **Customer: content and legal basis. Zolts: the system and its guardrails.** Explicit clause in the terms | Without a written split, the first incident is litigation |
| E6 | AI content disclosure? | **Configurable, enabled by default in the EU** | — |
| E7 | Global or per-tenant golden set? | **Global plus 50 cases per tenant** | — |
| E8 🔴 | Do you train on customer data? | Proposed: **no by default; opt-in with economic consideration** | "We do not train on your data" is either a major 2026 sales argument or an asset you give away |
| E9 | AI voice? | **Not before month 12** | Regulatory and brand risk disproportionate to the return |
| E10 | AI cost passed through or included? | **Passed through and visible as credits** | Cost transparency differentiates against black boxes |

## F · Execution and channels

| # | Question | Default recommendation | Consequence |
|---|---|---|---|
| F1 | Do you send, or does the customer with their mailboxes? | **Phase 1: customer mailboxes via partner. Phase 3: optional in-house infrastructure** | Taking on someone else's reputation too early burns yours |
| F2 | Sell managed domains and mailboxes? | **Yes, from phase 3** | High margin on what becomes the scarce resource |
| F3 | LinkedIn: API, partner, or nothing? | **Partner using customer accounts. Never in-house browser automation** | Getting customer accounts banned is irreparable reputational damage |
| F4 | WhatsApp? | **Yes, via an official BSP**, for LATAM and local services | The dominant channel in your design markets |
| F5 | Own dialer? | **No. Partner, phase 3** | — |
| F6 | Ads (synced audiences)? | **Yes, phase 2** | Cheap to build, high perceived value, covers tier 3 |
| F7 | Global cross-program frequency cap? | **Mandatory, not settable to zero** | Without it, three simultaneous programs burn the account |
| F8 | What if the customer wants to send 10× the recommendation? | **Hard limit with a signed, recorded override** | Do not be a silent accomplice: it is your platform reputation |
| F9 | Block current-customer and competitor domains? | **Yes, by default** | Emailing an existing customer is the classic incident |
| F10 | Reply handling inside the product? | **Yes, phase 2** (see C4) | — |

## G · Measurement

| # | Question | Default recommendation | Consequence |
|---|---|---|---|
| G1 | Mandatory holdout without exception? | **Mandatory with a written, recorded waiver** | A recorded exception disciplines better than an absolute ban |
| G2 | Default holdout percentage? | **10%**, minimum 5% | — |
| G3 | Randomisation unit | **Account** in B2B; person in B2C; geo where contamination exists | Randomising wrongly invalidates the entire experiment |
| G4 | Customers without volume for significance? | **Umbrella multi-program experiment, and say so explicitly** | Faking significance destroys the credibility of the whole thesis |
| G5 | Do you report negative results? | **Yes** | Your greatest trust generator and your biggest cultural differentiator |
| G6 | Default primary metric | **Opportunities created at 90 days**, not meetings | Meetings inflate; opportunities do not |
| G7 | Keep multi-touch attribution? | **Yes, labelled as correlational diagnosis** | Removing it entirely creates resistance from the marketing team |
| G8 | Anonymised cross-customer benchmark? | **Yes, from 20 customers** | A first-order marketing and product asset |

## H · Compliance and legal

| # | Question | Default recommendation | Consequence |
|---|---|---|---|
| H1 🔴 | Is Zolts a processor or a joint controller? | Proposed: **processor**. Requires counsel validation before the first contract | Getting this wrong is existential risk, not just another clause |
| H2 | EU residency from when? | **In the architecture from phase 1, activated in phase 3** | Retrofitting residency means rebuilding the infrastructure |
| H3 | Block Germany by default? | **Yes, with a customer-documented override** | The conservative stance sells in enterprise; the permissive one sues you |
| H4 | SOC 2 when? | **Type I month 6, Type II month 12** | Earlier is premature; later blocks pipeline |
| H5 | In-house or fractional DPO? | **Fractional** | — |
| H6 | Professional indemnity and cyber insurance? | **Yes, before the first enterprise contract** | — |
| H7 | Who is liable for how the customer uses it? | **Customer for content and legal basis; Zolts for the system.** Acceptable use policy with a right to cut off | — |
| H8 | Do you cut off a customer who spams? | **Yes, written into the contract** | A toxic customer contaminates the entire platform's reputation |
| H9 | External audit of the policy engine? | **Yes, month 12** | Turns compliance into marketing material |
| H10 | Default retention | **12 months for non-converted PII** | — |

## I · Pricing and business model

| # | Question | Default recommendation | Consequence |
|---|---|---|---|
| I1 | Freemium, trial, or paid only? | **14-day trial with limited credits. Never freemium** | Freemium with variable COGS is a haemorrhage |
| I2 | Public pricing? | **Yes on Starter/Growth; contact sales on Scale+** | Hiding the entry price inflates CAC |
| I3 | Annual or monthly? | **Annual with two months discount; monthly at +20%** | Cash and churn |
| I4 | Single credit or separate currencies? | **Single** | A simple unit sells; complexity generates disputes |
| I5 | Credit rollover? | **One month, maximum 50%** | No rollover breeds resentment; unlimited destroys the forecast |
| I6 | Automatic overage or cut-off? | **Overage with an 80% alert and a configurable hard ceiling** | One surprise invoice costs the whole account |
| I7 | Seats or unlimited? | **Operator seats; viewers free** | Charging to view reduces internal spread, your best salesperson |
| I8 | Design partner discount? | **50% for life in exchange for a public reference and case study, contractually** | An undocumented discount is never collected on |
| I9 | Euros or dollars? | **Both, local pricing, no conversion** | — |
| I10 ✅ | Act 2: anchored to governed spend or to incremental pipeline? | **DECIDED: hybrid.** A floor of 3-6% of governed GTM spend plus a bonus on holdout-verified lift. Predictable revenue with the incentive aligned, at the cost of a contract that takes longer to negotiate | Zolts is infrastructure with skin in the game, not a pure risk partner |
| I11 | Charge for implementation? | **Always** | What is free is neither implemented nor used |
| I12 | When do you raise prices? | **Customer 15: +30%, with 12-month grandfathering** | Raising late anchors every future cohort to a low price |

## J · Zolts's own GTM

| # | Question | Default recommendation | Consequence |
|---|---|---|---|
| J1 | Founder-led sales until when? | **Month 7 or 30 customers, whichever is later** | Hiring an AE without a validated playbook burns six months and €60k |
| J2 | Outbound, inbound, or community? | **Own outbound (dogfooding) plus technical GTM-engineering content** | Community is this ICP's natural channel |
| J3 | Who writes the content? | **The founder and the forward-deployed engineer** | Operator content, not marketing content: this ICP detects the latter instantly |
| J4 | Build in public? | **Yes** | The cheapest channel for this ICP and a recruiting engine |
| J5 | GTM/RevOps communities? | **Present from month 1** | — |
| J6 | Free tool as a lead magnet? | **Yes: a GTM spend audit or a deliverability validator** | Dual use: captures leads **and** captures the baseline Act 2 requires |
| J7 | Which category do you push? | **"GTM Operating System".** Pick one and repeat it a thousand times | Changing category every quarter resets recognition to zero |
| J8 | Product site or manifesto? | **Manifesto plus interactive demo** | This ICP buys the thesis before the features |
| J9 | Public demo without signup? | **Yes, sandbox with fictional data** | — |
| J10 | Hero metric on the site? | **Measured numbers** (−X% cost per contact, verified lift), never adjectives | — |
| J11 | Partner programme? | **Month 9** | — |
| J12 | When do you hire marketing? | **After the AE, month 10** | — |

## K · Team and organisation

| # | Question | Default recommendation | Consequence |
|---|---|---|---|
| K1 🔴 | Do you have a technical co-founder? | — | Without one, phase 1 becomes recruiting rather than building; the whole plan changes |
| K2 | Who owns the product? | **The founder through month 12** | Delegating product before finding fit dilutes the thesis |
| K3 | Remote, hybrid, or on-site? | **Hybrid with a core in one city** | Phase 0 needs human bandwidth, not calendars |
| K4 | Equity pool? | **12-15% for the first five hires** | — |
| K5 | Seniors or juniors? | **Three seniors, zero juniors, through month 12** | A junior in phase 0 consumes more time than they add |
| K6 | When the forward-deployed engineer? | **First non-founding hire** | The role that prevents the services trap |
| K7 | What do you outsource? | **Design and fractional legal. Never the core** | — |
| K8 | Internal language | **English in code and documentation** | Hire outside the country without rewriting anything |
| K9 | Who answers at 3 a.m.? | **Formal on-call from customer 5** | A nightly sending failure with no owner costs a logo |

## L · Funding

| # | Question | Default recommendation | Consequence |
|---|---|---|---|
| L1 🔴 | Bootstrapped, pre-seed, or seed? | Proposed: **pre-seed €700k-1.2M** | Variable COGS suffocates bootstrapping in this model |
| L2 | When do you raise? | **With 10 customers and demonstrated lift (month 5-7)** | Earlier you sell slides and get valued as slides |
| L3 | Which investors? | **Angels who are CROs or RevOps leaders at ICP companies** | Simultaneously capital, pipeline and validation |
| L4 | Minimum acceptable runway | **18 months post-round** | Less turns every decision into a survival decision |
| L5 | Venture debt? | **Not at pre-seed** | — |
| L6 | Grants (ENISA, CDTI, EIC)? | **Yes, with outsourced administration** | Non-dilutive money, but in-house administration devours the team |
| L7 | Maximum pre-seed dilution | **15-20%** | — |
| L8 | What milestone buys the next round? | **€1M ARR plus NRR >110%** | Without a defined milestone, the round is raised out of cash panic |

## M · Brand, domain and intellectual property

| # | Question | Default recommendation | Consequence |
|---|---|---|---|
| M1 🔴 | Is `zolts.com` available? Alternatives: `zolts.ai`, `getzolts.com` | **Check and buy today** | A low-cost action on a window that closes by itself |
| M2 | Trademark registration? | **EUIPO classes 9 and 42 before public launch** (~€850) | Rebranding at 30 customers costs 50× more |
| M3 | Prior-rights search? | **Before spending a euro on brand** | — |
| M4 | Social handles? | **Reserve today** | — |
| M5 | DSL IP open or closed? | See A7: **DSL open, runtime closed** | — |
| M6 | Who owns the code? | **The company, with IP assignment signed by everyone including freelancers** | Without it, funding due diligence collapses |

## O · Design

| # | Question | Decision | Consequence |
|---|---|---|---|
| O1 ✅ | Does the "no brand accent" rule hold everywhere? | **DECIDED: no. A brand accent ships in the product as well as in marketing.** This overrides the recommendation in the original `DESIGN.md`, which reserved colour entirely for measurement semantics | Buys the 30-second first impression in a side-by-side comparison; costs the argument that colour in this product always means something measured |
| O2 | Which hue may the accent occupy? | **Violet only.** Green, red, amber, cyan and neutral grey are spoken for by the five measurement semantics, so the accent must sit in a band no data series would occupy | Without this constraint the accent collides with lift or denial and the dense screens stop being scannable |
| O3 | Where is the accent forbidden? | **Inside any data region**: metric values, deltas, chart marks, decision chips, table cells. It lives in chrome — brand mark, primary action, focus ring, active navigation, link emphasis | This is the discipline that replaces scarcity. Without it the decision degrades into colour everywhere |
| O4 | Does the surface system change? | **No.** Near-black canvas, four-step surface ladder, hairlines, density, mono tabular numerals and no shadows all stand | Only the accent rule changed; the rest of the system was never contingent on it |

## N · Risk and horizon

| # | Question | Default recommendation | Consequence |
|---|---|---|---|
| N1 | What kills you within 12 months? | **Write the three fatal hypotheses and test them first, before the comfortable ones** | Test order determines whether you find the flaw with money or without it |
| N2 | What evidence would make you pivot? | **Define the threshold today, cold** | Defining it when it hurts is rationalising, not deciding |
| N3 | What if Clay ships your product in six months? | **Have the answer written:** verticalisation, jurisdictional compliance, incrementality | — |
| N4 | Do you sell if offered €20M in month 18? | **Define the number today** | Negotiating price in the heat of the moment destroys value and relationships |
| N5 | Maximum customer concentration | **25% of ARR from month 9** | — |
| N6 | What if the forward-deployed engineer leaves? | **Their knowledge lives in blueprints, not in their head** — a product requirement, not an HR one | — |

---

## The 12 blocking decisions, in order of urgency

| Order | Decision | Why it blocks |
|---|---|---|
| 1 | **M1** domain and brand | A window that closes by itself; trivial cost today |
| 2 | **K1** technical co-founder | Determines whether phase 1 is building or recruiting |
| 3 | **L1** capital and runway | Fixes team size and plan aggressiveness |
| 4 | **B6** three named design partners | Without them phase 1 does not start; with them it self-funds |
| 5 | ~~**B4** entry vertical~~ | **Closed: B2B SaaS 50-500.** B1 geography still open |
| 6 | **A1** product versus services | Defines the multiple and the cost structure |
| 7 | **A5** independent versus acquisition | Redirects where engineering is invested |
| 8 | **A6** the founding "no" | Without it, the first large customer redefines you |
| 9 | **H1** processor versus joint controller | Existential; requires counsel before the first contract |
| 10 | **E8** training on customer data or not | A sales argument or an asset given away; not both |
| 11 | ~~**I10** Act 2 anchor~~ | **Closed: hybrid floor plus lift bonus** |
| 12 | **B7/B8** paid pilot with signed criteria | Determines whether phase 1 feedback is real or polite |

## Five actions for this week, independent of everything above

1. Buy the domain and reserve the handles. Cost: under €100.
2. Run a prior-rights trademark search at EUIPO. Cost: zero.
3. Write the list of 30 target companies with names and contacts. Cost: four hours.
4. Speak to counsel about H1 (processor versus joint controller). Cost: roughly €300.
5. Write one page covering the founding "no" (A6) and the three fatal hypotheses (N1). Cost: two hours.

None depends on having a product, a team or capital. All of them decay with time.
