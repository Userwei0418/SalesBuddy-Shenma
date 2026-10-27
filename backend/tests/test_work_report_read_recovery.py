"""Offline recovery/ASGI proof; no claim of live PostgreSQL or host acceptance."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from uuid import uuid4

import jwt
import pytest

from sales_backend.api import work_reports as api
from sales_backend.db import DatabaseBusy
from sales_backend.domain.work_report import WorkReportError, default_scheme
from sales_backend.services import work_report_read_recovery as recovery
from tests.test_work_report_overview_http import get
from tests.test_work_report_overview_http import overview_http as _overview_http

overview_http = _overview_http


def assert_unavailable(error):
    assert error.code == 'WORK_REPORT_READ_TIMEOUT'
    assert error.status == 503
    assert error.retryable is True


@pytest.mark.asyncio
async def test_complete_rollback_and_release_precede_new_attempt():
    events = []
    attempts = 0

    @asynccontextmanager
    async def transaction(attempt):
        events.append(('begin', attempt))
        try:
            yield
        except BaseException:
            events.append(('rollback', attempt))
            raise
        finally:
            await asyncio.sleep(0)
            events.append(('release', attempt))

    async def once():
        nonlocal attempts
        attempts += 1
        async with transaction(attempts):
            if attempts == 1:
                raise recovery.OverviewReadTimeout()
            return {'facts': 'fresh', 'binding': 'fresh'}

    assert await recovery.read_overview_with_recovery(once) == {'facts': 'fresh', 'binding': 'fresh'}
    assert events == [('begin', 1), ('rollback', 1), ('release', 1), ('begin', 2), ('release', 2)]


@pytest.mark.asyncio
async def test_two_material_timeouts_return_retryable_503_without_third_attempt(caplog):
    attempts = 0

    async def once():
        nonlocal attempts
        attempts += 1
        raise recovery.OverviewReadTimeout('PRIVATE material must never enter logs')

    with pytest.raises(WorkReportError) as caught:
        await recovery.read_overview_with_recovery(once)
    assert_unavailable(caught.value)
    assert attempts == 2
    assert 'PRIVATE' not in caplog.text


@pytest.mark.asyncio
async def test_cleanup_timeout_replaces_read_timeout_and_never_retries():
    attempts = 0

    async def once():
        nonlocal attempts
        attempts += 1
        try:
            raise recovery.OverviewReadTimeout()
        finally:
            raise TimeoutError('connection release timed out')

    with pytest.raises(WorkReportError) as caught:
        await recovery.read_overview_with_recovery(once)
    assert_unavailable(caught.value)
    assert attempts == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('error', [
    WorkReportError('DENIED', 'denied', status=403),
    WorkReportError('STALE', 'stale', status=409),
    DatabaseBusy('no free connection'),
])
async def test_business_and_pool_errors_are_not_retried(error):
    attempts = 0

    async def once():
        nonlocal attempts
        attempts += 1
        raise error

    with pytest.raises(type(error)) as caught:
        await recovery.read_overview_with_recovery(once)
    assert caught.value is error
    assert attempts == 1


@pytest.mark.asyncio
async def test_shared_budget_exhaustion_cancels_read_and_does_not_retry(monkeypatch):
    monkeypatch.setattr(recovery, 'OVERVIEW_READ_BUDGET_SECONDS', 0.02)
    attempts = 0
    cleanup = asyncio.Event()

    async def once():
        nonlocal attempts
        attempts += 1
        try:
            await asyncio.Event().wait()
        finally:
            cleanup.set()

    with pytest.raises(WorkReportError) as caught:
        await asyncio.wait_for(recovery.read_overview_with_recovery(once), timeout=1)
    assert_unavailable(caught.value)
    assert attempts == 1 and cleanup.is_set()


@pytest.mark.asyncio
async def test_external_cancellation_is_not_converted_or_retried():
    started, cleanup = asyncio.Event(), asyncio.Event()
    attempts = 0

    async def once():
        nonlocal attempts
        attempts += 1
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup.set()

    task = asyncio.create_task(recovery.read_overview_with_recovery(once))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert attempts == 1 and cleanup.is_set()


@pytest.mark.asyncio
async def test_http_retry_recollects_facts_and_binding_in_new_read_transaction(overview_http, monkeypatch):
    http, db, _ = overview_http
    original_read = api.overview.read
    original_transaction = db.transaction
    events, discarded = [], []

    @asynccontextmanager
    async def transaction(*args, **kwargs):
        attempt = sum(event[0] == 'begin' for event in events) + 1
        events.append(('begin', attempt))
        assert kwargs == {'readonly': True, 'isolation': 'repeatable_read'}
        try:
            async with original_transaction(*args, **kwargs) as connection:
                yield connection
        except BaseException:
            events.append(('rollback', attempt))
            raise
        finally:
            events.append(('release', attempt))

    async def read(*args, **kwargs):
        result = await original_read(*args, **kwargs)
        if not discarded:
            discarded.append(deepcopy(result))
            db.connection.permission_version = 'v2'
            db.connection.material['opportunities'][0]['name'] = 'fresh material after retry'
            raise TimeoutError('synthetic DB command timeout')
        return result

    monkeypatch.setattr(db, 'transaction', transaction)
    monkeypatch.setattr(api.overview, 'read', read)
    response = await get(http)
    assert response.status_code == 200, response.text
    data = response.json()
    old = discarded[0].model_dump(mode='json')
    claims = jwt.decode(data['generation_binding']['token'], options={'verify_signature': False})
    assert claims['permission_version'] == 'v2'
    assert data['generation_binding']['token'] != old['generation_binding']['token']
    assert data['overview_hash'] != old['overview_hash']
    assert data['facts'] != old['facts']
    assert data['request_id'] == old['request_id']
    assert events == [('begin', 1), ('rollback', 1), ('release', 1), ('begin', 2), ('release', 2)]
    assert not db.connection.jobs and all(not rows for rows in db.connection.rows.values())


@pytest.mark.asyncio
async def test_http_permission_revoked_after_timeout_returns_403_not_old_material(overview_http, monkeypatch):
    http, db, _ = overview_http
    original_read = api.overview.read
    reads = 0

    async def read(*args, **kwargs):
        nonlocal reads
        reads += 1
        await original_read(*args, **kwargs)
        db.connection.denied.add('work_report.read')
        raise TimeoutError('synthetic command timeout')

    monkeypatch.setattr(api.overview, 'read', read)
    response = await get(http)
    assert response.status_code == 403
    assert response.json()['code'] == 'WORK_REPORT_PERMISSION_CHANGED'
    assert reads == 1
    assert 'facts' not in response.json()


@pytest.mark.asyncio
async def test_http_second_material_timeout_is_503(overview_http, monkeypatch):
    http, _, _ = overview_http
    reads = 0

    async def read(*args, **kwargs):
        nonlocal reads
        reads += 1
        raise TimeoutError('synthetic command timeout')

    monkeypatch.setattr(api.overview, 'read', read)
    response = await get(http)
    assert response.status_code == 503
    assert response.json()['code'] == 'WORK_REPORT_READ_TIMEOUT'
    assert reads == 2


@pytest.mark.asyncio
async def test_other_report_get_and_write_never_enter_recovery(overview_http, monkeypatch):
    http, _, _ = overview_http

    async def unexpected(*args, **kwargs):
        raise AssertionError('non-overview operation entered recovery')

    monkeypatch.setattr(api, 'read_overview_with_recovery', unexpected)
    assert (await http.get('/api/v1/work-reports/overview/options')).status_code == 200
    assert (await http.get('/api/v1/work-reports/schemes')).status_code == 200
    response = await http.post('/api/v1/work-reports/schemes',
        json={'name': 'recovery must not wrap writes', 'config': default_scheme().model_dump(mode='json')},
        headers={'Idempotency-Key': str(uuid4())})
    assert response.status_code == 201
