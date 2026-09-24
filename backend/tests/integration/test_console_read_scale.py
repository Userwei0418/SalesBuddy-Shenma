"""Production-shaped read volume, tenant boundaries and statement-local grants.

Seeds are confined to runner-created disposable databases. No production data or
model invocation is required to exercise the console's read paths.
"""
from datetime import UTC, datetime, timedelta
from time import perf_counter

import pytest

from sales_backend.db import set_request_context
from sales_backend.repositories.business_activity import BusinessActivityRepository
from sales_backend.repositories.feishu_sync import FeishuRepository
from sales_backend.repositories.operations_logs import OperationsLogRepository
from tests.integration.feishu_fixtures import assert_restricted, fixture_owner
from tests.integration.test_authorization_opportunities import grant
from tests.integration.test_feishu_storage import setup
from tests.integration.test_operations_api import client_for, sign_in
from tests.integration.test_operations_claims_sql import actor

pytestmark = pytest.mark.asyncio


async def test_large_console_reads_keep_tenant_isolation_and_recheck_revocation(connection):
    await assert_restricted(connection)
    admin = await actor(connection, 'ADMIN001')
    ws, cid = await setup(connection)
    # The Feishu fixture is a second company, used to exercise non-empty status
    # and then reject the same data after switching to the first company.
    async with fixture_owner(connection):
        await connection.execute("""INSERT INTO ops.feishu_event
          (connection_id,workspace_id,object_kind,object_id,status,completed_at)
          SELECT $1,$2,'customer',gen_random_uuid(),'succeeded',clock_timestamp()
          FROM generate_series(1,100000)""", cid, ws)
        await connection.execute("""INSERT INTO ops.audit_log
          (workspace_id,actor_user_ref_id,action_code,object_type,object_id,object_label)
          SELECT $1::uuid,$2::uuid,'http.read','api_request',gen_random_uuid(),'isolated polling'
          FROM generate_series(1,100000)""", admin.workspace_id, admin.user_id)
        await connection.execute("""INSERT INTO crm.customer(workspace_id,name,normalized_name,created_by_user_ref_id)
          SELECT $1::uuid,'isolated activity '||n,'isolated activity '||n,$2::uuid FROM generate_series(1,300)n""",
          admin.workspace_id, admin.user_id)
        await connection.execute('ANALYZE ops.audit_log; ANALYZE ops.feishu_event; ANALYZE crm.customer')
    await connection.execute("SET LOCAL statement_timeout='8s'")
    started = perf_counter()
    status = await FeishuRepository().status(connection, str(ws))
    assert sum(row['count'] for row in status['counts']) == 100000
    print(f'console_scale_feishu_ms={(perf_counter()-started)*1000:.1f}')
    await set_request_context(connection, admin)
    assert not (await FeishuRepository().status(connection, str(ws)))['counts']
    now = datetime.now(UTC)
    window = dict(start=now-timedelta(days=1), end=now+timedelta(days=1))
    started = perf_counter()
    audit = await OperationsLogRepository().audit(connection, **window)
    assert audit['total'] >= 100000 and len(audit['items']) == 50
    print(f'console_scale_audit_ms={(perf_counter()-started)*1000:.1f}')
    started = perf_counter()
    activity = await BusinessActivityRepository().list(connection, **window)
    assert activity['total'] >= 300 and len(activity['items']) == 50
    assert all('isolated polling' not in str(row) for row in activity['items'])
    print(f'console_scale_activity_ms={(perf_counter()-started)*1000:.1f}')
    # Every statement must use the current actor/permission snapshot, even on a
    # pooled connection that already executed the same prepared query.
    await grant(connection, admin, admin.user_id, [dict(permission=p,effect='deny') for p in
                ('audit.read','audit.export','audit.business_read','audit.business_export')])
    await set_request_context(connection, admin)
    assert (await OperationsLogRepository().audit(connection, **window))['total'] == 0
    assert (await BusinessActivityRepository().list(connection, **window))['total'] == 0
    await actor(connection, 'XS001')
    assert (await BusinessActivityRepository().list(connection, **window))['total'] == 0
    assert not (await FeishuRepository().status(connection, str(ws)))['counts']


# Each entry point currently used by the console, including secondary requests
# made by its page loader. Actual HTTP, permission gates and PostgreSQL are used.
@pytest.mark.parametrize('path', [
    '/api/v1/console/auth/me', '/api/v1/console/companies',
    '/api/v1/console/organization', '/api/v1/console/customers',
    '/api/v1/console/claims', '/api/v1/console/opportunities',
    '/api/v1/console/partners', '/api/v1/tasks?inbox=true',
    '/api/v1/console/targets', '/api/v1/console/target-requests', '/api/v1/console/target-batches',
    '/api/v1/console/permissions/catalog', '/api/v1/console/permissions/roles',
    '/api/v1/console/permissions/options', '/api/v1/console/permissions/me',
    '/api/v1/console/permissions/audit', '/api/v1/console/ai/runs',
    '/api/v1/console/ai/overview', '/api/v1/console/ai/calls',
    '/api/v1/console/ai/rules', '/api/v1/admin/model-apis',
    '/api/v1/console/ai/execution', '/api/v1/console/company-rules',
    '/api/v1/console/feishu-sync', '/api/v1/console/feishu-sync/catalog',
    '/api/v1/console/feishu-sync/status', '/api/v1/console/activities',
    '/api/v1/console/audit', '/api/v1/console/system-events',
])
async def test_all_admin_page_loading_endpoints(connection, path):
    async with await client_for(connection) as client:
        await sign_in(client, 'ADMIN001')
        response = await client.get(path)
        assert response.status_code == 200, (path, response.text)
        assert response.headers['content-type'].startswith('application/json')
