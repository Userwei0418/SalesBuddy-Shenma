#!/usr/bin/env python3
"""Disposable PostgreSQL V090 -> V092 target and customer projection checks; never uses a production DSN.

Run with the backend Python runtime:
  backend/.venv/bin/python database/scripts/verify_target_batches.py \
    --socket /tmp/salegent-target-pg.XXXXXX --port 55491
The PostgreSQL cluster must already exist. This script creates and drops only its
own UUID database and role, uses no credentials, and grants a NOBYPASSRLS runtime.
"""
import argparse
import asyncio
from contextlib import asynccontextmanager
from datetime import date
import json
from decimal import Decimal
from pathlib import Path
import sys
from uuid import UUID, uuid4

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent))
import migrate as migrations


async def main(socket, port):
    if not str(Path(socket).resolve()).startswith('/tmp/salegent-target-pg.') and not str(Path(socket).resolve()).startswith('/private/tmp/salegent-target-pg.'):
        raise ValueError('Only an explicitly created /tmp/salegent-target-pg.* socket is permitted')
    config = dict(host=socket, port=port, user='postgres')
    admin = await asyncpg.connect(database='postgres', **config)
    suffix = uuid4().hex[:12]
    dbname, role = 'salegent_target_verify_' + suffix, 'salegent_target_role_' + suffix
    conn = None
    created_role = False
    checks = []
    try:
        await admin.execute(f'CREATE DATABASE "{dbname}" TEMPLATE template0')
        conn = await asyncpg.connect(database=dbname, **config)
        discover = migrations.discover
        migrations.discover = lambda root=migrations.ROOT: [m for m in discover(root) if int(m.key[1:]) <= 90]
        await migrations.migrate(conn)
        migrations.discover = discover
        workspace, foreign_workspace = uuid4(), uuid4()
        south, north, fde_team = uuid4(), uuid4(), uuid4()
        for key in (workspace, foreign_workspace):
            await conn.execute('INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,$2)', key, 'isolated-' + str(key))
        for key, name in ((south, '南区'), (north, '北区'), (fde_team, 'FDE')):
            await conn.execute('INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,$3,$3)', key, workspace, name)
        actors = {}
        for code, actor_role, team, scope in [
            ('A','sales',south,'self'),('B','sales',south,'self'),('C','sales',north,'self'),
            ('S','supervisor',south,'team'),('M','manager',south,'workspace'),
            ('F','fde',fde_team,'self'),('L','fde_lead',fde_team,'team'),
            ('O','operations',south,'workspace'),('ADMIN','administrator',south,'workspace')]:
            user = uuid4(); actors[code] = (user, actor_role, team)
            await conn.execute('INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name,account_code) VALUES($1,$2,$3,$3,$3)', user, workspace, code)
            await conn.execute('INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) VALUES($1,$2,$3,$4,$5)', workspace,user,actor_role,scope,team)
            await conn.execute('INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) VALUES($1,$2,$3,$4)',workspace,user,team,actor_role)
        qstart = await conn.fetchval("SELECT date_trunc('quarter',timezone('Asia/Shanghai',clock_timestamp()))::date")
        qend = await conn.fetchval("SELECT ($1::date+interval '3 months'-interval '1 day')::date", qstart)
        legacy_target = await conn.fetchval('''INSERT INTO crm.sales_target(workspace_id,target_year,scope_type,user_ref_id,kind,amount,
            updated_by_user_ref_id,period_type,period_start,period_end) VALUES($1,$2,'person',$3,'recognized',100,$4,'year',$5,$6) RETURNING id''',
            workspace,qstart.year,actors['C'][0],actors['O'][0],date(qstart.year,1,1),date(qstart.year,12,31))
        legacy_request = await conn.fetchval('''INSERT INTO crm.sales_target_change_request(workspace_id,target_id,applicant_user_ref_id,
            base_version,previous_amount,proposed_amount) VALUES($1,$2,$3,1,100,120) RETURNING id''',workspace,legacy_target,actors['C'][0])
        results = await migrations.migrate(conn)
        assert any(x['key']=='V091' and x['status']=='applied' for x in results), results[-1]
        assert await conn.fetchval('SELECT amount FROM crm.sales_target WHERE id=$1',legacy_target)==100
        assert await conn.fetchval('SELECT department_code FROM crm.sales_target WHERE id=$1',legacy_target)=='sales'
        checks.append('upgrade_preserves_annual_values_ids_and_legacy_pending_requests')
        assert all(x['status']=='unchanged' for x in await migrations.migrate(conn))
        checks.append('migration_replay_is_unchanged')
        await admin.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        created_role = True
        for schema in ('platform','crm','activity','workflow','insight','ops','config','common','security','agent'):
            await conn.execute(f'GRANT USAGE ON SCHEMA {schema} TO "{role}"')
            await conn.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA {schema} TO "{role}"')
            await conn.execute(f'GRANT USAGE ON ALL SEQUENCES IN SCHEMA {schema} TO "{role}"')
            await conn.execute(f'GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA {schema} TO "{role}"')
        await conn.execute(f'REVOKE SELECT ON crm.sales_target_batch_request FROM "{role}"')
        await conn.execute('SELECT security.reconcile_runtime_grants()')
        assert await conn.fetchval("SELECT has_table_privilege($1,'crm.sales_target_batch_request','SELECT')",role)
        await conn.execute('SET row_security=on')
        await conn.set_type_codec('jsonb',schema='pg_catalog',encoder=json.dumps,decoder=json.loads,format='text')

        @asynccontextmanager
        async def actor(code, connection=conn, workspace_id=workspace):
            async with connection.transaction():
                await connection.execute(f'SET LOCAL ROLE "{role}"')
                user, actor_role, team = actors[code]
                await connection.execute("SELECT set_config('app.workspace_id',$1,true),set_config('app.user_ref_id',$2,true),set_config('app.role_code',$3,true),set_config('app.team_ids',$4,true),set_config('app.request_id',$5,true)",str(workspace_id),str(user),actor_role,str(team),str(uuid4()))
                yield connection

        async def save(code, items, reason='与负责人商定季度目标', scope='person', user=None, team=None, department='sales', period='quarter', start=qstart, end=qend, connection=conn):
            async with actor(code, connection) as c:
                return await c.fetchval('SELECT security.save_target_batch($1,$2,$3,$4,$5,$6,$7,$8,$9)',scope,actors[code][0] if scope=='person' and user is None else user,team,period,start,end,items,reason,department)

        async def review(req, decision='approved', reason='', code='O', connection=conn):
            async with actor(code, connection) as c:
                return await c.fetchval('SELECT security.review_target_batch($1,$2,$3)',req,decision,reason)

        async def denied(awaitable, message, error=asyncpg.PostgresError):
            try:
                await awaitable
            except error:
                checks.append(message)
            else:
                raise AssertionError(message)

        async def scalar(code, sql, *args):
            async with actor(code) as c:
                return await c.fetchval(sql,*args)

        def val(kind, amount, version=None):
            return dict(kind=kind,amount=amount,version_no=version)

        first = await save('A',[val('collection','100.25'),val('recognized',200)])
        assert first['status']=='effective' and len(first['items'])==2 and first['request'] is None
        assert all(t['version_no']==1 and t['source']=='self' for t in first['items'])
        for t in first['items']:
            assert await conn.fetchval("SELECT count(*) FROM ops.audit_log WHERE object_type='sales_target' AND object_id=$1",UUID(t['id']))==1
        checks.append('first_multi_metric_entry_effective_atomically_with_decimal_string')
        await denied(save('A',[val('collection',80)]),'existing_target_requires_version')
        await denied(save('A',[val('collection',80,99)]),'stale_version_rejected')
        await denied(save('A',[val('collection',80,2147483648)]),'oversized_version_rejected_with_input_error',asyncpg.InvalidParameterValueError)
        await denied(save('B',[val('collection',100)],reason='  '),'reason_is_required')
        for bad in ['NaN','Infinity','1e3','0','1.001','10000000000000000','-1']:
            await denied(save('B',[val('collection',bad)]),'invalid_amount_rejected_' + bad)
        await denied(save('B',[val('collection',100),val('collection',200)]),'duplicate_metric_rejected')
        await denied(save('B',[val('visit_count','2.50')]),'fractional_count_rejected')
        await denied(save('B',[val('collection',100)],period='year',start=date(qstart.year,1,1),end=date(qstart.year,12,31)),'person_nonquarter_write_rejected')
        past_start=date(qstart.year-1,1,1);past_end=date(qstart.year-1,3,31)
        await denied(save('B',[val('collection',100)],start=past_start,end=past_end),'person_past_quarter_write_rejected')
        mixed_items=[val('collection',80,1),val('recognized',200,1),val('acv',500)]
        pending=await save('A',mixed_items,reason='已与负责人商定调整')
        rid=UUID(pending['request']['id'])
        assert pending['status']=='pending' and len(pending['items'])==2
        assert await conn.fetchval("SELECT count(*) FROM crm.sales_target WHERE user_ref_id=$1 AND kind='acv'",actors['A'][0])==0
        assert await conn.fetchval("SELECT amount FROM crm.sales_target WHERE user_ref_id=$1 AND kind='collection'",actors['A'][0])==Decimal('100.25')
        checks.append('mixed_existing_and_new_metrics_wait_together_without_half_effect')
        retry=await save('A',list(reversed(mixed_items)),reason='已与负责人商定调整')
        assert retry['request']['id']==str(rid)
        checks.append('identical_retry_returns_same_immutable_batch')
        await denied(save('A',[val('collection',90,1)]),'overlap_pending_blocks_new_request')
        await denied(save('A',[val('acv',600)]),'pending_new_metric_cannot_be_first_written')
        await denied(review(rid,code='A'),'applicant_cannot_approve')
        await denied(review(rid,'rejected',''),'rejection_requires_reason')
        approved=await review(rid)
        assert approved['status']=='approved' and len(approved['items'])==3
        assert {x['kind']:x['version_no'] for x in approved['items']}=={'collection':2,'recognized':1,'acv':1}
        checks.append('approval_atomically_applies_changed_and_new_metrics_only')
        await denied(review(rid),'double_approval_rejected')
        unchanged=await save('A',[val('collection',80,2),val('recognized',200,1)])
        assert unchanged['status']=='unchanged'
        checks.append('unchanged_values_do_not_create_revision_or_approval')
        only_new=await save('A',[val('recognized',200,1),val('visit_count',10)])
        assert only_new['status']=='effective'
        checks.append('unchanged_existing_plus_new_metric_is_initial_entry')
        rejected=await save('A',[val('collection',70,2),val('demo_count',2)],reason='申请调整')
        rejected_id=UUID(rejected['request']['id'])
        assert (await review(rejected_id,'rejected','依据待补充'))['status']=='rejected'
        assert not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM crm.sales_target WHERE user_ref_id=$1 AND kind='demo_count')",actors['A'][0])
        checks.append('rejection_keeps_all_effective_values_and_absent_items')
        pending=await save('A',[val('collection',75,2),val('demo_count',2)])
        cancel_id=UUID(pending['request']['id'])
        admin_save=await save('O',[val('collection',90,2)],user=actors['A'][0],reason='运营重新确认')
        assert admin_save['status']=='effective'
        assert await conn.fetchval('SELECT status FROM crm.sales_target_batch_request WHERE id=$1',cancel_id)=='cancelled'
        assert not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM crm.sales_target WHERE user_ref_id=$1 AND kind='demo_count')",actors['A'][0])
        await denied(review(cancel_id),'cancelled_whole_batch_cannot_overwrite_operations_update')
        checks.append('operations_overlap_cancels_entire_old_batch_not_just_one_item')
        # Changed role/subject and stale baseline are both checked at approval time.
        stale=await save('A',[val('collection',85,3),val('demo_count',2)])
        stale_id=UUID(stale['request']['id'])
        await conn.execute("UPDATE crm.sales_target SET version_no=version_no+1 WHERE user_ref_id=$1 AND kind='collection'",actors['A'][0])
        await denied(review(stale_id),'approval_rechecks_every_baseline')
        assert not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM crm.sales_target WHERE user_ref_id=$1 AND kind='demo_count')",actors['A'][0])
        await review(stale_id,'rejected','版本变化')
        checks.append('stale_approval_does_not_partially_insert_missing_metric')
        for code in ('S','M','F','L'):
            assert (await save(code,[val('collection',100)]))['status']=='effective'
        checks.append('all_five_business_roles_can_first_set_own_quarter_target')
        for code,user in [('A',actors['B'][0]),('S',actors['A'][0]),('M',actors['A'][0]),('L',actors['F'][0])]:
            await denied(save(code,[val('collection',20)],user=user),'cannot_write_other_person_' + code,asyncpg.InsufficientPrivilegeError)
        await denied(save('S',[val('collection',20)],scope='team',team=south),'supervisor_cannot_write_team_target',asyncpg.InsufficientPrivilegeError)
        await denied(save('M',[val('collection',20)],scope='department'),'manager_cannot_write_department_target',asyncpg.InsufficientPrivilegeError)
        await save('O',[val('collection',1000)],scope='department')
        await save('O',[val('collection',2000)],scope='department',department='fde')
        await save('O',[val('collection',300)],scope='team',team=south)
        await save('O',[val('collection',400)],scope='team',team=fde_team)
        assert await scalar('M',"SELECT amount FROM crm.sales_target WHERE scope_type='department'")==1000
        assert await scalar('F',"SELECT count(*) FROM crm.sales_target WHERE scope_type='department'")==0
        assert await scalar('O',"SELECT count(*) FROM crm.sales_target WHERE scope_type='department'")==2
        checks.append('sales_and_fde_department_targets_separate_and_rls_scoped')
        for code,subject,want in [('S','A',True),('S','C',False),('M','A',True),('M','F',False),('M','O',False),('L','F',True),('L','A',False),('A','B',False)]:
            assert await scalar(code,'SELECT security.target_scope_read(\'person\',$1,NULL)',actors[subject][0]) is want,(code,subject)
        for code,team,want in [('M',south,True),('M',fde_team,False),('S',north,False),('S',south,True),('L',fde_team,True),('L',south,False)]:
            assert await scalar(code,'SELECT security.target_scope_read(\'team\',NULL,$1)',team) is want
        checks.append('read_permission_matrix_stops_cross_family_and_cross_team_profile_access')
        # A sales manager cannot infer FDE amounts through plain table SELECT.
        assert await scalar('M','SELECT count(*) FROM crm.sales_target WHERE user_ref_id=$1',actors['F'][0])==0
        assert await scalar('A','SELECT count(*) FROM crm.sales_target WHERE user_ref_id=$1',actors['S'][0])==0
        assert await scalar('B','SELECT count(*) FROM crm.sales_target_batch_request')==0
        checks.append('plain_select_rls_preserves_target_and_application_privacy')
        async def direct_update():
            async with actor('A') as c:
                return await c.execute("UPDATE crm.sales_target SET amount=1 WHERE user_ref_id=$1",actors['A'][0])
        assert await direct_update()=='UPDATE 0'
        await denied(scalar('A','SELECT security.save_period_target(\'person\',$1,NULL,\'quarter\',$2,$3,\'collection\',1,4)',actors['A'][0],qstart,qend),'legacy_writer_cannot_bypass_reason_and_batch_lock')
        checks.append('runtime_cannot_directly_change_effective_targets')
        # Pending request rows cannot be edited in place or removed, even by an
        # owner-level connection; runtime writes have no RLS insert/update policy.
        immutable=await save('A',[val('collection',83,4)])
        immutable_id=UUID(immutable['request']['id'])
        await denied(conn.execute('UPDATE crm.sales_target_batch_request SET reason=\'篡改\' WHERE id=$1',immutable_id),'pending_reason_is_immutable')
        await denied(conn.execute('UPDATE crm.sales_target_batch_request SET items=\'[]\'::jsonb WHERE id=$1',immutable_id),'pending_items_are_immutable')
        await denied(conn.execute('DELETE FROM crm.sales_target_batch_request WHERE id=$1',immutable_id),'batch_history_cannot_be_deleted')
        assert await scalar('A','SELECT count(*) FROM crm.sales_target_batch_request WHERE id=$1',immutable_id)==1
        async with actor('A') as c:
            assert await c.execute('UPDATE crm.sales_target_batch_request SET status=\'approved\' WHERE id=$1',immutable_id)=='UPDATE 0'
        checks.append('runtime_cannot_directly_approve_request')
        await conn.execute("UPDATE platform.user_ref SET status='inactive' WHERE id=$1",actors['A'][0])
        await denied(review(immutable_id),'inactive_subject_cannot_be_approved')
        await conn.execute("UPDATE platform.user_ref SET status='active' WHERE id=$1",actors['A'][0])
        await review(immutable_id,'rejected','状态复核后重新提交')
        await denied(conn.execute('UPDATE crm.sales_target_batch_request SET decision_reason=\'重写\' WHERE id=$1',immutable_id),'completed_decision_is_immutable')
        # An operations-created target is already effective. It is not the
        # person's first setting merely because they have not used the UI yet.
        await save('O',[val('collection',900)],user=actors['C'][0])
        preconfigured=await save('C',[val('collection',800,1)])
        assert preconfigured['status']=='pending'
        assert await conn.fetchval("SELECT amount FROM crm.sales_target WHERE user_ref_id=$1 AND period_type='quarter' AND kind='collection'",actors['C'][0])==900
        checks.append('operations_preconfigured_target_requires_personal_change_approval')
        await review(UUID(preconfigured['request']['id']),'rejected','保留商定值')
        # A full form with a later invalid field cannot persist its earlier item.
        await denied(save('C',[val('acv',150),val('demo_count','2.5')]),'invalid_later_item_rolls_back_whole_form')
        assert not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM crm.sales_target WHERE user_ref_id=$1 AND kind='acv')",actors['C'][0])
        # Review guards the previously absent rows too, not just existing versions.
        absent=await save('C',[val('collection',850,1),val('acv',700)])
        absent_id=UUID(absent['request']['id'])
        await conn.execute('''INSERT INTO crm.sales_target(workspace_id,target_year,scope_type,user_ref_id,kind,amount,
            updated_by_user_ref_id,period_type,period_start,period_end) VALUES($1,$2,'person',$3,'acv',900,$4,'quarter',$5,$6)''',
            workspace,qstart.year,actors['C'][0],actors['O'][0],qstart,qend)
        await denied(review(absent_id),'approval_rejects_new_metric_created_since_submission')
        assert await conn.fetchval("SELECT amount FROM crm.sales_target WHERE user_ref_id=$1 AND period_type='quarter' AND kind='collection'",actors['C'][0])==900
        await review(absent_id,'rejected','指标已设置，请刷新')
        assert (await save('O',[val('collection',100)],scope='team',team=north,start=past_start,end=past_end))['status']=='effective'
        checks.append('operations_can_maintain_independent_historical_team_target')
        # Legacy request retained across V090 migration remains reviewable.
        assert (await scalar('O','SELECT security.review_target_change($1,\'approved\',\'历史审核\')',legacy_request))['status']=='approved'
        assert await conn.fetchval('SELECT amount FROM crm.sales_target WHERE id=$1',legacy_target)==120
        checks.append('legacy_pending_approval_still_supported')
        async with actor('A',workspace_id=foreign_workspace) as c:
            assert await c.fetchval('SELECT count(*) FROM crm.sales_target')==0
            assert not await c.fetchval('SELECT security.target_scope_write(\'person\',$1,NULL)',actors['A'][0])
        checks.append('workspace_identity_mismatch_has_no_target_access')
        # V092 projects only the confirmed owner of an already-readable
        # customer. The underlying ownership workflow table stays private.
        customer=await conn.fetchval('''INSERT INTO crm.customer(workspace_id,name,normalized_name,owner_user_ref_id,
            owner_team_id,created_by_user_ref_id) VALUES($1,'归属投影隔离验证','归属投影隔离验证',$2,$3,$2) RETURNING id''',
            workspace,actors['A'][0],south)
        await conn.execute('''INSERT INTO crm.customer_sales_member(workspace_id,customer_id,user_ref_id)
            VALUES($1,$2,$3) ON CONFLICT DO NOTHING''',workspace,customer,actors['A'][0])
        for code,want in [('A',actors['A'][0]),('S',actors['A'][0]),('M',actors['A'][0]),('O',actors['A'][0]),
                          ('B',None),('C',None),('F',None),('L',None)]:
            assert await scalar(code,'SELECT security.profile_customer_owner($1)',customer)==want,(code,want)
        checks.append('confirmed_owner_projection_obeys_existing_customer_and_role_scope')
        assert await scalar('S','SELECT count(*) FROM crm.customer_ownership WHERE customer_id=$1',customer)==0
        assert await scalar('M','SELECT count(*) FROM crm.customer_ownership WHERE customer_id=$1',customer)==0
        checks.append('projection_does_not_expand_ownership_workflow_rls')
        for state in ('unclaimed','legacy_review'):
            await conn.execute('UPDATE crm.customer_ownership SET state=$2,owner_user_ref_id=NULL WHERE customer_id=$1',customer,state)
            assert await scalar('M','SELECT security.profile_customer_owner($1)',customer) is None
        checks.append('unclaimed_and_legacy_review_do_not_use_compatibility_owner')
        await conn.execute("UPDATE crm.customer_ownership SET state='claimed',owner_user_ref_id=$2 WHERE customer_id=$1",customer,actors['A'][0])
        async with actor('M',workspace_id=foreign_workspace) as c:
            assert await c.fetchval('SELECT security.profile_customer_owner($1)',customer) is None
        assert await scalar('M','SELECT security.profile_customer_owner($1)',uuid4()) is None
        checks.append('owner_projection_rejects_cross_workspace_and_nonexistent_customer')
        await conn.execute('UPDATE crm.customer SET deleted_at=clock_timestamp() WHERE id=$1',customer)
        assert await scalar('M','SELECT security.profile_customer_owner($1)',customer) is None
        checks.append('owner_projection_does_not_return_deleted_customer')
        # Historical-quarter approvals remain anchored to their original quarter.
        # The clock is not modified: create the pending fixture as a historical
        # approved workflow, then exercise the real reviewer.
        hist=await conn.fetchval('''INSERT INTO crm.sales_target_batch_request(workspace_id,scope_type,user_ref_id,period_type,period_start,period_end,
            applicant_user_ref_id,reason,items) VALUES($1,'person',$2,'quarter',$3,$4,$2,'历史待审',$5) RETURNING id''',workspace,actors['B'][0],past_start,past_end,
            [dict(kind='collection',target_id=None,previous_amount=None,proposed_amount=55,base_version=None)])
        await review(hist)
        assert await conn.fetchval("SELECT amount FROM crm.sales_target WHERE user_ref_id=$1 AND period_start=$2",actors['B'][0],past_start)==55
        checks.append('late_approval_remains_in_original_quarter')
        # True concurrent first entry: only one transaction may create a metric.
        c1=await asyncpg.connect(database=dbname,**config);c2=await asyncpg.connect(database=dbname,**config)
        try:
            for c in (c1,c2):
                await c.set_type_codec('jsonb',schema='pg_catalog',encoder=json.dumps,decoder=json.loads,format='text')
            outcomes=await asyncio.gather(save('B',[val('recognized',111)],connection=c1),save('B',[val('recognized',222)],connection=c2),return_exceptions=True)
            assert sum(isinstance(x,dict) and x['status']=='effective' for x in outcomes)==1,outcomes
            assert sum(isinstance(x,asyncpg.PostgresError) for x in outcomes)==1,outcomes
            assert await conn.fetchval("SELECT count(*) FROM crm.sales_target WHERE user_ref_id=$1 AND period_start=$2 AND kind='recognized'",actors['B'][0],qstart)==1
            checks.append('concurrent_first_entries_have_one_winner_and_no_overwrite')
            results=await asyncio.gather(save('B',[val('recognized',333,1)],reason='同一变更',connection=c1),save('B',[val('recognized',333,1)],reason='同一变更',connection=c2))
            assert len({x['request']['id'] for x in results})==1
            race_id=UUID(results[0]['request']['id'])
            checks.append('concurrent_duplicate_changes_replay_one_pending_batch')
            outcomes=await asyncio.gather(review(race_id,connection=c1),review(race_id,connection=c2),return_exceptions=True)
            assert sum(isinstance(x,dict) and x['status']=='approved' for x in outcomes)==1
            assert sum(isinstance(x,asyncpg.PostgresError) for x in outcomes)==1
            assert await conn.fetchval("SELECT version_no FROM crm.sales_target WHERE user_ref_id=$1 AND period_start=$2 AND kind='recognized'",actors['B'][0],qstart)==2
            checks.append('concurrent_approvals_apply_exactly_one_revision')
            pending=await save('B',[val('recognized',444,2),val('demo_count',4)])
            race_id=UUID(pending['request']['id'])
            async with actor('O',c1) as c:
                await c.fetchval('SELECT security.save_target_batch(\'person\',$1,NULL,\'quarter\',$2,$3,$4,\'运营更正\')',actors['B'][0],qstart,qend,[val('recognized',555,2)])
                task=asyncio.create_task(review(race_id,connection=c2))
                await asyncio.sleep(.15)
                assert not task.done(),'review did not wait for shared scope lock'
            await denied(task,'concurrent_operations_adjustment_cancels_waiting_approval')
            assert await conn.fetchval("SELECT amount FROM crm.sales_target WHERE user_ref_id=$1 AND period_start=$2 AND kind='recognized'",actors['B'][0],qstart)==555
            assert not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM crm.sales_target WHERE user_ref_id=$1 AND kind='demo_count')",actors['B'][0])
            checks.append('operations_and_approval_share_lock_without_half_effect')
        finally:
            await c1.close();await c2.close()
        print(json.dumps({'status':'passed','checks':checks,'count':len(checks),'database':'disposable V090 to V092','runtime':'NOSUPERUSER NOBYPASSRLS'},ensure_ascii=False,indent=2))
    finally:
        if 'discover' in locals(): migrations.discover = discover
        if conn is not None: await conn.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
        if created_role: await admin.execute(f'DROP ROLE "{role}"')
        await admin.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--socket',required=True)
    parser.add_argument('--port',type=int,default=55491)
    args=parser.parse_args()
    asyncio.run(main(args.socket,args.port))
