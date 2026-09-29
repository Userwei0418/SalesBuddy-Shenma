"""V157 boundaries against the real PG ACL/RLS and dedicated worker identity."""
import json
from hashlib import sha256
from uuid import uuid4

import asyncpg
import pytest

from sales_backend.db import set_request_context
from sales_backend.feishu_worker import verify_worker_role
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.services.weekly_source import build_snapshot, exact_json
from tests.integration.feishu_fixtures import fixture_owner, seed_execute, seed_fetchval
from tests.integration.test_weekly_feishu_publish import _fixture

pytestmark = pytest.mark.asyncio

PROTECTED = ('feishu_publish_event_id', 'feishu_publish_requested_at', 'feishu_publish_snapshot')


async def _deny(connection, actor, permission):
    await seed_execute(connection, '''INSERT INTO config.account_authorization(workspace_id,user_ref_id)
        VALUES($1::uuid,$2::uuid) ON CONFLICT DO NOTHING''', actor.workspace_id, actor.user_id)
    await seed_execute(connection, '''INSERT INTO config.account_permission_override
        (workspace_id,user_ref_id,permission_code,effect) VALUES($1::uuid,$2::uuid,$3,'deny')''',
        actor.workspace_id, actor.user_id, permission)


async def _clone_source(connection, report, source):
    """Create a separate unpublished fixture; never disable source immutability."""
    ident = uuid4()
    raw = exact_json(source)
    await connection.execute('''INSERT INTO insight.weekly_report
        (id,workspace_id,author_id,request_id,status,result_status,input_snapshot,input_sha256,
         binding,original_result,draft_markdown,draft_version,report_week,source_cutoff_at)
        SELECT $1,workspace_id,author_id,$2,status,result_status,$3,$4,binding,original_result,
         draft_markdown,draft_version,report_week,source_cutoff_at FROM insight.weekly_report WHERE id=$5''',
        ident, uuid4(), raw, sha256(raw.encode()).hexdigest(), report)
    return ident


async def _assert_update_boundary(connection, role=None):
    role = role or await connection.fetchval('SELECT current_user')
    assert not await connection.fetchval(
        "SELECT has_table_privilege($1,'insight.weekly_report','UPDATE')", role)
    for column in PROTECTED:
        assert not await connection.fetchval(
            "SELECT has_column_privilege($1,'insight.weekly_report',$2,'UPDATE')", role, column)
    for column in ('status', 'result_status', 'original_result', 'draft_markdown', 'draft_version',
                   'error_code', 'runtime_metadata', 'started_at', 'finished_at', 'updated_at'):
        assert await connection.fetchval(
            "SELECT has_column_privilege($1,'insight.weekly_report',$2,'UPDATE')", role, column)


async def test_actual_application_acl_and_post_publish_draft_edit(connection, sales_actor):
    await _assert_update_boundary(connection)
    _cid, report = await _fixture(connection, sales_actor)
    event = await connection.fetchval('SELECT security.publish_weekly_report_feishu($1,1)', report)
    await connection.execute("UPDATE insight.weekly_report SET draft_markdown='后续草稿',draft_version=2 WHERE id=$1", report)
    row = await connection.fetchrow('SELECT feishu_publish_event_id,feishu_publish_snapshot FROM insight.weekly_report WHERE id=$1', report)
    assert row['feishu_publish_event_id'] == event
    assert row['feishu_publish_snapshot']['draft_markdown'] == '## 联调周报\n人工确认正文'
    assert row['feishu_publish_snapshot']['draft_version'] == 1


@pytest.mark.parametrize('published', [False, True])
async def test_direct_snapshot_update_is_denied_by_real_column_acl(connection, sales_actor, published):
    _cid, report = await _fixture(connection, sales_actor)
    if published:
        await connection.fetchval('SELECT security.publish_weekly_report_feishu($1,1)', report)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with connection.transaction():
            await connection.execute('''UPDATE insight.weekly_report SET feishu_publish_event_id=$2,
                feishu_publish_requested_at=clock_timestamp(),feishu_publish_snapshot='{}' WHERE id=$1''', report, uuid4())


@pytest.mark.parametrize('column', PROTECTED)
async def test_insert_cannot_prepopulate_any_publication_field(connection, sales_actor, column):
    _cid, report = await _fixture(connection, sales_actor)
    value = {'feishu_publish_event_id': 'gen_random_uuid()',
             'feishu_publish_requested_at': 'clock_timestamp()',
             'feishu_publish_snapshot': "'{}'::jsonb"}[column]
    with pytest.raises(asyncpg.InsufficientPrivilegeError, match='WEEKLY_FEISHU_INITIAL_SNAPSHOT_FORBIDDEN'):
        async with connection.transaction():
            await connection.execute(f'''INSERT INTO insight.weekly_report
                (id,workspace_id,author_id,request_id,status,result_status,input_snapshot,input_sha256,
                 binding,original_result,draft_markdown,draft_version,report_week,source_cutoff_at,{column})
                SELECT gen_random_uuid(),workspace_id,author_id,gen_random_uuid(),status,result_status,
                 input_snapshot,input_sha256,binding,original_result,draft_markdown,draft_version,
                 report_week,source_cutoff_at,{value} FROM insight.weekly_report WHERE id=$1''', report)


async def test_trigger_rejects_uncontrolled_initialization_even_if_broad_update_returns(connection, sales_actor):
    _cid, report = await _fixture(connection, sales_actor)
    role = await connection.fetchval('SELECT current_user')
    await seed_execute(connection, f'GRANT UPDATE ON insight.weekly_report TO "{role}"')
    with pytest.raises(asyncpg.InsufficientPrivilegeError, match='WEEKLY_FEISHU_PUBLICATION_WRITE_FORBIDDEN'):
        async with connection.transaction():
            await connection.execute('''UPDATE insight.weekly_report SET feishu_publish_event_id=$2,
                feishu_publish_requested_at=clock_timestamp(),feishu_publish_snapshot='{}' WHERE id=$1''', report, uuid4())
    assert await connection.fetchval('SELECT feishu_publish_event_id FROM insight.weekly_report WHERE id=$1', report) is None


async def test_published_snapshot_stays_immutable_even_for_owner(connection, sales_actor):
    _cid, report = await _fixture(connection, sales_actor)
    await connection.fetchval('SELECT security.publish_weekly_report_feishu($1,1)', report)
    with pytest.raises(asyncpg.PostgresError, match='WEEKLY_FEISHU_SNAPSHOT_IMMUTABLE'):
        await seed_execute(connection, "UPDATE insight.weekly_report SET feishu_publish_snapshot='{}' WHERE id=$1", report)


async def test_null_expected_version_cannot_bypass_first_publish_version_check(connection, sales_actor):
    cid, report = await _fixture(connection, sales_actor)
    with pytest.raises(asyncpg.PostgresError, match='WEEKLY_DRAFT_VERSION_CONFLICT'):
        async with connection.transaction():
            await connection.fetchval('SELECT security.publish_weekly_report_feishu($1,NULL)', report)
    assert await seed_fetchval(connection, 'SELECT count(*) FROM ops.feishu_event WHERE connection_id=$1', cid) == 0


async def test_null_result_status_does_not_count_as_an_accepted_draft(connection, sales_actor):
    cid, report = await _fixture(connection, sales_actor)
    await connection.execute('UPDATE insight.weekly_report SET result_status=NULL WHERE id=$1', report)
    with pytest.raises(asyncpg.PostgresError, match='WEEKLY_DRAFT_NOT_READY'):
        async with connection.transaction():
            await connection.fetchval('SELECT security.publish_weekly_report_feishu($1,1)', report)
    assert await seed_fetchval(connection, 'SELECT count(*) FROM ops.feishu_event WHERE connection_id=$1', cid) == 0


async def test_reconcile_closes_inherited_and_explicit_column_grants(connection):
    parent, child, leaf = ['weekly_' + uuid4().hex for _ in range(3)]
    async with fixture_owner(connection):
        await connection.execute(f'CREATE ROLE {parent} NOLOGIN NOSUPERUSER NOBYPASSRLS')
        await connection.execute(f'CREATE ROLE {child} NOLOGIN NOSUPERUSER NOBYPASSRLS INHERIT')
        await connection.execute(f'CREATE ROLE {leaf} NOLOGIN NOSUPERUSER NOBYPASSRLS')
        await connection.execute(f'GRANT {parent} TO {child}')
        await connection.execute(f'GRANT USAGE ON SCHEMA insight TO {parent}')
        await connection.execute(f'GRANT UPDATE ON insight.weekly_report TO {parent} WITH GRANT OPTION')
        await connection.execute(f'GRANT USAGE ON SCHEMA insight TO {leaf}')
        await connection.execute(f'SET LOCAL ROLE {parent}')
        await connection.execute(f'GRANT UPDATE ON insight.weekly_report TO {child}')
        await connection.execute(f'GRANT UPDATE (draft_markdown) ON insight.weekly_report TO {leaf}')
        await connection.execute('RESET ROLE')
        await connection.execute(f'GRANT UPDATE (feishu_publish_snapshot) ON insight.weekly_report TO {child}')
        assert await connection.fetchval("SELECT has_table_privilege($1,'insight.weekly_report','UPDATE')", child)
        await connection.fetchval('SELECT security.reconcile_runtime_grants()')
        await _assert_update_boundary(connection, parent)
        await _assert_update_boundary(connection, child)
        assert await connection.fetchval("SELECT has_column_privilege($1,'insight.weekly_report','draft_markdown','UPDATE')", leaf)
        assert not await connection.fetchval("SELECT has_column_privilege($1,'insight.weekly_report','feishu_publish_snapshot','UPDATE')", leaf)
        # A later deployment's grant reconciliation cannot restore the hole.
        await connection.fetchval('SELECT security.reconcile_runtime_grants()')
        await _assert_update_boundary(connection, child)
    await _assert_update_boundary(connection)


@pytest.mark.parametrize('permission', ['weekly_report.edit', 'weekly_report.read', 'visit.read', 'customer.read'])
@pytest.mark.parametrize('published', [False, True])
async def test_revoked_permission_rejects_publish_and_replay(connection, sales_actor, permission, published):
    cid, report = await _fixture(connection, sales_actor)
    if published:
        await connection.fetchval('SELECT security.publish_weekly_report_feishu($1,1)', report)
    await _deny(connection, sales_actor, permission)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with connection.transaction():
            await connection.fetchval('SELECT security.publish_weekly_report_feishu($1,1)', report)
    assert await seed_fetchval(connection, 'SELECT count(*) FROM ops.feishu_event WHERE connection_id=$1', cid) == int(published)


@pytest.mark.parametrize('source', [{}, {'records': [], 'context': {'customers': [], 'opportunities': []}},
    {'records': [{'id': str(uuid4())}], 'context': {'customers': [{'id': str(uuid4())}], 'opportunities': []}}])
async def test_missing_or_unavailable_source_is_never_publishable(connection, sales_actor, source):
    cid, report = await _fixture(connection, sales_actor)
    forged = await _clone_source(connection, report, source)
    with pytest.raises(asyncpg.InsufficientPrivilegeError, match='WEEKLY_SOURCE_ACCESS_CHANGED'):
        async with connection.transaction():
            await connection.fetchval('SELECT security.publish_weekly_report_feishu($1,1)', forged)
    assert await seed_fetchval(connection, 'SELECT count(*) FROM ops.feishu_event WHERE connection_id=$1', cid) == 0


async def test_other_authors_source_cannot_be_reused_by_own_report(connection, sales_actor):
    cid, report = await _fixture(connection, sales_actor)
    source = json.loads(await connection.fetchval('SELECT input_snapshot FROM insight.weekly_report WHERE id=$1', report))
    other = await IdentityRepository().find_actor_by_account(
        connection, workspace_external_id='demo-sales-workspace', account_code='XS002')
    # Copy a syntactically valid snapshot to a second author's own report.
    own = uuid4()
    await seed_execute(connection, '''INSERT INTO insight.weekly_report
        (id,workspace_id,author_id,request_id,status,result_status,input_snapshot,input_sha256,binding,
         original_result,draft_markdown,draft_version,report_week,source_cutoff_at)
        SELECT $1,workspace_id,$2::uuid,$3,status,result_status,input_snapshot,input_sha256,binding,
         original_result,draft_markdown,draft_version,report_week,source_cutoff_at FROM insight.weekly_report WHERE id=$4''',
        own, other.context.user_id, uuid4(), report)
    await set_request_context(connection, other.context)
    assert source['records']
    with pytest.raises(asyncpg.InsufficientPrivilegeError, match='WEEKLY_SOURCE_ACCESS_CHANGED'):
        async with connection.transaction():
            await connection.fetchval('SELECT security.publish_weekly_report_feishu($1,1)', own)
    assert await seed_fetchval(connection, 'SELECT count(*) FROM ops.feishu_event WHERE connection_id=$1', cid) == 0


@pytest.mark.parametrize('published', [False, True])
async def test_opportunity_permission_is_rechecked_for_real_associated_source(connection, sales_actor, published):
    cid, report = await _fixture(connection, sales_actor)
    source = json.loads(await connection.fetchval('SELECT input_snapshot FROM insight.weekly_report WHERE id=$1', report))
    customer, opportunity = source['context']['customers'][0]['id'], uuid4()
    await connection.execute('''INSERT INTO crm.opportunity(id,workspace_id,customer_id,name,amount,currency,
        stage_code,owner_user_ref_id,owner_team_id,created_by_user_ref_id,updated_at)
        VALUES($1,$2::uuid,$3::uuid,'权限复核测试商机',100,'CNY','solution',$4::uuid,$5::uuid,$4::uuid,
        transaction_timestamp()-interval '1 day')''',
        opportunity, sales_actor.workspace_id, customer, sales_actor.user_id, sales_actor.team_ids[0])
    await connection.execute('''INSERT INTO activity.visit(workspace_id,customer_id,opportunity_id,
        recorder_user_ref_id,recorder_team_id,created_by_user_ref_id,form_version_id,status,created_at,interaction_at,follow_up_record)
        SELECT $1::uuid,$2::uuid,$3,$4::uuid,$5::uuid,$4::uuid,
        (SELECT id FROM config.form_version WHERE status='active' ORDER BY version_no DESC LIMIT 1),
        'archived',transaction_timestamp()-interval '1 hour',transaction_timestamp()-interval '1 hour','关联商机权限验收记录' ''',
        sales_actor.workspace_id, customer, opportunity, sales_actor.user_id, sales_actor.team_ids[0])
    source, _, _ = await build_snapshot(connection, sales_actor, str(uuid4()))
    assert source['context']['opportunities']
    report = await _clone_source(connection, report, source)
    if published:
        await connection.fetchval('SELECT security.publish_weekly_report_feishu($1,1)', report)
    await _deny(connection, sales_actor, 'opportunity.read')
    with pytest.raises(asyncpg.InsufficientPrivilegeError, match='WEEKLY_SOURCE_ACCESS_CHANGED'):
        async with connection.transaction():
            await connection.fetchval('SELECT security.publish_weekly_report_feishu($1,1)', report)
    assert await seed_fetchval(connection, "SELECT count(*) FROM ops.feishu_event WHERE connection_id=$1 AND object_kind='weekly_report'", cid) == int(published)


async def test_foreign_workspace_source_reference_is_rejected(connection, sales_actor):
    cid, report = await _fixture(connection, sales_actor)
    foreign_workspace, foreign_customer, foreign_user = uuid4(), uuid4(), uuid4()
    await seed_execute(connection, '''INSERT INTO platform.workspace(id,external_workspace_id,name)
        VALUES($1,$2,'另一个隔离企业')''', foreign_workspace, str(foreign_workspace))
    await seed_execute(connection, '''INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name)
        VALUES($1,$2,$3,'跨企业测试成员')''', foreign_user, foreign_workspace, str(foreign_user))
    await seed_execute(connection, '''INSERT INTO crm.customer(id,workspace_id,name,normalized_name,created_by_user_ref_id)
        VALUES($1,$2,'跨企业测试客户','跨企业测试客户',$3)''', foreign_customer, foreign_workspace, foreign_user)
    source = json.loads(await connection.fetchval('SELECT input_snapshot FROM insight.weekly_report WHERE id=$1', report))
    source['context']['customers'][0]['id'] = str(foreign_customer)
    report = await _clone_source(connection, report, source)
    with pytest.raises(asyncpg.InsufficientPrivilegeError, match='WEEKLY_SOURCE_ACCESS_CHANGED'):
        async with connection.transaction():
            await connection.fetchval('SELECT security.publish_weekly_report_feishu($1,1)', report)
    assert await seed_fetchval(connection, 'SELECT count(*) FROM ops.feishu_event WHERE connection_id=$1', cid) == 0


async def test_real_worker_role_passes_selfcheck_and_cannot_publish_or_mutate(connection, sales_actor):
    cid, report = await _fixture(connection, sales_actor)
    await connection.fetchval('SELECT security.publish_weekly_report_feishu($1,1)', report)
    relation_oid = await connection.fetchval("SELECT 'insight.weekly_report'::regclass::oid")
    await connection.execute('SET LOCAL ROLE salegent_feishu_worker')
    await verify_worker_role(connection)
    assert not await connection.fetchval("SELECT has_table_privilege(current_user,$1::oid,'INSERT,UPDATE,DELETE')", relation_oid)
    for column in PROTECTED:
        assert not await connection.fetchval("SELECT has_column_privilege(current_user,$1::oid,$2,'UPDATE')", relation_oid, column)
    assert not await connection.fetchval("SELECT has_function_privilege(current_user,'security.publish_weekly_report_feishu(uuid,integer)','EXECUTE')")
    row = await connection.fetchval('SELECT ops.feishu_weekly_source($1,$2)', cid, report)
    assert row['body_markdown'] == '## 联调周报\n人工确认正文'
    assert 'input_snapshot' not in row
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with connection.transaction():
            await connection.fetchval('SELECT ops.feishu_weekly_source($1,$2)', uuid4(), report)
