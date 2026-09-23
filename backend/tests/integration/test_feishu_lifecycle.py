"""All ten real SQL source shapes and transactional change capture; no Feishu writes."""
from uuid import uuid4

import pytest

from sales_backend.domain.feishu_sync.config import COMMON_FIELDS, SOURCE_FIELDS
from sales_backend.domain.feishu_sync.projection import project
from tests.integration.feishu_fixtures import seed_execute, seed_fetchval
from tests.integration.test_feishu_storage import setup

pytestmark = pytest.mark.asyncio


async def records(connection, ws, cid):
    user = await connection.fetchval('SELECT updated_by FROM config.feishu_connection WHERE id=$1', cid)
    customer = await seed_fetchval(connection,
        "INSERT INTO crm.customer(workspace_id,name,normalized_name,created_by_user_ref_id,data_kind) "
        "VALUES($1,'fixture customer','fixture customer',$2,'production') RETURNING id", ws, user)
    opportunity = await seed_fetchval(connection,
        "INSERT INTO crm.opportunity(workspace_id,customer_id,name,amount) "
        "VALUES($1,$2,'fixture opportunity',500000) RETURNING id", ws, customer)
    form = await seed_fetchval(connection,
        "INSERT INTO config.form_definition(workspace_id,form_code,name,object_type) "
        "VALUES($1,$2,'fixture','visit') RETURNING id", ws, str(uuid4()))
    version = await seed_fetchval(connection,
        "INSERT INTO config.form_version(form_definition_id,version_no,status) "
        "VALUES($1,1,'active') RETURNING id", form)
    visit = await seed_fetchval(connection,
        "INSERT INTO activity.visit(workspace_id,customer_id,opportunity_id,recorder_user_ref_id,"
        "form_version_id,status,archived_at,follow_up_record,is_first_visit) "
        "VALUES($1,$2,$3,$4,$5,'archived',clock_timestamp(),'fixture followup',false) RETURNING id",
        ws, customer, opportunity, user, version)
    partner = await seed_fetchval(connection,
        "INSERT INTO crm.partner(workspace_id,name) VALUES($1,'fixture partner') RETURNING id", ws)
    contact = await seed_fetchval(connection,
        "INSERT INTO crm.contact(workspace_id,customer_id,name) VALUES($1,$2,'fixture contact') RETURNING id",
        ws, customer)
    task = await seed_fetchval(connection,
        "INSERT INTO workflow.task(workspace_id,title,description,creator_user_ref_id,due_at,association_kind) "
        "VALUES($1,'fixture task','fixture description',$2,clock_timestamp()+interval '1 day','daily') RETURNING id",
        ws, user)
    scene = await seed_fetchval(connection,
        "INSERT INTO crm.opportunity_demo_scenes(workspace_id,opportunity_id,name,created_by) "
        "VALUES($1,$2,'fixture scene',$3) RETURNING id", ws, opportunity, user)
    actual = await seed_fetchval(connection,
        "INSERT INTO crm.customer_actual(workspace_id,customer_id,opportunity_id,kind,amount,occurred_on,"
        "source_ref,request_id,confirmed_by_user_ref_id) "
        "VALUES($1,$2,$3,'collection',1000,current_date,'fixture',$4,$5) RETURNING id",
        ws, customer, opportunity, uuid4(), user)
    target = await seed_fetchval(connection,
        "INSERT INTO crm.sales_target(workspace_id,target_year,scope_type,user_ref_id,kind,amount,"
        "updated_by_user_ref_id,period_type,period_start,period_end) "
        "VALUES($1,2026,'person',$2,'collection',1000,$2,'year','2026-01-01','2026-12-31') RETURNING id", ws, user)
    return dict(customer=customer, opportunity=opportunity, visit=visit, partner=partner, contact=contact,
                task=task, demo_scene=scene, actual=actual, target=target, member=user)


async def test_all_ten_sources_project_actual_schema_and_capture_lifecycle(connection):
    ws, cid = await setup(connection)
    ids = await records(connection, ws, cid)
    for kind, oid in ids.items():
        raw = await connection.fetchval('SELECT ops.feishu_source($1,$2,$3)', cid, kind, oid)
        assert raw['id'] == str(oid), kind
        value = project(kind, raw, SOURCE_FIELDS[kind] | COMMON_FIELDS)
        assert value['system_id'] == str(oid) and value['record_status'] == '有效', kind
        expected = {'visit': ('content', 'fixture followup'), 'member': ('name', 'sync fixture'),
                    'task': ('title', 'fixture task'), 'actual': ('kind', 'collection'),
                    'target': ('kind', 'collection'), 'demo_scene': ('name', 'fixture scene')}
        field, text = expected.get(kind, ('name', f'fixture {kind}'))
        assert value[field] == text, kind
        if kind != 'member':  # fixture member existed before connection was configured
            event = await connection.fetchrow(
                "SELECT * FROM ops.feishu_event WHERE connection_id=$1 AND object_kind=$2 AND object_id=$3 "
                "ORDER BY sequence_no DESC LIMIT 1", cid, kind, oid)
            assert event and event['first_formal_create'] and not event['historical'], kind

    # Update and tombstone exercise actual DB capture and source projection, not a constructed dict.
    for kind, table in [('partner', 'crm.partner'), ('demo_scene', 'crm.opportunity_demo_scenes'),
                        ('contact', 'crm.contact'), ('customer', 'crm.customer')]:
        await connection.execute(f"UPDATE {table} SET name='renamed' WHERE id=$1", ids[kind])  # noqa: S608
        raw = await connection.fetchval('SELECT ops.feishu_source($1,$2,$3)', cid, kind, ids[kind])
        assert project(kind, raw, {'name'})['name'] == 'renamed'
        event = await connection.fetchrow(
            "SELECT first_formal_create FROM ops.feishu_event WHERE connection_id=$1 AND object_kind=$2 "
            "AND object_id=$3 ORDER BY sequence_no DESC LIMIT 1", cid, kind, ids[kind])
        assert not event['first_formal_create']
    await connection.execute("UPDATE crm.customer SET data_kind='test' WHERE id=$1", ids['customer'])
    for kind in ('customer', 'opportunity', 'visit', 'contact', 'demo_scene', 'actual'):
        raw = await connection.fetchval('SELECT ops.feishu_source($1,$2,$3)', cid, kind, ids[kind])
        assert raw == {'id': str(ids[kind]), 'excluded': True}, kind


async def test_department_display_names_follow_system_updates_and_retirement(connection):
    ws, cid = await setup(connection)
    user = await connection.fetchval('SELECT updated_by FROM config.feishu_connection WHERE id=$1', cid)
    team = await seed_fetchval(connection,
        "INSERT INTO platform.team(workspace_id,external_team_id,code,name) "
        "VALUES($1,$2,'south','南区销售') RETURNING id", ws, str(uuid4()))
    await seed_execute(connection,
        "INSERT INTO platform.team_membership(workspace_id,team_id,user_ref_id,is_primary,membership_role) "
        "VALUES($1,$2,$3,true,'sales')",
        ws, team, user)
    raw = await connection.fetchval("SELECT ops.feishu_source($1,'member',$2)", cid, user)
    assert raw['department_names'] == ['南区销售'] and raw['primary_department_name'] == '南区销售'
    assert raw['department_ids'] == [str(team)] and raw['primary_department_id'] == str(team)
    await seed_execute(connection,
        "INSERT INTO platform.team_membership(workspace_id,team_id,user_ref_id,is_primary,membership_role) "
        "VALUES($1,$2,$3,false,'supervisor')", ws, team, user)
    raw = await connection.fetchval("SELECT ops.feishu_source($1,'member',$2)", cid, user)
    assert raw['department_ids'] == [str(team)] and raw['department_names'] == ['南区销售']
    await connection.execute("UPDATE platform.team SET name='华南销售' WHERE id=$1", team)
    raw = await connection.fetchval("SELECT ops.feishu_source($1,'member',$2)", cid, user)
    assert raw['department_names'] == ['华南销售']
    await connection.execute("UPDATE platform.team SET status='inactive' WHERE id=$1", team)
    raw = await connection.fetchval("SELECT ops.feishu_source($1,'member',$2)", cid, user)
    assert raw['department_names'] == [] and raw['primary_department_name'] is None
    assert raw['department_ids'] == [] and raw['primary_department_id'] is None
    assert await connection.fetchval(
        "SELECT count(*) FROM ops.feishu_event WHERE connection_id=$1 AND object_kind='refresh'", cid) >= 3


async def test_soft_deleted_parent_immediately_queues_dependent_refresh(connection):
    ws, cid = await setup(connection)
    ids = await records(connection, ws, cid)
    await connection.execute("UPDATE ops.feishu_event SET status='succeeded' WHERE connection_id=$1", cid)
    await connection.execute("UPDATE crm.customer SET deleted_at=clock_timestamp() WHERE id=$1", ids['customer'])
    assert await connection.fetchval(
        "SELECT count(*) FROM ops.feishu_event WHERE connection_id=$1 AND object_kind='refresh' "
        "AND status='pending' AND historical", cid) == 1
    for kind in ('opportunity', 'visit', 'contact', 'demo_scene', 'actual'):
        raw = await connection.fetchval('SELECT ops.feishu_source($1,$2,$3)', cid, kind, ids[kind])
        assert raw == {'id': str(ids[kind]), 'excluded': True}


@pytest.mark.parametrize('change', ['soft_delete', 'hard_delete', 'reparent'])
async def test_opportunity_parent_changes_immediately_refresh_related_objects(connection, change):
    ws, cid = await setup(connection)
    user = await connection.fetchval('SELECT updated_by FROM config.feishu_connection WHERE id=$1', cid)
    customers = []
    for label in ('first', 'second'):
        customers.append(await seed_fetchval(connection,
            "INSERT INTO crm.customer(workspace_id,name,normalized_name,created_by_user_ref_id,data_kind) "
            "VALUES($1,$2,$2,$3,'production') RETURNING id", ws, label, user))
    oid = await seed_fetchval(connection,
        "INSERT INTO crm.opportunity(workspace_id,customer_id,name) VALUES($1,$2,'fixture') RETURNING id",
        ws, customers[0])
    await connection.execute("UPDATE ops.feishu_event SET status='succeeded' WHERE connection_id=$1", cid)
    if change == 'soft_delete':
        # Seed the tombstone as owner: the normal business RLS deliberately
        # rejects direct soft deletion. Source/trigger assertions remain restricted.
        await seed_execute(connection, 'UPDATE crm.opportunity SET deleted_at=clock_timestamp() WHERE id=$1', oid)
    elif change == 'hard_delete':
        await connection.execute('DELETE FROM crm.opportunity WHERE id=$1', oid)
    else:
        await connection.execute('UPDATE crm.opportunity SET customer_id=$1 WHERE id=$2', customers[1], oid)
    assert await connection.fetchval(
        "SELECT count(*) FROM ops.feishu_event WHERE connection_id=$1 AND object_kind='refresh' "
        "AND status='pending' AND historical", cid) == 1
    raw = await connection.fetchval("SELECT ops.feishu_source($1,'opportunity',$2)", cid, oid)
    if change == 'reparent':
        assert raw['customer_id'] == str(customers[1]) and raw['customer_name'] == 'second'
    elif change == 'hard_delete':
        assert raw == {'id': str(oid), 'deleted': True}
    else:
        assert raw['deleted_at'] is not None


async def test_team_target_name_comes_from_same_company_team(connection):
    ws, cid = await setup(connection)
    user = await connection.fetchval('SELECT updated_by FROM config.feishu_connection WHERE id=$1', cid)
    team = await seed_fetchval(connection,
        "INSERT INTO platform.team(workspace_id,external_team_id,code,name) "
        "VALUES($1,$2,'south','南区销售') RETURNING id", ws, str(uuid4()))
    target = await seed_fetchval(connection,
        "INSERT INTO crm.sales_target(workspace_id,target_year,scope_type,team_id,kind,amount,"
        "updated_by_user_ref_id,period_type,period_start,period_end) "
        "VALUES($1,2026,'team',$2,'collection',1000,$3,'year','2026-01-01','2026-12-31') RETURNING id",
        ws, team, user)
    raw = await connection.fetchval("SELECT ops.feishu_source($1,'target',$2)", cid, target)
    assert raw['team_name'] == '南区销售'
    await connection.execute("UPDATE platform.team SET name='华南销售' WHERE id=$1", team)
    raw = await connection.fetchval("SELECT ops.feishu_source($1,'target',$2)", cid, target)
    assert raw['team_name'] == '华南销售'
