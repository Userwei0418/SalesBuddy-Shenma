import asyncio
from types import SimpleNamespace

import pytest

from sales_backend.performance import RequestTimings, record_pool_wait, record_query, request_timings, timing_stage


@pytest.mark.asyncio
async def test_parallel_request_timings_do_not_mix():
    async def request(elapsed):
        own = RequestTimings()
        token = request_timings.set(own)
        try:
            record_pool_wait(elapsed)
            await asyncio.sleep(0)
            record_query(SimpleNamespace(elapsed=elapsed * 2))
            return own
        finally:
            request_timings.reset(token)
    a, b = await asyncio.gather(request(.01), request(.02))
    assert (a.query_count, a.database_ms, a.pool_wait_ms) == (1, 20, 10)
    assert (b.query_count, b.database_ms, b.pool_wait_ms) == (1, 40, 20)
    assert request_timings.get() is None


def test_background_queries_do_not_accumulate_global_metrics():
    record_query(SimpleNamespace(elapsed=100))
    record_pool_wait(100)
    assert request_timings.get() is None


def test_max_query_and_stage_failure_leave_safe_request_local_evidence():
    timing = RequestTimings()
    token = request_timings.set(timing)
    try:
        record_query(SimpleNamespace(elapsed=.01))
        record_query(SimpleNamespace(elapsed=.02))
        with pytest.raises(ValueError, match="failed"):
            with timing_stage("map_projection"):
                raise ValueError("failed")
        assert timing.query_count == 2
        assert timing.database_ms == 30
        assert timing.database_max_ms == 20
        assert timing.stages["map_projection"] >= 0
        with pytest.raises(ValueError, match="unknown timing stage"):
            with timing_stage("untrusted customer label"):
                pass
        assert set(timing.stages) == {"map_projection"}
    finally:
        request_timings.reset(token)
