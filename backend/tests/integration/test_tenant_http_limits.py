"""Real HTTP admission, platform configuration and restricted PostgreSQL isolation."""
import asyncpg
import pytest

from sales_backend.db import set_request_context, json_value
from tests.integration.feishu_fixtures import seed_execute, seed_fetchval
from tests.integration.test_operations_api import client_for
from tests.integration.test_platform_control import platform, PASSWORD, tenant  # noqa: F401
from tests.integration.test_tenant_storage_quota import company

pytestmark = pytest.mark.asyncio


async def settings(client, actor):
    response = await client.get(f'/api/v1/platform/tenants/{actor.workspace_id}/http-limits')
    assert response.status_code == 200, response.text
    return response.json()


async def save(client, actor, bucket='read', rate=1, burst=2):
    before = await settings(client, actor)
    body = {'version_no': before['version_no'], 'bucket': bucket, 'rate_per_minute': rate,
            'burst': burst, 'reason': '合成限流验收'}
    response = await client.post(f'/api/v1/platform/tenants/{actor.workspace_id}/http-limits', json=body)
    assert response.status_code == 200, response.text
    return body, response.json()


async def consume(connection, actor, bucket='read'):
    async with connection.transaction():
        await set_request_context(connection, actor)
        return json_value(await connection.fetchval('SELECT security.consume_http_request($1)', bucket))


async def login(client, platform_client, actor):
    company_code = (await tenant(platform_client, actor.workspace_id))['company_code']
    response = await client.post('/api/v1/console/auth/login', json={
        'workspace': company_code, 'account_code': 'ADMIN', 'password': PASSWORD})
    assert response.status_code == 200, response.text
    client.headers['Authorization'] = 'Bearer ' + response.json()['access_token']


async def counts(client, actor, bucket='read'):
    return next(row for row in (await settings(client, actor))['items'] if row['bucket'] == bucket)


async def test_platform_limit_configuration_conflict_and_private_tables(platform, connection):  # noqa: F811
    client, _ = platform; actor = await company(client, connection)
    assert all(row['rate_per_minute'] is None and row['burst'] is None for row in (await settings(client, actor))['items'])
    body, _ = await save(client, actor)
    path = f'/api/v1/platform/tenants/{actor.workspace_id}/http-limits'
    assert (await client.post(path, json=body)).status_code == 409
    for changes in ({'rate_per_minute': 0}, {'burst': None}, {'rate_per_minute': True}, {'bucket': 'forged'}):
        body['version_no'] = (await settings(client, actor))['version_no']
        assert (await client.post(path, json={**body, **changes})).status_code == 422
    assert 'tenant.http_limit' in (await client.get('/api/v1/platform/audit')).text
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with connection.transaction():
            await connection.fetch('SELECT * FROM control.tenant_http_limit')
    async with await client_for(connection) as ordinary:
        await login(ordinary, client, actor)
        assert (await ordinary.get(path)).status_code == 401


async def test_refill_buckets_tenants_and_limit_updates(platform, connection):  # noqa: F811
    client, _ = platform; first = await company(client, connection); second = await company(client, connection)
    await save(client, first)
    assert (await consume(connection, first))['allowed']
    assert (await consume(connection, first))['allowed']
    denied = await consume(connection, first)
    assert not denied['allowed'] and 1 <= denied['retry_after'] <= 60
    assert (await consume(connection, first, 'write'))['allowed']
    assert (await consume(connection, second))['allowed']
    row = await counts(client, first)
    assert (row['accepted'], row['rejected']) == (2, 1)
    # Refilling is bounded by burst, even across long idle intervals.
    await seed_execute(connection, "UPDATE control.tenant_http_limit SET refilled_at=clock_timestamp()-interval '1 day' WHERE workspace_id=$1::uuid AND bucket='read'", first.workspace_id)
    await save(client, first, burst=100)
    balance = await seed_fetchval(connection, "SELECT tokens FROM control.tenant_http_limit WHERE workspace_id=$1::uuid AND bucket='read'", first.workspace_id)
    assert balance == 2  # Editing the limit does not mint 100 fresh tokens.
    await save(client, first, burst=1)
    assert (await consume(connection, first))['allowed']
    assert not (await consume(connection, first))['allowed']
    await save(client, first, rate=None, burst=None)
    assert (await consume(connection, first))['allowed']
    assert (await counts(client, first))['accepted'] == 3  # Unlimited requests are not counted.


async def test_http_counts_once_and_recovery_remains_available(platform, connection):  # noqa: F811
    client, _ = platform; actor = await company(client, connection)
    await save(client, actor, burst=1)
    async with await client_for(connection) as business:
        await login(business, client, actor)
        # Permission gate and endpoint both request identity. One HTTP call consumes one token.
        first = await business.get('/api/v1/console/organization')
        assert first.status_code == 200, first.text
        assert (await counts(client, actor))['accepted'] == 1
        denied = await business.get('/api/v1/console/organization')
        assert denied.status_code == 429 and denied.json()['code'] == 'TENANT_RATE_LIMITED'
        assert int(denied.headers['Retry-After']) == denied.json()['retry_after'] >= 1
        assert denied.headers['Cache-Control'] == 'no-store'
        assert (await business.get('/api/v1/console/auth/me')).status_code == 200
        assert (await business.post('/api/v1/console/auth/logout')).status_code == 200
    assert (await counts(client, actor))['rejected'] == 1


async def test_unauthorized_company_selection_does_not_consume_other_tenant(platform, connection):  # noqa: F811
    client, _ = platform; first = await company(client, connection); other = await company(client, connection)
    await save(client, first, burst=1); await save(client, other, burst=1)
    async with await client_for(connection) as business:
        await login(business, client, first)
        denied = await business.get('/api/v1/console/organization', headers={'X-Company-ID': other.workspace_id})
        assert denied.status_code == 403
        assert (await counts(client, other))['accepted'] == 0
        assert (await business.get('/api/v1/console/organization')).status_code == 200
        assert (await business.get('/api/v1/console/organization')).status_code == 429
    async with await client_for(connection) as second:
        await login(second, client, other)
        assert (await second.get('/api/v1/console/organization')).status_code == 200


async def test_failed_business_does_not_refund_and_uploads_have_separate_limit(platform, connection):  # noqa: F811
    client, _ = platform; actor = await company(client, connection)
    await save(client, actor, bucket='write', burst=1)
    await save(client, actor, bucket='upload', burst=1)
    assert (await consume(connection, actor, 'upload'))['allowed']
    async with await client_for(connection) as business:
        await login(business, client, actor)
        invalid = await business.put('/api/v1/console/password-policy', json={})
        assert invalid.status_code == 422, invalid.text
        assert (await business.put('/api/v1/console/password-policy', json={})).status_code == 429
        denied = await business.post('/api/v1/visit-imports', files={'file': ('test.txt', b'synthetic', 'text/plain')})
        assert denied.status_code == 429, denied.text
    assert (await counts(client, actor, 'upload'))['rejected'] == 1
    assert await seed_fetchval(connection, 'SELECT count(*) FROM activity.visit_import WHERE workspace_id=$1::uuid', actor.workspace_id) == 0


async def test_admission_overhead_report(platform, connection, monkeypatch):  # noqa: F811
    """Compare the same synthetic HTTP read with admission bypassed/unlimited/limited.

    This estimates local incremental overhead, not production capacity or an SLA.
    """
    import json
    import statistics
    from time import perf_counter
    from sales_backend.api import dependencies
    client, _ = platform; actor = await company(client, connection)
    admission = dependencies.admit_request
    async def without_admission(*args):
        return None
    async with await client_for(connection) as business:
        await login(business, client, actor)
        async def sample():
            timings = []
            for index in range(25):
                started = perf_counter()
                response = await business.get('/api/v1/console/organization')
                assert response.status_code == 200, response.text
                if index >= 5:
                    timings.append((perf_counter()-started)*1000)
            return {'median_ms': round(statistics.median(timings), 3), 'p95_ms': round(sorted(timings)[18], 3)}
        monkeypatch.setattr(dependencies, 'admit_request', without_admission)
        before = await sample()
        monkeypatch.setattr(dependencies, 'admit_request', admission)
        unlimited = await sample()
        await save(client, actor, rate=100000, burst=1000)
        limited = await sample()
    assert (await counts(client, actor))['accepted'] == 25
    print('HTTP_LIMIT_OVERHEAD '+json.dumps({'baseline_without_admission': before,
          'unlimited': unlimited, 'limited': limited, 'samples_each': 20}))
