"""Request-local timings, with no query text, arguments or business data retained."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from time import perf_counter


@dataclass
class RequestTimings:
    query_count: int = 0
    database_ms: float = 0
    pool_wait_ms: float = 0
    database_max_ms: float = 0
    stages: dict[str, float] = field(default_factory=dict)


request_timings: ContextVar[RequestTimings | None] = ContextVar('request_timings', default=None)


def record_query(record) -> None:
    timing = request_timings.get()
    if timing is not None:
        timing.query_count += 1
        timing.database_ms += record.elapsed * 1000
        timing.database_max_ms = max(timing.database_max_ms, record.elapsed * 1000)


def record_pool_wait(seconds: float) -> None:
    timing = request_timings.get()
    if timing is not None:
        timing.pool_wait_ms += seconds * 1000


@contextmanager
def timing_stage(name: str):
    """Record fixed server phase names only; never query text or parameters."""
    if name not in {"map_scope", "map_directory", "map_projection"}:
        raise ValueError("unknown timing stage")
    started = perf_counter()
    try:
        yield
    finally:
        timing = request_timings.get()
        if timing is not None:
            timing.stages[name] = timing.stages.get(name, 0) + (perf_counter() - started) * 1000
