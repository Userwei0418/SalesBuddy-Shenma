import os
from uuid import uuid4

import pytest

from sales_backend.db import set_request_context
from sales_backend.domain.agent import ActorContext, DataScope, RoleCode
from sales_backend.repositories.feishu_sync import FeishuRepository
from tests.integration.feishu_fixtures import assert_restricted, fixture_owner, seed_execute, seed_fetchval

pytestmark = pytest.mark.asyncio


async def setup(connection):
    if os.environ.get("SALES_TEST_ROLE"):
        await assert_restricted(connection)
    workspace, user, cid = uuid4(), uuid4(), uuid4()
    await seed_execute(connection,
        "INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'sync fixture')",
        workspace,
        str(workspace),
    )
    await seed_execute(connection,
        "INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name) VALUES($1,$2,$3,'sync fixture')",
        user,
        workspace,
        str(user),
    )
    await seed_execute(connection,
        "INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code) "
        "VALUES($1,$2,'administrator','workspace')", workspace, user)
    await set_request_context(connection, ActorContext(
        workspace_id=str(workspace), user_id=str(user), role=RoleCode.ADMINISTRATOR,
        data_scope=DataScope.WORKSPACE))
    settings = {
        "workspace_id": str(workspace),
        "connection_id": str(cid),
        "mappings": {"partner": {"enabled": True}},
        "notification": {"enabled": True},
    }
    await seed_execute(connection,
        """INSERT INTO config.feishu_connection
      (id,workspace_id,revision,settings,updated_by,validated_revision,enabled)
      VALUES($1,$2,1,$3,$4,1,true)""",
        cid,
        workspace,
        settings,
        user,
    )
    return workspace, cid


async def test_outbox_projection_and_guard(connection):
    ws, cid = await setup(connection)
    await assert_restricted(connection)
    oid = uuid4()
    await seed_execute(connection, "INSERT INTO crm.partner(id,workspace_id,name) VALUES($1,$2,'partner')", oid, ws)
    event = await connection.fetchrow("SELECT * FROM ops.feishu_event WHERE connection_id=$1", cid)
    assert event["first_formal_create"] and not event["historical"]
    repository = FeishuRepository()
    event = await repository.claim(connection)
    assert event is not None
    await repository.guard(connection, event, 1)
    raw = await repository.source(connection, event)
    assert raw["name"] == "partner"
    await connection.execute("UPDATE config.feishu_connection SET enabled=false WHERE id=$1", cid)
    with pytest.raises(RuntimeError, match="CONFIG_CHANGED"):
        await repository.guard(connection, event, 1)


async def test_reconciliation_enqueues_once_and_never_notifies(connection):
    ws, cid = await setup(connection)
    repo = FeishuRepository()
    assert await repo.schedule_reconcile(connection) is None
    assert await connection.fetchval("SELECT count(*) FROM ops.feishu_event WHERE connection_id=$1", cid) == 0
    await seed_execute(connection, "INSERT INTO crm.partner(workspace_id,name) VALUES($1,'initial')", ws)
    await connection.execute("UPDATE ops.feishu_event SET status='succeeded' WHERE connection_id=$1", cid)
    assert await repo.reconcile(connection, cid) == 1
    row = await connection.fetchrow("SELECT * FROM ops.feishu_event WHERE connection_id=$1 AND status='pending'", cid)
    assert row["historical"] and not row["first_formal_create"]


async def test_old_credential_validation_cannot_validate_rotated_secret(connection):
    ws, cid = await setup(connection)
    await seed_execute(connection,
        "INSERT INTO security.feishu_credential(connection_id,workspace_id,ciphertext,encryption_key_id) "
        "VALUES($1,$2,$3,'fixture')",
        cid,
        ws,
        b"not-a-real-secret",
    )
    await connection.execute(
        "UPDATE config.feishu_connection SET enabled=false,validated_revision=NULL,"
        "validation_requested=true WHERE id=$1",
        cid,
    )
    repo = FeishuRepository()
    row = await repo.worker_config(connection, validation=True)
    await connection.execute(
        "UPDATE security.feishu_credential SET updated_at=updated_at+interval '1 second' WHERE connection_id=$1", cid
    )
    await repo.validation_result(connection, row, None)
    assert await connection.fetchval("SELECT validated_revision FROM config.feishu_connection WHERE id=$1", cid) is None


async def test_history_import_while_live_never_qualifies_for_notification(connection):
    ws, cid = await setup(connection)
    for metadata in ({'import_type': 'crm_history'}, {}):
        if not metadata:
            await connection.execute("SET LOCAL app.feishu_historical_import='on'")
        oid = uuid4()
        await seed_execute(connection,
            "INSERT INTO crm.customer(id,workspace_id,name,normalized_name,import_meta,"
            "created_by_user_ref_id,data_kind) "
            "SELECT $1::uuid,$2,$1::uuid::text,$1::uuid::text,$3,updated_by,'production' "
            "FROM config.feishu_connection WHERE id=$4",
            oid, ws, metadata, cid)
        event = await connection.fetchrow(
            'SELECT * FROM ops.feishu_event WHERE connection_id=$1 AND object_id=$2', cid, oid)
        assert event['historical'] is True


async def test_dedicated_worker_role_cannot_write_business_or_use_superuser(connection):
    from sales_backend.feishu_worker import verify_worker_role

    with pytest.raises(RuntimeError, match='non-elevated'):
        await verify_worker_role(connection)
    await connection.execute('SET LOCAL ROLE salegent_feishu_worker')
    await verify_worker_role(connection)
    assert not await connection.fetchval(
        "SELECT has_table_privilege(current_user,'crm.customer','INSERT,UPDATE,DELETE')")
    assert await connection.fetchval(
        "SELECT has_function_privilege(current_user,'ops.feishu_source(uuid,text,uuid)','EXECUTE')")


async def test_configuration_and_credential_audit_never_contains_ciphertext(connection):
    ws, cid = await setup(connection)
    await seed_execute(connection,
        "INSERT INTO security.feishu_credential(connection_id,workspace_id,ciphertext,encryption_key_id) "
        "VALUES($1,$2,$3,'fixture-key')", cid, ws, b'secret-sentinel')
    rows = await connection.fetch('SELECT * FROM ops.feishu_config_audit WHERE connection_id=$1', cid)
    assert len(rows) == 2
    credential = next(r for r in rows if r['action'].startswith('security.'))
    assert credential['after_snapshot'] == {'credential_rotated': True, 'encryption_key_id': 'fixture-key'}
    assert 'secret-sentinel' not in str(rows)
    assert 'ciphertext' not in str(rows)


async def test_production_exit_emits_refresh_and_redacts_source(connection):
    ws, cid = await setup(connection)
    oid = uuid4()
    await seed_execute(connection,
        "INSERT INTO crm.customer(id,workspace_id,name,normalized_name,"
        "created_by_user_ref_id,owner_user_ref_id,data_kind) "
        "SELECT $1::uuid,$2,'private-name','private-name',updated_by,updated_by,'production' "
        "FROM config.feishu_connection WHERE id=$3", oid, ws, cid)
    active = await connection.fetchval("SELECT ops.feishu_source($1,'customer',$2)", cid, oid)
    assert active['owner_name'] == 'sync fixture'
    assert active['company_name'] == 'sync fixture'
    assert active['name'] == 'private-name'
    await connection.execute("UPDATE crm.customer SET data_kind='test' WHERE id=$1", oid)
    source = await connection.fetchval("SELECT ops.feishu_source($1,'customer',$2)", cid, oid)
    assert source == {'id': str(oid), 'excluded': True}
    assert await connection.fetchval(
        "SELECT count(*) FROM ops.feishu_event WHERE connection_id=$1 AND object_kind='customer'", cid) == 2
    refresh = await connection.fetchrow(
        "SELECT * FROM ops.feishu_event WHERE connection_id=$1 AND object_kind='refresh'", cid)
    assert refresh['historical'] and not refresh['first_formal_create']


@pytest.mark.parametrize('statement', [
    "UPDATE ops.feishu_config_audit SET action='tampered'",
    'DELETE FROM ops.feishu_config_audit',
    'TRUNCATE ops.feishu_config_audit',
])
async def test_audit_history_rejects_mutation_even_for_table_owner(connection, statement):
    import asyncpg

    await setup(connection)
    with pytest.raises(asyncpg.PostgresError):
        async with fixture_owner(connection):
            await connection.execute(statement)


async def test_worker_cannot_invoke_business_definers_with_valid_management_context(connection):
    import asyncpg

    from sales_backend.feishu_worker import verify_worker_role

    ws, cid = await setup(connection)
    user = await connection.fetchval('SELECT updated_by FROM config.feishu_connection WHERE id=$1', cid)
    await seed_execute(connection,
        "INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code) "
        "VALUES($1,$2,'operations','workspace')", ws, user)
    for setting, value in [('app.workspace_id', str(ws)), ('app.user_ref_id', str(user)),
                           ('app.role_code', 'operations')]:
        await connection.execute('SELECT set_config($1,$2,true)', setting, value)
    assert await connection.fetchval("SELECT security.has_active_role('operations')")
    await connection.execute('SET LOCAL ROLE salegent_feishu_worker')
    await verify_worker_role(connection)
    assert not await connection.fetchval(
        "SELECT has_function_privilege(current_user,"
        "'security.save_target_batch(text,uuid,uuid,text,date,date,jsonb,text,text)','EXECUTE')")
    for sql in [
        "SELECT security.save_target_batch('workspace',NULL,NULL,'year','2026-01-01','2026-12-31','[]','fixture')",
        "SELECT security.set_feishu_credential(NULL,'fake','fake')",
        "SELECT security.prepare_feishu_migration(NULL)",
        "SELECT security.feishu_initialize(NULL)",
        "TRUNCATE crm.customer",
        "UPDATE crm.customer SET name='forbidden'",
        "DELETE FROM crm.customer",
        "INSERT INTO crm.customer(name) VALUES('forbidden')",
    ]:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with connection.transaction():
                await connection.execute(sql)
    # Allowed worker functions and queue paths must remain usable.
    assert isinstance(await connection.fetchval('SELECT ops.feishu_reconcile($1)', cid), int)
    assert await connection.fetchval('SELECT count(*) FROM ops.feishu_event') >= 1


async def test_real_target_migration_is_company_scoped_and_rolls_back_atomically(connection):
    import asyncpg

    first, first_id = await setup(connection)
    second, second_id = await setup(connection)
    actor = await seed_fetchval(connection, 'SELECT updated_by FROM config.feishu_connection WHERE id=$1', first_id)
    await seed_execute(connection,
        "INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code) "
        "VALUES($1,$2,'operations','workspace')", first, actor)
    for ws, cid in [(first, first_id), (second, second_id)]:
        await seed_execute(connection, "INSERT INTO crm.partner(workspace_id,name) VALUES($1,'migration fixture')", ws)
        await seed_execute(connection,
            "INSERT INTO ops.feishu_record_map(connection_id,workspace_id,object_kind,object_id,"
            "table_id,record_id,projection_hash) VALUES($1,$2,'partner',$3,'tblOld','recOld','fixture')",
            cid, ws, uuid4())
    role = 'sync_console_' + uuid4().hex
    await seed_execute(connection, f'CREATE ROLE {role} NOLOGIN NOSUPERUSER NOBYPASSRLS')
    await seed_execute(connection, f'GRANT USAGE ON SCHEMA config,ops,security,common TO {role}')
    await seed_execute(connection, f'GRANT SELECT,UPDATE ON config.feishu_connection TO {role}')
    # This isolated console role needs the same bounded RLS predicate as the application role.
    await seed_execute(connection, f'GRANT EXECUTE ON FUNCTION security.authorization_has(text) TO {role}')
    await seed_execute(connection,
        f'GRANT SELECT ON ops.feishu_event,ops.feishu_record_map,ops.feishu_config_audit TO {role}')
    await seed_execute(connection, f'GRANT EXECUTE ON FUNCTION security.prepare_feishu_migration(uuid) TO {role}')
    for key, value in [('app.workspace_id', first), ('app.user_ref_id', actor), ('app.role_code', 'operations')]:
        await connection.execute('SELECT set_config($1,$2,true)', key, str(value))
    await connection.execute(f'SET LOCAL ROLE {role}')
    await assert_restricted(connection)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with connection.transaction():
            await connection.execute('SELECT security.prepare_feishu_migration($1)', second_id)
    with pytest.raises(asyncpg.InvalidParameterValueError):
        async with connection.transaction():
            await connection.execute('SELECT security.prepare_feishu_migration($1)', first_id)
    await connection.execute('UPDATE config.feishu_connection SET enabled=false WHERE id=$1', first_id)
    with pytest.raises(RuntimeError, match='save rejected'):
        async with connection.transaction():
            await connection.execute('SELECT security.prepare_feishu_migration($1)', first_id)
            raise RuntimeError('save rejected')
    assert await connection.fetchval('SELECT count(*) FROM ops.feishu_record_map') == 1
    await connection.execute('SELECT security.prepare_feishu_migration($1)', first_id)
    assert await connection.fetchval('SELECT count(*) FROM ops.feishu_record_map') == 0
    assert await connection.fetchval(
        "SELECT count(*) FROM ops.feishu_event WHERE status='pending'") == 0
    assert await connection.fetchval(
        "SELECT count(*) FROM ops.feishu_config_audit WHERE action='target_migration'") == 1
    await connection.execute('RESET ROLE')
    assert await connection.fetchval(
        'SELECT count(*) FROM ops.feishu_record_map WHERE connection_id=$1', second_id) == 1
    assert await connection.fetchval(
        "SELECT count(*) FROM ops.feishu_event WHERE connection_id=$1 AND status='pending'", second_id) >= 1


@pytest.mark.parametrize('blocker', ['running', 'unknown'])
async def test_target_migration_blocks_inflight_and_unknown_without_partial_changes(connection, blocker):
    import asyncpg

    ws, cid = await setup(connection)
    user = await seed_fetchval(connection, 'SELECT updated_by FROM config.feishu_connection WHERE id=$1', cid)
    await seed_execute(connection,
        "INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code) "
        "VALUES($1,$2,'operations','workspace')", ws, user)
    for key, value in [('app.workspace_id', ws), ('app.user_ref_id', user), ('app.role_code', 'operations')]:
        await connection.execute('SELECT set_config($1,$2,true)', key, str(value))
    await connection.execute('UPDATE config.feishu_connection SET enabled=false WHERE id=$1', cid)
    event_id = await seed_fetchval(connection,
        "INSERT INTO ops.feishu_event(connection_id,workspace_id,object_kind,object_id,status) "
        "VALUES($1,$2,'partner',$3,$4) RETURNING id", cid, ws, uuid4(),
        'running' if blocker == 'running' else 'failed')
    if blocker == 'unknown':
        await seed_execute(connection,
            "INSERT INTO ops.feishu_delivery(dedupe_key,event_id,connection_id,workspace_id,"
            "config_revision,chat_id,payload,status) VALUES($1,$2,$3,$4,1,'oc_fixture','{}','unknown')",
            str(uuid4()), event_id, cid, ws)
    before = await connection.fetchrow('SELECT status,error_code FROM ops.feishu_event WHERE id=$1', event_id)
    with pytest.raises(asyncpg.InvalidParameterValueError):
        async with connection.transaction():
            await connection.execute('SELECT security.prepare_feishu_migration($1)', cid)
    assert await connection.fetchrow('SELECT status,error_code FROM ops.feishu_event WHERE id=$1', event_id) == before
    assert await connection.fetchval(
        "SELECT count(*) FROM ops.feishu_config_audit WHERE connection_id=$1 AND action='target_migration'", cid) == 0


@pytest.mark.parametrize('resolution,expected', [('confirm_sent', 'sent'), ('suppress', 'failed')])
async def test_recovery_verifies_unknown_before_retry_and_audits_with_company_isolation(
    connection, resolution, expected,
):
    import asyncpg

    ws, cid = await setup(connection)
    other_ws, other_cid = await setup(connection)
    user = await seed_fetchval(connection, 'SELECT updated_by FROM config.feishu_connection WHERE id=$1', cid)
    await seed_execute(connection,
        "INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code) "
        "VALUES($1,$2,'operations','workspace')", ws, user)
    event = await seed_fetchval(connection,
        "INSERT INTO ops.feishu_event(connection_id,workspace_id,object_kind,object_id,status) "
        "VALUES($1,$2,'customer',$3,'dead_letter') RETURNING id", cid, ws, uuid4())
    key = str(uuid4())
    await seed_execute(connection,
        "INSERT INTO ops.feishu_delivery(dedupe_key,event_id,connection_id,workspace_id,config_revision,"
        "chat_id,payload,status) VALUES($1,$2,$3,$4,1,'oc_fixture','{}','unknown')", key, event, cid, ws)
    role = 'sync_recovery_' + uuid4().hex
    await seed_execute(connection, f'CREATE ROLE {role} NOLOGIN NOSUPERUSER NOBYPASSRLS')
    await seed_execute(connection, f'GRANT USAGE ON SCHEMA config,ops,security,common TO {role}')
    await seed_execute(connection, f'GRANT SELECT,UPDATE ON config.feishu_connection TO {role}')
    # This isolated console role needs the same bounded RLS predicate as the application role.
    await seed_execute(connection, f'GRANT EXECUTE ON FUNCTION security.authorization_has(text) TO {role}')
    await seed_execute(connection,
        f'GRANT SELECT ON ops.feishu_event,ops.feishu_delivery,ops.feishu_config_audit TO {role}')
    await seed_execute(connection,
        f'GRANT EXECUTE ON FUNCTION security.feishu_recover(uuid,uuid,text,text,text) TO {role}')
    for name, value in [('app.workspace_id', ws), ('app.user_ref_id', user), ('app.role_code', 'operations')]:
        await connection.execute('SELECT set_config($1,$2,true)', name, str(value))
    await connection.execute(f'SET LOCAL ROLE {role}')
    sql = 'SELECT security.feishu_recover($1,$2,$3,$4,$5)'
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with connection.transaction():
            await connection.execute(sql, other_cid, event, resolution, key, 'verified')
    with pytest.raises(asyncpg.InvalidParameterValueError):
        async with connection.transaction():
            await connection.execute(sql, cid, event, resolution, key, 'verified')  # still enabled
    await connection.execute('UPDATE config.feishu_connection SET enabled=false WHERE id=$1', cid)
    with pytest.raises(asyncpg.InvalidParameterValueError):
        async with connection.transaction():
            await connection.execute(sql, cid, event, 'retry', None, 'must verify first')
    await connection.execute(sql, cid, event, resolution, key, 'group manually checked')
    assert await connection.fetchval('SELECT status FROM ops.feishu_delivery WHERE dedupe_key=$1', key) == expected
    await connection.execute(sql, cid, event, 'retry', None, 'resume data synchronization')
    row = await connection.fetchrow('SELECT status,attempts FROM ops.feishu_event WHERE id=$1', event)
    assert dict(row) == {'status': 'pending', 'attempts': 0}
    audit = await connection.fetch('SELECT after_snapshot FROM ops.feishu_config_audit WHERE action=\'recovery\'')
    assert len(audit) == 2
    assert audit[0]['after_snapshot']['event_id'] == str(event)
    # Suppressed/sent notification remains terminal; retry cannot silently reset it.
    assert await connection.fetchval('SELECT status FROM ops.feishu_delivery WHERE dedupe_key=$1', key) == expected


async def test_target_migration_permanently_ends_old_dead_letters(connection):
    import asyncpg

    ws, cid = await setup(connection)
    user = await connection.fetchval('SELECT updated_by FROM config.feishu_connection WHERE id=$1', cid)
    await seed_execute(connection,
        "INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code) "
        "VALUES($1,$2,'operations','workspace')", ws, user)
    for key, value in [('app.workspace_id', ws), ('app.user_ref_id', user), ('app.role_code', 'operations')]:
        await connection.execute('SELECT set_config($1,$2,true)', key, str(value))
    await connection.execute('UPDATE config.feishu_connection SET enabled=false WHERE id=$1', cid)
    event = await seed_fetchval(connection,
        "INSERT INTO ops.feishu_event(connection_id,workspace_id,object_kind,object_id,status,"
        "error_code,first_formal_create,historical) VALUES($1,$2,'customer',$3,'dead_letter',"
        "'FIELD_MISSING',true,false) RETURNING id", cid, ws, uuid4())
    await connection.execute('SELECT security.prepare_feishu_migration($1)', cid)
    row = await connection.fetchrow('SELECT error_code,notification_planned FROM ops.feishu_event WHERE id=$1', event)
    assert row['error_code'] == 'TARGET_MIGRATED' and row['notification_planned']
    with pytest.raises(asyncpg.InvalidParameterValueError):
        async with connection.transaction():
            await connection.execute('SELECT security.feishu_recover($1,$2,$3,$4,$5)',
                                     cid, event, 'retry', None, 'must not revive old target')


async def queue_event(connection, cid, ws, oid, *, historical, available=True):
    return await seed_fetchval(connection,
        "INSERT INTO ops.feishu_event(connection_id,workspace_id,object_kind,object_id,"
        "historical,available_at,queue_origin) "
        "VALUES($1,$2,'customer',$3,$4,clock_timestamp()+make_interval(secs=>$5),$6) RETURNING id",
        cid, ws, oid, historical, 0 if available else 600, 'reconcile' if historical else 'business')


async def test_live_change_precedes_unrelated_history_backlog(connection):
    ws, cid = await setup(connection)
    for _ in range(30):
        await queue_event(connection, cid, ws, uuid4(), historical=True)
    live = await queue_event(connection, cid, ws, uuid4(), historical=False)
    event = await FeishuRepository().claim(connection)
    assert event['id'] == live


async def test_history_before_same_object_live_change_is_promoted_without_reordering(connection):
    ws, cid = await setup(connection)
    for _ in range(30):
        await queue_event(connection, cid, ws, uuid4(), historical=True)
    oid = uuid4()
    parent = await queue_event(connection, cid, ws, oid, historical=True)
    live = await queue_event(connection, cid, ws, oid, historical=False)
    repo = FeishuRepository()
    event = await repo.claim(connection)
    assert event['id'] == parent
    await repo.finish(connection, event)
    assert (await repo.claim(connection))['id'] == live


async def test_fairness_slot_services_oldest_history_even_with_live_backlog(connection):
    ws, cid = await setup(connection)
    first = await queue_event(connection, cid, ws, uuid4(), historical=True)
    for _ in range(30):
        await queue_event(connection, cid, ws, uuid4(), historical=False)
    assert (await FeishuRepository().claim(connection, prefer_history=True))['id'] == first


async def test_priority_does_not_bypass_same_object_retry_delay_or_configuration(connection):
    ws, cid = await setup(connection)
    oid = uuid4()
    await queue_event(connection, cid, ws, oid, historical=True, available=False)
    await queue_event(connection, cid, ws, oid, historical=False)
    repo = FeishuRepository()
    assert await repo.claim(connection) is None
    await connection.execute('UPDATE config.feishu_connection SET enabled=false WHERE id=$1', cid)
    await queue_event(connection, cid, ws, uuid4(), historical=False)
    assert await repo.claim(connection) is None


@pytest.mark.parametrize('imported', [False, True])
async def test_real_business_capture_is_priority_with_notifications_disabled(connection, imported):
    from sales_backend.domain.feishu_sync.config import SyncConfig
    from sales_backend.domain.feishu_sync.planning import SourceEvent, notification_plans
    from tests.test_feishu_sync_policy import payload
    ws, cid = await setup(connection)
    user = await connection.fetchval('SELECT updated_by FROM config.feishu_connection WHERE id=$1', cid)
    await connection.execute(
        "UPDATE config.feishu_connection SET settings=jsonb_set(settings,'{notification,enabled}','false') "
        "WHERE id=$1", cid)
    for _ in range(30):
        await queue_event(connection, cid, ws, uuid4(), historical=True)
    oid = await seed_fetchval(connection,
        "INSERT INTO crm.customer(workspace_id,name,normalized_name,created_by_user_ref_id,data_kind,import_meta) "
        "VALUES($1,'fixture priority','fixture priority',$2,'production',$3) RETURNING id",
        ws, user, {'import_type': 'crm_history'} if imported else {})
    if imported:
        await connection.execute("UPDATE ops.feishu_event SET status='succeeded' WHERE object_id=$1", oid)
        # Retained import metadata must NOT turn a later real edit into a background job.
        await connection.execute("UPDATE crm.customer SET demand_summary='updated' WHERE id=$1", oid)
    event = await FeishuRepository().claim(connection)
    assert event['object_id'] == oid and event['queue_origin'] == 'business'
    assert event['historical'] is True  # Never relax eligibility just to gain priority.
    config = payload()
    config.update(workspace_id=str(ws), connection_id=str(cid))
    config['notification']['enabled'] = True  # Even enabling later must not back-notify.
    source = SourceEvent(event['id'], ws, 'customer', oid, event['first_formal_create'],
                         event['historical'], True, ())
    assert not notification_plans(SyncConfig.model_validate(config), source)


async def test_reconciliation_and_explicit_import_use_background_origin(connection):
    ws, cid = await setup(connection)
    await connection.execute("SET LOCAL app.feishu_historical_import='on'")
    oid = await seed_fetchval(connection,
        "INSERT INTO crm.partner(workspace_id,name) VALUES($1,'fixture') RETURNING id", ws)
    row = await connection.fetchrow('SELECT * FROM ops.feishu_event WHERE object_id=$1', oid)
    assert row['queue_origin'] == 'reconcile' and row['historical']
    await connection.execute("UPDATE ops.feishu_event SET status='succeeded' WHERE connection_id=$1", cid)
    await FeishuRepository().reconcile(connection, cid)
    row = await connection.fetchrow("SELECT * FROM ops.feishu_event WHERE object_id=$1 AND status='pending'", oid)
    assert row['queue_origin'] == 'reconcile' and row['historical']
    await FeishuRepository().schedule_reconcile(connection)
    row = await connection.fetchrow(
        "SELECT * FROM ops.feishu_event WHERE connection_id=$1 AND object_kind='refresh'", cid)
    assert row is None  # No timer-generated full refresh.


async def test_forecast_raw_projection_queue_reconcile_and_parent_isolation(connection):
    ws, cid = await setup(connection)
    await connection.execute("UPDATE config.feishu_connection SET settings=jsonb_set(settings,'{mappings,forecast}','{\"enabled\":true}') WHERE id=$1", cid)
    customer, opportunity, forecast = uuid4(), uuid4(), uuid4()
    await seed_execute(connection, "INSERT INTO crm.customer(id,workspace_id,name,normalized_name,data_kind,created_by_user_ref_id) SELECT $1,$2,'forecast fixture','forecast fixture','production',updated_by FROM config.feishu_connection WHERE id=$3", customer, ws, cid)
    await seed_execute(connection, "INSERT INTO crm.opportunity(id,workspace_id,customer_id,name,follow_up_plan,partner_name) VALUES($1,$2,$3,'forecast opportunity','next meeting','recorded partner')", opportunity, ws, customer)
    await seed_execute(connection, "INSERT INTO crm.opportunity_forecast(id,workspace_id,opportunity_id,year,quarter,recognized_amount,collection_amount) VALUES($1,$2,$3,2026,3,0,NULL)", forecast, ws, opportunity)
    raw = await connection.fetchval("SELECT ops.feishu_source($1,'forecast',$2)", cid, forecast)
    assert raw['recognized_amount'] == 0 and raw['collection_amount'] is None
    assert raw['customer_id'] == str(customer) and raw['opportunity_id'] == str(opportunity)
    assert raw['customer_name'] == 'forecast fixture' and raw['opportunity_name'] == 'forecast opportunity'
    assert raw['year'] == 2026 and raw['quarter'] == 3
    assert 'created_at' not in raw and 'version_no' not in raw
    opp = await connection.fetchval("SELECT ops.feishu_source($1,'opportunity',$2)", cid, opportunity)
    assert opp['follow_up_plan'] == 'next meeting' and opp['partner_name'] == 'recorded partner'
    assert await connection.fetchval("SELECT count(*) FROM ops.feishu_event WHERE connection_id=$1 AND object_kind='forecast'", cid) == 1
    await seed_execute(connection, "UPDATE crm.opportunity_forecast SET collection_amount=12.34 WHERE id=$1", forecast)
    raw = await connection.fetchval("SELECT ops.feishu_source($1,'forecast',$2)", cid, forecast)
    assert str(raw['collection_amount']) == '12.34'
    assert await connection.fetchval("SELECT count(*) FROM ops.feishu_event WHERE connection_id=$1 AND object_kind='forecast'", cid) == 2
    await connection.execute("UPDATE ops.feishu_event SET status='succeeded' WHERE connection_id=$1", cid)
    await connection.fetchval('SELECT ops.feishu_reconcile($1)', cid)
    await connection.fetchval('SELECT ops.feishu_reconcile($1)', cid)
    events = await connection.fetch("SELECT * FROM ops.feishu_event WHERE connection_id=$1 AND object_kind='forecast' AND status='pending'", cid)
    assert len(events) == 1 and events[0]['historical'] and not events[0]['first_formal_create']
    await seed_execute(connection, "UPDATE crm.customer SET deleted_at=clock_timestamp() WHERE id=$1", customer)
    assert await connection.fetchval("SELECT ops.feishu_source($1,'forecast',$2)", cid, forecast) == {'id':str(forecast),'excluded':True}
    await seed_execute(connection, "DELETE FROM crm.opportunity_forecast WHERE id=$1", forecast)
    assert await connection.fetchval("SELECT ops.feishu_source($1,'forecast',$2)", cid, forecast) == {'id':str(forecast),'deleted':True}
    assert await connection.fetchval("SELECT count(*) FROM ops.feishu_event WHERE connection_id=$1 AND object_kind='forecast'", cid) == 4


async def test_source_fingerprint_updates_on_existing_map(connection):
    ws,cid=await setup(connection)
    oid=uuid4()
    event={'connection_id':cid,'workspace_id':ws,'object_kind':'partner','object_id':oid}
    repo=FeishuRepository()
    await repo.remember(connection,event,'tblPartner','recPartner','hash-one','source-one')
    await repo.remember(connection,event,'tblPartner','recPartner','hash-two','source-two')
    mapped=await repo.mapped(connection,cid,'partner',oid)
    assert mapped['projection_hash']=='hash-two'
    assert mapped['source_fingerprint']=='source-two'


async def test_manual_repair_marks_pending_background_work_for_remote_verification(connection):
    ws,cid=await setup(connection)
    oid=uuid4()
    await seed_execute(connection,"INSERT INTO crm.partner(id,workspace_id,name) VALUES($1,$2,'manual repair')",oid,ws)
    await connection.execute("UPDATE ops.feishu_event SET status='succeeded' WHERE connection_id=$1",cid)
    repo=FeishuRepository()
    await repo.reconcile(connection,cid)
    assert not await connection.fetchval(
        "SELECT force_remote_check FROM ops.feishu_event WHERE connection_id=$1 AND status='pending'",cid)
    await repo.initialize(connection,cid)
    assert await connection.fetchval(
        "SELECT force_remote_check FROM ops.feishu_event WHERE connection_id=$1 AND object_id=$2 AND status='pending'",cid,oid)
    assert await connection.fetchval(
        "SELECT bool_and(historical AND NOT first_formal_create) FROM ops.feishu_event "
        "WHERE connection_id=$1 AND queue_origin='reconcile'",cid)


async def test_manual_repair_enqueues_forced_successor_of_running_event(connection):
    ws, cid = await setup(connection)
    oid = uuid4()
    await seed_execute(connection, "INSERT INTO crm.partner(id,workspace_id,name) VALUES($1,$2,'running repair')", oid, ws)
    await connection.execute("UPDATE ops.feishu_event SET status='running' WHERE connection_id=$1", cid)
    await FeishuRepository().initialize(connection, cid)
    pending = await connection.fetch("SELECT * FROM ops.feishu_event WHERE connection_id=$1 AND object_id=$2 AND status='pending'", cid, oid)
    assert len(pending) == 1 and pending[0]['force_remote_check']
    assert pending[0]['historical'] and not pending[0]['first_formal_create']
