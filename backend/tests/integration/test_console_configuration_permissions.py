"""Fine-grained console configuration through real HTTP and non-bypass PostgreSQL."""
from uuid import uuid4

import pytest

from sales_backend.db import set_request_context
from sales_backend.domain.model_api import ConnectionSnapshot
from tests.integration.feishu_fixtures import fixture_owner
from tests.integration.test_authorization_opportunities import grant
from tests.integration.test_operations_api import client_for, sign_in
from tests.integration.test_sales_opportunity_permissions import PASSWORD, account_fixture

pytestmark = pytest.mark.asyncio
ENTRY = dict(permission="access.console", effect="allow", scope="workspace")


def allows(*permissions):
    return [dict(permission=p, effect="allow", scope="workspace") for p in permissions]


async def test_rule_editor_uses_narrow_fde_options_without_organization_access(connection):
    user, _, admin = await account_fixture(connection, ["sales"])
    fde, _, _ = await account_fixture(connection, ["fde"])
    disabled, _, _ = await account_fixture(connection, ["fde"])
    async with fixture_owner(connection):
        await connection.execute("UPDATE platform.user_ref SET status='inactive' WHERE id=$1::uuid", disabled['id'])
        foreign = uuid4()
        await connection.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'外公司')", foreign, str(foreign))
        team = await connection.fetchval("INSERT INTO platform.team(workspace_id,code,name,attributes) VALUES($1,'fde','外公司FDE',jsonb_build_object('kind','fde')) RETURNING id", foreign)
        member = await connection.fetchval("INSERT INTO platform.user_ref(workspace_id,external_user_id,display_name) VALUES($1,$2,'外公司成员') RETURNING id", foreign, str(uuid4()))
        await connection.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) VALUES($1,$2,'fde','self',$3)", foreign, member, team)
        await connection.execute("INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) VALUES($1,$2,$3,'fde')", foreign, member, team)
    permissions = [ENTRY, *allows('rule.read', 'rule.draft', 'rule.preview'),
                   dict(permission='organization.read', effect='deny'), dict(permission='directory.read', effect='deny')]
    await grant(connection, admin, user['id'], permissions)
    async with await client_for(connection) as client:
        await sign_in(client, user['account_code'], PASSWORD)
        assert (await client.get('/api/v1/console/organization')).status_code == 403
        assert (await client.get('/api/v1/directory/fde-members')).status_code == 403
        response = await client.get('/api/v1/console/company-rules')
        assert response.status_code == 200, response.text
        item = next(i for i in response.json()['items'] if i['code'] == 'fde_capabilities')
        assert fde['id'] in {u['id'] for u in item['member_options']}
        assert disabled['id'] not in {u['id'] for u in item['member_options']}
        assert str(member) not in {u['id'] for u in item['member_options']}
        assert all(set(u) == {'id', 'name'} for u in item['member_options'])
        body = dict(definition={**item['current']['definition'], 'user_overrides': {fde['id']: True}}, reason='独立规则编辑', base_id=item['current']['id'])
        assert (await client.post('/api/v1/console/company-rules/fde_capabilities/preview', json=body)).status_code == 200
        draft = await client.post('/api/v1/console/company-rules/fde_capabilities/drafts', json=body)
        assert draft.status_code == 200, draft.text
        assert (await client.post('/api/v1/console/company-rules/versions/'+draft.json()['id']+'/publish', json={'revision': 1})).status_code == 403
        await grant(connection, admin, user['id'], [ENTRY, dict(permission='rule.read', effect='deny')], version=1)
        assert (await client.get('/api/v1/console/company-rules')).status_code == 403


async def test_model_restore_requires_rollback_at_test_publish_and_replay(connection):
    user, _, admin = await account_fixture(connection, ['sales'])
    permissions = [ENTRY, *allows('ai.config_read', 'ai.config_test', 'ai.config_publish')]
    await grant(connection, admin, user['id'], permissions)
    base = '/api/v1/admin/model-apis/text'
    config = ConnectionSnapshot(mode='disabled', protocol='chat_completions').model_dump()
    async with await client_for(connection) as client:
        await sign_in(client, user['account_code'], PASSWORD)
        assert (await client.get('/api/v1/admin/model-apis')).status_code == 200
        ordinary = dict(request_id=str(uuid4()), expected_version=0, configuration=config)
        tested = await client.post(base+'/tests', json=ordinary)
        assert tested.status_code == 200 and tested.json()['status'] == 'passed', tested.text
        published = await client.post(base+'/publish', json={'test_id': ordinary['request_id'], 'expected_version': 0})
        assert published.status_code == 200, published.text
        restored = dict(request_id=str(uuid4()), expected_version=1, configuration=config, restored_from_version=1)
        assert (await client.post(base+'/tests', json=restored)).status_code == 403
        await grant(connection, admin, user['id'], [*permissions, *allows('ai.config_rollback')], version=1)
        result = await client.post(base+'/tests', json=restored)
        assert result.status_code == 200 and result.json()['status'] == 'passed', result.text
        await grant(connection, admin, user['id'], [*permissions, dict(permission='ai.config_rollback', effect='deny')], version=2)
        assert (await client.post(base+'/tests', json=restored)).status_code == 403
        publish_body = dict(test_id=restored['request_id'], expected_version=1)
        assert (await client.post(base+'/publish', json=publish_body)).status_code == 403
        await grant(connection, admin, user['id'], [*permissions, *allows('ai.config_rollback')], version=3)
        published = await client.post(base+'/publish', json=publish_body)
        assert published.status_code == 200 and published.json()['published_version'] == 2, published.text
        await grant(connection, admin, user['id'], [*permissions, dict(permission='ai.config_rollback', effect='deny')], version=4)
        assert (await client.post(base+'/publish', json=publish_body)).status_code == 403
    await set_request_context(connection, admin)
    assert await connection.fetchval("SELECT count(*) FROM config.model_api_release WHERE purpose='text'") == 2
    assert await connection.fetchval("SELECT count(*) FROM agent.model_invocation WHERE actor_user_ref_id=$1::uuid", user['id']) == 0
