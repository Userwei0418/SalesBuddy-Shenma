"""Real restricted PostgreSQL transactions and mocked external transports."""
from uuid import uuid4

import asyncpg
import httpx
import pytest

from sales_backend.db import TenantInactive, set_request_context
from sales_backend.domain.agent import ChatMessage
from sales_backend.integrations.senseaudio import SenseAudioClient
from sales_backend.services.model_calls import CallQuotaExceeded, DatabaseModelObserver
from tests.integration.feishu_fixtures import seed_execute, seed_fetchval, fixture_owner
from tests.integration.test_operations_api import TransactionDatabase
from tests.integration.test_platform_control import platform, provision, activate, tenant, transition  # noqa: F401
from tests.integration.test_tenant_storage_quota import company
from tests.test_senseaudio import settings

pytestmark = pytest.mark.asyncio


async def quotas(client, actor):
    result = await client.get(f'/api/v1/platform/tenants/{actor.workspace_id}/call-quotas')
    assert result.status_code == 200, result.text
    return result.json()


async def item(client, actor, resource='ai_calls'):
    return next(i for i in (await quotas(client, actor))['items'] if i['resource'] == resource)


async def limit(client, actor, value, resource='ai_calls', period='lifetime'):
    q = await quotas(client, actor)
    result = await client.post(f'/api/v1/platform/tenants/{actor.workspace_id}/call-quotas', json={
        'version_no': q['version_no'], 'resource': resource, 'call_limit': value, 'period': period, 'reason': '合成调用额度验收'})
    assert result.status_code == 200, result.text
    return result.json()


async def begin(db, actor, *, operation_id=None, provider='senseaudio', endpoint='/v1/chat/completions'):
    observer = DatabaseModelObserver(db, actor, 'quota_fixture', operation_id=operation_id, provider=provider)
    invocation = await observer.start(endpoint, 'fixture', {'synthetic': True}, 1)
    return observer, invocation


async def test_quota_blocks_actual_network_and_other_tenant_remains_independent(platform, connection):  # noqa: F811
    client, _ = platform
    first = await company(client, connection); second = await company(client, connection)
    await limit(client, first, 0)
    sent = []
    async def transport(request):
        sent.append(request)
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"ok":true}'}}]})
    async with httpx.AsyncClient(base_url='https://api.senseaudio.cn', transport=httpx.MockTransport(transport)) as http:
        for actor in [first, second]:
            observer = DatabaseModelObserver(TransactionDatabase(connection), actor, 'quota_fixture')
            model = SenseAudioClient(settings(), client=http, observer=observer)
            if actor == first:
                with pytest.raises(CallQuotaExceeded):
                    await model.chat_json(messages=[ChatMessage(role='user', content='synthetic')])
                assert not sent
            else:
                assert await model.chat_json(messages=[ChatMessage(role='user', content='synthetic')]) == {'ok': True}
    assert len(sent) == 1
    assert (await item(client, first))['used'] == 0
    assert (await item(client, second))['used'] == 1
    assert await seed_fetchval(connection, 'SELECT count(*) FROM agent.model_invocation WHERE workspace_id=$1::uuid', first.workspace_id) == 0


async def test_retry_fallback_and_chunks_share_logical_event_not_attempt_usage(platform, connection):  # noqa: F811
    client, _ = platform; actor = await company(client, connection); db = TransactionDatabase(connection)
    await limit(client, actor, 1)
    operation = str(uuid4())
    first, a = await begin(db, actor, operation_id=operation, provider='agent_platform', endpoint='agent-chat-messages')
    await first.prepare_dispatch(a)
    await first.finish(a, httpx.Response(503, json={'usage': {'prompt_tokens': 7}}), RuntimeError('synthetic'))
    fallback, b = await begin(db, actor, operation_id=operation)
    await fallback.prepare_dispatch(b)
    await fallback.finish(b, httpx.Response(200, json={'usage': {'prompt_tokens': 9}}), None)
    q = await item(client, actor)
    assert (q['used'], q['reserved'], q['remaining']) == (1, 0, 0)
    with pytest.raises(CallQuotaExceeded):
        await begin(db, actor)
    assert await seed_fetchval(connection, 'SELECT sum(input_tokens) FROM agent.model_invocation WHERE workspace_id=$1::uuid', actor.workspace_id) == 16
    await limit(client, actor, 1, resource='asr_calls')
    for _ in range(3):
        observer, attempt = await begin(db, actor, operation_id=operation, endpoint='/v1/audio/transcriptions')
        await observer.prepare_dispatch(attempt)
        await observer.finish(attempt, httpx.Response(200, json={'duration': 240}), None)
    assert (await item(client, actor, 'asr_calls'))['used'] == 1
    assert await seed_fetchval(connection, 'SELECT sum(audio_seconds) FROM agent.model_invocation WHERE workspace_id=$1::uuid', actor.workspace_id) == 720


async def test_uncertain_result_retains_quota_receipts_are_idempotent_and_private(platform, connection):  # noqa: F811
    client, _ = platform; actor = await company(client, connection); db = TransactionDatabase(connection)
    await limit(client, actor, 1)
    observer, attempt = await begin(db, actor)
    await observer.prepare_dispatch(attempt)
    await observer.finish(attempt, None, TimeoutError())
    await observer.finish(attempt, None, TimeoutError())
    q = await item(client, actor)
    assert (q['used'], q['reserved'], q['pending_attempts']) == (0, 1, 1)
    with pytest.raises(CallQuotaExceeded):
        await begin(db, actor)
    for receipt in [None, uuid4()]:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with connection.transaction():
                await connection.fetchval('SELECT security.finish_model_call($1::uuid,$2::uuid,false,$3::jsonb)', attempt, receipt, {'status':'failed'})
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with connection.transaction():
            await connection.fetch('SELECT * FROM control.call_attempt')
    assert await seed_fetchval(connection, 'SELECT input_tokens FROM agent.model_invocation WHERE id=$1::uuid', attempt) is None


async def test_predispatch_failure_releases_and_cannot_dispatch_released_receipt(platform, connection):  # noqa: F811
    client, _ = platform; actor = await company(client, connection); db = TransactionDatabase(connection)
    await limit(client, actor, 1)
    observer, attempt = await begin(db, actor)
    # Even if the dispatch marker committed, transport code knows it never sent.
    await observer.prepare_dispatch(attempt)
    await observer.finish(attempt, None, RuntimeError('before network'), network_sent=False)
    assert (await item(client, actor))['remaining'] == 1
    with pytest.raises(asyncpg.PostgresError) as exc:
        await observer.prepare_dispatch(attempt)
    assert exc.value.sqlstate == 'P7409'
    await begin(db, actor)


async def test_late_usage_can_settle_after_pause_without_business_authority(platform, connection):  # noqa: F811
    client, _ = platform; actor = await company(client, connection); db = TransactionDatabase(connection)
    observer, attempt = await begin(db, actor)
    await observer.prepare_dispatch(attempt)
    assert (await transition(client, await tenant(client, actor.workspace_id), 'suspended')).status_code == 200
    with pytest.raises(TenantInactive):
        await begin(db, actor)
    await observer.finish(attempt, httpx.Response(200, json={'usage': {'prompt_tokens': 11}}), None)
    assert (await item(client, actor))['used'] == 1
    assert await seed_fetchval(connection, 'SELECT input_tokens FROM agent.model_invocation WHERE id=$1::uuid', attempt) == 11
    with pytest.raises(TenantInactive):
        await set_request_context(connection, actor)


async def test_period_changes_recount_history_cas_and_limits(platform, connection):  # noqa: F811
    client, _ = platform; actor = await company(client, connection); db = TransactionDatabase(connection)
    observer, attempt = await begin(db, actor)
    await observer.prepare_dispatch(attempt)
    await observer.finish(attempt, httpx.Response(200), None)
    await seed_execute(connection, "UPDATE control.call_event SET created_at=date_trunc('month',now())-interval '1 day' WHERE workspace_id=$1::uuid", actor.workspace_id)
    await limit(client, actor, 1, period='month_utc')
    assert (await item(client, actor))['remaining'] == 1
    await limit(client, actor, 1, period='lifetime')
    assert (await item(client, actor))['remaining'] == 0
    before = await quotas(client, actor)
    await limit(client, actor, None)
    result = await client.post(f'/api/v1/platform/tenants/{actor.workspace_id}/call-quotas', json={
        'version_no':before['version_no'], 'resource':'ai_calls', 'call_limit':0, 'period':'month_utc', 'reason':'stale'})
    assert result.status_code == 409
    for invalid in [True, -1, 1.2]:
        result = await client.post(f'/api/v1/platform/tenants/{actor.workspace_id}/call-quotas', json={
            'version_no':before['version_no'], 'resource':'ai_calls', 'call_limit':invalid, 'period':'month_utc', 'reason':'invalid'})
        assert result.status_code == 422


async def test_owner_reconciliation_fences_unstarted_and_preserves_unknown_usage(platform, connection):  # noqa: F811
    from sales_backend.services.call_reconciliation import pending, reconcile
    from sales_backend.services.storage_reconciliation import ReconciliationConflict
    client, _ = platform; actor = await company(client, connection); db = TransactionDatabase(connection)
    observer, attempt = await begin(db, actor)
    with pytest.raises(PermissionError):
        await pending(connection, actor.workspace_id)
    async with fixture_owner(connection):
        rows = await pending(connection, actor.workspace_id)
        assert len(rows['items']) == 1 and 'receipt' not in str(rows)
        stamp = await connection.fetchval('SELECT updated_at FROM control.call_attempt WHERE invocation_id=$1::uuid', attempt)
        await reconcile(connection, actor.workspace_id, attempt, 'release-unstarted', expected_at=stamp, reason='合成未发出验收')
    with pytest.raises(asyncpg.PostgresError):
        await observer.prepare_dispatch(attempt)
    observer, attempt = await begin(db, actor)
    await observer.prepare_dispatch(attempt)
    async with fixture_owner(connection):
        stamp = await connection.fetchval('SELECT updated_at FROM control.call_attempt WHERE invocation_id=$1::uuid', attempt)
        with pytest.raises(ReconciliationConflict):
            await reconcile(connection, actor.workspace_id, attempt, 'confirm-not-sent', expected_at=stamp, reason='不能干预在途请求', evidence='synthetic:check')
    await observer.finish(attempt, None, TimeoutError())
    async with fixture_owner(connection):
        stamp = await connection.fetchval('SELECT updated_at FROM control.call_attempt WHERE invocation_id=$1::uuid', attempt)
        with pytest.raises(ValueError):
            await reconcile(connection, actor.workspace_id, attempt, 'confirm-response', expected_at=stamp, reason='缺少依据', http_status=503)
        await reconcile(connection, actor.workspace_id, attempt, 'confirm-response', expected_at=stamp, reason='核验合成回执', evidence='synthetic:provider-receipt-1', http_status=503)
        with pytest.raises(ReconciliationConflict):
            await reconcile(connection, actor.workspace_id, attempt, 'confirm-not-sent', expected_at=stamp, reason='旧读不能覆盖', evidence='synthetic:old')
    # A delayed duplicate completion cannot overwrite the operator's resolution.
    await observer.finish(attempt, None, TimeoutError())
    assert (await item(client, actor))['used'] == 1
    assert (await item(client, actor))['pending_attempts'] == 0
    assert await seed_fetchval(connection, 'SELECT input_tokens FROM agent.model_invocation WHERE id=$1::uuid', attempt) is None
    assert await seed_fetchval(connection, 'SELECT http_status FROM agent.model_invocation WHERE id=$1::uuid', attempt) == 503


@pytest.mark.parametrize('job_type,aggregate_type,per_job', [
    ('business.advice', 'business_advice', False),
    ('battle_map.review', 'customer', True),
])
async def test_typed_worker_retry_uses_business_aggregate_and_expired_lease_cannot_send(platform, connection, job_type, aggregate_type, per_job, monkeypatch):  # noqa: F811
    from dataclasses import replace
    from sales_backend.db import Database
    from sales_backend.job_context import current_job_lease, JobLease, JobLeaseLost
    from sales_backend.main import app
    # These tests exercise job/usage accounting, not a live provider. Supply
    # explicit synthetic setup facts rather than inheriting process defaults.
    monkeypatch.setattr(app.state, 'settings', replace(app.state.settings,
        senseaudio_base_url='https://synthetic-provider.invalid',
        senseaudio_api_key='synthetic-setup-key', llm_model='synthetic'))
    class LeaseDatabase(TransactionDatabase):
        transaction = Database.transaction
    client, _ = platform; actor = await company(client, connection); db = LeaseDatabase(connection)
    await limit(client, actor, 1)
    job, aggregate, first_token, second_token = [uuid4() for _ in range(4)]
    await seed_execute(connection, """INSERT INTO ops.job(id,workspace_id,job_type,payload,aggregate_id,status,lease_token,locked_until,aggregate_type)
        VALUES($1,$2::uuid,$5,'{}',$3,'running',$4,clock_timestamp()+interval '1 hour',$6)""",
        job, actor.workspace_id, aggregate, first_token, job_type, aggregate_type)
    context = current_job_lease.set(JobLease(str(job),str(first_token)))
    try:
        # New tenants have AI disabled until explicitly enabled by the platform.
        with pytest.raises(JobLeaseLost):
            await begin(db, actor)
        module_settings = (await client.get(f'/api/v1/platform/tenants/{actor.workspace_id}/modules')).json()
        response = await client.post(f'/api/v1/platform/tenants/{actor.workspace_id}/modules', json={
            'version_no': module_settings['version_no'],
            'modules': [{'code': m['code'], 'enabled': m['code'] == 'ai' or m['enabled'],
                         'expires_at': m['expires_at']} for m in module_settings['modules']],
            'reason': '明确为合成 Worker 验收开通 AI'})
        assert response.status_code == 200, response.text
        first, a = await begin(db, actor)
        await first.prepare_dispatch(a)
        await first.finish(a, httpx.Response(503), RuntimeError('synthetic retry'))
        await seed_execute(connection, 'UPDATE ops.job SET lease_token=$2 WHERE id=$1', job, second_token)
        with pytest.raises(JobLeaseLost):
            await begin(db, actor)
        current_job_lease.set(JobLease(str(job),str(second_token)))
        second, b = await begin(db, actor)  # New inference ID, same business aggregate.
        await seed_execute(connection, "UPDATE ops.job SET locked_until=clock_timestamp()-interval '1 second' WHERE id=$1", job)
        with pytest.raises(JobLeaseLost):
            await second.prepare_dispatch(b)
        await second.finish(b, None, JobLeaseLost(), network_sent=False)
        await seed_execute(connection, "UPDATE ops.job SET locked_until=clock_timestamp()+interval '1 hour' WHERE id=$1", job)
        third, c = await begin(db, actor)
        await third.prepare_dispatch(c)
        await seed_execute(connection, "UPDATE ops.job SET locked_until=clock_timestamp()-interval '1 second' WHERE id=$1", job)
        await third.finish(c, httpx.Response(200, json={'usage': {'prompt_tokens':5}}), None)
        if per_job:
            # A later evaluation of the same customer is a new billable event,
            # not another retry of that customer's previous evaluation.
            fresh = uuid4()
            await seed_execute(connection, """INSERT INTO ops.job(id,workspace_id,job_type,payload,aggregate_id,status,lease_token,locked_until,aggregate_type)
                VALUES($1,$2::uuid,$5,'{}',$3,'running',$4,clock_timestamp()+interval '1 hour',$6)""",
                fresh, actor.workspace_id, aggregate, second_token, job_type, aggregate_type)
            current_job_lease.set(JobLease(str(fresh),str(second_token)))
            with pytest.raises(CallQuotaExceeded):
                await begin(db, actor)
            await limit(client, actor, 2)
            fourth, d = await begin(db, actor)
            await fourth.prepare_dispatch(d)
            await fourth.finish(d, httpx.Response(200), None)
    finally:
        current_job_lease.reset(context)
    assert (await item(client, actor))['used'] == (2 if per_job else 1)
    assert await seed_fetchval(connection, 'SELECT count(*) FROM control.call_event WHERE workspace_id=$1::uuid AND event_id=$2', actor.workspace_id, job if per_job else aggregate) == 1
    assert await seed_fetchval(connection, 'SELECT count(*) FROM agent.model_invocation WHERE workspace_id=$1::uuid', actor.workspace_id) == (4 if per_job else 3)
    assert await seed_fetchval(connection, 'SELECT input_tokens FROM agent.model_invocation WHERE id=$1::uuid', c) == 5
