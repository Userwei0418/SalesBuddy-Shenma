import importlib.util
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

SPEC = importlib.util.spec_from_file_location(
    "paced_entrypoint", Path(__file__).parents[1] / "deploy" / "feishu_history_paced_worker.py")
paced = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(paced)


@pytest.mark.parametrize("origin,live,expected", [
    ("reconcile", False, True), ("reconcile", True, False),
    ("business", False, False),
])
@pytest.mark.asyncio
async def test_claim_keeps_business_and_its_predecessors_unthrottled(monkeypatch, origin, live, expected):
    event = {"queue_origin": origin, "historical": True,
             "connection_id": "c", "object_kind": "opportunity", "object_id": "o"}
    original_claim = AsyncMock(return_value=event)
    monkeypatch.setattr(paced.FeishuRepository, "claim", original_claim)
    connection = AsyncMock()
    connection.fetchval.return_value = live
    repository = paced.PacedRepository()
    assert await repository.claim(connection, prefer_history=True) is event
    assert repository.background_claim is expected
    original_claim.assert_awaited_once_with(connection, prefer_history=True)
    assert connection.fetchval.await_count == (1 if origin == "reconcile" else 0)


@pytest.mark.asyncio
async def test_only_background_work_delays_after_normal_processing_returns(monkeypatch):
    worker = object.__new__(paced.PacedWorker)
    worker.repository = paced.PacedRepository()
    worker.history_interval_seconds = 5
    steps = []

    async def processed(_):
        worker.repository.background_claim = True
        steps.append("processing_and_lock_cleanup_complete")
        return True

    async def slept(seconds):
        assert seconds == 5
        steps.append("sleep")

    monkeypatch.setattr(paced.runtime.FeishuWorker, "run_once", processed)
    monkeypatch.setattr(paced.asyncio, "sleep", slept)
    assert await worker.run_once() is True
    assert steps == ["processing_and_lock_cleanup_complete", "sleep"]


@pytest.mark.asyncio
async def test_validation_or_idle_does_not_inherit_last_history_delay(monkeypatch):
    worker = object.__new__(paced.PacedWorker)
    worker.repository = paced.PacedRepository()
    worker.repository.background_claim = True
    worker.history_interval_seconds = 5
    monkeypatch.setattr(paced.runtime.FeishuWorker, "run_once", AsyncMock(return_value=True))
    sleep = AsyncMock()
    monkeypatch.setattr(paced.asyncio, "sleep", sleep)
    assert await worker.run_once() is True
    sleep.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome,expected", [
    ("source_unchanged", 0.1), (None, 5), ("remote_write", 5),
    ("legacy_unchanged", 5), ("failed", 5), ("refresh", 5),
])
async def test_only_explicit_fast_outcome_uses_local_budget(monkeypatch, outcome, expected):
    worker = object.__new__(paced.PacedWorker)
    worker.repository = paced.PacedRepository()
    worker.history_interval_seconds = 5
    worker.last_outcome = "source_unchanged"
    steps = []

    async def processed(_):
        assert worker.last_outcome is None
        worker.repository.background_claim = True
        worker.last_outcome = outcome
        steps.append("released")
        return True

    async def slept(delay):
        assert steps == ["released"]
        assert delay == expected

    monkeypatch.setattr(paced.runtime.FeishuWorker, "run_once", processed)
    monkeypatch.setattr(paced.asyncio, "sleep", slept)
    assert await worker.run_once()


@pytest.mark.asyncio
async def test_fast_local_budget_is_bounded_even_when_remote_delay_is_disabled(monkeypatch):
    worker = object.__new__(paced.PacedWorker)
    worker.repository = paced.PacedRepository()
    worker.history_interval_seconds = 0

    async def processed(_):
        worker.repository.background_claim = True
        worker.last_outcome = "source_unchanged"
        return True

    sleep = AsyncMock()
    monkeypatch.setattr(paced.runtime.FeishuWorker, "run_once", processed)
    monkeypatch.setattr(paced.asyncio, "sleep", sleep)
    for _ in range(10):
        assert await worker.run_once()
    assert sleep.await_count == 10
    assert all(call.args == (0.1,) for call in sleep.await_args_list)


@pytest.mark.asyncio
async def test_live_dependency_keeps_existing_priority_without_history_wait(monkeypatch):
    worker = object.__new__(paced.PacedWorker)
    worker.repository = paced.PacedRepository()
    worker.history_interval_seconds = 5

    async def processed(_):
        worker.repository.background_claim = False
        worker.last_outcome = "source_unchanged"
        return True

    sleep = AsyncMock()
    monkeypatch.setattr(paced.runtime.FeishuWorker, "run_once", processed)
    monkeypatch.setattr(paced.asyncio, "sleep", sleep)
    assert await worker.run_once()
    sleep.assert_not_awaited()


@pytest.mark.parametrize("value", ["nan", "inf", "-1", "61"])
def test_invalid_interval_fails_closed(monkeypatch, value):
    monkeypatch.setenv("FEISHU_HISTORY_INTERVAL_SECONDS", value)
    with pytest.raises(ValueError):
        paced.history_interval()


@pytest.mark.parametrize("scope", [
    {"workspace_id": "not-a-uuid", "connection_id": "also-invalid", "object_ids": []},
    {"workspace_id": "11111111-1111-1111-1111-111111111111",
     "connection_id": "22222222-2222-2222-2222-222222222222", "object_ids": []},
    {"workspace_id": "11111111-1111-1111-1111-111111111111",
     "connection_id": "22222222-2222-2222-2222-222222222222",
     "object_ids": ["33333333-3333-3333-3333-333333333333"] * 2},
])
def test_invalid_priority_scope_fails_closed(monkeypatch, tmp_path, scope):
    path = tmp_path / "priority.json"
    path.write_text(json.dumps(scope))
    monkeypatch.setenv("FEISHU_HISTORY_PRIORITY_FILE", str(path))
    with pytest.raises(ValueError):
        paced.PacedRepository()
