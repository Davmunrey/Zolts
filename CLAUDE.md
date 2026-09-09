# Zolts — repository conventions

## Language policy (non-negotiable)

**Everything in this repository is written in English.** Product surface, source code, identifiers, comments, documentation, file names, commit messages, PR titles and descriptions, issue text, error strings, and configuration.

No exceptions, including:
- Code comments and docstrings.
- Documentation under `docs/`, examples under `examples/`, and scripts under `scripts/`.
- Commit messages and pull request bodies.
- Any user-facing product copy shipped in the platform.

Rationale: the product targets an international market, the team hires across borders, and investor diligence reads the repository. A mixed-language codebase silently narrows the hiring pool and the market.

Spoken and chat communication with the founding team may be in any language. That never reaches the repository.

## Documentation conventions

- One topic per file under `docs/`, numbered with a two-digit prefix and a kebab-case English slug.
- Lead with the conclusion. Tables over prose. State the consequence of getting a decision wrong, not only the recommendation.
- Every architectural decision that constrains future work goes in `docs/02-architecture.md` as an ADR.
- Every open decision goes in `docs/18-decision-register.md` with a default. A decision that is not registered does not exist and will be reopened.

## Code conventions

- GTM program definitions are declarative YAML validated against `examples/schema/zolts-program.schema.json`.
- Never use `on:` as a YAML key — YAML 1.1 coerces `on`/`off`/`yes`/`no` to booleans and validation fails silently. Use `events:`.
- `python3 scripts/validate.py` and `PYTHONPATH=. python3 -m pytest tests/ -q` must both pass before every commit. CI enforces both on every push, and also `scripts/run_program_tests.py`, the `site/` and `vercel.json` freshness check, `scripts/mutation_check.py`, `scripts/mutation_coverage.py` (a sample, reported rather than gated), `scripts/browser_console.py` and `scripts/smoke_runtime.py`.
- `zolts/` is the reference core: pure logic, no I/O, no external services. Anything requiring infrastructure belongs in the production runtime, not here.
- A claim asserted in `docs/` that can be tested must have a test. If a measurement contradicts a document, the document is corrected — never the other way round.
- **A test may not depend on ambient time, ambient configuration or ambient data.** Where it must touch one, it establishes the premise itself and asserts that premise before the verdict — open a connection with `sslmode=disable` and check it really is plaintext; remove the send window and check the shipped programme still declares one. Four defects have been this shape (D-35, D-59, D-68, D-71); one left the suite red about seventy per cent of the week on code that was never wrong, and one had the verification gate itself report eight broken guards on a machine with no database. **The rule binds the checks as much as the tests:** a script that reports a verdict must establish that it ran, and say so when it did not. A suite that fails on correct code teaches the team to press re-run, and a team that presses re-run has no suite.

## Product invariants

These are settled and must not be relaxed without updating the decision register:

1. Agents propose; the runtime disposes. No agent performs an external action directly.
2. Every external action carries an idempotency key.
3. Every external action records a policy decision, allow or deny, with a reason.
4. Every program declares a holdout. A waiver requires a written justification stored with the program.
5. Program logic is versioned configuration, never ad-hoc code.
