"""Exercise private historical provenance with the actual new API database login."""
import uuid

import asyncpg

from bootstrap import identity


async def context(connection, account, role, historical):
    prefix = account[0]
    await connection.execute("""SELECT set_config('app.workspace_id',$1,false),
        set_config('app.user_ref_id',$2,false),set_config('app.role_code',$3,false),
        set_config('app.team_ids',$4,false),set_config('app.feishu_historical_import',$5,false)""",
        str(identity(prefix)), str(identity(account)), role, str(identity(prefix+'-sales')), historical)


async def verify_history_policies(owner, config, database, role, password):
    checks = []
    fixtures = []
    for prefix in ('A', 'B'):
        batch = await owner.fetchval("""INSERT INTO ops.crm_import_batch(workspace_id,source_system,
            source_base_id,manifest_sha256,status,source_snapshot_at,created_by_user_ref_id)
            VALUES($1,'synthetic','local-only',repeat('a',64),'approved',clock_timestamp(),$2) RETURNING id""",
            identity(prefix), identity(prefix+'ADMIN'))
        for kind in ('opportunity', 'visit', 'period_actual_snapshot'):
            target = uuid.uuid4()
            record = await owner.fetchval("""INSERT INTO ops.crm_import_record(workspace_id,batch_id,
                source_table_id,source_record_id,object_kind,source_sha256,raw_fields,status,target_id)
                VALUES($1,$2,'synthetic',$3,$3,repeat('b',64),'{}','approved',$4) RETURNING id""",
                identity(prefix), batch, kind, target)
            fixtures.append((prefix, kind, target, record, batch))
    runtime = await asyncpg.connect(database=database, host=config['host'], port=config['port'],
                                    user=role, password=password)
    sql = 'SELECT security.authorization_history_provenance($1,$2,$3,$4)'
    try:
        for table in ('ops.crm_import_batch', 'ops.crm_import_record', 'ops.crm_import_binding'):
            try:
                await runtime.fetch(f'SELECT * FROM {table}')
            except asyncpg.InsufficientPrivilegeError:
                pass
            else:
                raise AssertionError('Private history table became readable')
        checks.append('private import ledger remains unreadable')
        await context(runtime, 'AADMIN', 'administrator', 'on')
        for prefix, kind, target, record, batch in fixtures:
            assert bool(await runtime.fetchval(sql, kind, target, str(record), str(batch))) == (prefix == 'A')
            for changed in ((kind, uuid.uuid4(), str(record), str(batch)),
                            (kind, target, str(uuid.uuid4()), str(batch)),
                            (kind, target, str(record), str(uuid.uuid4())),
                            (kind, target, 'invalid-uuid', str(batch)),
                            ('invalid-kind', target, str(record), str(batch))):
                assert not await runtime.fetchval(sql, *changed)
        checks.append('exact approved target/record/batch required; cross-company and malformed IDs rejected')
        a = fixtures[0]
        args = (a[1], a[2], str(a[3]), str(a[4]))
        await owner.execute("UPDATE ops.crm_import_record SET status='rejected' WHERE id=$1", a[3])
        assert not await runtime.fetchval(sql, *args)
        await owner.execute("UPDATE ops.crm_import_record SET status='approved' WHERE id=$1", a[3])
        await owner.execute("UPDATE ops.crm_import_batch SET status='cancelled' WHERE id=$1", a[4])
        assert not await runtime.fetchval(sql, *args)
        await owner.execute("UPDATE ops.crm_import_batch SET status='approved' WHERE id=$1", a[4])
        await context(runtime, 'AADMIN', 'administrator', 'off')
        assert not await runtime.fetchval(sql, *args)
        await context(runtime, 'ASALES', 'sales', 'on')
        assert not await runtime.fetchval(sql, *args)
        checks.append('cancelled/rejected imports, missing import flag and sales role remain denied')
        await context(runtime, 'ASALES', 'sales', 'off')
        form = await owner.fetchval("SELECT id FROM config.form_version WHERE status='active' ORDER BY version_no DESC LIMIT 1")
        transaction = runtime.transaction()
        await transaction.start()
        try:
            visit = await runtime.fetchval("""INSERT INTO activity.visit(workspace_id,customer_id,
                recorder_user_ref_id,recorder_team_id,created_by_user_ref_id,form_version_id,status,follow_up_record)
                VALUES($1,$2,$3,$4,$3,$5,'pending_confirm','本地权限回归合成记录') RETURNING id""",
                identity('A'), identity('A-customer'), identity('ASALES'), identity('A-sales'), form)
            assert visit
        finally:
            await transaction.rollback()
        checks.append('normal visit insert succeeds without import table privileges; test rolled back')
    finally:
        await runtime.close()
    return checks
