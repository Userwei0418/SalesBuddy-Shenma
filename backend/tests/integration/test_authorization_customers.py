"""Organization appointments stay unchanged while explicit business grants work."""
from uuid import uuid4

import asyncpg
import pytest

from sales_backend.db import set_request_context
from sales_backend.repositories.customer_mutations import CustomerMutationRepository
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.repositories.operations_customers import OperationsCustomerRepository
from tests.integration.test_authorization_opportunities import grant
from tests.integration.test_operations_claims_sql import actor, create_customer
from tests.integration.test_sales_opportunity_permissions import account_fixture

pytestmark = pytest.mark.asyncio

async def account_context(connection, user, admin):
    person=(await IdentityRepository().find_actor_by_id(connection,workspace_id=admin.workspace_id,
        user_id=user['id'],role='fde')).context
    await set_request_context(connection,person)
    return person

async def test_fde_can_claim_only_with_explicit_grant_and_revoke_blocks_approval(connection):
    customer=await create_customer(connection)
    user, _, admin=await account_fixture(connection,['fde'])
    await account_context(connection,user,admin)
    assert not await connection.fetchval('SELECT security.can_claim_customer($1::uuid)',customer['id'])
    await grant(connection,admin,user['id'],[dict(permission='customer.claim',effect='allow',scope='self')])
    await account_context(connection,user,admin)
    request=await connection.fetchval('SELECT security.claim_customer($1::uuid)',customer['id'])
    await grant(connection,admin,user['id'],[],version=1)
    with pytest.raises(asyncpg.RaiseError,match='权限已失效'):
        async with connection.transaction():
            await connection.fetchval("SELECT security.review_customer_claim($1::uuid,'approved','验证撤权')",request['request_id'])
    await grant(connection,admin,user['id'],[dict(permission='customer.claim',effect='allow',scope='self')],version=2)
    await connection.fetchval("SELECT security.review_customer_claim($1::uuid,'approved','有效授权')",request['request_id'])
    assert await connection.fetchval('SELECT owner_user_ref_id::text FROM crm.customer_ownership WHERE customer_id=$1::uuid',customer['id'])==user['id']
    assert await connection.fetchval('SELECT array_agg(DISTINCT role_code) FROM platform.role_binding WHERE user_ref_id=$1::uuid',user['id'])==['fde']

async def test_fde_customer_creation_and_scoped_review_do_not_imply_release(connection):
    user, teams, admin=await account_fixture(connection,['fde'])
    permissions=[dict(permission=p,effect='allow',scope='teams',team_ids=[teams['fde']])
        for p in ('customer.create','customer.read','customer.claim_review')]
    await grant(connection,admin,user['id'],permissions)
    current=await account_context(connection,user,admin)
    customer=await CustomerMutationRepository().create(connection,current,data=dict(
        name='逐功能建档-'+uuid4().hex,industry='软件',customer_type='潜在客户',source='销售线索',level_code='Tier-2',
        target_team_id=teams['fde'],company_reference='ISOLATED-'+uuid4().hex,partner_name='',
        contact_name='测试联系人',contact_title='经理',contact_role='决策者'))
    await actor(connection,'XS001')
    req=await connection.fetchval('SELECT security.claim_customer($1::uuid)',customer['id'])
    await set_request_context(connection,current)
    result=await OperationsCustomerRepository().review(connection,req['request_id'],'approved','核验',actor=current)
    assert result['status']=='approved'
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with connection.transaction():
            await OperationsCustomerRepository().release(connection,customer['id'],2,'不得越权')
    other=await create_customer(connection)
    await actor(connection,'XS001')
    req2=await connection.fetchval('SELECT security.claim_customer($1::uuid)',other['id'])
    await set_request_context(connection,current)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with connection.transaction():
            await OperationsCustomerRepository().review(connection,req2['request_id'],'approved','不得跨团队',actor=current)
