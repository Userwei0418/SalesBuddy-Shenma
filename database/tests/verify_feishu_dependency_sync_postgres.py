"""V199 dependency capture verification in disposable local PostgreSQL only."""
from __future__ import annotations

import asyncio
import json
import os
from uuid import uuid4

import asyncpg
from verify_feishu_customer_insight_sync_postgres import (
    ROOT, SCHEMAS, insight_fixture, grant_worker_fixture, risk,
)
from verify_feishu_customer_member_sync_postgres import denied, events, rollback, watermark
from verify_fde_postgres import context
from verify_phone_first_login_postgres import effective_acl
from verify_runtime_grants_postgres import migrate_through
from migrate import apply, item

MIGRATION = ROOT / 'migrations/V199__feishu_projection_dependency_sync.sql'
HELPERS = ('ops.feishu_dependency_objects(text,jsonb)', 'ops.capture_feishu_dependencies()')
DIRECT = {
    'crm.customer': 'customer', 'crm.opportunity': 'opportunity', 'activity.visit': 'visit',
    'crm.partner': 'partner', 'workflow.task': 'task', 'crm.opportunity_demo_scenes': 'demo_scene',
    'crm.customer_actual': 'actual', 'crm.sales_target': 'target', 'platform.user_ref': 'member',
    'crm.contact': 'contact', 'crm.opportunity_forecast': 'forecast',
}
RELATIONS = {'platform.team', 'platform.team_membership', 'platform.role_binding',
             'workflow.task_assignee', 'workflow.task_candidate', 'crm.opportunity_participant',
             'activity.visit_participant'}


async def rows(conn, after, expected, *, origin='business', direct=()):
    actual = await events(conn, after)
    keys = [(r['workspace_id'], r['object_kind'], r['object_id']) for r in actual]
    assert sorted(keys, key=str) == sorted(expected, key=str), (keys, expected)
    assert all(r['object_kind'] != 'refresh' and r['queue_origin'] == origin for r in actual)
    for r in actual:
        if (r['workspace_id'], r['object_kind'], r['object_id']) not in direct:
            assert not r['first_formal_create'] and r['historical'] and not r['notification_planned']
    return actual


def node(f, kind, key=None):
    return (f['ws'], kind, f[key or kind])


async def source(conn, f, kind, key=None):
    return json.loads(await conn.fetchval('SELECT ops.feishu_source($1,$2,$3)', f['sync'], kind, f[key or kind]))


async def participant_identity(conn,f,key='second'):
    await conn.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) VALUES($1,$2,'fde','self',$3)",f['ws'],f[key],f['team'])
    await conn.execute("INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) VALUES($1,$2,$3,'fde')",f['ws'],f[key],f['team'])


async def fixture(conn, label):
    f = await insight_fixture(conn, label)
    f['member'] = f['user']
    f['team'] = await conn.fetchval("INSERT INTO platform.team(workspace_id,code,name) VALUES($1,$2,'Synthetic team') RETURNING id", f['ws'], label)
    await conn.execute('UPDATE crm.customer SET owner_user_ref_id=$2,owner_team_id=$3 WHERE id=$1', f['customer'], f['user'], f['team'])
    await conn.execute('UPDATE crm.opportunity SET owner_team_id=$2 WHERE id=$1', f['opportunity'], f['team'])
    await conn.execute('UPDATE activity.visit SET archived_at=clock_timestamp(),recorder_team_id=$2 WHERE id=$1', f['visit'], f['team'])
    f['membership'] = await conn.fetchval("""INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role,is_primary)
        VALUES($1,$2,$3,'manager',true) RETURNING id""", f['ws'], f['user'], f['team'])
    await conn.execute("UPDATE platform.user_ref SET attributes=jsonb_build_object('organization_team_id',$2::text) WHERE id=$1", f['second'], str(f['team']))
    f['other_opportunity'] = await conn.fetchval("""INSERT INTO crm.opportunity(workspace_id,customer_id,name,
        created_by_user_ref_id,owner_user_ref_id) VALUES($1,$2,'Unrelated project',$3,$3) RETURNING id""", f['ws'], f['other'], f['second'])
    for key in ('task', 'candidate_task'):
        f[key] = await conn.fetchval("""INSERT INTO workflow.task(workspace_id,title,description,customer_id,opportunity_id,
            creator_user_ref_id,due_at,association_kind,target_position)
            VALUES($1,'Synthetic dependency task','Synthetic action',$2,$3,$4,clock_timestamp()+interval '2 days',
            'customer',CASE WHEN $5 THEN 'manager' END) RETURNING id""",
            f['ws'], f['customer'], f['opportunity'], f['user'], key == 'candidate_task')
    await conn.execute("""INSERT INTO workflow.task_assignee(task_id,workspace_id,assignee_user_ref_id,assignee_role,assignee_team_id)
        VALUES($1,$2,$3,'manager',$4)""", f['task'], f['ws'], f['user'], f['team'])
    f['candidate'] = await conn.fetchval("""INSERT INTO workflow.task_candidate(workspace_id,task_id,user_ref_id,role_code,team_id)
        VALUES($1,$2,$3,'manager',$4) RETURNING id""", f['ws'], f['candidate_task'], f['user'], f['team'])
    f['contact'] = await conn.fetchval("INSERT INTO crm.contact(workspace_id,customer_id,name) VALUES($1,$2,'Synthetic contact') RETURNING id", f['ws'], f['customer'])
    f['forecast'] = await conn.fetchval("""INSERT INTO crm.opportunity_forecast(workspace_id,opportunity_id,year,quarter,recognized_amount)
        VALUES($1,$2,2026,3,10) RETURNING id""", f['ws'], f['opportunity'])
    f['actual'] = await conn.fetchval("""INSERT INTO crm.customer_actual(workspace_id,customer_id,opportunity_id,kind,amount,
        occurred_on,source_ref,request_id,confirmed_by_user_ref_id)
        VALUES($1,$2,$3,'recognized',10,current_date,'SYNTHETIC',$4,$5) RETURNING id""",
        f['ws'], f['customer'], f['opportunity'], uuid4(), f['user'])
    f['demo_scene'] = await conn.fetchval("""INSERT INTO crm.opportunity_demo_scenes(workspace_id,opportunity_id,name,created_by)
        VALUES($1,$2,'Synthetic scene',$3) RETURNING id""", f['ws'], f['opportunity'], f['user'])
    f['target'] = await conn.fetchval("""INSERT INTO crm.sales_target(workspace_id,target_year,scope_type,team_id,kind,amount,
        updated_by_user_ref_id,period_start,period_end) VALUES($1,2026,'team',$2,'recognized',100,$3,
        '2026-01-01','2026-12-31') RETURNING id""", f['ws'], f['team'], f['user'])
    f['partner'] = await conn.fetchval("INSERT INTO crm.partner(workspace_id,name) VALUES($1,'Synthetic partner') RETURNING id", f['ws'])
    await conn.execute("UPDATE crm.opportunity SET partner_id=$2,sales_channel='partner' WHERE id=$1", f['opportunity'], f['partner'])
    await conn.execute('UPDATE activity.visit SET partner_id=$2 WHERE id=$1', f['visit'], f['partner'])
    batch = await conn.fetchval("""INSERT INTO ops.crm_import_batch(workspace_id,source_system,source_base_id,manifest_sha256,source_snapshot_at)
        VALUES($1,'synthetic','synthetic',$2,clock_timestamp()) RETURNING id""", f['ws'], 'a' * 64)
    record = await conn.fetchval("""INSERT INTO ops.crm_import_record(workspace_id,batch_id,source_table_id,source_record_id,object_kind,source_sha256,raw_fields)
        VALUES($1,$2,'synthetic','synthetic','period_actual_snapshot',$3,'{}') RETURNING id""", f['ws'], batch, 'b' * 64)
    f['period_actual_snapshot'] = await conn.fetchval("""INSERT INTO crm.opportunity_period_actual_snapshot(workspace_id,opportunity_id,year,quarter,kind,
        source_field,raw_amount,source_unit,source_record_id,import_batch_id)
        VALUES($1,$2,2026,3,'recognized','synthetic',10,'cny',$3,$4) RETURNING id""", f['ws'], f['opportunity'], record, batch)
    return f


async def invariant_snapshot(conn):
    business = {}
    for table in list(DIRECT) + sorted(RELATIONS) + ['ops.feishu_event','config.feishu_connection']:
        business[table] = await conn.fetchval(f'SELECT jsonb_agg(to_jsonb(r) ORDER BY to_jsonb(r)::text)::text FROM {table} r')
    functions = [tuple(r) for r in await conn.fetch("""SELECT p.oid,p.proowner,p.proacl::text,p.prosecdef,p.proconfig,
        CASE WHEN p.proname='capture_feishu_event' THEN '' ELSE p.prosrc END
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=ANY($1::text[])
        AND p.proname NOT IN ('feishu_dependency_objects','capture_feishu_dependencies') ORDER BY p.oid""", SCHEMAS)]
    triggers = [tuple(r) for r in await conn.fetch("""SELECT t.oid,pg_get_triggerdef(t.oid) FROM pg_trigger t
        WHERE NOT t.tgisinternal AND t.tgname NOT IN ('feishu_dependents_update','feishu_dependents_delete')
        AND NOT (t.tgname='feishu_capture' AND t.tgrelid::regclass::text=ANY($1::text[])) ORDER BY t.oid""", sorted(RELATIONS))]
    return (business, functions, triggers,
            [tuple(r) for r in await conn.fetch('SELECT * FROM pg_policies ORDER BY schemaname,tablename,policyname')],
            [tuple(r) for r in await conn.fetch('SELECT oid,relrowsecurity,relforcerowsecurity FROM pg_class ORDER BY oid')],
            [tuple(r) for r in await conn.fetch('SELECT * FROM pg_default_acl ORDER BY oid')])


async def verify():
    host = os.environ.get('PGHOST', '')
    if not host.startswith('/'):
        raise ValueError('Explicit local Unix-socket PGHOST required')
    config = dict(host=host, port=int(os.environ.get('PGPORT','5432')), user=os.environ.get('PGUSER','postgres'),
                  password='', passfile='/dev/null', ssl=False)
    admin = await asyncpg.connect(database='postgres', **config)
    db, runtime = 'sb_dependencies_' + uuid4().hex[:12], 'sb_dependencies_app_' + uuid4().hex[:12]
    conn = None
    checks = []
    try:
        await admin.execute(f'CREATE DATABASE "{db}" TEMPLATE template0')
        conn = await asyncpg.connect(database=db, **config)
        await migrate_through(conn,195)
        for version in (197,198):
            path = next((ROOT/'migrations').glob(f'V{version}__*.sql'))
            await apply(conn, item(f'V{version}',path), ROOT)
        own, foreign = await fixture(conn,'A'), await fixture(conn,'B')
        await context(conn, own['ws'], own['user'], 'manager')
        async with rollback(conn):
            before = await watermark(conn)
            await conn.execute("UPDATE platform.team SET name='Old refresh reproduction' WHERE id=$1", own['team'])
            await conn.execute("UPDATE platform.user_ref SET display_name='Old refresh reproduction' WHERE id=$1", own['user'])
            assert sum(e['object_kind']=='refresh' for e in await events(conn,before))==2
        checks.append('v198_still_reproduces_explicit_team_and_implicit_member_refresh')
        await conn.execute(f'CREATE ROLE "{runtime}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        await grant_worker_fixture(conn,runtime)
        await conn.execute(f'ALTER DEFAULT PRIVILEGES IN SCHEMA ops GRANT EXECUTE ON FUNCTIONS TO "{runtime}",salegent_feishu_worker')
        before = await invariant_snapshot(conn)
        acls = {r: await effective_acl(conn,r,SCHEMAS) for r in (runtime,'salegent_feishu_worker')}
        migration = item('V199',MIGRATION)
        assert await apply(conn,migration,ROOT)=='applied'
        assert await invariant_snapshot(conn)==before
        for role, acl in acls.items():
            assert await effective_acl(conn,role,SCHEMAS)==acl
            for signature in HELPERS:
                assert not await conn.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')",role,signature)
        assert not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM ops.schema_migration WHERE version='V196')")
        direct = {r['table_name']: bytes(r['tgargs']).decode().strip('\x00') for r in await conn.fetch("""
            SELECT tgrelid::regclass::text table_name,tgargs FROM pg_trigger WHERE tgfoid='ops.capture_feishu_event()'::regprocedure""")}
        assert direct==DIRECT, direct
        assert not await conn.fetchval("""SELECT EXISTS(SELECT 1 FROM pg_trigger
            WHERE tgfoid='ops.capture_feishu_event()'::regprocedure AND encode(tgargs,'escape') LIKE 'refresh%')""")
        checks.append('isolated_v195_v197_v198_upgrade_exact_direct_trigger_inventory_no_refresh_binding_no_grants_or_business_changes')

        children = [node(own,k) for k in ('visit','task','forecast','actual','demo_scene','period_actual_snapshot')]
        children.append(node(own,'task','candidate_task'))
        async with rollback(conn):
            before=await watermark(conn)
            await conn.execute("UPDATE crm.customer SET name='Changed customer' WHERE id=$1",own['customer'])
            await rows(conn,before,[node(own,'customer'),node(own,'opportunity'),node(own,'contact')]+children,direct=[node(own,'customer')])
            assert (await source(conn,own,'visit'))['customer_name']=='Changed customer'
        checks.append('customer_name_propagates_only_full_related_projection_closure_including_period_snapshot')
        async with rollback(conn):
            before=await watermark(conn)
            await conn.execute("UPDATE crm.opportunity SET name='Changed opportunity' WHERE id=$1",own['opportunity'])
            await rows(conn,before,[node(own,'opportunity'),node(own,'customer')]+children,direct=[node(own,'opportunity')])
            assert (await source(conn,own,'forecast'))['opportunity_name']=='Changed opportunity'
        checks.append('opportunity_name_propagates_only_dependents_and_parent_customer')
        async with rollback(conn):
            before=await watermark(conn)
            await conn.execute("UPDATE platform.user_ref SET display_name='Changed member' WHERE id=$1",own['user'])
            await rows(conn,before,[node(own,k) for k in ('member','customer','opportunity','visit','task')]+[node(own,'task','candidate_task')],direct=[node(own,'member')])
            assert (await source(conn,own,'customer'))['owner_name']=='Changed member'
        checks.append('member_name_updates_owners_participants_and_current_task_names_not_unrelated_company_rows')
        async with rollback(conn):
            before=await watermark(conn)
            await conn.execute("UPDATE platform.team SET name='Changed team' WHERE id=$1",own['team'])
            await rows(conn,before,[node(own,k) for k in ('member','customer','opportunity','visit','task','target')]+
                       [node(own,'member','second'),node(own,'task','candidate_task')])
            assert (await source(conn,own,'target'))['team_name']=='Changed team'
        checks.append('team_name_updates_organization_members_departments_targets_and_current_recipient_routing')
        async with rollback(conn):
            before=await watermark(conn)
            await conn.execute("UPDATE crm.partner SET name='Changed partner' WHERE id=$1",own['partner'])
            await rows(conn,before,[node(own,k) for k in ('partner','opportunity','visit')],direct=[node(own,'partner')])
            assert (await source(conn,own,'opportunity'))['partner_name']=='Changed partner'
        checks.append('partner_rename_propagates_project_and_visit_names')

        for table, predicate, args in (
            ('platform.team_membership','id=$1',(own['membership'],)),
            ('platform.role_binding','user_ref_id=$1 AND workspace_id=$2',(own['user'],own['ws'])),
        ):
            async with rollback(conn):
                before=await watermark(conn)
                await conn.execute(f"UPDATE {table} SET valid_to=clock_timestamp()-interval '1 second' WHERE {predicate}",*args)
                await rows(conn,before,[node(own,'member'),node(own,'task'),node(own,'task','candidate_task')])
                assert (await source(conn,own,'task'))['notification_recipient_ids']==([] if table.endswith('role_binding') else [str(own['user'])])
        checks.append('membership_and_role_changes_only_refresh_member_and_related_owner_candidate_tasks')
        async with rollback(conn):
            before=await watermark(conn)
            await conn.execute("UPDATE workflow.task_candidate SET decision='declined',decided_at=clock_timestamp() WHERE id=$1",own['candidate'])
            await rows(conn,before,[node(own,'task','candidate_task')])
            assert (await source(conn,own,'task','candidate_task'))['notification_recipient_ids']==[]
        checks.append('candidate_decision_updates_task_without_first_create_or_group_notice')
        async with rollback(conn):
            before=await watermark(conn)
            await conn.execute("UPDATE workflow.task_assignee SET assignee_user_ref_id=$2 WHERE task_id=$1",own['task'],own['second'])
            await rows(conn,before,[node(own,'task')])
            assert (await source(conn,own,'task'))['owner_id']==str(own['second'])
        checks.append('assignee_changes_only_refresh_task_current_owner')

        async with rollback(conn):
            await participant_identity(conn,own)
            before=await watermark(conn)
            await conn.execute("""INSERT INTO crm.opportunity_participant(workspace_id,opportunity_id,user_ref_id,participant_role,source_code,assigned_by_user_ref_id)
                VALUES($1,$2,$3,'fde','manual',$4)""",own['ws'],own['opportunity'],own['second'],own['user'])
            await rows(conn,before,[node(own,'opportunity'),node(own,'customer')])
            before=await watermark(conn)
            await denied(conn,'UPDATE crm.opportunity_participant SET opportunity_id=$2 WHERE opportunity_id=$1 AND user_ref_id=$3',own['opportunity'],own['other_opportunity'],own['second'],code='23514')
            await rows(conn,before,[])
            await conn.execute("UPDATE crm.opportunity_participant SET valid_to=clock_timestamp() WHERE opportunity_id=$1 AND user_ref_id=$2",own['opportunity'],own['second'])
            await rows(conn,before,[node(own,'opportunity'),node(own,'customer')])
            before=await watermark(conn)
            await conn.execute('DELETE FROM crm.opportunity_participant WHERE opportunity_id=$1 AND user_ref_id=$2',own['opportunity'],own['second'])
            await rows(conn,before,[node(own,'opportunity'),node(own,'customer')])
        checks.append('opportunity_participant_insert_end_delete_refreshes_parents_and_identity_move_guard_remains')
        async with rollback(conn):
            before=await watermark(conn)
            await conn.execute("""INSERT INTO activity.visit_participant(workspace_id,visit_id,user_ref_id,participant_role,role_code_at_event)
                VALUES($1,$2,$3,'collaborator','manager')""",own['ws'],own['visit'],own['second'])
            await rows(conn,before,[node(own,'visit')])
            assert (await source(conn,own,'visit'))['collaborators']
        checks.append('visit_participant_only_refreshes_parent_visit')
        async with rollback(conn):
            await risk(conn,{**own,'customer':own['other']},linked=True)
            before=await watermark(conn)
            await conn.execute('UPDATE crm.opportunity SET deleted_at=clock_timestamp() WHERE id=$1',own['opportunity'])
            await rows(conn,before,[node(own,'opportunity'),node(own,'customer'),node(own,'customer','other')]+children,direct=[node(own,'opportunity')])
            assert (await source(conn,own,'customer','other'))['risk_title'] is None
        checks.append('opportunity_soft_delete_refreshes_explicit_risk_parent_outside_original_customer')
        async with rollback(conn):
            before=await watermark(conn)
            await conn.execute("SELECT set_config('app.feishu_historical_import','on',true)")
            await conn.execute("UPDATE crm.customer SET data_kind='demo' WHERE id=$1",own['customer'])
            await rows(conn,before,[node(own,'customer'),node(own,'opportunity'),node(own,'contact')]+children,origin='reconcile',direct=[node(own,'customer')])
            assert (await source(conn,own,'opportunity'))['excluded']
        checks.append('production_exit_propagates_tombstones_with_historical_origin_and_no_new_notices')
        async with rollback(conn):
            before=await watermark(conn)
            await conn.execute("UPDATE platform.user_ref SET attributes=attributes||'{\"unrelated\":true}' WHERE id=$1",own['user'])
            await rows(conn,before,[node(own,'member')],direct=[node(own,'member')])
            before=await watermark(conn)
            await conn.execute('UPDATE workflow.task_candidate SET decision=decision WHERE id=$1',own['candidate'])
            await rows(conn,before,[])
        checks.append('unrelated_member_metadata_does_not_expand_and_relation_noop_skips_all_events')

        async with rollback(conn):
            form = await conn.fetchval("SELECT form_version_id FROM activity.visit WHERE id=$1",own['visit'])
            multi = await conn.fetchval("""INSERT INTO activity.visit(workspace_id,customer_id,opportunity_id,
                recorder_user_ref_id,created_by_user_ref_id,form_version_id,status,archived_at,interaction_at)
                VALUES($1,$2,$3,$4,$4,$5,'archived',clock_timestamp(),clock_timestamp()) RETURNING id""",
                own['ws'],own['other'],own['other_opportunity'],own['user'],form)
            await conn.execute("INSERT INTO activity.visit_opportunity(workspace_id,visit_id,opportunity_id) VALUES($1,$2,$3)",own['ws'],multi,own['opportunity'])
            before=await watermark(conn)
            await conn.execute("UPDATE crm.customer SET name='Multi linked customer' WHERE id=$1",own['customer'])
            await rows(conn,before,[node(own,'customer'),node(own,'opportunity'),node(own,'contact')]+children+[(own['ws'],'visit',multi)],direct=[node(own,'customer')])
            assert 'Multi linked customer' in (await source(conn,{**own,'visit':multi},'visit'))['customer_names']
        checks.append('multi_link_visit_without_matching_legacy_parent_is_included_once')

        async with rollback(conn):
            before=await watermark(conn)
            await conn.execute('UPDATE crm.opportunity SET customer_id=$2 WHERE id=$1',own['opportunity'],own['other'])
            await rows(conn,before,[node(own,'opportunity'),node(own,'customer'),node(own,'customer','other')]+children,direct=[node(own,'opportunity')])
            assert (await source(conn,own,'forecast'))['customer_id']==str(own['other'])
            for deleted in (True,False):
                before=await watermark(conn)
                await conn.execute('UPDATE crm.opportunity SET deleted_at=CASE WHEN $2 THEN clock_timestamp() END WHERE id=$1',own['opportunity'],deleted)
                await rows(conn,before,[node(own,'opportunity'),node(own,'customer','other')]+children,direct=[node(own,'opportunity')])
                assert bool((await source(conn,own,'opportunity')).get('deleted_at'))==deleted
        checks.append('opportunity_parent_move_and_soft_delete_restore_preserve_old_new_parents_and_children')

        async with rollback(conn):
            before=await watermark(conn)
            await conn.execute('UPDATE workflow.task_candidate SET workspace_id=$2,task_id=$3,user_ref_id=$4,team_id=$5 WHERE id=$1',own['candidate'],foreign['ws'],foreign['task'],foreign['second'],foreign['team'])
            await rows(conn,before,[node(own,'task','candidate_task'),node(foreign,'task')])
            assert await conn.fetchval("SELECT current_setting('app.workspace_id')")==str(own['ws'])
        checks.append('controlled_relation_tenant_move_keeps_old_new_explicit_tenants_and_ambient_context')

        async with rollback(conn):
            pid = await conn.fetchval("INSERT INTO crm.partner(workspace_id,name) VALUES($1,'Movable partner') RETURNING id",own['ws'])
            before=await watermark(conn)
            await conn.execute('UPDATE crm.partner SET workspace_id=$2 WHERE id=$1',pid,foreign['ws'])
            await rows(conn,before,[(own['ws'],'partner',pid),(foreign['ws'],'partner',pid)],direct=[(foreign['ws'],'partner',pid)])
            assert (await source(conn,{**own,'partner':pid},'partner'))['deleted']
        checks.append('controlled_direct_object_tenant_move_keeps_old_projection_tombstone')

        async with rollback(conn):
            await participant_identity(conn,own)
            await conn.execute("INSERT INTO crm.opportunity_participant(workspace_id,opportunity_id,user_ref_id,participant_role,source_code,assigned_by_user_ref_id) VALUES($1,$2,$3,'fde','manual',$4)",own['ws'],own['other_opportunity'],own['second'],own['user'])
            before=await watermark(conn)
            await conn.execute('DELETE FROM crm.opportunity WHERE id=$1',own['other_opportunity'])
            await rows(conn,before,[node(own,'opportunity','other_opportunity'),node(own,'customer','other')],direct=[node(own,'opportunity','other_opportunity')])
            assert await conn.fetchval('SELECT count(*) FROM crm.opportunity_participant WHERE opportunity_id=$1',own['other_opportunity'])==0
            assert (await source(conn,own,'opportunity','other_opportunity'))['deleted']
            before=await watermark(conn)
            await denied(conn,'DELETE FROM crm.customer WHERE id=$1',own['customer'],code='23503')
            await rows(conn,before,[])
        checks.append('before_hard_delete_captures_dependencies_before_cascade_failed_delete_rolls_back_all_events')

        async with rollback(conn):
            await conn.execute('INSERT INTO crm.opportunity_related_partner(workspace_id,opportunity_id,partner_id) VALUES($1,$2,$3)',own['ws'],own['other_opportunity'],own['partner'])
            before=await watermark(conn)
            await conn.execute("UPDATE crm.partner SET name='Associated partner changed' WHERE id=$1",own['partner'])
            await rows(conn,before,[node(own,'partner'),node(own,'opportunity'),node(own,'opportunity','other_opportunity'),node(own,'visit')],direct=[node(own,'partner')])
        checks.append('partner_rename_includes_indirect_associated_partner_projects')

        async with rollback(conn):
            await context(conn,own['ws'],own['user'],'manager')
            before=await watermark(conn)
            await conn.execute('SET LOCAL row_security=on')
            await conn.execute(f'SET LOCAL ROLE "{runtime}"')
            assert not await conn.fetchval('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')
            assert await conn.fetchval('SELECT count(*) FROM workflow.task_candidate WHERE workspace_id=$1',foreign['ws'])==0
            assert await conn.execute("UPDATE workflow.task_candidate SET decision='declined',decided_at=clock_timestamp() WHERE id=$1",foreign['candidate'])=='UPDATE 0'
            await conn.execute("UPDATE workflow.task_candidate SET decision='declined',decided_at=clock_timestamp() WHERE id=$1",own['candidate'])
            await denied(conn,"SELECT * FROM ops.feishu_dependency_objects('crm.customer','[]')")
            await denied(conn,'SELECT ops.capture_feishu_dependencies()')
            await denied(conn,"INSERT INTO ops.feishu_event(connection_id,workspace_id,object_kind,object_id) VALUES($1,$2,'refresh',$2)",own['sync'],own['ws'])
            await conn.execute('RESET ROLE')
            await rows(conn,before,[node(own,'task','candidate_task')])
        checks.append('limited_runtime_can_mutate_own_candidate_with_private_trigger_and_cannot_access_other_tenant_or_queue')

        # The same parent may already have pending/running events. A fresh edit
        # still gets a successor; neither producer nor migration clears history.
        async with rollback(conn):
            before=await watermark(conn)
            await conn.execute("UPDATE crm.opportunity SET name='First update' WHERE id=$1",own['opportunity'])
            # A worker can already hold the first event and read its old
            # projection. The second edit must not be suppressed by its lease.
            old_projection = await source(conn,own,'opportunity')
            await conn.execute("""UPDATE ops.feishu_event SET status='running',lease_token=$3,
                locked_until=clock_timestamp()+interval '1 minute' WHERE sequence_no>$1
                AND object_kind='opportunity' AND object_id=$2""",before,own['opportunity'],uuid4())
            await conn.execute("UPDATE crm.opportunity SET name='Second update' WHERE id=$1",own['opportunity'])
            await rows(conn,before,2*([node(own,'opportunity'),node(own,'customer')]+children),direct=[node(own,'opportunity')])
            assert old_projection['name']=='First update' and (await source(conn,own,'opportunity'))['name']=='Second update'
            assert await conn.fetchval("SELECT count(*) FROM ops.feishu_event WHERE sequence_no>$1 AND object_id=$2 AND status='pending'",before,own['opportunity'])==1
        checks.append('successive_updates_keep_distinct_successors_without_pending_queue_dedup')
        assert await apply(conn,migration,ROOT)=='unchanged'
        checks.append('exact_checksum_replay_is_unchanged')
        definition=await conn.fetchval("SELECT pg_get_functiondef('ops.capture_feishu_event()'::regprocedure)")
        before=await watermark(conn)
        await migrate_through(conn,199)
        assert await conn.fetchval("SELECT EXISTS(SELECT 1 FROM ops.schema_migration WHERE version='V196')")
        assert await conn.fetchval("SELECT pg_get_functiondef('ops.capture_feishu_event()'::regprocedure)")==definition
        await rows(conn,before,[])
        checks.append('normal_migration_chain_fills_v196_without_replaying_v199_or_mutating_queue')
        print(json.dumps({'passed':len(checks),'checks':checks,'skipped':0}))
    finally:
        if conn:
            await conn.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
        await admin.execute(f'DROP ROLE IF EXISTS "{runtime}"')
        await admin.close()


if __name__ == '__main__':
    asyncio.run(verify())
