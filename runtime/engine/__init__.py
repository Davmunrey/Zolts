"""The execution engine.

The path a signal takes: `triggers` decides whether it matches a live program,
`enroll` creates the enrollment and assigns the holdout variant, `planner`
turns the enrollment's position in the play into the next action, `gate`
decides whether that action is permitted, and `worker` carries it out.

`admission` is not on that path: it decides whether a program may exist at
all, and the repo calls it when a version is stored. It lives here because it
is the engine that has to run the program afterwards.

Every decision the engine makes comes from `zolts/`. This package contributes
the I/O and the ordering, never the rules.
"""
