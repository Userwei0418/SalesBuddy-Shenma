"""Infrastructure context inherited by a worker's handler and heartbeat tasks.

Database.transaction fences writes while this context is active. HTTP requests do
not populate it. Final result repositories write a completion receipt in their
business transaction before the worker acknowledges the queue item.
"""
from contextvars import ContextVar
from dataclasses import dataclass


class JobLeaseLost(RuntimeError):
    """The task must stop; its result no longer belongs to this worker attempt."""


@dataclass(frozen=True, slots=True)
class JobLease:
    job_id: str
    token: str


current_job_lease: ContextVar[JobLease | None] = ContextVar('current_job_lease', default=None)
