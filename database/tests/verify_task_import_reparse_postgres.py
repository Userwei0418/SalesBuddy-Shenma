#!/usr/bin/env python3
"""V193 -> V194 guarded draft reparse in a disposable UTF8 PG16 database."""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
from pathlib import Path
import subprocess
import tempfile
from uuid import uuid4

import asyncpg

from verify_runtime_grants_postgres import base_runtime_grants, migrate_through
from verify_task_import_postgres import confirm, context, draft, fixture, link, rejected, task, value

FUNCTION = 'security.reparse_task_import_batch(uuid,integer,jsonb,jsonb,jsonb)'
SQL = 'SELECT security.reparse_task_import_batch($1,$2,$3::jsonb,$4::jsonb,$5::jsonb)'


def parsed(items):
    sources = [{**item, 'item_id': str(uuid4()), 'description': item['description'] + ' improved'} for item in items]
    editable = [{'item_id': item['item_id'], 'description': item['description'], 'selected': True,
                 'duplicate_acknowledged': False} for item in sources]
    return sources, editable


async def reparse(connection, batch, version, items, editable):
    return await connection.fetchval(SQL, batch, version, json.dumps(items), '[]', json.dumps(editable))


async def verify(config):
    suffix = uuid4().hex[:12]
    database, api = 'sb_reparse_db_' + suffix, 'sb_reparse_api_' + suffix
    historical_owner = 'sb_reparse_owner_' + suffix
    admin = await asyncpg.connect(database='postgres', **config)
    connections, roles, checks = [], [], []
    try:
        await admin.execute(f'CREATE DATABASE "{database}" TEMPLATE template0 ENCODING \'UTF8\'')
        owner = await asyncpg.connect(database=database, **config)
        connections.append(owner)
        await migrate_through(owner, 193)
        data, foreign = await fixture(owner), await fixture(owner)
        await base_runtime_grants(owner, api)
        roles.append(api)
        await owner.execute(f'GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA security,common TO "{api}"')
        await owner.fetchval('SELECT security.reconcile_runtime_grants()')
        app = await asyncpg.connect(database=database, **config)
        connections.append(app)
        await app.execute(f'SET ROLE "{api}"')
        await context(app, data)
        batch, items = await draft(app, data)
        before = await value(owner, 'SELECT to_jsonb(b) FROM workflow.task_import_batch b WHERE id=$1', batch)
        sources, editable = parsed(items)
        await rejected(app, "UPDATE workflow.task_import_batch SET parse_method='ai',items=$2::jsonb,version_no=version_no+1 WHERE id=$1", batch, json.dumps(sources))
        # A deployed CREATE OR REPLACE can retain an older schema owner. Keep
        # that identity separate from the new migration/helper owner in this test.
        await owner.execute(f'CREATE ROLE "{historical_owner}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        roles.append(historical_owner)
        await owner.execute(f'GRANT USAGE ON SCHEMA workflow TO "{historical_owner}"')
        await owner.execute(f'ALTER FUNCTION workflow.guard_task_import_batch() OWNER TO "{historical_owner}"')
        await migrate_through(owner, 194)
        assert await value(owner, 'SELECT to_jsonb(b) FROM workflow.task_import_batch b WHERE id=$1', batch) == before
        assert await owner.fetchval("SELECT pg_get_userbyid(proowner)=current_user FROM pg_proc WHERE oid='workflow.guard_task_import_batch()'::regprocedure")
        assert not await owner.fetchval("SELECT has_function_privilege($1,'workflow.task_import_editable_projection(jsonb,boolean)','EXECUTE')", historical_owner)
        checks.append('upgrade_preserves_existing_draft_and_source')
        checks.append('mixed_historical_guard_owner_migrates_with_private_helper_without_extra_grants')

        assert await owner.fetchval('SELECT has_function_privilege($1,$2,\'EXECUTE\')', api, FUNCTION)
        assert await reparse(app, batch, 1, sources, editable) == batch
        after = await value(owner, 'SELECT to_jsonb(b) FROM workflow.task_import_batch b WHERE id=$1', batch)
        assert after['version_no'] == 2 and after['parse_method'] == 'ai'
        changed = {'items', 'editable_items', 'parse_method', 'parse_warnings', 'version_no', 'updated_at'}
        assert {k: v for k, v in before.items() if k not in changed} == {k: v for k, v in after.items() if k not in changed}
        assert not await reparse(app, batch, 1, sources, editable)
        assert await app.fetchval("SELECT COALESCE(current_setting('app.task_import_reparse',true),'')") != 'true'
        assert await owner.fetchval('SELECT count(*) FROM workflow.task_import_item') == 0
        assert await owner.fetchval('SELECT count(*) FROM workflow.task') == 0
        checks.append('only_parse_fields_change_once_with_expected_version_no_dispatch_and_flag_restored')

        # Normal save remains usable after the scoped flag was restored.
        edited = [dict(row) for row in editable]
        edited[0]['description'] = 'A manually reviewed task description'
        await app.execute('UPDATE workflow.task_import_batch SET editable_items=$2::jsonb,version_no=version_no+1 WHERE id=$1', batch, json.dumps(edited))
        assert not await reparse(app, batch, 3, sources, editable)
        assert (await value(app, 'SELECT editable_items FROM workflow.task_import_batch WHERE id=$1', batch)) == edited
        for field, replacement in [('selected', False), ('duplicate_acknowledged', True),
                                   ('assignee_account_code', 'PEER'), ('due_at', '2026-09-30T23:59:00+08:00'),
                                   ('priority_code', 'high'), ('association_kind', 'customer'),
                                   ('customer_id', str(data['customer'])), ('opportunity_id', str(uuid4()))]:
            candidate, original = await draft(app, data)
            edits = [{'item_id': row['item_id'], 'description': row['description']} for row in original]
            edits[0][field] = replacement
            await app.execute('UPDATE workflow.task_import_batch SET editable_items=$2::jsonb,version_no=version_no+1 WHERE id=$1', candidate, json.dumps(edits))
            assert not await reparse(app, candidate, 2, *parsed(original)), field
        checks.append('all_saved_human_choices_block_reparse_without_overwrite')

        untouched, original = await draft(app, data)
        sources, editable = parsed(original)
        await context(app, data, 'peer')
        assert not await reparse(app, untouched, 1, sources, editable)
        await context(app, foreign)
        assert not await reparse(app, untouched, 1, sources, editable)
        await context(app, data)
        # Even a forged operation flag cannot alter source, authorship, date,
        # state, version, or overwrite a prior reviewed draft.
        async with app.transaction():
            await app.execute("SELECT set_config('app.task_import_reparse','true',true)")
            for expression in ("source_text='Changed original source'", "source_hash=repeat('f',64)",
                               "meeting_date=meeting_date+1", "creator_user_ref_id='" + str(data['peer']) + "'::uuid",
                               'version_no=version_no+2', "parse_method='rules'"):
                changes = [part for part in ("parse_method='ai'", 'version_no=version_no+1')
                           if part.split('=', 1)[0] != expression.split('=', 1)[0]]
                changes.append(expression)
                await rejected(app, 'UPDATE workflow.task_import_batch SET ' + ','.join(changes)
                               + ' WHERE id=$1', untouched)
            await rejected(app, "UPDATE workflow.task_import_batch SET parse_method='ai',version_no=version_no+1 WHERE id=$1", batch)
        await context(owner, data, 'peer')
        async with owner.transaction():
            await owner.execute("SELECT set_config('app.task_import_reparse','true',true)")
            await rejected(owner, "UPDATE workflow.task_import_batch SET parse_method='ai',version_no=version_no+1 WHERE id=$1", untouched)
        checks.append('cross_actor_cross_tenant_and_forged_flag_cannot_mutate_original_or_reviewed_content')

        await rejected(app, "UPDATE workflow.task_import_batch SET parse_method='ai',version_no=version_no+1 WHERE id=$1", untouched)
        await rejected(app, SQL, untouched, 1, '[]', '[]', '[]', codes=('22023',))
        await rejected(app, SQL, untouched, 1, json.dumps(sources), '[]', json.dumps([dict(editable[0], selected=False), editable[1]]))
        assert not await reparse(app, untouched, 99, sources, editable)
        checks.append('normal_save_cannot_replace_parse_and_invalid_payload_or_version_is_rejected')

        async with app.transaction():
            tid = await task(app, data)
            await link(app, data, untouched, original[0], tid)
            assert not await reparse(app, untouched, 1, sources, editable)
            assert await confirm(app, untouched) == 'UPDATE 1'
        assert not await reparse(app, untouched, 2, sources, editable)
        checks.append('linked_and_confirmed_batches_cannot_be_reparsed')
        for signature in (FUNCTION, 'workflow.task_import_editable_projection(jsonb,boolean)'):
            assert not await owner.fetchval('SELECT has_function_privilege(\'salegent_feishu_worker\',$1,\'EXECUTE\')', signature)
            assert not await owner.fetchval("SELECT EXISTS(SELECT 1 FROM pg_proc p CROSS JOIN LATERAL aclexplode(p.proacl) a WHERE p.oid=$1::regprocedure AND a.grantee=0)", signature)
        assert not await owner.fetchval("SELECT has_function_privilege($1,'workflow.task_import_editable_projection(jsonb,boolean)','EXECUTE')", api)
        checks.append('private_projection_public_and_feishu_permissions_remain_closed')
        late = 'sb_reparse_late_' + suffix
        await owner.execute(f'CREATE ROLE "{late}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        roles.append(late)
        await owner.execute(f'GRANT USAGE ON SCHEMA workflow,security TO "{late}"')
        await owner.execute(f'GRANT INSERT,UPDATE ON workflow.task_import_batch TO "{late}"')
        assert not await owner.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", late, FUNCTION)
        await owner.fetchval('SELECT security.reconcile_runtime_grants()')
        assert await owner.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", late, FUNCTION)
        for signature in (FUNCTION, 'security.finish_inference_operation(uuid,text,jsonb,jsonb,text)'):
            assert not await owner.fetchval("SELECT has_function_privilege('pg_write_all_data',$1,'EXECUTE')", signature)
        checks.append('explicit_maintenance_reconciles_late_runtime_without_granting_new_calls_to_builtin_roles')
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
    args = parser.parse_args()
    if args.port:
        if args.port == 15432 or not 1 <= args.port <= 65535:
            raise ValueError('Do not use the acceptance database server')
        asyncio.run(verify(dict(host='127.0.0.1', port=args.port, user=args.user)))
        return
    with tempfile.TemporaryDirectory(prefix='sb-task-reparse-', dir='/tmp') as temporary:
        root, binary = Path(temporary), Path(args.pg_bin)
        data, socket = root / 'data', root / 'socket'
        socket.mkdir()
        subprocess.run([str(binary / 'initdb'), '-D', str(data), '-U', getpass.getuser(), '-A', 'trust', '--no-locale', '-E', 'UTF8'], check=True, capture_output=True)
        subprocess.run([str(binary / 'pg_ctl'), '-D', str(data), '-l', str(root / 'postgres.log'), '-o', f"-k {socket} -c listen_addresses=''", '-w', 'start'], check=True, capture_output=True)
        try:
            asyncio.run(verify(dict(host=str(socket), user=getpass.getuser())))
        finally:
            subprocess.run([str(binary / 'pg_ctl'), '-D', str(data), '-m', 'immediate', '-w', 'stop'], check=True, capture_output=True)


if __name__ == '__main__':
    main()
