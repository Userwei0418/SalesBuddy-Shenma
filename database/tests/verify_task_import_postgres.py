#!/usr/bin/env python3
"""V189 -> V190 task import isolation and provenance in disposable PostgreSQL.

Default starts a private Unix-socket-only PG16 cluster. CI may use --port 5432
--user postgres; all fixtures live in a random database which is dropped.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import hashlib
import json
from pathlib import Path
import secrets
import subprocess
import tempfile
from uuid import UUID, uuid4

import asyncpg

from verify_runtime_grants_postgres import migrate_through, base_runtime_grants
from migrate import migrate
from runtime_roles import load_policy, verify_service_roles
from upgrade_permissions import reconcile_service_roles


async def value(conn, sql, *args):
    result = await conn.fetchval(sql, *args)
    return json.loads(result) if isinstance(result, str) else result


async def rejected(conn, sql, *args, codes=('23514', '42501', '23503', '23505')):
    try:
        async with conn.transaction():
            await conn.execute(sql, *args)
    except asyncpg.PostgresError as error:
        assert error.sqlstate in codes, (error.sqlstate, str(error))
    else:
        raise AssertionError('Expected rejection: ' + sql)


async def context(conn, fixture, user='creator'):
    await conn.execute("""SELECT set_config('app.workspace_id',$1,false),
      set_config('app.user_ref_id',$2,false),set_config('app.role_code',$3,false),
      set_config('app.team_ids',$4,false),set_config('app.authorization_context','',false)""",
      str(fixture['ws']), str(fixture[user]), 'manager' if user == 'manager' else 'sales', str(fixture['team']))


async def fixture(conn):
    f = {key: uuid4() for key in ('ws', 'team', 'creator', 'member', 'peer', 'manager', 'customer', 'sync')}
    await conn.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'Synthetic meeting company')", f['ws'], str(f['ws']))
    await conn.execute("UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1 AND module_code='feishu'", f['ws'])
    await conn.execute("INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,'SALES','Synthetic sales')", f['team'], f['ws'])
    for user in ('creator', 'member', 'peer', 'manager'):
        await conn.execute('''INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name)
          VALUES($1,$2,$3,$3,$3)''', f[user], f['ws'], user.upper())
        await conn.execute('''INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id)
          VALUES($1,$2,$3,$4,$5)''', f['ws'], f[user], 'manager' if user == 'manager' else 'sales',
                           'workspace' if user == 'manager' else 'self', f['team'])
        await conn.execute('''INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role)
          VALUES($1,$2,$3,$4)''', f['ws'], f[user], f['team'], 'manager' if user == 'manager' else 'sales')
    await conn.execute('''INSERT INTO crm.customer(id,workspace_id,name,normalized_name,created_by_user_ref_id,owner_user_ref_id,owner_team_id)
      VALUES($1,$2,'Synthetic customer','synthetic customer',$3,$3,$4)''', f['customer'], f['ws'], f['creator'], f['team'])
    await conn.execute('''INSERT INTO config.feishu_connection(id,workspace_id,revision,settings,updated_by)
      VALUES($1,$2,1,$3::jsonb,$4)''', f['sync'], f['ws'], json.dumps({'workspace_id': str(f['ws']), 'connection_id': str(f['sync'])}), f['creator'])
    return f


async def draft(conn, f, *, text=None):
    text = text or 'Synthetic meeting ' + uuid4().hex
    items = [{'item_id': str(uuid4()), 'source_line': i, 'source_text': 'PRIVATE source ' + str(i),
              'description': 'Synthetic task ' + str(i)} for i in (1, 2)]
    batch = await conn.fetchval('''INSERT INTO workflow.task_import_batch
      (workspace_id,creator_user_ref_id,batch_name,meeting_date,source_text,source_hash,parse_method,items)
      VALUES($1,$2,'Synthetic weekly meeting',current_date,$3,$4,'rules',$5::jsonb) RETURNING id''',
      f['ws'], f['creator'], text, hashlib.sha256(text.encode()).hexdigest(), json.dumps(items))
    return batch, items


async def task(conn, f, user='creator'):
    tid = await conn.fetchval('''INSERT INTO workflow.task
      (workspace_id,title,description,creator_user_ref_id,creator_team_id,due_at,association_kind,source_code)
      VALUES($1,'Synthetic task','Synthetic action',$2,$3,clock_timestamp()+interval '1 day','daily','meeting_import') RETURNING id''',
      f['ws'], f['creator'], f['team'])
    await conn.execute('''INSERT INTO workflow.task_assignee(task_id,workspace_id,assignee_user_ref_id,assignee_team_id,assignee_role)
      VALUES($1,$2,$3,$4,'sales')''', tid, f['ws'], f[user], f['team'])
    return tid


async def link(conn, f, bid, item, tid, user='creator'):
    await conn.execute('''INSERT INTO workflow.task_import_item
      (batch_id,item_id,workspace_id,task_id,source_line,original_assignee_user_ref_id,confirmed_item)
      VALUES($1,$2,$3,$4,$5,$6,$7::jsonb)''', bid, UUID(item['item_id']), f['ws'], tid,
      item['source_line'], f[user], json.dumps(item))


async def confirm(conn, bid, version=1):
    return await conn.execute('''UPDATE workflow.task_import_batch SET status='confirmed',
      version_no=version_no+1,confirmed_at=clock_timestamp(),confirmed_payload_hash=$2
      WHERE id=$1 AND version_no=$3''', bid, 'a' * 64, version)


async def verify(config, evidence_path=None):
    suffix = uuid4().hex[:12]
    database, api, observer, late = ['sb_import_' + kind + '_' + suffix for kind in ('db', 'api', 'observer', 'late')]
    admin = await asyncpg.connect(database='postgres', **config)
    connections, roles, checks = [], [], []
    try:
        await admin.execute(f'CREATE DATABASE "{database}" TEMPLATE template0')
        owner = await asyncpg.connect(database=database, **config)
        connections.append(owner)
        await migrate_through(owner, 189)
        f, other = await fixture(owner), await fixture(owner)
        await context(owner, f)
        oldtask = await task(owner, f)
        prior = await value(owner, 'SELECT to_jsonb(t) FROM workflow.task t WHERE id=$1', oldtask)
        customer_before = await value(owner, "SELECT ops.feishu_source($1,'customer',$2)", f['sync'], f['customer'])
        source_acl = dict(await owner.fetchrow("SELECT oid,proowner,proacl::text FROM pg_proc WHERE oid='ops.feishu_source(uuid,text,uuid)'::regprocedure"))
        await base_runtime_grants(owner, api)
        roles.append(api)
        # Existing runtime functions predate the feature; new tables and helpers
        # must acquire exact grants from V190 rather than fixture broad grants.
        await owner.execute(f'GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA security,common TO "{api}"')
        await owner.execute(f'CREATE ROLE "{observer}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        roles.append(observer)
        for schema in ('workflow', 'security', 'ops'):
            await owner.execute(f'ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT ALL ON TABLES TO "{observer}"')
            await owner.execute(f'ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT EXECUTE ON FUNCTIONS TO "{observer}"')
        await migrate(owner)
        assert await value(owner, 'SELECT to_jsonb(t) FROM workflow.task t WHERE id=$1', oldtask) == prior
        assert await value(owner, "SELECT ops.feishu_source($1,'customer',$2)", f['sync'], f['customer']) == customer_before
        assert source_acl == dict(await owner.fetchrow("SELECT oid,proowner,proacl::text FROM pg_proc WHERE oid='ops.feishu_source(uuid,text,uuid)'::regprocedure"))
        checks.append('upgrade_preserves_existing_tasks_non_task_projection_and_source_acl_oid')

        for table in ('workflow.task_import_batch', 'workflow.task_import_item'):
            for privilege in ('SELECT', 'INSERT'):
                assert await owner.fetchval('SELECT has_table_privilege($1,$2,$3)', api, table, privilege)
            assert not await owner.fetchval("SELECT has_table_privilege($1,$2,'DELETE')", api, table)
            for role in (observer, 'salegent_feishu_worker'):
                for privilege in ('SELECT', 'INSERT', 'UPDATE', 'DELETE'):
                    assert not await owner.fetchval('SELECT has_table_privilege($1,$2,$3)', role, table, privilege)
        for signature in ('workflow.guard_task_import_batch()', 'workflow.guard_task_import_item()',
                          'workflow.check_task_import_confirmation()', 'ops.feishu_source_before_task_import(uuid,text,uuid)',
                          'security.reconcile_runtime_grants()'):
            for role in (observer, 'salegent_feishu_worker'):
                assert not await owner.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, signature)
        for signature in ('security.task_import_is_creator(uuid)', 'security.task_import_batch_summary(uuid)',
                          'security.task_import_batch_page(text,integer,integer)'):
            assert await owner.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", api, signature)
            for role in (observer, 'salegent_feishu_worker'):
                assert not await owner.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, signature)
        checks.append('precise_api_grants_no_worker_writes_or_default_grant_leak')

        app = await asyncpg.connect(database=database, **config)
        connections.append(app)
        await app.execute(f'SET ROLE "{api}"')
        await context(app, f)
        bid, items = await draft(app, f)
        assert await value(app, 'SELECT editable_items FROM workflow.task_import_batch WHERE id=$1', bid) == items
        await app.execute('UPDATE workflow.task_import_batch SET editable_items=$2::jsonb,version_no=version_no+1 WHERE id=$1', bid, json.dumps(items[:1]))
        await rejected(app, "UPDATE workflow.task_import_batch SET source_text='Changed source',version_no=version_no+1 WHERE id=$1", bid)
        await rejected(app, 'UPDATE workflow.task_import_batch SET editable_items=$2::jsonb,version_no=version_no+1 WHERE id=$1', bid, json.dumps([{'item_id': str(uuid4())}]))
        assert await confirm(app, bid, 1) == 'UPDATE 0'
        await context(app, f, 'manager')
        assert await app.fetchval('SELECT count(*) FROM workflow.task_import_batch WHERE id=$1', bid) == 0
        assert await value(app, 'SELECT security.task_import_batch_summary($1)', bid) == []
        await context(app, other)
        assert await app.fetchval('SELECT count(*) FROM workflow.task_import_batch WHERE id=$1', bid) == 0
        await context(app, f)
        await rejected(app, '''UPDATE workflow.task_import_batch SET status='confirmed',version_no=version_no+1,
          confirmed_at=clock_timestamp(),confirmed_payload_hash=$2 WHERE id=$1''', bid, 'a' * 64)
        checks.append('draft_creator_only_immutable_source_editable_subset_and_version_guard')

        # Native FK constraints also block maintenance connections bypassing RLS.
        await rejected(owner, '''INSERT INTO workflow.task_import_batch(workspace_id,creator_user_ref_id,batch_name,meeting_date,source_text,source_hash,parse_method,items)
          VALUES($1,$2,'Cross-company',current_date,'Source text',$3,'rules',$4::jsonb)''', other['ws'], f['creator'], hashlib.sha256(b'Source text').hexdigest(), json.dumps(items))
        # A source hash uniquely identifies the same author's paste on that date.
        source = await app.fetchval('SELECT source_text FROM workflow.task_import_batch WHERE id=$1', bid)
        try:
            async with app.transaction():
                await draft(app, f, text=source)
        except asyncpg.UniqueViolationError:
            pass
        else:
            raise AssertionError('Duplicate paste accepted')
        checks.append('same_company_creator_fk_and_duplicate_source_constraint')

        # Unconfirmed partial dispatch must roll back even if the API forgets its
        # final parent update. The task itself must not survive the failed commit.
        partial = None
        try:
            async with app.transaction():
                partial = await task(app, f)
                await link(app, f, bid, items[0], partial)
        except asyncpg.CheckViolationError:
            pass
        else:
            raise AssertionError('Partial import committed')
        assert await owner.fetchval('SELECT count(*) FROM workflow.task WHERE id=$1', partial) == 0
        checks.append('deferred_confirmation_rolls_back_partial_tasks_and_links')

        # Even a same-company creator cannot retroactively attach an old task;
        # a mismatched owner/item or another company's task cannot be imported.
        async with app.transaction():
            await rejected(app, '''INSERT INTO workflow.task_import_item(batch_id,item_id,workspace_id,task_id,source_line,original_assignee_user_ref_id,confirmed_item)
              VALUES($1,$2,$3,$4,1,$5,$6::jsonb)''', bid, UUID(items[0]['item_id']), f['ws'], oldtask, f['creator'], json.dumps(items[0]))
            fresh = await task(app, f)
            for wrong_user, wrong_item in ((f['peer'], items[0]), (f['creator'], {**items[0], 'item_id': str(uuid4())})):
                await rejected(app, '''INSERT INTO workflow.task_import_item(batch_id,item_id,workspace_id,task_id,source_line,original_assignee_user_ref_id,confirmed_item)
                  VALUES($1,$2,$3,$4,1,$5,$6::jsonb)''', bid, UUID(wrong_item['item_id']), f['ws'], fresh, wrong_user, json.dumps(wrong_item))
        fk_defs = [r['definition'] for r in await owner.fetch("SELECT pg_get_constraintdef(oid) definition FROM pg_constraint WHERE conrelid='workflow.task_import_item'::regclass AND contype='f'")]
        for keys in ('batch_id, workspace_id', 'task_id, workspace_id', 'original_assignee_user_ref_id, workspace_id'):
            assert any('FOREIGN KEY (' + keys + ')' in definition for definition in fk_defs)
        # Existing parent ownership cannot migrate across companies while linked.
        checks.append('only_new_owned_task_matching_source_item_and_original_owner_can_link')

        async with app.transaction():
            await app.fetchrow('SELECT id FROM workflow.task_import_batch WHERE id=$1 FOR UPDATE', bid)
            first = await task(app, f, 'member')
            second = await task(app, f, 'peer')
            await link(app, f, bid, items[0], first, 'member')
            await link(app, f, bid, items[1], second, 'peer')
            assert await confirm(app, bid, 2) == 'UPDATE 1'
        await rejected(owner, "UPDATE workflow.task_import_item SET confirmed_item='{}' WHERE task_id=$1", first)
        await rejected(owner, 'DELETE FROM workflow.task_import_item WHERE task_id=$1', first)
        await rejected(owner, 'UPDATE workflow.task_import_batch SET version_no=version_no+1 WHERE id=$1', bid)
        await rejected(owner, 'UPDATE workflow.task SET workspace_id=$2 WHERE id=$1', first, other['ws'], codes=('23503',))
        await context(app, f, 'member')
        assert await app.fetchval('SELECT count(*) FROM workflow.task_import_batch WHERE id=$1', bid) == 0
        assert await app.fetchval('SELECT count(*) FROM workflow.task_import_item WHERE batch_id=$1', bid) == 0
        summary = await value(app, 'SELECT security.task_import_batch_summary($1)', bid)
        # V209 ships department reads. Same-team tasks are visible while raw
        # meeting source remains creator-only. A narrower configured role must
        # still redact the peer task, its provenance and any hidden counts.
        assert len(summary) == 1 and set(summary[0]['task_ids']) == {str(first), str(second)}
        assert not {'source_text', 'source_hash', 'items', 'editable_items', 'total_count'} & summary[0].keys()
        assert 'PRIVATE source' not in json.dumps(summary)
        role_id = await owner.fetchval("""SELECT g.role_id FROM config.permission_role_grant g
          JOIN config.permission_role r ON r.id=g.role_id AND r.workspace_id=g.workspace_id
          WHERE g.workspace_id=$1 AND r.builtin_role_code='sales' AND g.permission_code='task.read'
          AND g.scope_code='departments' AND cardinality(g.team_ids)=0""", f['ws'])
        assert role_id is not None, 'Expected current shipped sales task.read department scope'
        try:
            await owner.execute("UPDATE config.permission_role_grant SET scope_code='self' WHERE role_id=$1 AND permission_code='task.read'", role_id)
            await context(app, f, 'member')
            summary = await value(app, 'SELECT security.task_import_batch_summary($1)', bid)
            assert len(summary) == 1 and summary[0]['task_ids'] == [str(first)]
            assert summary[0]['task_items'][0]['original_assignee']['name'] == 'MEMBER'
            assert not {'source_text', 'source_hash', 'items', 'editable_items', 'total_count'} & summary[0].keys()
            assert 'PRIVATE source' not in json.dumps(summary) and str(second) not in json.dumps(summary)
        finally:
            await owner.execute("UPDATE config.permission_role_grant SET scope_code='departments' WHERE role_id=$1 AND permission_code='task.read'", role_id)
        await context(app, f, 'member')
        assert set((await value(app, 'SELECT security.task_import_batch_summary($1)', bid))[0]['task_ids']) == {str(first), str(second)}
        checks.append('department_default_and_explicit_self_scope_both_hide_private_meeting_source')
        await context(app, f, 'manager')
        assert set((await value(app, 'SELECT security.task_import_batch_summary($1)', bid))[0]['task_ids']) == {str(first), str(second)}
        await context(app, other, 'manager')
        assert await value(app, 'SELECT security.task_import_batch_summary($1)', bid) == []
        checks.append('confirmed_summary_follows_each_task_scope_and_hides_source_and_hidden_counts')

        # Role revocation and tenant suspension apply to the new summary too.
        await owner.execute('INSERT INTO config.account_authorization(workspace_id,user_ref_id) VALUES($1,$2)', f['ws'], f['member'])
        await owner.execute("INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect) VALUES($1,$2,'task.read','deny')", f['ws'], f['member'])
        await context(app, f, 'member')
        assert await value(app, 'SELECT security.task_import_batch_summary($1)', bid) == []
        await owner.execute("UPDATE platform.workspace SET status='suspended' WHERE id=$1", f['ws'])
        await context(app, f, 'manager')
        assert await value(app, 'SELECT security.task_import_batch_summary($1)', bid) == []
        await owner.execute("UPDATE platform.workspace SET status='active' WHERE id=$1", f['ws'])
        checks.append('revoked_task_read_and_suspended_company_cannot_read_summaries')

        # A transfer changes the operational owner, never the provenance owner.
        await owner.execute('DELETE FROM workflow.task_assignee WHERE task_id=$1', first)
        await owner.execute('''INSERT INTO workflow.task_assignee(task_id,workspace_id,assignee_user_ref_id,assignee_team_id,assignee_role)
          VALUES($1,$2,$3,$4,'sales')''', first, f['ws'], f['peer'], f['team'])
        await owner.execute("UPDATE workflow.task SET status='completed',accepted_at=clock_timestamp()-interval '1 hour',completion_submitted_at=clock_timestamp()-interval '1 minute',completed_at=clock_timestamp(),verification_status='passed' WHERE id=$1", first)
        projected = await value(owner, "SELECT ops.feishu_source($1,'task',$2)", f['sync'], first)
        assert projected['owner_id'] == str(f['peer']) and projected['owner_name'] == 'PEER'
        assert projected['original_owner_id'] == str(f['member']) and projected['original_owner_name'] == 'MEMBER'
        assert projected['original_dispatcher_id'] == str(f['creator'])
        assert projected['batch_id'] == str(bid) and projected['source_code'] == 'meeting_import'
        assert projected['accepted_at'] and projected['completion_submitted_at'] and projected['completed_at']
        assert projected['verification_status'] == 'passed' and projected['association_kind'] == 'daily'
        assert not {'source_text', 'items', 'editable_items', 'confirmed_item', 'parse_warnings'} & projected.keys()
        assert 'PRIVATE source' not in json.dumps(projected)
        assert await value(owner, "SELECT ops.feishu_source($1,'task',$2)", other['sync'], first) == {'id': str(first), 'deleted': True}
        checks.append('feishu_progress_uses_current_owner_and_frozen_original_without_meeting_source')
        audit = await value(owner, "SELECT jsonb_agg(to_jsonb(a)) FROM ops.audit_log a WHERE object_type IN ('task_import_batch','task_import_item') AND object_id=$1", bid)
        assert audit and any(a['action_code'] == 'workflow.task_import_batch.update' for a in audit)
        for event in audit:
            for field in ('before_snapshot', 'after_snapshot'):
                snapshot = event[field]
                if snapshot:
                    assert not {'source_text', 'items', 'editable_items', 'confirmed_item', 'parse_warnings'} & snapshot.keys()
                    assert len(snapshot['content_hash']) == 64
        assert 'PRIVATE source' not in json.dumps(audit)
        checks.append('draft_edits_dispatch_and_links_audited_without_source_or_payload_text')

        # Confirmation serialization: the same parent lock blocks a competing
        # writer; when released it observes confirmed and cannot alter the batch.
        await context(app, f)
        cbid, citems = await draft(app, f)
        competing = await asyncpg.connect(database=database, **config)
        connections.append(competing)
        await competing.execute(f'SET ROLE "{api}"')
        await context(competing, f)
        tx = app.transaction()
        await tx.start()
        await app.fetchrow('SELECT id FROM workflow.task_import_batch WHERE id=$1 FOR UPDATE', cbid)
        attempt = asyncio.create_task(competing.fetchval('SELECT status FROM workflow.task_import_batch WHERE id=$1 FOR UPDATE', cbid))
        try:
            # A separate observer checks the wait, not an arbitrary sleep.
            for _ in range(100):
                blocked = await owner.fetchval("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=$1 AND query LIKE 'SELECT status FROM workflow.task_import_batch%' AND wait_event_type='Lock')", database)
                if blocked:
                    break
                await asyncio.sleep(.01)
            else:
                raise AssertionError('Competing confirmation did not wait on batch')
            tid = await task(app, f)
            await link(app, f, cbid, citems[0], tid)
            await confirm(app, cbid)
            await tx.commit()
            # The creator can still lock a confirmed row for idempotent receipt
            # lookup; the immutable guard, not RLS, prevents changing it again.
            assert await attempt == 'confirmed'
        finally:
            if not attempt.done():
                attempt.cancel()
                await asyncio.gather(attempt, return_exceptions=True)
        assert await owner.fetchval('SELECT count(*) FROM workflow.task_import_item WHERE batch_id=$1', cbid) == 1
        checks.append('concurrent_confirmation_parent_lock_cannot_duplicate_dispatch')
        # Header-only pages do not expand all historical task IDs/payloads. The
        # caller fetches one extra row (maximum 51) to determine has_more.
        await draft(app, f)
        await draft(app, f)
        page_one = await value(app, 'SELECT security.task_import_batch_page(NULL,2,0)')
        page_two = await value(app, 'SELECT security.task_import_batch_page(NULL,2,2)')
        assert len(page_one) == len(page_two) == 2
        assert not {r['id'] for r in page_one} & {r['id'] for r in page_two}
        assert all(row['status'] == 'draft' for row in page_one)
        for row in page_one + page_two:
            assert not {'source_text', 'items', 'editable_items', 'source_hash', 'task_ids', 'task_items', 'total_count'} & row.keys()
        assert len(await value(app, "SELECT security.task_import_batch_page('draft',51,0)")) == 2
        for status, limit, offset in (('unknown', 2, 0), (None, 52, 0), (None, 0, 0), (None, 2, -1), (None, None, 0)):
            await rejected(app, 'SELECT security.task_import_batch_page($1,$2,$3)', status, limit, offset, codes=('22023',))
        await context(app, f, 'manager')
        assert await value(app, "SELECT security.task_import_batch_page('draft',51,0)") == []
        assert len(await value(app, 'SELECT security.task_import_batch_page(NULL,51,0)')) == 2
        await context(app, other, 'manager')
        assert await value(app, 'SELECT security.task_import_batch_page(NULL,51,0)') == []
        await context(app, f)
        checks.append('bounded_header_pages_preserve_draft_and_task_visibility_without_expanding_task_ids')
        for code in ('agent_execution.task_import', 'agent_business.task_import', 'opportunity_amount', 'agent_business.visit_quality'):
            assert await owner.fetchval('SELECT security.company_rule_supported($1)', code)
        assert not await owner.fetchval("SELECT security.company_rule_supported('agent_execution.unreviewed')")
        checks.append('task_import_company_policy_codes_added_without_broadening_other_capabilities')

        await base_runtime_grants(owner, late)
        roles.append(late)
        await owner.execute(f'GRANT EXECUTE ON FUNCTION security.save_account_authorization(uuid,jsonb) TO "{late}"')
        assert all(row['status'] == 'unchanged' for row in await migrate(owner))
        # Since V246, a historical marker function does not identify a newly
        # created API role. Only explicit reviewed service provisioning may
        # supply its privileges; rerunning migrations must not widen them.
        assert not await owner.fetchval("SELECT has_table_privilege($1,'workflow.task_import_batch','UPDATE')", late)
        assert not await owner.fetchval("SELECT has_function_privilege($1,'security.task_import_batch_summary(uuid)','EXECUTE')", late)
        assert not await owner.fetchval("SELECT has_function_privilege($1,'security.task_import_batch_page(text,integer,integer)','EXECUTE')", late)
        policy = load_policy()
        password = await owner.fetchval('SELECT quote_literal($1::text)', secrets.token_urlsafe(40))
        await owner.execute(f'ALTER ROLE "{late}" LOGIN PASSWORD {password}')
        await owner.execute(f'GRANT CONNECT ON DATABASE "{database}" TO "{late}"')
        async with owner.transaction():
            await reconcile_service_roles(owner, {'api': late}, policy)
        assert await owner.fetchval("SELECT has_table_privilege($1,'workflow.task_import_batch','UPDATE')", late)
        assert await owner.fetchval("SELECT has_function_privilege($1,'security.task_import_batch_summary(uuid)','EXECUTE')", late)
        assert await owner.fetchval("SELECT has_function_privilege($1,'security.task_import_batch_page(text,integer,integer)','EXECUTE')", late)
        assert all(row['status'] == 'unchanged' for row in await migrate(owner))
        await verify_service_roles(owner, {'api': late}, policy)
        async with owner.transaction():
            await reconcile_service_roles(owner, {'api': late}, policy)
        checks.append('late_api_requires_explicit_exact_policy_and_repeat_migration_is_idempotent')
        # The current V250 schema is intentionally outside the reviewed reset
        # catalog. Refuse before disabling guards or clearing any business data;
        # a future catalog change needs its own explicit acceptance.
        from demo_reset_catalog import CLEARED, RETAINED, structure, validate_structure
        from demo_reset import sequence_counters
        from instance_backup import inventory
        from instance_files import InstanceError
        await context(app, f)
        await draft(app, f)
        await owner.execute("INSERT INTO ops.job(workspace_id,job_type,payload) VALUES($1,'synthetic-reset-refusal','{}')", f['ws'])
        before_structure = await structure(owner)
        expected_unreviewed = {
            'crm.opportunity_state_event', 'crm.opportunity_forecast_event',
            'crm.review_key_result', 'crm.review_key_result_progress', 'crm.review_metric',
            'crm.review_metric_history', 'crm.review_objective', 'crm.review_okr_history',
            'workflow.task_alignment', 'workflow.task_alignment_history',
            'workflow.task_key_result_alignment', 'workflow.task_metric_alignment', 'workflow.task_target_alignment',
        }
        assert set(before_structure['tables']) - CLEARED - RETAINED == expected_unreviewed
        async with owner.transaction():
            before = await inventory(owner)
            counters = await sequence_counters(owner, before['sequences'])
        # Nonempty fixtures make the no-change assertions cover both tenants,
        # original task source text, confirmed assignments and captured evidence.
        for table in ('workflow.task_import_batch', 'workflow.task_import_item',
                      'workflow.task', 'ops.audit_log', 'ops.job'):
            assert before['tables'][table]['rows'] > 0, table
        catalog_path = Path(__file__).resolve().parents[2] / 'tools/installation/demo_reset_catalog.json'
        try:
            validate_structure(before_structure, json.loads(catalog_path.read_text())['structure'])
        except InstanceError as error:
            assert str(error) == 'Unreviewed business tables; reset refused'
        else:
            raise AssertionError('Current unreviewed schema unexpectedly accepted for reset')
        async with owner.transaction():
            assert await inventory(owner) == before
            assert await sequence_counters(owner, before['sequences']) == counters
        assert await structure(owner) == before_structure
        checks.append('v250_reset_refused_before_writes_preserves_all_tenant_sources_queues_history_and_guards')
        if evidence_path:
            # Schema metadata only, for the parent task's reviewed installation
            # fingerprint/catalog. No task, account, credential or source rows.
            from runtime_roles import SCHEMAS
            tables = [dict(r) for r in await owner.fetch('''SELECT n.nspname||'.'||c.relname AS name,c.relrowsecurity,c.relforcerowsecurity
              FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=ANY($1::text[]) AND c.relkind IN ('r','p') ORDER BY 1''', SCHEMAS)]
            policies = [dict(r) for r in await owner.fetch('''SELECT schemaname,tablename,policyname,permissive,roles::text[],cmd,qual,with_check
              FROM pg_policies WHERE schemaname=ANY($1::text[]) ORDER BY 1,2,3''', SCHEMAS)]
            Path(evidence_path).write_text(json.dumps({'demo_reset_structure': await structure(owner),
                'rls_tables': tables, 'rls_policies': policies}, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({'passed': len(checks), 'checks': checks, 'production_data': False, 'external_calls': False}, indent=2))
    finally:
        for connection in reversed(connections):
            await connection.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
        for role in reversed(roles):
            await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        await admin.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pg-bin', default='/opt/homebrew/opt/postgresql@16/bin')
    parser.add_argument('--port', type=int)
    parser.add_argument('--user', default='postgres')
    parser.add_argument('--schema-evidence', help='Optional file containing schema metadata only, for installation review')
    args = parser.parse_args()
    if args.port:
        if args.port == 15432 or not 1 <= args.port <= 65535:
            raise ValueError('Do not use the acceptance database server')
        asyncio.run(verify(dict(host='127.0.0.1', port=args.port, user=args.user), args.schema_evidence))
        return
    with tempfile.TemporaryDirectory(prefix='sb-task-import-', dir='/tmp') as temporary:
        root, binary = Path(temporary), Path(args.pg_bin)
        data, socket = root / 'data', root / 'socket'
        socket.mkdir()
        subprocess.run([str(binary / 'initdb'), '-D', str(data), '-U', getpass.getuser(), '-A', 'trust', '--no-locale', '--encoding=UTF8'], check=True, capture_output=True)
        subprocess.run([str(binary / 'pg_ctl'), '-D', str(data), '-l', str(root / 'postgres.log'), '-o', f"-k {socket} -c listen_addresses=''", '-w', 'start'], check=True, capture_output=True)
        try:
            asyncio.run(verify(dict(host=str(socket), user=getpass.getuser()), args.schema_evidence))
        finally:
            subprocess.run([str(binary / 'pg_ctl'), '-D', str(data), '-m', 'immediate', '-w', 'stop'], check=True, capture_output=True)


if __name__ == '__main__':
    main()
