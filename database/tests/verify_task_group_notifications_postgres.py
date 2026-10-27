"""V194 -> V195 group notification routing using a private PostgreSQL 16 cluster.

Only random databases and synthetic records are used; remote calls are replaced
by a recording Feishu adapter while the worker uses its actual restricted role.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
from verify_runtime_grants_postgres import migrate_through
from verify_task_import_postgres import (
    confirm,
    context,
    draft,
    fixture,
    link,
    rejected,
    task,
    value,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend' / 'src'))
from sales_backend.api.task_imports import task_dispatch_notification_origin
from sales_backend.domain.feishu_sync.config import SyncConfig
from sales_backend.repositories.feishu_sync import FeishuRepository
from sales_backend.services.feishu_sync import FeishuSyncService


def settings(f, enabled=False):
    return SyncConfig.model_validate({
        'workspace_id': str(f['ws']), 'connection_id': str(f['sync']), 'revision': 1,
        'enabled': True, 'app_id': 'cli_fixture', 'credential_ref': str(uuid4()),
        'base_token': 'fixtureBase', 'base_url': 'https://fixture.feishu.cn/base/fixtureBase',
        'mappings': {'task': {'enabled': True, 'table_id': 'tblTask', 'id_field_id': 'fldSystem',
                              'fields': {'title': 'fldTitle', 'record_status': 'fldStatus'}}},
        'notification': {'enabled': True, 'default_chat_id': 'oc_default', 'routing_mode': 'department_routes',
            'on_create': ['customer', 'opportunity', 'visit'] + (['task'] if enabled else []),
            'routes': [
                {'department_id': str(f['team']), 'chat_ids': ['oc_creator']},
                {'department_id': str(f['recipient_team']), 'chat_ids': ['oc_recipient', 'oc_shared']},
                {'department_id': str(f['peer_team']), 'chat_ids': ['oc_peer', 'oc_shared']},
            ]},
    })


async def configure(connection, f, config):
    await connection.execute('''UPDATE config.feishu_connection SET settings=$2::jsonb,
      enabled=true,validated_revision=revision WHERE id=$1''', f['sync'], config.model_dump_json())


async def groups(connection, f):
    for key, name in [('recipient_team', 'RECIPIENT'), ('peer_team', 'PEER')]:
        f[key] = uuid4()
        await connection.execute('''INSERT INTO platform.team(id,workspace_id,code,name)
          VALUES($1,$2,$3,$3)''', f[key], f['ws'], name)
    for person, team in [('member', 'recipient_team'), ('peer', 'peer_team')]:
        await connection.execute('DELETE FROM platform.team_membership WHERE workspace_id=$1 AND user_ref_id=$2',
                                 f['ws'], f[person])
        await connection.execute('''INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role)
          VALUES($1,$2,$3,'sales')''', f['ws'], f[person], f[team])


async def source(connection, f, tid):
    return await value(connection, "SELECT ops.feishu_source($1,'task',$2)", f['sync'], tid)


async def first_event(connection, f, tid):
    return dict(await connection.fetchrow('''SELECT * FROM ops.feishu_event WHERE connection_id=$1
      AND object_kind='task' AND object_id=$2 AND first_formal_create ORDER BY sequence_no LIMIT 1''', f['sync'], tid))


async def weekly_task(connection, f, *, role='administrator', channel='web'):
    identity = SimpleNamespace(actor=SimpleNamespace(role=SimpleNamespace(value=role)),
                               client_channel=channel, auth_method='password')
    async with connection.transaction():
        await connection.execute("SELECT set_config('app.role_code',$1,true)", role)
        batch, items = await draft(connection, f)
        async with task_dispatch_notification_origin(connection, identity):
            tid = await task(connection, f, 'member')
            await link(connection, f, batch, items[0], tid, 'member')
            assert await confirm(connection, batch) == 'UPDATE 1'
        assert await connection.fetchval("SELECT current_setting('app.task_group_notification_origin',true)") == ''
    return tid


class Recorder:
    def __init__(self):
        self.cards, self.records = [], {}
        self.on_update = None

    async def close(self):
        pass

    async def fields(self, base, table):
        return {key: {'field_name': name, 'type': 1} for key, name in
                [('fldSystem', 'system'), ('fldTitle', 'title'), ('fldStatus', 'record_status')]}

    async def find_record(self, base, table, id_field, identity):
        return self.records.get(identity)

    async def create_record(self, base, table, fields, key):
        record = 'rec' + fields['system'].replace('-', '')
        self.records[fields['system']] = record
        if self.on_update:
            await self.on_update()
        return record

    async def update_record(self, base, table, record, fields):
        if self.on_update:
            await self.on_update()

    async def send_card(self, chat, card, key):
        self.cards.append((chat, card, key))
        return 'om' + str(len(self.cards))


async def run_event(owner, worker, f, tid, config, recorder):
    event = await first_event(owner, f, tid)
    await owner.execute("""UPDATE ops.feishu_event SET status='running',lease_token=$2,
      locked_until=clock_timestamp()+interval '2 minutes',attempts=attempts+1 WHERE id=$1""",
      event['id'], uuid4())
    event = await first_event(owner, f, tid)
    service = FeishuSyncService(None)
    service.client = lambda row: (config, recorder)
    await service.handle(worker, event, {})
    await FeishuRepository().finish(worker, event)
    return event


async def verify(config):
    database = 'sb_task_groups_' + uuid4().hex[:12]
    admin = await asyncpg.connect(database='postgres', **config)
    connections, checks = [], []
    try:
        await admin.execute(f'CREATE DATABASE "{database}" TEMPLATE template0 ENCODING \'UTF8\'')
        owner = await asyncpg.connect(database=database, **config)
        connections.append(owner)
        await migrate_through(owner, 194)
        f, foreign = await fixture(owner), await fixture(owner)
        await groups(owner, f)
        await groups(owner, foreign)
        await context(owner, f)
        off, on = settings(f), settings(f, True)
        await configure(owner, f, off)
        # Legacy events were eligible under the old general notification switch.
        old = []
        for status in ['pending', 'failed', 'dead_letter', 'succeeded', 'running']:
            tid = await task(owner, f, 'member')
            row = await first_event(owner, f, tid)
            assert row['historical'] is False
            await owner.execute('''UPDATE ops.feishu_event SET status=$2,attempts=3,
              lease_token=$3,locked_until=clock_timestamp()+interval '1 minute' WHERE id=$1''',
              row['id'], status, uuid4())
            old.append(await first_event(owner, f, tid))
        planned_tid = await task(owner, f)
        planned = await first_event(owner, f, planned_tid)
        await owner.execute('UPDATE ops.feishu_event SET notification_planned=true WHERE id=$1', planned['id'])
        planned = await first_event(owner, f, planned_tid)
        tasks_before = await value(owner, 'SELECT jsonb_agg(to_jsonb(t) ORDER BY id) FROM workflow.task t')
        connection_before = await value(owner, 'SELECT to_jsonb(c) FROM config.feishu_connection c WHERE id=$1', f['sync'])
        unrelated_before = await value(owner, "SELECT jsonb_agg(to_jsonb(e) ORDER BY id) FROM ops.feishu_event e WHERE object_kind<>'task'")
        customer_before = await value(owner, "SELECT ops.feishu_source($1,'customer',$2)", f['sync'], f['customer'])
        signatures = ['ops.feishu_source(uuid,text,uuid)', 'ops.capture_feishu_event()']
        functions_before = [dict(await owner.fetchrow(
            'SELECT oid,proowner,proacl::text FROM pg_proc WHERE oid=$1::regprocedure', name)) for name in signatures]
        await migrate_through(owner, 195)
        assert tasks_before == await value(owner, 'SELECT jsonb_agg(to_jsonb(t) ORDER BY id) FROM workflow.task t')
        assert connection_before == await value(owner, 'SELECT to_jsonb(c) FROM config.feishu_connection c WHERE id=$1', f['sync'])
        assert unrelated_before == await value(owner, "SELECT jsonb_agg(to_jsonb(e) ORDER BY id) FROM ops.feishu_event e WHERE object_kind<>'task'")
        assert customer_before == await value(owner, "SELECT ops.feishu_source($1,'customer',$2)", f['sync'], f['customer'])
        for signature, before in zip(signatures, functions_before, strict=True):
            assert before == dict(await owner.fetchrow(
                'SELECT oid,proowner,proacl::text FROM pg_proc WHERE oid=$1::regprocedure', signature))
        for before in old:
            after = dict(await owner.fetchrow('SELECT * FROM ops.feishu_event WHERE id=$1', before['id']))
            assert after == {**before, 'historical': True}
        assert await first_event(owner, f, planned_tid) == planned
        checks.append('upgrade_changes_only_legacy_unplanned_task_notification_flag_preserves_tasks_configs_other_events_oid_owner_acl')

        worker = await asyncpg.connect(database=database, **config)
        connections.append(worker)
        await worker.execute('SET ROLE salegent_feishu_worker')
        await worker.set_type_codec('jsonb', schema='pg_catalog', encoder=json.dumps, decoder=json.loads)
        await rejected(worker, 'SELECT * FROM workflow.task', codes=('42501',))
        await rejected(worker, 'SELECT * FROM workflow.task_candidate', codes=('42501',))
        # Preserve a deliberately foreign pre-existing context inside the same
        # transaction, not merely by relying on autocommit to reset LOCAL GUCs.
        async with worker.transaction():
            await worker.execute("SELECT set_config('app.workspace_id',$1,false)", str(foreign['ws']))
            current = await source(worker, f, old[0]['object_id'])
            assert current['notification_recipient_ids'] == [str(f['member'])]
            assert current['notification_recipient_names'] == ['MEMBER']
            assert current['notification_department_ids'] == [str(f['recipient_team'])]
            assert await worker.fetchval("SELECT current_setting('app.workspace_id')") == str(foreign['ws'])
            assert await source(worker, foreign, old[0]['object_id']) == {'id': str(old[0]['object_id']), 'deleted': True}
        assert not {'source_text', 'items', 'editable_items'} & current.keys()
        checks.append('restricted_worker_scoped_projection_recipient_groups_and_foreign_context_restoration_no_direct_task_access')

        no_notice = await weekly_task(owner, f)
        assert (await first_event(owner, f, no_notice))['historical'] is True
        await configure(owner, f, on)
        assert (await first_event(owner, f, no_notice))['historical'] is True
        live = await weekly_task(owner, f)
        assert (await first_event(owner, f, live))['historical'] is False
        silent_tasks = []
        for role, channel in [('administrator', 'wechat-mini-program'), ('sales', 'web'),
                              ('sales', 'wechat-mini-program')]:
            tid = await weekly_task(owner, f, role=role, channel=channel)
            assert (await first_event(owner, f, tid))['historical'] is True
            silent_tasks.append(tid)
        # Even a task written under the trusted origin must have the weekly
        # import source and a committed confirmed-batch link before it can send.
        async with owner.transaction():
            await owner.execute("SELECT set_config('app.role_code','administrator',true),"
                                "set_config('app.task_group_notification_origin','admin_weekly_dispatch',true)")
            unlinked = await task(owner, f, 'member')
            assert (await first_event(owner, f, unlinked))['historical'] is False
            for source_code in ('mini_program', 'business_advice'):
                tid = await owner.fetchval('''INSERT INTO workflow.task(workspace_id,title,description,creator_user_ref_id,
                  creator_team_id,due_at,association_kind,source_code) VALUES($1,'Direct task','Synthetic direct task',
                  $2,$3,clock_timestamp()+interval '1 day','daily',$4) RETURNING id''',
                  f['ws'], f['creator'], f['team'], source_code)
                assert (await first_event(owner, f, tid))['historical'] is True
                silent_tasks.append(tid)
        silent_tasks.append(unlinked)
        failed_task = None
        try:
            async with owner.transaction():
                await owner.execute("SELECT set_config('app.role_code','administrator',true)")
                who = SimpleNamespace(actor=SimpleNamespace(role=SimpleNamespace(value='administrator')),
                                      client_channel='web', auth_method='password')
                async with task_dispatch_notification_origin(owner, who):
                    failed_task = await task(owner, f, 'member')
                    await owner.fetchval('SELECT 1/0')
        except asyncpg.DivisionByZeroError:
            pass
        else:
            raise AssertionError('Failed dispatch unexpectedly committed')
        assert await owner.fetchval('SELECT count(*) FROM workflow.task WHERE id=$1', failed_task) == 0
        assert await owner.fetchval("SELECT current_setting('app.task_group_notification_origin',true)") == ''
        after_failure = await task(owner, f, 'member')
        assert (await first_event(owner, f, after_failure))['historical'] is True
        silent_tasks.append(after_failure)
        checks.append('failed_dispatch_preserves_original_sql_error_rolls_back_task_and_origin_never_leaks_to_next_task')
        customer = await owner.fetchval('''INSERT INTO crm.customer(workspace_id,name,normalized_name,
          created_by_user_ref_id,owner_user_ref_id,owner_team_id) VALUES($1,'New synthetic','new synthetic',$2,$2,$3)
          RETURNING id''', f['ws'], f['creator'], f['team'])
        assert await owner.fetchval("SELECT historical FROM ops.feishu_event WHERE object_id=$1 AND object_kind='customer'", customer) is False
        before_count = await owner.fetchval("SELECT count(*) FROM ops.feishu_event WHERE object_kind='task'")
        await draft(owner, f)
        assert await owner.fetchval("SELECT count(*) FROM ops.feishu_event WHERE object_kind='task'") == before_count
        checks.append('only_admin_console_weekly_dispatch_eligible_member_mini_direct_and_unconfirmed_sources_remain_silent')

        # Current departments, not the creator's or old assignment snapshot.
        await owner.execute('DELETE FROM platform.team_membership WHERE user_ref_id=$1 AND workspace_id=$2', f['member'], f['ws'])
        await owner.execute('''INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role)
          VALUES($1,$2,$3,'sales')''', f['ws'], f['member'], f['peer_team'])
        assert (await source(worker, f, live))['notification_department_ids'] == [str(f['peer_team'])]
        await owner.execute("UPDATE platform.user_ref SET status='inactive' WHERE id=$1", f['member'])
        assert (await source(worker, f, live))['notification_recipient_ids'] == []
        await owner.execute("UPDATE platform.user_ref SET status='active' WHERE id=$1", f['member'])
        await owner.execute('UPDATE platform.team_membership SET team_id=$2 WHERE user_ref_id=$1 AND workspace_id=$3',
                            f['member'], f['recipient_team'], f['ws'])
        checks.append('current_recipient_department_change_survives_old_assignment_and_disabled_account_is_excluded')

        position = await owner.fetchval('''INSERT INTO workflow.task(workspace_id,title,description,creator_user_ref_id,
          creator_team_id,due_at,association_kind,source_code,target_position)
          VALUES($1,'Position task','Synthetic candidate task',$2,$3,clock_timestamp()+interval '1 day',
          'daily','mini_program','self') RETURNING id''', f['ws'], f['creator'], f['team'])
        for person, team, role in [('member', 'recipient_team', 'sales'), ('peer', 'peer_team', 'sales'),
                                   ('creator', 'team', 'supervisor')]:
            await owner.execute('''INSERT INTO workflow.task_candidate(workspace_id,task_id,user_ref_id,team_id,role_code)
              VALUES($1,$2,$3,$4,$5)''', f['ws'], position, f[person], f[team], role)
        # Legacy single-column person/team FKs can hold a mismatched tenant; the
        # projection must still fail closed independently of those old FKs.
        await owner.execute('''INSERT INTO workflow.task_candidate(workspace_id,task_id,user_ref_id,team_id,role_code)
          VALUES($1,$2,$3,$4,'sales')''', f['ws'], position, foreign['member'], foreign['team'])
        projected = await source(worker, f, position)
        assert set(projected['notification_recipient_ids']) == {str(f['member']), str(f['peer'])}
        assert set(projected['notification_department_ids']) == {str(f['recipient_team']), str(f['peer_team'])}
        assert set(projected['notification_recipient_names']) == {'MEMBER', 'PEER'}
        assert projected['owner_id'] is None and projected['target_position'] == 'self'
        await owner.execute("UPDATE workflow.task_candidate SET decision='declined',decided_at=clock_timestamp() WHERE task_id=$1 AND user_ref_id=$2", position, f['peer'])
        assert (await source(worker, f, position))['notification_recipient_ids'] == [str(f['member'])]
        await owner.execute("UPDATE workflow.task_candidate SET decision='pending',decided_at=NULL WHERE task_id=$1 AND user_ref_id=$2", position, f['peer'])
        checks.append('position_task_filters_wrong_role_declined_cross_tenant_candidates_routes_all_valid_recipient_groups')

        recorder = Recorder()
        await run_event(owner, worker, f, live, on, recorder)
        assert {row[0] for row in recorder.cards} == {'oc_recipient', 'oc_shared'}
        assert all(row[1]['header']['title']['content'] == '新增任务' for row in recorder.cards)
        assert await owner.fetchval("SELECT count(*) FROM ops.feishu_delivery WHERE event_id=$1 AND status='sent'",
                                    (await first_event(owner, f, live))['id']) == 2
        await run_event(owner, worker, f, live, on, recorder)
        assert len(recorder.cards) == 2
        await run_event(owner, worker, f, position, on, recorder)
        assert len(recorder.cards) == 2  # Direct candidate tasks remain visible in V4 without group notices.
        await run_event(owner, worker, f, no_notice, on, recorder)
        for tid in silent_tasks:
            await run_event(owner, worker, f, tid, on, recorder)
        assert len(recorder.cards) == 2
        checks.append('real_restricted_worker_freezes_sends_and_persists_one_receipt_per_task_group_retries_no_duplicates_no_history')

        single = on.model_copy(update={'notification': on.notification.model_copy(update={'routing_mode': 'single_group'})})
        await configure(owner, f, single)
        default_task = await weekly_task(owner, f)
        await run_event(owner, worker, f, default_task, single, recorder)
        assert len(recorder.cards) == 3 and recorder.cards[-1][0] == 'oc_default'
        await run_event(owner, worker, f, default_task, single, recorder)
        assert len(recorder.cards) == 3
        await configure(owner, f, on)
        checks.append('existing_single_default_group_receives_one_weekly_task_card_without_new_groups_or_duplicate_retry')

        quick = await weekly_task(owner, f)
        async def cancel_before_notification():
            await owner.execute("UPDATE workflow.task SET status='cancelled',version_no=version_no+1 WHERE id=$1", quick)
        recorder.on_update = cancel_before_notification
        await run_event(owner, worker, f, quick, on, recorder)
        assert len(recorder.cards) == 3
        rows = await owner.fetch('SELECT status,error_code FROM ops.feishu_delivery WHERE event_id=$1',
                                 (await first_event(owner, f, quick))['id'])
        assert rows and all(row['status'] == 'failed' and row['error_code'] == 'SOURCE_CHANGED_BEFORE_NOTIFICATION' for row in rows)
        checks.append('cancellation_committed_during_remote_sync_blocks_stale_new_task_cards_but_retains_failed_receipts')

        await owner.execute("UPDATE platform.workspace SET status='suspended' WHERE id=$1", f['ws'])
        await rejected(worker, "SELECT ops.feishu_source($1,'task',$2)", f['sync'], live, codes=('42501',))
        assert await worker.fetchval("SELECT current_setting('app.workspace_id')") == str(foreign['ws'])
        checks.append('tenant_suspension_fails_closed_and_worker_context_remains_unchanged')
        result = {'passed': len(checks), 'checks': checks, 'production_data': False, 'external_calls': False}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return result
    finally:
        for connection in reversed(connections):
            await connection.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
        await admin.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pg-bin', default='/opt/homebrew/opt/postgresql@16/bin')
    parser.add_argument('--port', type=int)
    parser.add_argument('--user', default='postgres')
    parser.add_argument('--evidence', type=Path)
    args = parser.parse_args()
    if args.port:
        if args.port == 15432 or not 1 <= args.port <= 65535:
            raise ValueError('Do not use the acceptance database server')
        result = asyncio.run(verify({'host': '127.0.0.1', 'port': args.port, 'user': args.user}))
    else:
        with tempfile.TemporaryDirectory(prefix='sb-task-groups-', dir='/tmp') as temporary:
            root, binary = Path(temporary), Path(args.pg_bin)
            data, socket = root / 'data', root / 'socket'
            socket.mkdir()
            subprocess.run([str(binary / 'initdb'), '-D', str(data), '-U', getpass.getuser(),
                            '-A', 'trust', '--no-locale', '-E', 'UTF8'], check=True, capture_output=True)
            subprocess.run([str(binary / 'pg_ctl'), '-D', str(data), '-l', str(root / 'postgres.log'),
                            '-o', f"-k {socket} -c listen_addresses=''", '-w', 'start'], check=True, capture_output=True)
            try:
                result = asyncio.run(verify({'host': str(socket), 'user': getpass.getuser()}))
            finally:
                subprocess.run([str(binary / 'pg_ctl'), '-D', str(data), '-m', 'immediate', '-w', 'stop'],
                               check=True, capture_output=True)
    if args.evidence:
        args.evidence.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
