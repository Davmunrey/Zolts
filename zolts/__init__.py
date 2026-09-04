"""Zolts reference core.

A dependency-light implementation of the primitives the plan asserts:
overlay resolution, deterministic holdout assignment, signal decay and
PIT-R scoring, the waterfall cost optimiser, and the policy engine.

This is a reference implementation used to validate the claims made in
`docs/`, not the production runtime. Every public function here backs a
specific numeric or behavioural claim, and `tests/` proves it.
"""

__version__ = "0.1.0"
