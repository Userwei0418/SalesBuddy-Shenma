"""Explicit extra permissions never imply voiding or deletion rights."""
from uuid import UUID

import asyncpg
import pytest

from sales_backend.contracts.demo_scenes import DemoSceneCreate, DemoSceneUpdate, DemoSceneDelete
from sales_backend.db import set_request_context
from sales_backend.repositories.customer_assets import CustomerAssetRepository, can_manage
from sales_backend.repositories.demo_scenes import DemoSceneRepository
from sales_backend.repositories.identity import IdentityRepository
from tests.integration.test_authorization_opportunities import grant
from tests.integration.test_business_rankings import opportunity
from tests.integration.test_customer_assets import fact
from tests.integration.test_operations_claims_sql import actor
from tests.integration.test_sales_opportunity_permissions import account_fixture

pytestmark = pytest.mark.asyncio

async def test_scoped_fde_extra_actual_create_does_not_allow_void(connection):
    op = await opportunity(connection, 'XS001', 100)
    owner = await actor(connection, 'XS001')
    user, _, admin = await account_fixture(connection, ['fde'])
    grants = [dict(permission=p, effect='allow', scope='teams', team_ids=list(owner.team_ids))
              for p in ('customer.read', 'opportunity.read', 'actual.read', 'actual.create')]
    await grant(connection, admin, user['id'], grants)
    current = (await IdentityRepository().find_actor_by_id(connection, workspace_id=admin.workspace_id,
        user_id=user['id'], role='fde')).context
    await set_request_context(connection, current)
    repo = CustomerAssetRepository()
    created = await repo.create(connection, current, fact({'id':op['customer_id']}, opportunity_id=UUID(op['id'])))
    rows = await repo.read(connection, customer_id=op['customer_id'], period='all')
    assert rows['items'][0]['can_void'] is False
    assert await can_manage(connection, customer_id=op['customer_id'], opportunity_id=op['id'])
    with pytest.raises(PermissionError):
        await repo.void(connection, current, UUID(created['id']), '不得越权')
    async with connection.transaction():
        result = await connection.execute('UPDATE crm.customer_actual SET voided_at=clock_timestamp(),'
            'voided_by_user_ref_id=$2::uuid,void_reason=\'拒绝绕过API\' WHERE id=$1::uuid', created['id'],user['id'])
        assert result == 'UPDATE 0'
    other_user, _, _ = await account_fixture(connection, ['sales'])
    other = await opportunity(connection, other_user['account_code'], 100)
    await set_request_context(connection, current)
    assert not await can_manage(connection, customer_id=other['customer_id'], opportunity_id=other['id'])
    with pytest.raises(PermissionError):
        await repo.create(connection, current, fact({'id':other['customer_id']}, opportunity_id=UUID(other['id'])))
    await grant(connection, admin, user['id'], [*grants,dict(permission='actual.void', effect='allow', scope='teams',team_ids=list(owner.team_ids))], version=1)
    await set_request_context(connection, current)
    rows = await repo.read(connection, customer_id=op['customer_id'], period='all')
    assert rows['items'][0]['can_void'] is True
    assert (await repo.void(connection,current,UUID(created['id']),'金额修正'))['voided']

async def test_demo_update_and_delete_are_separate_scoped_actions(connection):
    op = await opportunity(connection, 'XS001', 100)
    owner = await actor(connection, 'XS001')
    repo = DemoSceneRepository()
    scene = await repo.create(connection,owner,UUID(op['id']),DemoSceneCreate(name='演示方案'))
    await actor(connection,'ADMIN001')
    await grant(connection,await actor(connection,'ADMIN001'),owner.user_id,
        [dict(permission='demo_scene.delete',effect='deny')])
    await set_request_context(connection,owner)
    updated=await repo.update(connection,scene['id'],DemoSceneUpdate(name='已修改',version_no=scene['version_no']))
    with pytest.raises(PermissionError):
        await repo.update(connection,scene['id'],DemoSceneDelete(version_no=updated['version_no']),delete=True)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with connection.transaction():
            await connection.execute('UPDATE crm.opportunity_demo_scenes SET deleted_at=clock_timestamp() WHERE id=$1',scene['id'])
    assert (await repo.get(connection,scene['id']))['name']=='已修改'
