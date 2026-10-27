"""Custom identity roles are visible without granting sales appointments."""
import asyncpg
import pytest

from sales_backend.contracts.authorization import AccountAuthorizationSave, RoleSave
from sales_backend.db import set_request_context
from sales_backend.repositories.authorization import AuthorizationRepository
from sales_backend.repositories.operations_accounts import OperationsAccountRepository
from sales_backend.services.operations_accounts import OperationsAccountService
from tests.integration.test_operations_claims_sql import actor

pytestmark = pytest.mark.asyncio


async def test_custom_role_labels_permissions_and_operations_reader(connection):
    admin = await actor(connection, 'ADMIN001')
    auth = AuthorizationRepository()
    org = OperationsAccountRepository()
    created = await OperationsAccountService().create(connection, admin, dict(
        account_code='MARKETFIXTURE', display_name='隔离市场', roles=['administrator'],
        company_roles=['administrator'], memberships=[], team_id=None), 'Isolated-Testing-2026')
    uid = created['id']
    before = (await auth.effective(connection, uid)).capabilities()
    role = await auth.save_role(connection, None, RoleSave(name='市场', reason='隔离角色展示', permissions=[]).model_dump(mode='json'))
    await auth.save_account(connection, uid, AccountAuthorizationSave(version_no=0, reason='市场身份',
        roles=[dict(role_id=role['id'], scope='workspace')]).model_dump(mode='json'))
    result = await org.organization(connection)
    user = next(u for u in result['accounts'] if u['id'] == uid)
    assert user['company_roles'] == ['administrator'] and user['memberships'] == []
    assert user['permission_roles'] == [dict(id=role['id'], name='市场', scope='workspace', user_ref_id=uid)]
    assert dict(id=role['id'], name='市场') in result['permission_roles']
    assert (await auth.effective(connection, uid)).capabilities() == before
    # Saving old-format account details cannot strip the separate custom role.
    await OperationsAccountService().update(connection, admin, uid, dict(
        version_no=1, display_name='隔离市场', status='active', roles=['administrator'],
        company_roles=['administrator'], team_id=None, memberships=[]))
    await actor(connection, 'OPS001')
    assert not (await auth.effective(connection)).allows('authorization.read')
    result = await org.organization(connection)
    assert next(u for u in result['accounts'] if u['id'] == uid)['permission_roles'][0]['name'] == '市场'
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with connection.transaction():
            await auth.save_role(connection, role['id'], RoleSave(name='越权', reason='拒绝', version_no=1).model_dump(mode='json'))
    await set_request_context(connection, admin)
    await auth.save_role(connection, role['id'], RoleSave(name='市场', reason='停用', status='inactive', version_no=1).model_dump(mode='json'))
    assert next(u for u in (await org.organization(connection))['accounts'] if u['id'] == uid)['permission_roles'] == []
    await actor(connection, 'XS001')
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with connection.transaction():
            await connection.fetchval('SELECT security.organization_permission_roles()')


async def test_custom_role_directory_is_company_scoped(connection):
    from tests.integration.test_company_tenants import provision_fixture
    from sales_backend.repositories.identity import IdentityRepository
    admin, _, other_workspace, _ = await provision_fixture(connection)
    await set_request_context(connection, admin)
    auth = AuthorizationRepository()
    role = await auth.save_role(connection, None, RoleSave(name='仅源公司市场', reason='公司隔离').model_dump(mode='json'))
    await auth.save_account(connection, admin.user_id, AccountAuthorizationSave(version_no=0, reason='公司隔离',
        roles=[dict(role_id=role['id'], scope='workspace')]).model_dump(mode='json'))
    own = await connection.fetchval('SELECT security.organization_permission_roles()')
    assert any(r['id'] == role['id'] for r in own['roles'])
    delegated = await connection.fetchval('SELECT security.company_management_actor($1::uuid)', other_workspace)
    assert delegated
    await set_request_context(connection, IdentityRepository()._actor(delegated).context)
    other = await connection.fetchval('SELECT security.organization_permission_roles()')
    assert all(r['id'] != role['id'] for r in other['roles'])
    assert all(r['id'] != role['id'] for r in other['assignments'])
