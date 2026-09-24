"""Large customer reads and equivalence of workspace and record-level access."""
from datetime import UTC, datetime, timedelta
from time import perf_counter
from uuid import uuid4

import pytest

from sales_backend.db import set_request_context
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.repositories.operations_customers import OperationsCustomerRepository
from tests.integration.feishu_fixtures import assert_restricted, fixture_owner
from tests.integration.test_authorization_customers import account_context
from tests.integration.test_authorization_opportunities import grant
from tests.integration.test_operations_claims_sql import actor, create_customer
from tests.integration.test_sales_opportunity_permissions import account_fixture

pytestmark = pytest.mark.asyncio


async def test_console_customer_reads_scale_without_enriching_all_rows(connection):
    await assert_restricted(connection)
    admin = await actor(connection, 'ADMIN001')
    marker = 'console-scale-' + uuid4().hex
    async with fixture_owner(connection):
        await connection.execute("""INSERT INTO crm.customer
          (workspace_id,name,normalized_name,created_by_user_ref_id,industry_code,level_code,company_reference)
          SELECT $1::uuid,$2||n,$2||n,$3::uuid,
            CASE WHEN n%2=0 THEN '软件' ELSE '制造' END,'Tier-2',$2||n
          FROM generate_series(1,17200)n""", admin.workspace_id, marker, admin.user_id)
        await connection.execute('ANALYZE crm.customer; ANALYZE crm.customer_ownership')
    await connection.execute("SET LOCAL statement_timeout='3s'")
    repo = OperationsCustomerRepository()
    started = perf_counter()
    summary = await repo.summary(connection)
    assert summary['customers'] >= 17200 and summary['unclaimed'] >= 17200
    print(f'console_customer_summary_ms={(perf_counter()-started)*1000:.1f}')
    started = perf_counter()
    first = await repo.list(connection, q=marker, limit=50)
    second = await repo.list(connection, q=marker, limit=50, offset=50)
    assert first['total'] == second['total'] == 17200
    assert len(first['items']) == len(second['items']) == 50
    assert not ({r['id'] for r in first['items']} & {r['id'] for r in second['items']})
    filtered = await repo.list(connection, q=marker, industry='软件', level='Tier-2', state='unclaimed')
    assert filtered['total'] == 8600 and len(filtered['items']) == 50
    assert all(r['industry_code'] == '软件' and r['total_count'] == 8600 for r in filtered['items'])
    assert not (await repo.list(connection, q=marker, state='claimed'))['items']
    assert not (await repo.list(connection, q=marker, owner=admin.user_id))['items']
    print(f'console_customer_pages_ms={(perf_counter()-started)*1000:.1f}')


async def test_workspace_shortcut_matches_original_policy_after_scope_changes(connection):
    item = await create_customer(connection)
    user, teams, admin = await account_fixture(connection, ['fde'])
    other_workspace, foreign_id, deleted_id, foreign_user = uuid4(), uuid4(), uuid4(), uuid4()
    async with fixture_owner(connection):
        await connection.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'隔离公司')",
                                 other_workspace, 'isolated-'+uuid4().hex)
        await connection.execute("""INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name)
          VALUES($1,$2,$3,'隔离人员')""", foreign_user, other_workspace, 'isolated-'+uuid4().hex)
        await connection.execute("""INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code)
          VALUES($1,$2,'administrator','workspace')""", other_workspace, foreign_user)
        await connection.execute("""INSERT INTO crm.customer(id,workspace_id,name,normalized_name,deleted_at,created_by_user_ref_id)
          VALUES($1,$2,'外公司','外公司',NULL,$5),($3,$4::uuid,'已删除','已删除',clock_timestamp(),$6::uuid)""",
          foreign_id, other_workspace, deleted_id, admin.workspace_id, foreign_user, admin.user_id)
    # These exact statements are reused beyond asyncpg's prepared-statement
    # threshold, then on new scope snapshots and projected feature changes.
    reads = [
        "SELECT id::text FROM crm.customer ORDER BY id",
        "SELECT customer_id::text FROM crm.customer_ownership ORDER BY customer_id",
    ]
    async def equivalent(context, feature=''):
        await set_request_context(connection, context)
        await connection.execute("SELECT set_config('app.authorized_feature',$1,true)", feature)
        actual = [list(await connection.fetchval('SELECT COALESCE(array_agg(id),ARRAY[]::text[]) FROM ('+q+
                  ') x(id)')) for q in reads]
        async with fixture_owner(connection):
            expected_customers = await connection.fetchval("""SELECT COALESCE(array_agg(id::text ORDER BY id),ARRAY[]::text[])
              FROM crm.customer WHERE workspace_id=common.current_workspace_id() AND deleted_at IS NULL AND (
              security.authorization_customer(security.authorization_read_feature('customer'),id)
              OR security.authorization_allows(security.authorization_read_feature('customer'),workspace_id,
                owner_user_ref_id,ARRAY[owner_team_id],owner_user_ref_id=common.current_user_ref_id()))""")
            expected_owners = await connection.fetchval("""SELECT COALESCE(array_agg(customer_id::text ORDER BY customer_id),ARRAY[]::text[])
              FROM crm.customer_ownership WHERE workspace_id=common.current_workspace_id() AND (
              owner_user_ref_id=common.current_user_ref_id()
              OR security.authorization_customer(security.authorization_read_feature('customer'),customer_id)
              OR security.authorization_customer('customer.claim_review',customer_id))""")
        assert actual == [expected_customers, expected_owners]
        hidden_id = str(foreign_id) if context.workspace_id == admin.workspace_id else item['id']
        assert hidden_id not in actual[0] and hidden_id not in actual[1]
        assert str(deleted_id) not in actual[0] and str(deleted_id) not in actual[1]
        return actual

    for _ in range(6):
        assert item['id'] in (await equivalent(admin))[0]
    # Resolve only the synthetic foreign identity as fixture owner; querying it
    # as the first tenant correctly returns nothing. Actual reads stay restricted.
    async with fixture_owner(connection):
        foreign = await IdentityRepository().find_actor_by_id(connection,
            workspace_id=str(other_workspace), user_id=str(foreign_user), role='administrator')
    assert str(foreign_id) in (await equivalent(foreign.context))[0]
    assert item['id'] in (await equivalent(admin))[0]
    scenarios = [
        [dict(permission='customer.read', effect='allow', scope='workspace')],
        [dict(permission='customer.read', effect='allow', scope='teams', team_ids=[teams['fde']])],
        [dict(permission='customer.read', effect='allow', scope='self')],
        [dict(permission='customer.read', effect='allow', scope='assigned')],
        [dict(permission='customer.claim_review', effect='allow', scope='workspace'),
         dict(permission='customer.read', effect='deny')],
        [dict(permission='dashboard.read', effect='allow', scope='workspace'),
         dict(permission='customer.read', effect='deny')],
        [dict(permission='customer.read', effect='deny'), dict(permission='customer.claim_review', effect='deny')],
    ]
    for version, overrides in enumerate(scenarios):
        await grant(connection, admin, user['id'], overrides, version=version)
        context = await account_context(connection, user, admin)
        await equivalent(context)
        await equivalent(context, 'dashboard.read')
    await equivalent(await actor(connection, 'XS001'))


async def test_paged_console_enrichment_keeps_contact_and_filter_contract(connection):
    customer = await create_customer(connection)
    admin = await actor(connection, 'ADMIN001')
    async with fixture_owner(connection):
        await connection.execute("""UPDATE crm.contact SET is_primary=false
          WHERE customer_id=$1::uuid""", customer['id'])
        await connection.execute("""INSERT INTO crm.contact(workspace_id,customer_id,name,title,phone,email,is_primary,created_at)
          VALUES($1::uuid,$2::uuid,'主联系人','经理','10000000000','isolated@example.test',true,$3),
          ($1::uuid,$2::uuid,'旧联系人','顾问','10000000001',NULL,false,$4)""",
          admin.workspace_id, customer['id'], datetime.now(UTC), datetime.now(UTC)-timedelta(days=1))
    repo = OperationsCustomerRepository()
    result = await repo.list(connection, q=customer['name'], state='unclaimed', limit=1)
    assert result['total'] == 1 and result['offset'] == 0 and result['limit'] == 1
    row = result['items'][0]
    assert row['id'] == customer['id'] and row['total_count'] == 1
    assert row['contact_name'] == '主联系人' and row['contact_email'] == 'isolated@example.test'
    assert row['ownership_state'] == 'unclaimed' and row['ownership_version'] >= 1
    assert (await repo.list(connection, q=row['company_reference']))['items'] == result['items']
    assert not (await repo.list(connection, q=customer['name'], offset=1))['items']
    assert (await repo.list(connection, q=customer['name'], limit=10001))['items'] == result['items']
