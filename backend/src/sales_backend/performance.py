"""Request-local timings, with no query text, arguments or business data retained."""
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass
class RequestTimings:
    query_count: int = 0
    database_ms: float = 0
    pool_wait_ms: float = 0


request_timings: ContextVar[RequestTimings | None] = ContextVar('request_timings', default=None)


def record_query(record) -> None:
    timing = request_timings.get()
    if timing is not None:
        timing.query_count += 1
        timing.database_ms += record.elapsed * 1000


def record_pool_wait(seconds: float) -> None:
    timing = request_timings.get()
    if timing is not None:
        timing.pool_wait_ms += seconds * 1000
