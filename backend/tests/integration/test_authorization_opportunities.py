"""Configured grants reach real business writes without changing business appointments."""
from uuid import uuid4

import asyncpg
import pytest

from sales_backend.contracts.authorization import AccountAuthorizationSave
from sales_backend.db import set_request_context
from sales_backend.repositories.authorization import AuthorizationRepository
from sales_backend.repositories.operations_accounts import OperationsAccountRepository
from tests.integration.test_authorization_store import resolve
from tests.integration.test_operations_api import client_for
from tests.integration.test_operations_claims_sql import create_customer
from tests.integration.test_sales_opportunity_permissions import account_fixture, native_login, opportunity_payload

pytestmark = pytest.mark.asyncio


async def grant(connection, admin, user_id, permissions, version=0):
    await set_request_context(connection, admin)
    return await AuthorizationRepository().save_account(connection, user_id, AccountAuthorizationSave(
        version_no=version, reason="验证逐功能授权", overrides=permissions,
    ).model_dump(mode="json"))


async def test_fde_extra_create_is_scoped_and_does_not_grant_update(connection):
    customer = await create_customer(connection)
    user, teams, admin = await account_fixture(connection, ["fde"])
    async with await client_for(connection) as client:
        await native_login(client, user)
        path = f"/api/v1/customers/{customer['id']}/opportunities"
        body = opportunity_payload()
        assert (await client.post(path, json=body)).status_code == 403
        permissions = [dict(permission="opportunity.create", effect="allow", scope="teams", team_ids=[teams['fde']])]
        await grant(connection, admin, user['id'], permissions)
        denied = await client.post(path, json={**body, "owner_team_id": str(uuid4())})
        assert denied.status_code == 403, denied.text
        response = await client.post(path, json={**body, "owner_team_id": teams['fde']})
        assert response.status_code == 201, response.text
        created = response.json()
        update = await client.post(path, json={**body, 'action': 'update', 'opportunity_id': created['id'],
            'version_no': created['version_no'], 'amount': 2000})
        assert update.status_code == 403, update.text
        await set_request_context(connection, admin)
        roles = await connection.fetch("SELECT DISTINCT role_code FROM platform.role_binding WHERE user_ref_id=$1::uuid", user['id'])
        assert [r['role_code'] for r in roles] == ['fde']
        await grant(connection, admin, user['id'], [], version=1)
        assert (await client.post(path, json=opportunity_payload())).status_code == 403


async def test_sales_and_fde_union_can_create_under_either_display_role(connection):
    customer = await create_customer(connection)
    user, teams, _ = await account_fixture(connection, ["sales", "fde"])
    async with await client_for(connection) as client:
        await native_login(client, user, 'fde')
        response = await client.post(f"/api/v1/customers/{customer['id']}/opportunities", json={
            **opportunity_payload(), "owner_team_id": teams['sales'],
        })
        assert response.status_code == 201, response.text
        snapshot = (await client.get('/api/v1/auth/permissions')).json()
        assert snapshot['permissions']['opportunity.create'] and snapshot['permissions']['profile.fde_read']


async def test_deny_update_blocks_existing_sales_permission_but_preserves_create(connection):
    customer = await create_customer(connection)
    user, _, admin = await account_fixture(connection, ['sales'])
    async with await client_for(connection) as client:
        await native_login(client, user)
        path = f"/api/v1/customers/{customer['id']}/opportunities"
        body = opportunity_payload()
        created = (await client.post(path, json=body)).json()
        await grant(connection, admin, user['id'], [dict(permission='opportunity.update', effect='deny')])
        result = await client.post(path, json={**body, 'action': 'update', 'opportunity_id': created['id'],
            'version_no': created['version_no'], 'amount': 2000})
        assert result.status_code == 403, result.text
        assert (await client.post(path, json=opportunity_payload())).status_code == 201


async def test_self_creation_cannot_assign_an_unrelated_team_without_an_explicit_team_grant(connection):
    customer = await create_customer(connection)
    user, _, admin = await account_fixture(connection, ['sales'])
    _, foreign_teams, _ = await account_fixture(connection, ['sales'])
    team = foreign_teams['sales']
    async with await client_for(connection) as client:
        await native_login(client, user)
        path = f"/api/v1/customers/{customer['id']}/opportunities"
        response = await client.post(path, json={**opportunity_payload(), 'owner_team_id':team})
        assert response.status_code == 403, response.text
        await resolve(connection, admin, user, 'sales')
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with connection.transaction():
                await connection.execute("INSERT INTO crm.opportunity(workspace_id,customer_id,name,owner_user_ref_id,"
                    "owner_team_id,created_by_user_ref_id) VALUES($1::uuid,$2::uuid,'不得转移团队归属',$3::uuid,$4::uuid,$3::uuid)",
                    admin.workspace_id,customer['id'],user['id'],team)
        await grant(connection,admin,user['id'],[dict(permission='opportunity.create',effect='allow',scope='teams',team_ids=[team])])
        response = await client.post(path, json={**opportunity_payload(), 'owner_team_id':team})
        assert response.status_code == 201, response.text
        # Extra functionality did not create a membership or sales appointment.
        await set_request_context(connection,admin)
        assert not await connection.fetchval('SELECT 1 FROM platform.team_membership WHERE user_ref_id=$1::uuid AND team_id=$2::uuid',user['id'],team)


async def test_business_read_adapter_keeps_sales_self_scope_without_expanding_writes(connection):
    user, teams, admin = await account_fixture(connection, ['sales'])
    foreign_user, foreign, _ = await account_fixture(connection, ['sales'])
    current = await resolve(connection, admin, user, 'sales')
    assert not await connection.fetchval(
        "SELECT security.authorization_business_read('opportunity.read',$1::uuid,$2::uuid,ARRAY[$3::uuid],false)",
        admin.workspace_id, user['id'], teams['sales'],
    )
    assert not await connection.fetchval(
        "SELECT security.authorization_business_read('opportunity.read',$1::uuid,$2::uuid,ARRAY[$3::uuid],false)",
        admin.workspace_id, user['id'], foreign['sales'],
    )
    # The adapter is read-only: a team read grant cannot be used as an update grant.
    assert not await connection.fetchval(
        "SELECT security.authorization_allows('opportunity.update',$1::uuid,$2::uuid,ARRAY[$3::uuid],false)",
        admin.workspace_id, foreign_user['id'], foreign['sales'],
    )
    assert current.workspace_id == admin.workspace_id


async def test_supervisor_business_read_includes_active_descendants_only(connection):
    user, teams, admin = await account_fixture(connection, ['supervisor'])
    repo = OperationsAccountRepository()
    child = await repo.save_department(connection, admin, None, dict(
        code=uuid4().hex, name='权限测试-主管子团队', kind='sales', status='active', parent_team_id=teams['supervisor'],
    ))
    sibling_in_department = await repo.save_department(connection, admin, None, dict(
        code=uuid4().hex, name='权限测试-主管同部门团队', kind='sales', status='active',
        parent_team_id=teams['supervisor'],
    ))
    _, sibling, _ = await account_fixture(connection, ['sales'])
    await resolve(connection, admin, user, 'supervisor')
    assert await connection.fetchval(
        "SELECT security.authorization_business_read('battle_map.read',$1::uuid,NULL,ARRAY[$2::uuid],false)",
        admin.workspace_id, child['id'],
    )
    assert await connection.fetchval(
        "SELECT security.authorization_business_read('battle_map.read',$1::uuid,NULL,ARRAY[$2::uuid],false)",
        admin.workspace_id, sibling_in_department['id'],
    )
    assert not await connection.fetchval(
        "SELECT security.authorization_business_read('battle_map.read',$1::uuid,NULL,ARRAY[$2::uuid],false)",
        admin.workspace_id, sibling['sales'],
    )


async def test_nested_supervisor_does_not_expand_to_parent_siblings(connection):
    user, teams, admin = await account_fixture(connection, ['supervisor'])
    repo = OperationsAccountRepository()
    appointed = await repo.save_department(connection, admin, None, dict(
        code=uuid4().hex, name='权限测试-嵌套任命团队', kind='sales', status='active',
        parent_team_id=teams['supervisor'],
    ))
    appointed_child = await repo.save_department(connection, admin, None, dict(
        code=uuid4().hex, name='权限测试-嵌套任命子团队', kind='sales', status='active',
        parent_team_id=appointed['id'],
    ))
    parent_sibling = await repo.save_department(connection, admin, None, dict(
        code=uuid4().hex, name='权限测试-嵌套任命同级团队', kind='sales', status='active',
        parent_team_id=teams['supervisor'],
    ))
    # Move the supervisor appointment below the root. The read scope must
    # start there, rather than climbing back to the root and widening again.
    await connection.execute(
        "UPDATE platform.team_membership SET team_id=$1::uuid WHERE user_ref_id=$2::uuid AND membership_role='supervisor'",
        appointed['id'], user['id'],
    )
    await resolve(connection, admin, user, 'supervisor')
    allowed = "SELECT security.authorization_business_read('battle_map.read',$1::uuid,NULL,ARRAY[$2::uuid],false)"
    assert await connection.fetchval(allowed, admin.workspace_id, appointed['id'])
    assert await connection.fetchval(allowed, admin.workspace_id, appointed_child['id'])
    assert not await connection.fetchval(allowed, admin.workspace_id, parent_sibling['id'])


async def test_operations_extra_create_returns_only_authorized_team_choices_without_inventing_appointments(connection):
    user, _, admin = await account_fixture(connection, [], company_role='operations')
    _, a, _ = await account_fixture(connection, ['sales'])
    _, b, _ = await account_fixture(connection, ['fde'])
    selected = [a['sales'], b['fde']]
    customer = await create_customer(connection)
    await grant(connection, admin, user['id'], [
        dict(permission='access.mini_program',effect='allow',scope='workspace'),
        dict(permission='opportunity.create',effect='allow',scope='teams',team_ids=selected),
    ])
    async with await client_for(connection) as client:
        await native_login(client,user)
        response = await client.get('/api/v1/opportunities/create-options')
        assert response.status_code == 200, response.text
        assert {x['id'] for x in response.json()['teams']} == set(selected)
        assert response.json()['default_team_id'] is None
        result = await client.post(f"/api/v1/customers/{customer['id']}/opportunities",json={**opportunity_payload(),'owner_team_id':selected[0]})
        assert result.status_code == 201, result.text
        await grant(connection,admin,user['id'],[],version=1)
        assert (await client.get('/api/v1/opportunities/create-options')).status_code == 403
