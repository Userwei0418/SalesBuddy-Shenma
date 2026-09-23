from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio

from sales_backend.db import set_request_context
from sales_backend.domain.agent import ActorContext, DataScope, RoleCode
from sales_backend.maintenance.feishu_test_archive import ArchiveManifest, ArchiveRejected, archive
from tests.integration.feishu_fixtures import assert_restricted, seed_execute, seed_fetchrow, seed_fetchval
from tests.integration.test_feishu_storage import setup

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def legacy_v108_schema_only(connection):
    latest = await connection.fetchval(
        "SELECT max(substring(version FROM 2)::int) FROM ops.schema_migration WHERE version ~ '^V[0-9]+$'"
    )
    if latest > 108:
        pytest.skip(
            "Historical V108 success scenarios require the isolated V108 database; "
            "V117 rejection and retention are tested in test_feishu_notification_archive_retention.py"
        )


async def batch(connection, *, full=False, mixed_creators=False):
    ws, cid = await setup(connection)
    user = await connection.fetchval('SELECT updated_by FROM config.feishu_connection WHERE id=$1', cid)
    await seed_execute(connection,
        "INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code) "
        "VALUES($1,$2,'administrator','workspace')", ws, user)
    actor = ActorContext(workspace_id=str(ws), user_id=str(user), role=RoleCode.ADMINISTRATOR,
                         data_scope=DataScope.WORKSPACE)
    await set_request_context(connection, actor)
    request, run = uuid4(), uuid4()
    await connection.execute("SELECT set_config('app.request_id',$1,true)", str(request))
    start = datetime.now(UTC)-timedelta(seconds=1)
    name = f'飞书联调测试-{run.hex[:8]}'
    customer = await seed_fetchrow(connection,
        "INSERT INTO crm.customer(workspace_id,name,normalized_name,created_by_user_ref_id,data_kind) "
        "VALUES($1,$2,$2,$3,'production') RETURNING id,version_no", ws, name, user)
    manifest = ArchiveManifest(workspace_id=ws, creator_id=user, run_id=run,
        started_at=start, ended_at=datetime.now(UTC)+timedelta(seconds=5),
        reason='用户授权的飞书联调测试验收结束',
        records=[dict(kind='customer',id=customer['id'],version=customer['version_no'],creation_request_id=request,creator_id=user)])
    if full:
        opportunity = await seed_fetchrow(connection,
            "INSERT INTO crm.opportunity(workspace_id,customer_id,name,created_by_user_ref_id) "
            "VALUES($1,$2,$3,$4) RETURNING id,version_no", ws, customer['id'], name, user)
        contact = await seed_fetchrow(connection,
            "INSERT INTO crm.contact(workspace_id,customer_id,name,created_by_user_ref_id) "
            "VALUES($1,$2,$3,$4) RETURNING id,version_no", ws, customer['id'], name, user)
        form = await seed_fetchval(connection,
            "INSERT INTO config.form_definition(workspace_id,form_code,name,object_type) "
            "VALUES($1,$2,'fixture','visit') RETURNING id", ws, str(uuid4()))
        version = await seed_fetchval(connection,
            "INSERT INTO config.form_version(form_definition_id,version_no,status) "
            "VALUES($1,1,'active') RETURNING id", form)
        visitor = user
        if mixed_creators:
            visitor = uuid4()
            await seed_execute(connection,
                "INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name) "
                "VALUES($1::uuid,$2::uuid,$1::text,'fixture visit recorder')", visitor, ws)
            await seed_execute(connection,
                "INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code) "
                "VALUES($1,$2,'manager','workspace')", ws, visitor)
            await set_request_context(connection, ActorContext(workspace_id=str(ws), user_id=str(visitor),
                role=RoleCode.MANAGER, data_scope=DataScope.WORKSPACE))
        await connection.execute("SELECT set_config('app.request_id',$1,true)", str(request))
        visit = await seed_fetchrow(connection,
            "INSERT INTO activity.visit(workspace_id,customer_id,opportunity_id,recorder_user_ref_id,"
            "form_version_id,status,archived_at,confirmed_at,confirmed_by_user_ref_id,"
            "created_by_user_ref_id,follow_up_record,is_first_visit) "
            "VALUES($1,$2,$3,$4,$5,'archived',clock_timestamp(),clock_timestamp(),$4,$4,$6,false) "
            "RETURNING id,version_no", ws, customer['id'], opportunity['id'], visitor, version, name)
        await set_request_context(connection, actor)
        body = manifest.model_dump(mode='json')
        body['records'] += [dict(kind=kind,id=str(row['id']),version=row['version_no'],
                                creation_request_id=str(request),creator_id=str(visitor if kind == 'visit' else user))
                            for kind,row in [('opportunity',opportunity),('contact',contact),('visit',visit)]]
        manifest = ArchiveManifest.model_validate(body)
    role = 'archive_fixture_' + uuid4().hex
    await seed_execute(connection, f'CREATE ROLE {role} NOLOGIN NOSUPERUSER NOBYPASSRLS')
    for schema in ('crm','activity','workflow','insight','agent','ops','platform','common','security','config'):
        await seed_execute(connection, f'GRANT USAGE ON SCHEMA {schema} TO {role}')
        await seed_execute(connection, f'GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO {role}')
    for schema in ('common','security'):
        await seed_execute(connection, f'GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA {schema} TO {role}')
    await seed_execute(connection, f'GRANT UPDATE ON crm.customer,crm.contact,crm.opportunity,activity.visit TO {role}')
    await seed_execute(connection, f'GRANT INSERT ON ops.audit_log TO {role}')
    await seed_execute(connection, f'GRANT USAGE ON ALL SEQUENCES IN SCHEMA ops TO {role}')
    await connection.execute(f'SET LOCAL ROLE {role}')
    await assert_restricted(connection)
    await connection.execute("SELECT set_config('app.request_id',$1,true)", str(uuid4()))
    return actor, manifest


async def test_precise_batch_dry_run_apply_and_idempotent_receipt(connection):
    actor, manifest = await batch(connection)
    plan = await archive(connection, actor, manifest)
    assert plan['dry_run'] and plan['records'] == 1
    assert await connection.fetchval('SELECT deleted_at IS NULL FROM crm.customer WHERE id=$1',
                                     manifest.records[0].id)
    result = await archive(connection, actor, manifest, expected_plan=plan['plan_hash'])
    assert result['archived']
    assert (await archive(connection, actor, manifest, expected_plan=plan['plan_hash']))['already_archived']
    assert await connection.fetchval("SELECT count(*) FROM ops.audit_log WHERE object_id=$1 "
                                    "AND action_code='feishu.integration_test.archive'", manifest.run_id) == 1
    assert await connection.fetchval("SELECT count(*) FROM ops.audit_log WHERE object_id=$1 "
                                    "AND action_code='crm.customer.update'", manifest.records[0].id) == 1
    assert await connection.fetchval("SELECT count(*) FROM ops.feishu_event WHERE object_id=$1 "
                                    "AND NOT first_formal_create", manifest.records[0].id) >= 1


@pytest.mark.parametrize('change', ['workspace', 'creator', 'request', 'version', 'prefix'])
async def test_mismatched_receipt_rejects_without_mutation(connection, change):
    actor, manifest = await batch(connection)
    body = manifest.model_dump(mode='json')
    if change == 'workspace':
        body['workspace_id'] = str(uuid4())
    elif change == 'creator':
        body['records'][0]['creator_id'] = str(uuid4())
    elif change == 'request':
        body['records'][0]['creation_request_id'] = str(uuid4())
    elif change == 'version':
        body['records'][0]['version'] += 1
    else:
        body['run_id'] = str(uuid4())
    with pytest.raises(ArchiveRejected):
        await archive(connection, actor, ArchiveManifest.model_validate(body))
    assert await connection.fetchval('SELECT deleted_at IS NULL FROM crm.customer WHERE id=$1',
                                     manifest.records[0].id)


async def test_unlisted_child_and_changed_plan_reject(connection):
    actor, manifest = await batch(connection)
    await archive(connection, actor, manifest)
    with pytest.raises(ArchiveRejected, match='PLAN_CHANGED'):
        await archive(connection, actor, manifest, expected_plan='different')
    role = await connection.fetchval('SELECT current_user')
    await connection.execute('RESET ROLE')
    await seed_execute(connection, "INSERT INTO crm.contact(workspace_id,customer_id,name) VALUES($1,$2,'external')",
                             manifest.workspace_id, manifest.records[0].id)
    await connection.execute(f'SET LOCAL ROLE {role}')
    with pytest.raises(ArchiveRejected, match='UNLISTED_CHILD_RECORD'):
        await archive(connection, actor, manifest)


@pytest.mark.parametrize('mixed_creators', [False, True])
async def test_four_objects_retire_child_first_and_preserve_confirmation(connection, mixed_creators):
    actor, manifest = await batch(connection, full=True, mixed_creators=mixed_creators)
    visit = next(r.id for r in manifest.records if r.kind == 'visit')
    original = await connection.fetchrow(
        'SELECT status,archived_at,confirmed_at,archived_fields FROM activity.visit WHERE id=$1', visit)
    plan = await archive(connection, actor, manifest)
    result = await archive(connection, actor, manifest, expected_plan=plan['plan_hash'])
    assert result['records'] == 4
    # Privileged readback proves actual retained row state; writes above used only the normal role.
    await connection.execute('RESET ROLE')
    for receipt in manifest.records:
        table = {'customer':'crm.customer','opportunity':'crm.opportunity',
                 'visit':'activity.visit','contact':'crm.contact'}[receipt.kind]
        assert await connection.fetchval(
            f'SELECT deleted_at IS NOT NULL FROM {table} WHERE id=$1', receipt.id)  # noqa: S608
    after = await connection.fetchrow(
        'SELECT status,archived_at,confirmed_at,archived_fields FROM activity.visit WHERE id=$1', visit)
    assert after == original


async def test_archive_failure_rolls_back_children_and_audit(connection):
    actor, manifest = await batch(connection, full=True)
    role = await connection.fetchval('SELECT current_user')
    await connection.execute('RESET ROLE')
    await seed_execute(connection, """CREATE FUNCTION pg_temp.reject_test_customer_archive() RETURNS trigger
      LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected archive failure'; END $$""")
    await seed_execute(connection, """CREATE TRIGGER reject_test_archive BEFORE UPDATE ON crm.customer
      FOR EACH ROW EXECUTE FUNCTION pg_temp.reject_test_customer_archive()""")
    await connection.execute(f'SET LOCAL ROLE {role}')
    plan = await archive(connection, actor, manifest)
    import asyncpg
    with pytest.raises(asyncpg.RaiseError, match='injected archive failure'):
        async with connection.transaction():
            await archive(connection, actor, manifest, expected_plan=plan['plan_hash'])
    await connection.execute('RESET ROLE')
    assert await connection.fetchval('SELECT count(*) FROM activity.visit WHERE id=$1 AND deleted_at IS NULL',
                                     next(r.id for r in manifest.records if r.kind == 'visit')) == 1
    assert not await connection.fetchval("SELECT EXISTS(SELECT 1 FROM ops.audit_log WHERE object_id=$1 "
        "AND action_code='feishu.integration_test.archive')", manifest.run_id)


async def test_hidden_other_recipient_notification_blocks_retirement(connection):
    actor, manifest = await batch(connection)
    role = await connection.fetchval('SELECT current_user')
    await connection.execute('RESET ROLE')
    recipient = uuid4()
    await seed_execute(connection,
        "INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name) "
        "VALUES($1::uuid,$2::uuid,$1::text,'other notification recipient')", recipient, manifest.workspace_id)
    notification = await seed_fetchval(connection,
        "INSERT INTO workflow.notification(workspace_id,recipient_user_ref_id,template_code,title,body,"
        "object_type,object_id) VALUES($1,$2,'fixture','fixture','fixture','customer',$3) RETURNING id",
        manifest.workspace_id, recipient, manifest.records[0].id)
    await connection.execute(f'SET LOCAL ROLE {role}')
    assert not await connection.fetchval('SELECT EXISTS(SELECT 1 FROM workflow.notification WHERE id=$1)', notification)
    with pytest.raises(ArchiveRejected):
        await archive(connection, actor, manifest)
    assert await connection.fetchval('SELECT deleted_at IS NULL FROM crm.customer WHERE id=$1', manifest.records[0].id)


async def test_feishu_worker_cannot_invoke_test_retirement(connection):
    import asyncpg

    await connection.execute('SET LOCAL ROLE salegent_feishu_worker')
    assert not await connection.fetchval(
        "SELECT has_function_privilege(current_user,'security.feishu_test_archive(jsonb,text)','EXECUTE')")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with connection.transaction():
            await connection.fetchval('SELECT security.feishu_test_archive($1,NULL)', {})


async def test_authorized_test_edit_requires_its_own_receipt_before_archive(connection):
    actor, manifest = await batch(connection, full=True)
    cid = manifest.records[0].id
    request = uuid4()
    await connection.execute("SELECT set_config('app.request_id',$1,true)", str(request))
    # A controlled test edit has its own immutable audit receipt; it is not the original INSERT.
    await connection.execute("UPDATE crm.customer SET name=name||' updated' WHERE id=$1", cid)
    version = await connection.fetchval('SELECT version_no FROM crm.customer WHERE id=$1', cid)
    body = manifest.model_dump(mode='json')
    body['records'][0]['version'] = version
    without_receipt = ArchiveManifest.model_validate(body)
    with pytest.raises(ArchiveRejected):
        await archive(connection, actor, without_receipt)
    body['mutations'] = [dict(request_id=str(request),actor_id=actor.user_id)]
    with_receipt = ArchiveManifest.model_validate(body)
    await connection.execute("SELECT set_config('app.request_id',$1,true)", str(uuid4()))
    plan = await archive(connection, actor, with_receipt)
    assert (await archive(connection, actor, with_receipt, expected_plan=plan['plan_hash']))['archived']
