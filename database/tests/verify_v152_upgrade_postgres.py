#!/usr/bin/env python3
"""Populated production-baseline V152 upgrade; exclusively disposable databases.

Default: private temporary PG16 cluster (Unix socket only). CI: --port 5432
--user postgres creates/deletes only a randomly named database and NOLOGIN role.
No DATABASE_URL, runtime environment files, or production data are consumed.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid

import asyncpg

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'database/scripts'))
import migrate

BASELINE = '5d55f49eca809d3cc715f636c753d38dc039c60b'
# Deliberately frozen: a new migration requires reviewing and extending this
# upgrade contract, not silently treating whatever discover() finds as tested.
TARGET = 'V194'
INCREMENTS = tuple(f'V{number:03d}' for number in range(153, 195))
BASELINE_CHECKSUM_COUNT = 120
TARGET_CHECKSUM_COUNT = 162
INFERENCE_FINISH = 'security.finish_inference_operation(uuid,text,jsonb,jsonb,text)'
TASK_REPARSE = 'security.reparse_task_import_batch(uuid,integer,jsonb,jsonb,jsonb)'
PRIVATE_REPARSE_PROJECTION = 'workflow.task_import_editable_projection(jsonb,boolean)'
PRIVATE_RECONCILIATION = ('security.reconcile_runtime_grants()',
    'security.reconcile_runtime_grants_before_inference_finish()',
    'security.reconcile_runtime_grants_before_task_reparse()')
SCHEMAS = ('platform','crm','activity','workflow','insight','ops','config','common','security','agent')
HISTORY = ('platform.workspace','platform.user_ref','platform.role_binding','platform.team_membership',
           'crm.customer','crm.opportunity','crm.business_change',
           'workflow.task','workflow.task_assignee','workflow.task_event','workflow.notification','insight.risk',
           'activity.visit','activity.visit_import','activity.visit_import_content',
           'config.form_definition','config.form_version','config.agent_runtime_config','config.agent_runtime_release',
           'config.account_authorization','config.account_permission_override',
           'config.feishu_connection','ops.feishu_event','ops.feishu_record_map','ops.feishu_delivery')


def frozen_migrations():
    migrations = migrate.discover()
    assert migrations[-1].key == TARGET, 'Review upgrade coverage before changing the frozen target'
    assert tuple(m.key for m in migrations if m.key > 'V152') == INCREMENTS
    return migrations


async def ledger(conn):
    return {row['migration_key']: dict(row) for row in await conn.fetch(
        'SELECT * FROM ops.migration_checksum ORDER BY migration_key')}


def assert_upgrade_results(results):
    expected_keys = [path.stem.split('__', 1)[0] for path in sorted((migrate.ROOT / 'seeds').glob('S*.sql'))]
    expected_keys += [migration.key for migration in frozen_migrations()]
    assert [row['key'] for row in results] == expected_keys
    assert tuple(row['key'] for row in results if row['status'] == 'applied') == INCREMENTS
    assert all(row['status'] == ('applied' if row['key'] in INCREMENTS else 'unchanged') for row in results)


async def rejected(conn, statement, *args, code='42501'):
    try:
        async with conn.transaction():
            await conn.execute(statement, *args)
    except asyncpg.PostgresError as error:
        assert error.sqlstate == code, error.sqlstate
    else:
        raise AssertionError('Expected rejection: ' + statement)


def archive_baseline(path):
    data = subprocess.check_output(['git','archive',BASELINE,'database'], cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for member in archive:
            if member.isfile():
                target = path / member.name
                if not target.resolve().is_relative_to(path.resolve()):
                    raise ValueError('Unsafe archive member')
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.extractfile(member).read())
    return path / 'database'


async def snapshot(conn, columns=None):
    if columns is None:
        columns = {}
        for table in HISTORY:
            columns[table] = [row['attname'] for row in await conn.fetch('''SELECT attname
                FROM pg_attribute WHERE attrelid=$1::regclass AND attnum>0 AND NOT attisdropped ORDER BY attnum''', table)]
    hashes = {}
    for table, fields in columns.items():
        select = ','.join('"'+field+'"' for field in fields)
        values = await conn.fetch(f'SELECT row_to_json(r)::text AS data FROM (SELECT {select} FROM {table}) r')
        hashes[table] = hashlib.sha256('\n'.join(sorted(row['data'] for row in values)).encode()).hexdigest()
    return columns, hashes


async def fixture(conn):
    result = []
    for index in range(2):
        ws, team, sales, admin = [uuid.uuid4() for _ in range(4)]
        await conn.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,$2)", ws, f'upgrade-company-{index}')
        await conn.execute("INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,'north','Synthetic north')",team,ws)
        for user, role, scope in ((sales,'sales','self'),(admin,'administrator','workspace')):
            await conn.execute('''INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name,account_code)
              VALUES($1,$2,$3,$3,$3)''',user,ws,role.upper())
            await conn.execute('''INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id)
              VALUES($1,$2,$3,$4,$5)''',ws,user,role,scope,team)
            await conn.execute('''INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role)
              VALUES($1,$2,$3,$4)''',ws,user,team,role)
        await conn.execute('INSERT INTO config.account_authorization(workspace_id,user_ref_id) VALUES($1,$2)',ws,sales)
        await conn.execute("INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect) VALUES($1,$2,'opportunity.update','deny')",ws,sales)
        await conn.execute("""INSERT INTO config.agent_runtime_release(workspace_id,version_no,config_snapshot,created_by_user_ref_id)
          VALUES($1,1,'{"source":"isolated-upgrade-fixture","llm_model":"synthetic-text"}',$2)""",ws,admin)
        await conn.execute("""INSERT INTO config.agent_runtime_config(workspace_id,provider_base_url,llm_model,asr_model,tts_model,
          api_key_ciphertext,cipher_format,encryption_key_id,api_key_tail,revision_no,encryption_revision)
          VALUES($1,'https://synthetic.invalid','synthetic-text','synthetic-asr','synthetic-tts',$2,'aes256gcm-v1','synthetic','test',1,1)""",ws,bytes(29))
        customer = await conn.fetchval('''INSERT INTO crm.customer(workspace_id,name,normalized_name,owner_user_ref_id,
          owner_team_id,created_by_user_ref_id,data_kind)
          VALUES($1,'Synthetic customer','synthetic customer',$2,$3,$2,'production') RETURNING id''',ws,sales,team)
        opportunity = await conn.fetchval('''INSERT INTO crm.opportunity(workspace_id,customer_id,name,amount,
          owner_user_ref_id,owner_team_id,created_by_user_ref_id,version_no)
          VALUES($1,$2,'Synthetic historical opportunity',10000000,$3,$4,$3,2) RETURNING id''',ws,customer,sales,team)
        change = await conn.fetchval('''INSERT INTO crm.business_change(workspace_id,customer_id,opportunity_id,
          actor_user_ref_id,version_no,changes) VALUES($1,$2,$3,$4,2,
          '[{"label":"金额","before":"900 万元","after":"1,000 万元"}]') RETURNING id''',ws,customer,opportunity,sales)
        sync = uuid.uuid4()
        settings = {'workspace_id': str(ws), 'connection_id': str(sync),
                    'notification': {'enabled': True}, 'mappings': {'opportunity': {'enabled': True}}}
        await conn.execute('''INSERT INTO config.feishu_connection(id,workspace_id,revision,enabled,
          validated_revision,settings,updated_by) VALUES($1,$2,1,true,1,$3::jsonb,$4)''',sync,ws,json.dumps(settings),admin)
        event = await conn.fetchval('''INSERT INTO ops.feishu_event(connection_id,workspace_id,object_kind,
          object_id,first_formal_create) VALUES($1,$2,'opportunity',$3,true) RETURNING id''',sync,ws,opportunity)
        await conn.execute('''INSERT INTO ops.feishu_record_map(connection_id,workspace_id,object_kind,object_id,
          table_id,record_id,projection_hash) VALUES($1,$2,'opportunity',$3,'synthetic-table','synthetic-record','synthetic-hash')''',sync,ws,opportunity)
        await conn.execute('''INSERT INTO ops.feishu_delivery(dedupe_key,event_id,connection_id,workspace_id,
          config_revision,chat_id,payload,status,message_id) VALUES($1,$2,$3,$4,1,'synthetic-chat',
          '{"synthetic":true}','sent','synthetic-message')''',f'fixture-{index}',event,sync,ws)
        task = await conn.fetchval('''INSERT INTO workflow.task(workspace_id,title,description,creator_user_ref_id,creator_team_id,due_at,association_kind)
          VALUES($1,'Historical task','Preserve real current owner',$2,$3,clock_timestamp()+interval '1 day','daily') RETURNING id''',ws,sales,team)
        await conn.execute('''INSERT INTO workflow.task_assignee(task_id,workspace_id,assignee_user_ref_id,assignee_team_id,assignee_role)
          VALUES($1,$2,$3,$4,'sales')''',task,ws,sales,team)
        await conn.execute('''INSERT INTO workflow.task_event(workspace_id,task_id,event_type,actor_user_ref_id,note)
          VALUES($1,$2,'create',$3,'Immutable synthetic history')''',ws,task,sales)
        definition = await conn.fetchval("INSERT INTO config.form_definition(workspace_id,form_code,name,object_type) VALUES($1,'upgrade','Upgrade fixture','visit') RETURNING id",ws)
        form = await conn.fetchval("INSERT INTO config.form_version(form_definition_id,version_no,status) VALUES($1,1,'active') RETURNING id", definition)
        content = b'Synthetic preserved original; no external integration.'
        original = uuid.uuid4()
        await conn.execute('''INSERT INTO activity.visit_import(id,workspace_id,created_by_user_ref_id,filename,file_path,file_size,status,
          storage_profile,storage_driver,storage_key,content_sha256) VALUES($1,$2,$3,'fixture.txt','',$4,'succeeded','postgres','postgres',$5,$6)''',
          original,ws,sales,len(content),f'{ws}/{original}',hashlib.sha256(content).hexdigest())
        await conn.execute('INSERT INTO activity.visit_import_content(import_id,workspace_id,chunk_no,content) VALUES($1,$2,0,$3)',original,ws,content)
        await conn.execute('''INSERT INTO activity.visit(workspace_id,customer_id,recorder_user_ref_id,form_version_id,status,interaction_at,
          follow_up_record,source_import_id,confirmed_at,confirmed_by_user_ref_id)
          VALUES($1,$2,$3,$4,'confirmed',clock_timestamp(),'Synthetic archived visit',$5,clock_timestamp(),$3)''',ws,customer,sales,form,original)
        result.append(dict(workspace=ws,team=team,sales=sales,administrator=admin,task=task,bytes=len(content),
                           customer=customer,opportunity=opportunity,change=change,sync=sync,event=event))
    return result


async def context(conn, actor, identity='sales'):
    await conn.execute("""SELECT set_config('app.workspace_id',$1,true),set_config('app.user_ref_id',$2,true),
      set_config('app.role_code',$3,true),set_config('app.team_ids',$4,true),set_config('app.authorization_context','',true)""",
      str(actor['workspace']),str(actor[identity]),identity,str(actor['team']))
    await conn.execute('SELECT security.authorization_refresh()')


async def permissions(conn, role, actor, identity='sales'):
    async with conn.transaction():
        await conn.execute('SET LOCAL row_security=on')
        await conn.execute(f'SET LOCAL ROLE "{role}"')
        await context(conn,actor,identity)
        return {r['code']:await conn.fetchval('SELECT security.authorization_has($1)',r['code'])
                for r in await conn.fetch('SELECT code FROM config.permission_catalog ORDER BY code')}


async def priority_history_fixture(conn, actors):
    """Retained reminder keys exist before upgrading; no legacy scan is needed."""
    for actor, status in zip(actors, ('cancelled', 'read')):
        actor['risk'] = await conn.fetchval('''INSERT INTO insight.risk(workspace_id,customer_id,
            risk_type_code,title,severity_code,owner_user_ref_id,status)
            VALUES($1,$2,'budget_risk','Historical synthetic risk','high',$3,'resolved') RETURNING id''',
            actor['workspace'], actor['customer'], actor['sales'])
        for kind, template in (('task', 'priority_task_due'), ('risk', 'priority_risk')):
            await conn.execute('''INSERT INTO workflow.notification(workspace_id,recipient_user_ref_id,
                template_code,title,body,object_type,object_id,status,dedupe_key,payload)
                VALUES($1,$2,$3,'Historical reminder','Preserve complete history',$4,$5,$6,$7,
                '{"historical_upgrade_fixture":true}')''', actor['workspace'], actor['sales'],
                template, kind, actor[kind], status, f'priority_monitor:{kind}:{actor[kind]}')


async def priority_boundary(conn):
    return (await conn.fetchrow('''SELECT proowner,proacl,prosecdef,proconfig FROM pg_proc
                WHERE oid='ops.run_priority_reminder_monitor()'::regprocedure'''),
            await conn.fetchval("SELECT pg_get_indexdef('workflow.uq_notification_dedupe_present'::regclass)"))


async def feishu_recovery_boundary(conn):
    return await conn.fetchrow('''SELECT proowner,proacl,prosecdef,proconfig FROM pg_proc
        WHERE oid='security.feishu_recover(uuid,uuid,text,text,text)'::regprocedure''')


async def verify_priority_upgrade(conn, role, actors):
    history = await conn.fetch('SELECT * FROM workflow.notification ORDER BY id')
    assert len(history) == 4
    for actor in actors:
        await conn.execute("UPDATE workflow.task SET due_at=clock_timestamp()+interval '6 hours' WHERE id=$1", actor['task'])
        await conn.execute("UPDATE insight.risk SET status='pending' WHERE id=$1", actor['risk'])

    async def scan():
        original_role = await conn.fetchval('SHOW role')
        try:
            async with conn.transaction():
                await conn.execute(f'SET LOCAL ROLE "{role}"')
                assert not await conn.fetchval('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')
                return await conn.fetchval('SELECT ops.run_priority_reminder_monitor()')
        finally:
            # A released savepoint retains SET LOCAL through the outer probe
            # transaction. Restore the observer role before comparing all tenants.
            await conn.execute("SELECT set_config('role',$1,true)", original_role)

    # Eligible sources do not revive cancelled/read history or collide with its keys.
    assert await scan() == 0
    assert await conn.fetch('SELECT * FROM workflow.notification ORDER BY id') == history
    actor = actors[0]
    task = await conn.fetchval('''INSERT INTO workflow.task(workspace_id,title,description,creator_user_ref_id,due_at,association_kind)
        VALUES($1,'New synthetic reminder task','Synthetic reminder upgrade probe',$2,
        clock_timestamp()+interval '6 hours','daily') RETURNING id''',
        actor['workspace'], actor['sales'])
    await conn.execute('''INSERT INTO workflow.task_assignee(task_id,workspace_id,assignee_user_ref_id,assignee_role)
        VALUES($1,$2,$3,'sales')''', task, actor['workspace'], actor['sales'])
    risk = await conn.fetchval('''INSERT INTO insight.risk(workspace_id,customer_id,risk_type_code,title,severity_code,owner_user_ref_id)
        VALUES($1,$2,'budget_risk','New synthetic reminder risk','high',$3) RETURNING id''',
        actor['workspace'], actor['customer'], actor['sales'])
    assert await scan() == 2
    assert await scan() == 0
    history_ids = [row['id'] for row in history]
    assert await conn.fetch('SELECT * FROM workflow.notification WHERE id=ANY($1::uuid[]) ORDER BY id', history_ids) == history
    assert {row['object_id'] for row in await conn.fetch(
        'SELECT object_id FROM workflow.notification WHERE id<>ALL($1::uuid[])', history_ids)} == {task, risk}


async def verify_release_additions(conn, role, actors):
    """Check new contracts against the upgraded historical tenants and ACLs."""
    await verify_priority_upgrade(conn, role, actors)
    rule = await conn.fetchrow("""SELECT version_no,status,definition FROM config.rule_set
        WHERE rule_code='opportunity_amount' AND workspace_id IS NULL""")
    assert rule and rule['version_no'] == 1 and rule['status'] == 'active'
    assert json.loads(rule['definition']) == {'schema_version': 1, 'warning_threshold_wan': 1000}
    for code in ('opportunity_amount', 'agent_execution.task_import', 'agent_business.task_import',
                 'agent_business.visit_quality'):
        assert await conn.fetchval('SELECT security.company_rule_supported($1)', code)
    assert not await conn.fetchval("SELECT security.company_rule_supported('agent_execution.unreviewed')")

    # No historical edits or already delivered create events become new cards.
    assert await conn.fetchval('SELECT count(*) FROM ops.feishu_event WHERE notification_snapshot IS NOT NULL') == 0
    assert await conn.fetchval('SELECT count(*) FROM ops.feishu_delivery') == len(actors)
    async with conn.transaction():
        actor = actors[0]
        await context(conn, actor, 'administrator')
        changes = [{'label': '金额', 'before': '1,000 万元', 'after': '1,100 万元'}]
        event = await conn.fetchval('''INSERT INTO crm.business_change(workspace_id,customer_id,opportunity_id,
            actor_user_ref_id,version_no,changes) VALUES($1,$2,$3,$4,3,$5::jsonb) RETURNING id''',
            actor['workspace'], actor['customer'], actor['opportunity'], actor['administrator'], json.dumps(changes))
        snapshot_value = json.loads(await conn.fetchval(
            'SELECT notification_snapshot FROM ops.feishu_event WHERE id=$1', event))
        assert snapshot_value['change_id'] == str(event)
        assert snapshot_value['workspace_id'] == str(actor['workspace'])
        assert snapshot_value['opportunity_id'] == str(actor['opportunity'])
        assert snapshot_value['actor_user_ref_id'] == str(actor['administrator'])
        assert snapshot_value['changes'] == changes
        await rejected(conn, 'UPDATE ops.feishu_event SET notification_snapshot=NULL WHERE id=$1', event, code='23514')
        await conn.execute("UPDATE ops.feishu_event SET status='failed',error_code='SYNTHETIC_RETRY' WHERE id=$1", event)
        assert json.loads(await conn.fetchval('SELECT notification_snapshot FROM ops.feishu_event WHERE id=$1', event)) == snapshot_value
        # Even an owner connection cannot produce a tenant-mismatched human card.
        other = actors[1]
        mismatched = await conn.fetchval('''INSERT INTO crm.business_change(workspace_id,customer_id,opportunity_id,
            actor_user_ref_id,version_no,changes) VALUES($1,$2,$3,$4,3,$5::jsonb) RETURNING id''',
            other['workspace'], other['customer'], other['opportunity'], other['administrator'], json.dumps(changes))
        assert not await conn.fetchval('SELECT EXISTS(SELECT 1 FROM ops.feishu_event WHERE id=$1)', mismatched)

    assert await conn.fetchval("SELECT has_function_privilege($1,'security.company_entry(text)','EXECUTE')", role)
    for signature in (
        'control.begin_login(uuid,text,text,text,integer)',
        'control.complete_login(text,jsonb,integer,bigint,text,text)',
        'control.confirm_mfa(text,jsonb,integer,bigint,text,text)',
        'control.rotate_mfa_session(text,text,jsonb,integer,bigint,text,text)',
        'ops.capture_feishu_opportunity_change()', 'ops.protect_feishu_notification_snapshot()',
        'security.reconcile_runtime_grants()', 'workflow.guard_task_import_batch()',
        'workflow.guard_task_import_item()', 'workflow.check_task_import_confirmation()',
    ):
        assert not await conn.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, signature), signature
        assert not await conn.fetchval("""SELECT EXISTS(SELECT 1 FROM pg_proc p,
            LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
            WHERE p.oid=$1::regprocedure AND a.grantee=0 AND a.privilege_type='EXECUTE')""", signature), signature

    for table, grants in (
        ('workflow.task_import_batch', {'SELECT', 'INSERT', 'UPDATE'}),
        ('workflow.task_import_item', {'SELECT', 'INSERT'}),
    ):
        assert await conn.fetchval('SELECT relrowsecurity FROM pg_class WHERE oid=$1::regclass', table)
        for privilege in ('SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE'):
            assert await conn.fetchval('SELECT has_table_privilege($1,$2,$3)', role, table, privilege) == (privilege in grants)
            assert not await conn.fetchval('SELECT has_table_privilege($1,$2,$3)', 'salegent_feishu_worker', table, privilege)
    for signature in ('security.task_import_is_creator(uuid)', 'security.task_import_batch_summary(uuid)',
                      'security.task_import_batch_page(text,integer,integer)'):
        assert await conn.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, signature)
        assert not await conn.fetchval("SELECT has_function_privilege('salegent_feishu_worker',$1,'EXECUTE')", signature)
    foreign_keys = await conn.fetch("""SELECT confrelid::regclass::text AS parent,
        array_length(conkey,1) AS columns FROM pg_constraint
        WHERE conrelid='workflow.task_import_item'::regclass AND contype='f'""")
    assert {row['parent']: row['columns'] for row in foreign_keys} == {
        'workflow.task_import_batch': 2, 'workflow.task': 2, 'platform.user_ref': 2}
    assert await conn.fetchval('SELECT count(*) FROM workflow.task_import_batch') == 0
    assert await conn.fetchval('SELECT count(*) FROM workflow.task_import_item') == 0
    async with conn.transaction():
        await conn.execute(f'SET LOCAL ROLE "{role}"')
        await context(conn, actors[0])
        entry = json.loads(await conn.fetchval("SELECT security.company_entry(' UPGRADE-COMPANY-0 ' )"))
        assert entry == {'company_code': 'upgrade-company-0', 'company_name': 'upgrade-company-0'}
        assert await conn.fetchval("SELECT security.company_entry('not-a-company')") is None
        source = 'Synthetic imported task source'
        items = [{'item_id': str(uuid.uuid4()), 'source_line': 1}]
        batch = await conn.fetchval('''INSERT INTO workflow.task_import_batch(workspace_id,creator_user_ref_id,
            batch_name,meeting_date,source_text,source_hash,parse_method,items)
            VALUES($1,$2,'Synthetic new draft',current_date,$3,$4,'rules',$5::jsonb) RETURNING id''',
            actors[0]['workspace'], actors[0]['sales'], source, hashlib.sha256(source.encode()).hexdigest(), json.dumps(items))
        assert await conn.fetchval('SELECT source_text FROM workflow.task_import_batch WHERE id=$1', batch) == source
        await context(conn, actors[0], 'administrator')
        assert await conn.fetchval('SELECT count(*) FROM workflow.task_import_batch WHERE id=$1', batch) == 0
        await context(conn, actors[1])
        assert await conn.fetchval('SELECT count(*) FROM workflow.task_import_batch') == 0
        assert json.loads(await conn.fetchval('SELECT security.task_import_batch_summary($1)', batch)) == []
        await rejected(conn, '''INSERT INTO workflow.task_import_batch(workspace_id,creator_user_ref_id,
            batch_name,meeting_date,source_text,source_hash,parse_method,items)
            VALUES($1,$2,'Cross company draft',current_date,$3,$4,'rules',$5::jsonb)''',
            actors[0]['workspace'], actors[0]['sales'], source, hashlib.sha256(source.encode()).hexdigest(), json.dumps(items))


async def verify_v193_v194_grants(conn, roles):
    """Exact caller matrix; never widen a legacy role to make new calls pass."""
    for role, finish, reparse in roles:
        assert await conn.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, INFERENCE_FINISH) == finish
        assert await conn.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, TASK_REPARSE) == reparse
        assert not await conn.fetchval("SELECT has_any_column_privilege($1,'agent.inference_operation','UPDATE')", role)
        for signature in (PRIVATE_REPARSE_PROJECTION, *PRIVATE_RECONCILIATION):
            assert not await conn.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, signature)
    for signature in (INFERENCE_FINISH, TASK_REPARSE, PRIVATE_REPARSE_PROJECTION, *PRIVATE_RECONCILIATION):
        assert not await conn.fetchval("""SELECT EXISTS(SELECT 1 FROM pg_proc p,
            LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
            WHERE p.oid=$1::regprocedure AND a.grantee=0 AND a.privilege_type='EXECUTE')""", signature)
    for table in ('agent.inference_operation', 'workflow.task_import_batch'):
        assert await conn.fetchval('SELECT relrowsecurity FROM pg_class WHERE oid=$1::regclass', table)
    for signature in (INFERENCE_FINISH, TASK_REPARSE, 'workflow.guard_task_import_batch()'):
        security = await conn.fetchrow('SELECT prosecdef,proconfig FROM pg_proc WHERE oid=$1::regprocedure', signature)
        assert security['prosecdef'] and security['proconfig'] == ['search_path=pg_catalog']


async def verify_v193_v194_calls(conn, role, actors):
    """Exercise the new narrow calls on old tenants; caller rolls back probes."""
    observer_role = await conn.fetchval('SHOW role')
    task_count = await conn.fetchval('SELECT count(*) FROM workflow.task')
    operation = uuid.uuid4()
    async with conn.transaction():
        await conn.execute('SET LOCAL row_security=on')
        await conn.execute(f'SET LOCAL ROLE "{role}"')
        await context(conn, actors[0])
        await conn.execute('''INSERT INTO agent.inference_operation
            (id,workspace_id,actor_user_ref_id,actor_role_code,capability)
            VALUES($1,$2,$3,'sales','task_import')''', operation, actors[0]['workspace'], actors[0]['sales'])
        await rejected(conn, "UPDATE agent.inference_operation SET status='failed' WHERE id=$1", operation)
        await context(conn, actors[1])
        assert not await conn.fetchval("SELECT security.finish_inference_operation($1,'failed','{}','[]',NULL)", operation)
        await context(conn, actors[0], 'administrator')
        assert not await conn.fetchval("SELECT security.finish_inference_operation($1,'failed','{}','[]',NULL)", operation)
        await context(conn, actors[0])
        assert await conn.fetchval("SELECT security.finish_inference_operation($1,'failed','{}','[]','SyntheticFailure')", operation)
        assert not await conn.fetchval("SELECT security.finish_inference_operation($1,'accepted','{}','[]',NULL)", operation)
    # These transactions are savepoints inside the caller's rollback probe;
    # released savepoints retain SET LOCAL until we restore the observer.
    await conn.execute("SELECT set_config('role',$1,true)", observer_role)
    row = await conn.fetchrow('SELECT status,completed_at,error_code FROM agent.inference_operation WHERE id=$1', operation)
    assert row['status'] == 'failed' and row['completed_at'] and row['error_code'] == 'SyntheticFailure'
    async with conn.transaction():
        await conn.execute('SET LOCAL row_security=on')
        await conn.execute(f'SET LOCAL ROLE "{role}"')
        await context(conn, actors[0])
        source = 'Synthetic retained meeting source'
        items = [{'item_id': str(uuid.uuid4()), 'source_line': 1, 'description': 'Original parsed task'}]
        batch = await conn.fetchval('''INSERT INTO workflow.task_import_batch
            (workspace_id,creator_user_ref_id,batch_name,meeting_date,source_text,source_hash,parse_method,items)
            VALUES($1,$2,'Upgrade reparse probe',current_date,$3,$4,'rules',$5::jsonb) RETURNING id''',
            actors[0]['workspace'], actors[0]['sales'], source, hashlib.sha256(source.encode()).hexdigest(), json.dumps(items))
        before = json.loads(await conn.fetchval('SELECT to_jsonb(b) FROM workflow.task_import_batch b WHERE id=$1', batch))
        parsed = [{**items[0], 'item_id': str(uuid.uuid4()), 'description': 'New AI parsed task'}]
        call = 'SELECT security.reparse_task_import_batch($1,$2,$3::jsonb,\'[]\',$3::jsonb)'
        await context(conn, actors[1])
        assert await conn.fetchval(call, batch, 1, json.dumps(parsed)) is None
        await context(conn, actors[0], 'administrator')
        assert await conn.fetchval(call, batch, 1, json.dumps(parsed)) is None
        await context(conn, actors[0])
        assert await conn.fetchval(call, batch, 1, json.dumps(parsed)) == batch
        after = json.loads(await conn.fetchval('SELECT to_jsonb(b) FROM workflow.task_import_batch b WHERE id=$1', batch))
        changed = {'items', 'editable_items', 'parse_method', 'parse_warnings', 'version_no', 'updated_at'}
        assert {k: v for k, v in after.items() if k not in changed} == {k: v for k, v in before.items() if k not in changed}
        assert after['version_no'] == 2 and after['parse_method'] == 'ai' and after['items'] == parsed
        assert await conn.fetchval(call, batch, 1, json.dumps(parsed)) is None
        assert await conn.fetchval("SELECT COALESCE(current_setting('app.task_import_reparse',true),'')") != 'true'
        await rejected(conn, "UPDATE workflow.task_import_batch SET source_text='Changed original',version_no=version_no+1 WHERE id=$1", batch, code='23514')
        edited = [{**parsed[0], 'description': 'Manually reviewed description'}]
        await conn.execute('UPDATE workflow.task_import_batch SET editable_items=$2::jsonb,version_no=version_no+1 WHERE id=$1', batch, json.dumps(edited))
        assert await conn.fetchval(call, batch, 3, json.dumps(parsed)) is None
        assert json.loads(await conn.fetchval('SELECT editable_items FROM workflow.task_import_batch WHERE id=$1', batch)) == edited
        assert await conn.fetchval('SELECT count(*) FROM workflow.task_import_item WHERE batch_id=$1', batch) == 0
    await conn.execute("SELECT set_config('role',$1,true)", observer_role)
    assert await conn.fetchval('SELECT count(*) FROM workflow.task') == task_count


async def verify(config, baseline_root):
    migrations = frozen_migrations()
    suffix=uuid.uuid4().hex[:12]
    name='salegent_verify_v152_'+suffix
    role='salegent_verify_v152_'+suffix
    historical_guard_owner='salegent_guard_owner_'+suffix
    admin=await asyncpg.connect(database='postgres',**config)
    conn=None
    created_role=False
    created_guard_owner=False
    try:
        await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
        conn=await asyncpg.connect(database=name,command_timeout=300,**config)
        encoding = await conn.fetchval('SHOW server_encoding')
        assert encoding == 'UTF8', 'The release contract includes Chinese text and requires a UTF8 test database'
        postgres_version = await conn.fetchval('SHOW server_version')
        await migrate.migrate(conn,baseline_root)
        assert await conn.fetchval('SELECT max(version) FROM ops.schema_migration')=='V152'
        baseline_ledger_count=await conn.fetchval('SELECT count(*) FROM ops.schema_migration')
        print(f'Baseline schema ledger rows: {baseline_ledger_count}',flush=True)
        baseline_ledger = await ledger(conn)
        assert len(baseline_ledger) == BASELINE_CHECKSUM_COUNT
        await admin.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        created_role=True
        await admin.execute(f'CREATE ROLE "{historical_guard_owner}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        created_guard_owner=True
        await conn.execute(f'GRANT USAGE ON SCHEMA workflow TO "{historical_guard_owner}"')
        for schema in SCHEMAS:
            await conn.execute(f'GRANT USAGE ON SCHEMA {schema} TO "{role}"')
            await conn.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA {schema} TO "{role}"')
            await conn.execute(f'GRANT USAGE ON ALL SEQUENCES IN SCHEMA {schema} TO "{role}"')
            await conn.execute(f'GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA {schema} TO "{role}"')
        await conn.execute(f'REVOKE ALL ON platform.password_credential,security.login_throttle FROM "{role}"')
        await conn.fetchval('SELECT security.reconcile_runtime_grants()')
        assert not await conn.fetchval("SELECT has_table_privilege($1,'platform.password_credential','SELECT')",role)
        actors=await fixture(conn)
        await priority_history_fixture(conn, actors)
        await conn.execute('SET row_security=on')
        columns,history=await snapshot(conn)
        historical_rules = {row['id']: row['data'] for row in await conn.fetch(
            'SELECT id,to_jsonb(r)::text AS data FROM config.rule_set r')}
        before=[{identity:await permissions(conn,role,a,identity) for identity in ('sales','administrator')} for a in actors]
        assert all(not grants['sales']['opportunity.update'] for grants in before)
        # V153 must fail atomically for previously legal mixed-case duplicate company codes.
        tx=conn.transaction(); await tx.start()
        try:
            await conn.execute("INSERT INTO platform.workspace(external_workspace_id,name) VALUES('UPGRADE-COMPANY-0','Duplicate')")
            try:
                await migrate.apply(conn,next(m for m in migrate.discover() if m.key=='V153'),migrate.ROOT)
            except asyncpg.UniqueViolationError:
                pass
            else:
                raise AssertionError('V153 accepted duplicate company codes')
            assert await conn.fetchval('SELECT max(version) FROM ops.schema_migration')=='V152'
            assert not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname='control')")
        finally:
            await tx.rollback()
        print('PASS V152 populated baseline; case-fold collision fails before committing V153',flush=True)
        # The installation outer-transaction boundary must fully roll back all pending migrations.
        tx=conn.transaction(); await tx.start()
        try:
            rolled=await migrate.migrate(conn)
            assert_upgrade_results(rolled)
        finally:
            await tx.rollback()
        assert await conn.fetchval('SELECT max(version) FROM ops.schema_migration')=='V152'
        assert await ledger(conn) == baseline_ledger
        assert not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname='control')")
        assert await conn.fetchval("SELECT to_regclass('workflow.task_import_batch') IS NULL")
        assert (await snapshot(conn,columns))[1]==history
        durations={}; original_apply=migrate.apply
        async def timed(*args,**kwargs):
            start=time.monotonic()
            boundary = await priority_boundary(conn) if args[1].key == 'V191' else None
            recovery_boundary = await feishu_recovery_boundary(conn) if args[1].key == 'V192' else None
            if args[1].key == 'V194':
                # CREATE OR REPLACE must not retain a historical owner that
                # cannot execute the new private projection dependency.
                await conn.execute(f'ALTER FUNCTION workflow.guard_task_import_batch() OWNER TO "{historical_guard_owner}"')
            state=await original_apply(*args,**kwargs)
            if boundary is not None:
                assert await priority_boundary(conn) == boundary, 'V191 changed function security or reminder index'
            if recovery_boundary is not None:
                assert await feishu_recovery_boundary(conn) == recovery_boundary, 'V192 changed recovery function security'
            if args[1].key == 'V194':
                assert await conn.fetchval("SELECT pg_get_userbyid(proowner)=current_user FROM pg_proc WHERE oid='workflow.guard_task_import_batch()'::regprocedure")
                assert not await conn.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", historical_guard_owner, PRIVATE_REPARSE_PROJECTION)
            if state=='applied': durations[args[1].key]=round(time.monotonic()-start,4)
            return state
        migrate.apply=timed
        try:
            start=time.monotonic()
            # Exercise the legacy deployment's per-file transactions, not only new instance installer semantics.
            results=await migrate.migrate(conn)
            elapsed=time.monotonic()-start
        finally:
            migrate.apply=original_apply
        assert_upgrade_results(results)
        assert await conn.fetchval('SELECT max(version) FROM ops.schema_migration') == TARGET
        assert await conn.fetchval('SELECT count(*) FROM ops.schema_migration') == baseline_ledger_count + len(INCREMENTS)
        current_ledger = await ledger(conn)
        assert len(current_ledger) == TARGET_CHECKSUM_COUNT
        assert {key: current_ledger[key] for key in baseline_ledger} == baseline_ledger
        for migration in migrations:
            assert current_ledger[migration.key]['checksum_sha256'] == migration.checksum
        current_rules = {row['id']: row['data'] for row in await conn.fetch(
            'SELECT id,to_jsonb(r)::text AS data FROM config.rule_set r')}
        assert {key: current_rules[key] for key in historical_rules} == historical_rules
        assert (await snapshot(conn,columns))[1]==history
        for actor, expected in zip(actors,before):
            for identity,grants in expected.items():
                current=await permissions(conn,role,actor,identity)
                changed={key:(value,current.get(key)) for key,value in grants.items() if current.get(key)!=value}
                assert not changed,f'Existing {identity} permission set changed: {changed}'
            ws=actor['workspace']
            assert await conn.fetchval('SELECT bool_and(enabled AND expires_at IS NULL) FROM control.tenant_module WHERE workspace_id=$1',ws)
            assert await conn.fetchval('SELECT count(*) FROM control.tenant_module WHERE workspace_id=$1',ws)==10
            assert await conn.fetchval('SELECT shared_enabled FROM control.tenant_ai_service WHERE workspace_id=$1',ws)
            quota=await conn.fetchrow('SELECT account_limit,active_accounts FROM control.tenant_account_quota WHERE workspace_id=$1',ws)
            assert quota['account_limit'] is None and quota['active_accounts']==2
            storage=await conn.fetchrow('SELECT limit_bytes,stored_bytes,reserved_bytes FROM control.tenant_storage_quota WHERE workspace_id=$1',ws)
            assert storage['limit_bytes'] is None and storage['stored_bytes']==actor['bytes'] and storage['reserved_bytes']==0
            assert await conn.fetchval('SELECT bool_and(call_limit IS NULL) FROM control.tenant_call_quota WHERE workspace_id=$1',ws)
            assert await conn.fetchval('SELECT bool_and(rate_per_minute IS NULL AND burst IS NULL) FROM control.tenant_http_limit WHERE workspace_id=$1',ws)
            async with conn.transaction():
                await conn.execute(f'SET LOCAL ROLE "{role}"'); await context(conn,actor)
                assert [row['id'] for row in await conn.fetch('SELECT id FROM crm.customer')] == [actor['customer']]
                assert [row['id'] for row in await conn.fetch('SELECT id FROM workflow.task')] == [actor['task']]
                assert json.loads(await conn.fetchval("SELECT security.consume_http_request('read')"))['allowed']
                assert json.loads(await conn.fetchval('SELECT security.ai_service_policy()'))['shared_enabled']
        for function in ('security.consume_http_request(text)','security.ai_service_policy()',
                         'security.reserve_model_call(uuid,uuid)','security.finish_model_call(uuid,uuid,boolean,jsonb)',
                         'security.tenant_modules()'):
            assert await conn.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')",role,function),function
        assert not await conn.fetchval("SELECT has_schema_privilege($1,'control','USAGE')",role)
        assert not await conn.fetchval("SELECT has_table_privilege($1,'platform.password_credential','SELECT')",role)
        tx = conn.transaction(); await tx.start()
        try:
            observer_role = await conn.fetchval('SHOW role')
            await verify_release_additions(conn, role, actors)
            await conn.execute("SELECT set_config('role',$1,true)", observer_role)
            await verify_v193_v194_grants(conn, [(role, True, True), ('salegent_feishu_worker', False, False)])
            await verify_v193_v194_calls(conn, role, actors)
        finally:
            # Capability probes do not change the preserved history used by the
            # replay check, nor do they require fixture cleanup privileges.
            await tx.rollback()
        assert (await snapshot(conn,columns))[1] == history
        assert all(r['status']=='unchanged' for r in await migrate.migrate(conn))
        assert await ledger(conn) == current_ledger
        assert (await snapshot(conn,columns))[1] == history
        assert await conn.fetchval('SELECT count(*) FROM workflow.task_import_batch') == 0
        assert await conn.fetchval('SELECT count(*) FROM ops.feishu_event WHERE notification_snapshot IS NOT NULL') == 0
        new_ws=await conn.fetchval("INSERT INTO platform.workspace(external_workspace_id,name) VALUES('new-post-upgrade','New fixture') RETURNING id")
        assert await conn.fetchval("SELECT bool_and(NOT enabled) FROM control.tenant_module WHERE workspace_id=$1 AND module_code IN ('ai','feishu','history')",new_ws)
        assert await conn.fetchval('SELECT count(*) FROM control.operator')==0
        suspended=actors[0]
        await conn.execute("UPDATE platform.workspace SET status='suspended' WHERE id=$1",suspended['workspace'])
        async with conn.transaction():
            await conn.execute(f'SET LOCAL ROLE "{role}"')
            try:
                async with conn.transaction(): await context(conn,suspended)
            except asyncpg.PostgresError as e:
                assert e.sqlstate=='P7403'
            else: raise AssertionError('Suspended tenant context accepted')
        print(json.dumps({'status':'passed','baseline_revision':BASELINE,'from':'V152','to':TARGET,'applied':len(INCREMENTS),
            'postgres_version':postgres_version,'server_encoding':encoding,
            'target_checksums':len(current_ledger),'historical_checksums_unchanged':len(baseline_ledger),
            'history_tables_unchanged':len(history),'companies':2,'runtime_role':'non-superuser, non-bypass, legacy ACL',
            'permissions_preserved':True,'runtime_new_function_grants':True,'old_company_modules_and_shared_ai':True,
            'quotas_unlimited':True,'explicit_account_deny_preserved':True,'stored_runtime_configuration_preserved':True,'new_company_ai_feishu_history_off':True,'cross_tenant_rls':True,
            'rollback_before_commit':True,'repeat_noop':True,'upgrade_seconds':round(elapsed,3),
            'v187_default_amount_rule_and_existing_rules_preserved':True,
            'v188_no_backfill_immutable_human_update_snapshot':True,
            'v189_api_company_lookup_without_platform_privileges':True,
            'v190_precise_grants_composite_tenant_foreign_keys_private_drafts':True,
            'v191_retained_task_risk_reminders_not_revived_new_keys_once':True,
            'v191_function_owner_acl_security_and_unique_index_preserved':True,
            'v192_recovery_function_owner_acl_security_preserved':True,
            'v193_owned_terminal_receipt_once_without_direct_update':True,
            'v194_owned_unedited_reparse_preserves_source_and_dispatch_count':True,
            'v194_mixed_historical_guard_owner_private_dependency':True,
            'slowest_migrations':sorted(durations.items(),key=lambda x:x[1],reverse=True)[:5],
            'external_integrations':False,'production_data':False},indent=2),flush=True)
    finally:
        if conn: await conn.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        if created_role: await admin.execute(f'DROP ROLE "{role}"')
        if created_guard_owner: await admin.execute(f'DROP ROLE "{historical_guard_owner}"')
        await admin.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pg-bin',default='/opt/homebrew/opt/postgresql@16/bin')
    parser.add_argument('--port',type=int,help='Explicit disposable CI PostgreSQL port on localhost; creates a random database')
    parser.add_argument('--user',default='postgres')
    args=parser.parse_args()
    frozen_migrations()
    with tempfile.TemporaryDirectory(prefix='sb-v152-',dir='/tmp') as temp:
        root=Path(temp); old=archive_baseline(root/'baseline')
        assert max(m.key for m in migrate.discover(old))=='V152'
        for historical in [old/'baseline/V001__current_schema.sql',*(old/'seeds').glob('S*.sql'),*(m.path for m in migrate.discover(old))]:
            assert historical.read_bytes()==(migrate.ROOT/historical.relative_to(old)).read_bytes(),'Historical SQL edited'
        if args.port:
            if args.port==15432 or not 1<=args.port<=65535: raise ValueError('Use an isolated CI server, not the acceptance database')
            asyncio.run(verify(dict(host='127.0.0.1',port=args.port,user=args.user),old))
        else:
            binary=Path(args.pg_bin); data=root/'data'; socket=root/'socket';socket.mkdir()
            subprocess.run([str(binary/'initdb'),'-D',str(data),'-U',getpass.getuser(),'-A','trust',
                            '--no-locale','--encoding=UTF8'],check=True,capture_output=True)
            subprocess.run([str(binary/'pg_ctl'),'-D',str(data),'-l',str(root/'postgres.log'),'-o',f"-k {socket} -c listen_addresses=''",'-w','start'],check=True,capture_output=True)
            try: asyncio.run(verify(dict(host=str(socket),user=getpass.getuser()),old))
            finally: subprocess.run([str(binary/'pg_ctl'),'-D',str(data),'-m','immediate','-w','stop'],check=True,capture_output=True)


if __name__=='__main__': main()
