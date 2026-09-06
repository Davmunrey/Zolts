"""Data access. Every function takes a cursor already bound to a tenant.

Repositories never open transactions of their own. The caller owns the
transaction boundary, which is what makes it possible to write a state change
and the action it justifies in one atomic step.
"""

from runtime.repo import actions, enrollments, entities, ledger, programs, signals

__all__ = ["actions", "enrollments", "entities", "ledger", "programs", "signals"]
