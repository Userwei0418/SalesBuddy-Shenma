"""One disposable-PostgreSQL group exercising the actual API ACL and live RLS."""
from uuid import uuid4

import asyncpg
import pytest

from sales_backend.repositories.opportunity_access import follow_up_reference
from sales_backend.repositories.visit_management import VisitManagementRepository
from sales_backend.repositories.visits import VisitRepository
from tests.integration.feishu_fixtures import assert_restricted, seed_execute, seed_fetchval
from tests.integration.test_operations_claims_sql import actor


@pytest.mark.asyncio
async def test_company_customer_recording_and_current_hierarchy_are_independent(connection):
    ws = await seed_fetchval(connection, "SELECT id FROM platform.workspace WHERE external_workspace_id='demo-sales-workspace'")
    teams = {key: uuid4() for key in ('root', 'middle', 'child', 'other')}
    for key, parent in (('root', None), ('middle', 'root'), ('child', 'middle'), ('other', None)):
        await seed_execute(connection, "INSERT INTO platform.team(id,workspace_id,code,name,parent_team_id) VALUES($1,$2,$3,$3,$4)",
                           teams[key], ws, key, teams.get(parent))
    users = {}
    for code, role, team in (('CLAIM', 'sales', 'child'), ('WRITER', 'sales', 'other'),
                              ('HEAD', 'supervisor', 'root'), ('FDEHEAD', 'fde_lead', 'middle'),
                              ('PEER', 'supervisor', 'other'), ('MANAGER', 'manager', None)):
        users[code] = uuid4()
        await seed_execute(connection, """INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name,account_code)
          VALUES($1,$2,$3,$3,$3)""", users[code], ws, code)
        await seed_execute(connection, """INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code)
          VALUES($1,$2,$3,$4)""", ws, users[code], role, 'team' if role == 'fde_lead' else 'self')
        if team:
            await seed_execute(connection, """INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role)
              VALUES($1,$2,$3,$4)""", ws, users[code], teams[team], role)
    # Remove old business grants only in this synthetic tenant, proving the new
    # read branch instead of accidentally testing a manager's old workspace grant.
    await seed_execute(connection, "DELETE FROM config.permission_role_grant WHERE workspace_id=$1", ws)
    claimed, unclaimed, deleted, foreign, opportunity = [uuid4() for _ in range(5)]
    other_ws, other_user = uuid4(), uuid4()
    await seed_execute(connection, "INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'异公司')",
                       other_ws, 'foreign-' + other_ws.hex)
    await seed_execute(connection, """INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name)
      VALUES($1,$2,'foreign','异公司人员')""", other_user, other_ws)
    for ident, tenant, creator, name in ((claimed, ws, users['WRITER'], 'A认领客户'),
                                       (unclaimed, ws, users['WRITER'], 'B未认领客户'),
                                       (deleted, ws, users['WRITER'], 'C已删客户'),
                                       (foreign, other_ws, other_user, 'D异公司客户')):
        await seed_execute(connection, """INSERT INTO crm.customer(id,workspace_id,name,normalized_name,created_by_user_ref_id)
          VALUES($1,$2,$3,$3,$4)""", ident, tenant, name, creator)
    await seed_execute(connection, "UPDATE crm.customer SET deleted_at=clock_timestamp() WHERE id=$1", deleted)
    await seed_execute(connection, """INSERT INTO crm.customer_ownership(customer_id,workspace_id,state,owner_user_ref_id)
      VALUES($1,$2,'claimed',$3) ON CONFLICT(customer_id) DO UPDATE SET state='claimed',owner_user_ref_id=$3""",
                       claimed, ws, users['CLAIM'])
    await actor(connection, 'WRITER')
    await seed_execute(connection, """INSERT INTO crm.opportunity(id,workspace_id,customer_id,name,owner_user_ref_id,owner_team_id)
      VALUES($1,$2,$3,'旧公司商机',$4,$5)""", opportunity, ws, claimed, users['CLAIM'], teams['child'])
    await actor(connection, 'WRITER')
    await assert_restricted(connection)
    for signature in ('security.company_customer_follow_up_actor()',
                      'security.company_customer_follow_up_reference(uuid)',
                      'security.company_customer_follow_up_options(text,integer,integer,text)'):
        assert await connection.fetchval("SELECT has_function_privilege(current_user,$1,'EXECUTE')", signature)
    assert not await connection.fetchval("""SELECT has_function_privilege(current_user,
      'security.customer_claim_visit_customer_access(uuid)','EXECUTE')""")
    directory = await connection.fetchval("SELECT security.company_customer_follow_up_options('',100,0,'name')")
    assert {item['id'] for item in directory['items']} == {str(claimed), str(unclaimed)}
    assert all(set(item) == {'id', 'name'} for item in directory['items'])
    assert directory['total'] == 2
    assert await follow_up_reference(connection, str(claimed)) == {'id': str(claimed), 'name': 'A认领客户'}
    for target in (foreign, deleted, uuid4()):
        assert await connection.fetchval("SELECT security.company_customer_follow_up_reference($1)", target) is None
    for permission in ('visit.create', 'visit.structure', 'visit.quality_review'):
        assert await connection.fetchval("SELECT security.authorization_visit_target($1,$2,NULL,$3,$4)",
                                         permission, claimed, users['WRITER'], teams['other'])
    for permission in ('visit.read', 'visit.supplement', 'visit.first_visit', 'visit.attendance_manage', 'visit.export'):
        assert not await connection.fetchval("SELECT COALESCE(security.authorization_visit_target($1,$2,NULL,$3,$4),false)",
                                             permission, claimed, users['WRITER'], teams['other'])
    assert not await connection.fetchval("SELECT COALESCE(security.authorization_visit_target('visit.create',$1,NULL,$2,$3),false)",
                                         claimed, users['CLAIM'], teams['child'])
    assert await connection.fetchval("SELECT security.company_opportunity_follow_up_reference($1)", opportunity)
    assert await follow_up_reference(connection, str(claimed), str(opportunity))
    assert await follow_up_reference(connection, str(unclaimed), str(opportunity)) is None

    # Actual INSERT through the API role and the same authorization_visit_target
    # RLS path used by archive; it may create only the caller's valid tenant visit.
    form = await connection.fetchval("SELECT id FROM config.form_version WHERE status='active' ORDER BY version_no DESC LIMIT 1")
    visit = uuid4()
    insert = """INSERT INTO activity.visit(id,workspace_id,customer_id,recorder_user_ref_id,recorder_team_id,
      created_by_user_ref_id,confirmed_by_user_ref_id,form_version_id,status,interaction_at,follow_up_record,next_action,archived_fields)
      VALUES($1,$2,$3,$4,$5,$4,$4,$6,'archived',clock_timestamp(),'正式跟进正文','下次确认',
       '{"customer_name":"A认领客户","can_open_customer":true}')"""
    await connection.execute("SELECT set_config('app.authorized_feature','visit.create',true)")
    await connection.execute(insert, visit, ws, claimed, users['WRITER'], teams['other'], form)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with connection.transaction():
            await connection.execute(insert, uuid4(), ws, claimed, users['CLAIM'], teams['child'], form)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with connection.transaction():
            await connection.execute(insert, uuid4(), other_ws, foreign, users['WRITER'], teams['other'], form)

    extra = {}
    for name, customer, status in (('unclaimed', unclaimed, 'confirmed'), ('draft', claimed, 'pending_confirm'),
                                    ('deleted', claimed, 'archived'), ('foreign', foreign, 'archived')):
        extra[name] = uuid4()
        await seed_execute(connection, """INSERT INTO activity.visit(id,workspace_id,customer_id,recorder_user_ref_id,
          created_by_user_ref_id,form_version_id,status,interaction_at,follow_up_record)
          VALUES($1,$2,$3,$4,$4,$5,$6,clock_timestamp(),'其他记录')""",
                           extra[name], other_ws if name == 'foreign' else ws, customer,
                           other_user if name == 'foreign' else users['WRITER'], form, status)
    await seed_execute(connection, "UPDATE activity.visit SET deleted_at=clock_timestamp() WHERE id=$1", extra['deleted'])

    async def readable(code):
        await actor(connection, code)
        await connection.execute("SELECT set_config('app.authorized_feature','visit.read',true)")
        return {str(row['id']) for row in await connection.fetch('SELECT id FROM activity.visit')}

    assert await readable('WRITER') == set()  # Recording alone never grants history.
    for code in ('CLAIM', 'HEAD', 'FDEHEAD'):
        assert await readable(code) == {str(visit)}
        assert await connection.fetchval('SELECT security.customer_claim_visit_reader()')
        assert await connection.fetchval('SELECT security.customer_claim_visit_reference($1,NULL)', claimed)
        permissions = await connection.fetchval('SELECT security.visit_saved_read_permissions($1)', visit)
        assert permissions == {'can_read': True, 'can_export': False}
        detail = await VisitRepository().detail(connection, str(visit))
        assert not detail['can_open_customer'] and not detail['can_open_opportunity'] and not detail['can_export']
        assert 'audio' not in detail
        page = await VisitManagementRepository().page(connection, customer_id=str(claimed))
        assert [item['id'] for item in page['items']] == [str(visit)]
        assert page['summary']['total'] == 1
        await connection.execute("SELECT set_config('app.authorized_feature','visit.export',true)")
        assert not await connection.fetchval('SELECT security.customer_claim_visit_match($1,NULL,NULL)', visit)
    assert await readable('PEER') == set()
    assert await readable('MANAGER') == {str(visit), str(extra['unclaimed'])}

    # Dynamic membership, hierarchy and ownership revoke only the new branch.
    await seed_execute(connection, """INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role)
      VALUES($1,$2,$3,'sales')""", ws, users['CLAIM'], teams['other'])
    assert await readable('HEAD') == set()
    assert await readable('CLAIM') == {str(visit)}
    await seed_execute(connection, "DELETE FROM platform.team_membership WHERE user_ref_id=$1 AND team_id=$2",
                       users['CLAIM'], teams['other'])
    await seed_execute(connection, "UPDATE platform.team_membership SET valid_to=clock_timestamp() WHERE user_ref_id=$1",
                       users['HEAD'])
    # A stale authenticated actor must not retain expired appointment authority.
    await connection.execute("SELECT set_config('app.user_ref_id',$1,true),set_config('app.role_code','supervisor',true)", str(users['HEAD']))
    assert not await connection.fetchval('SELECT security.customer_claim_visit_match($1,NULL,NULL)', visit)
    await seed_execute(connection, "UPDATE platform.team SET parent_team_id=NULL WHERE id=$1", teams['child'])
    assert await readable('FDEHEAD') == set()
    await seed_execute(connection, "UPDATE crm.customer_ownership SET owner_user_ref_id=$1 WHERE customer_id=$2", users['WRITER'], claimed)
    assert await readable('CLAIM') == set()
    assert await readable('WRITER') == {str(visit)}
    await seed_execute(connection, "UPDATE crm.customer_ownership SET state='unclaimed',owner_user_ref_id=NULL WHERE customer_id=$1", claimed)
    assert await readable('WRITER') == set()
    assert await readable('MANAGER') == {str(visit), str(extra['unclaimed'])}
    await seed_execute(connection, "UPDATE platform.user_ref SET status='inactive' WHERE id=$1", users['MANAGER'])
    assert not await connection.fetchval('SELECT security.customer_claim_visit_reader()')
    assert not await connection.fetchval('SELECT security.company_customer_follow_up_actor()')
    # Invalid/visitor role context cannot use the recording directory.
    await actor(connection, 'WRITER')
    await connection.execute("SELECT set_config('app.role_code','visitor',true)")
    assert not await connection.fetchval('SELECT security.company_customer_follow_up_actor()')
    assert (await connection.fetchval("SELECT security.company_customer_follow_up_options('',20,0,'name')"))['items'] == []
