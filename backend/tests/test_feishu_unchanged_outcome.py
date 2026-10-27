"""Only acknowledged, silent reconciliation can consume the local fast budget."""
import io
import logging
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock

import pytest

from sales_backend import feishu_worker as worker_runtime
from sales_backend.feishu_worker import FeishuWorker
from sales_backend.integrations.feishu import FeishuError
from sales_backend.repositories.feishu_sync import SyncLeaseLost
from tests.test_feishu_event_driven import acknowledged
from tests.test_feishu_sync_service import Connection, setup
from tests.test_feishu_worker_scheduling import Database


async def silent_acknowledged():
    service, event, client = setup()
    saved = await acknowledged(service, event, client)
    event.update(queue_origin="reconcile", historical=True, first_formal_create=False,
                 notification_planned=False, notification_snapshot=None, attempts=1)
    return service, event, client, saved


@pytest.mark.asyncio
async def test_only_silent_fingerprint_hit_reports_source_unchanged():
    service, event, client, _ = await silent_acknowledged()
    service.repository.guard.reset_mock()
    assert await service.handle(Connection(), event, {}) == "source_unchanged"
    assert service.repository.guard.await_count >= 3
    service.repository.deliveries.assert_awaited()
    for name in ("fields", "find_record", "create_record", "update_record", "send_card"):
        getattr(client, name).assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [
    {"queue_origin": "business"}, {"historical": False}, {"first_formal_create": True},
    {"notification_planned": True}, {"first_formal_create": None}, {"notification_planned": None},
])
async def test_other_event_intent_cannot_use_fast_outcome(changed):
    service, event, _, _ = await silent_acknowledged()
    event.update(changed)
    assert await service.handle(Connection(), event, {}) is None


@pytest.mark.asyncio
async def test_snapshot_is_never_accelerated_or_silently_discarded():
    service, event, _, _ = await silent_acknowledged()
    event["notification_snapshot"] = {}
    with pytest.raises(FeishuError, match="INVALID_UPDATE_SNAPSHOT"):
        await service.handle(Connection(), event, {})


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "sent", "unknown", "failed"])
async def test_existing_delivery_receipts_always_keep_normal_pacing(status):
    service, event, client, _ = await silent_acknowledged()
    service.repository.deliveries.return_value = [{"status": status, "dedupe_key": "safe-key"}]
    assert await service.handle(Connection(), event, {}) is None
    client.send_card.assert_not_awaited()
    if status == "pending":
        service.repository.delivery_state.assert_awaited_once_with(
            ANY, "safe-key", "failed", error="NOTIFICATION_DISABLED")


@pytest.mark.asyncio
async def test_unchanged_business_create_still_plans_its_first_notification():
    service, event, _, _ = await silent_acknowledged()
    event.update(queue_origin="business", historical=False, first_formal_create=True)
    assert await service.handle(Connection(), event, {}) is None
    service.repository.freeze_delivery.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [{"force_remote_check": True}, {"attempts": 2}])
async def test_force_or_retry_does_not_shortcut_remote_repair(changed):
    service, event, client, _ = await silent_acknowledged()
    event.update(changed)
    assert await service.handle(Connection(), event, {}) is None
    client.fields.assert_awaited_once()
    client.find_record.assert_awaited_once()
    client.update_record.assert_awaited_once()


@pytest.mark.asyncio
async def test_legacy_hash_bootstrap_is_not_a_fast_outcome():
    service, event, client, saved = await silent_acknowledged()
    saved["source_fingerprint"] = None
    service._comparison_schemas.clear()
    assert await service.handle(Connection(), event, {}) is None
    client.fields.assert_awaited_once()
    client.update_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_refresh_and_disabled_mapping_are_not_fast_outcomes():
    service, event, _, _ = await silent_acknowledged()
    service.repository.reconcile = AsyncMock()
    event["object_kind"] = "refresh"
    assert await service.handle(Connection(), event, {}) is None
    service.repository.reconcile.assert_awaited_once()
    event["object_kind"] = "task"  # The fixture has no enabled task mapping.
    assert await service.handle(Connection(), event, {}) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["guard", "notify"])
async def test_failed_guard_or_notification_does_not_return_success(method):
    service, event, _, _ = await silent_acknowledged()
    target = service.repository if method == "guard" else service
    setattr(target, method, AsyncMock(side_effect=SyncLeaseLost("lost")))
    with pytest.raises(SyncLeaseLost):
        await service.handle(Connection(), event, {})


def worker_with_outcome(outcome="source_unchanged"):
    worker = FeishuWorker(Database())
    event = dict(id="safe-id", connection_id="connection", object_kind="customer", object_id="customer")
    worker.repository = SimpleNamespace(
        worker_config=AsyncMock(side_effect=lambda *args, **kwargs: (
            None if kwargs.get("validation") else {"configured": True})),
        claim=AsyncMock(return_value=event), try_lock=AsyncMock(return_value=True),
        finish=AsyncMock(), unlock=AsyncMock())
    worker.service = SimpleNamespace(handle=AsyncMock(return_value=outcome))
    return worker, event


@pytest.mark.asyncio
async def test_worker_reports_outcome_only_after_successful_finish(caplog):
    worker, event = worker_with_outcome()
    seen = []

    async def finished(*args, **kwargs):
        seen.append(worker.last_outcome)

    worker.repository.finish.side_effect = finished
    with caplog.at_level(logging.INFO, logger="sales_backend.feishu_worker"):
        assert await worker.run_once()
    assert seen == [None]
    assert worker.last_outcome == "source_unchanged"
    assert "event_id=safe-id object_kind=customer outcome=source_unchanged" in caplog.text
    worker.repository.unlock.assert_awaited_once()
    worker.repository.claim.return_value = None
    assert await worker.run_once() is False
    assert worker.last_outcome is None


@pytest.mark.asyncio
async def test_lost_finish_lease_cannot_report_or_log_fast_success(caplog):
    worker, _ = worker_with_outcome()
    worker.repository.finish.side_effect = SyncLeaseLost("lost")
    with caplog.at_level(logging.INFO, logger="sales_backend.feishu_worker"):
        assert await worker.run_once()
    assert worker.last_outcome is None
    assert "feishu event completed" not in caplog.text
    worker.repository.unlock.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    FeishuError("RATE_LIMITED", retryable=True, retry_after=20),
    FeishuError("TRANSPORT_UNKNOWN", unknown=True),
    FeishuError("DEPENDENCY_PENDING", retryable=True),
    SyncLeaseLost("lost"), RuntimeError("not logged"),
])
async def test_errors_cannot_reuse_a_previous_fast_outcome(error, caplog):
    worker, _ = worker_with_outcome()
    worker.last_outcome = "source_unchanged"
    worker.service.handle.side_effect = error
    with caplog.at_level(logging.INFO, logger="sales_backend.feishu_worker"):
        assert await worker.run_once()
    assert worker.last_outcome is None
    assert "feishu event completed" not in caplog.text
    worker.repository.unlock.assert_awaited_once()


@pytest.mark.asyncio
async def test_validation_resets_previous_outcome():
    worker, _ = worker_with_outcome()
    worker.last_outcome = "source_unchanged"
    worker.repository.worker_config.side_effect = None
    worker.repository.worker_config.return_value = {"validate": True}
    worker.service.validate = AsyncMock()
    assert await worker.run_once()
    assert worker.last_outcome is None
    worker.repository.claim.assert_not_awaited()


@pytest.mark.asyncio
async def test_safe_receipts_are_actually_emitted_once_without_enabling_other_loggers(monkeypatch):
    worker, _ = worker_with_outcome()
    logger = worker_runtime.logger
    previous = (list(logger.handlers), logger.level, logger.propagate)
    root = logging.getLogger()
    http = logging.getLogger("httpx")
    other_state = (root.level, list(root.handlers), http.level, list(http.handlers), http.propagate)
    stream = io.StringIO()
    monkeypatch.setattr("sys.stderr", stream)
    logger.handlers = []
    try:
        worker_runtime.configure_logging()
        worker_runtime.configure_logging()
        assert len(logger.handlers) == 1
        assert logger.level == logging.INFO and logger.propagate is False
        assert await worker.run_once()
        output = stream.getvalue()
        assert output.count("feishu event completed") == 1
        assert "event_id=safe-id object_kind=customer outcome=source_unchanged" in output
        assert "connection" not in output and "configured" not in output
        assert other_state == (root.level, list(root.handlers), http.level, list(http.handlers), http.propagate)
    finally:
        for handler in logger.handlers:
            handler.close()
        logger.handlers, logger.level, logger.propagate = previous
