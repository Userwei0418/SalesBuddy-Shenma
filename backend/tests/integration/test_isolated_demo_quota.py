"""Independent demo seats preserve formal quota and tenant authority on real PG."""

import os
from uuid import UUID, uuid4

import asyncpg
import pytest

from sales_backend.db import set_request_context
from sales_backend.domain.agent import ActorContext, RoleCode
from tests.integration.test_account_quota import insert_user, maintenance, quota
from tests.integration.test_operations_claims_sql import actor

pytestmark = pytest.mark.asyncio


async def demo(connection, limit=3):
    workspace = uuid4()
    await maintenance(connection, """INSERT INTO platform.workspace(id,external_workspace_id,name,attributes)
        VALUES($1,$2,'隔离额度测试公司','{"kind":"demo"}')""", workspace, str(workspace))
    await maintenance(connection, "SELECT security.register_isolated_demo_quota($1,$2)", workspace, limit)
    uid = uuid4()
    await maintenance(connection, """INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name)
        VALUES($1,$2,$3,$3,'演示管理员')""", uid, workspace, "DEMOQ" + uid.hex)
    await maintenance(connection, """INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code)
        VALUES($1,$2,'administrator','workspace')""", workspace, uid)
    identity = ActorContext(workspace_id=str(workspace), user_id=str(uid), role=RoleCode.ADMINISTRATOR,
                            data_scope="workspace", team_ids=())
    await set_request_context(connection, identity)
    return identity


async def counters_match(connection):
    assert await maintenance(connection, """SELECT active_accounts=(SELECT count(*) FROM platform.user_ref u
        WHERE u.status='active' AND u.deleted_at IS NULL AND COALESCE(u.attributes->>'platform_managed','false')<>'true'
        AND NOT EXISTS(SELECT 1 FROM security.isolated_demo_account_quota q WHERE q.workspace_id=u.workspace_id))
        FROM security.deployment_account_quota""")
    assert await maintenance(connection, """SELECT bool_and(q.active_accounts=(SELECT count(*) FROM platform.user_ref u
        WHERE u.workspace_id=q.workspace_id AND u.status='active' AND u.deleted_at IS NULL
        AND COALESCE(u.attributes->>'platform_managed','false')<>'true')) FROM security.isolated_demo_account_quota q""")


async def test_full_formal_quota_does_not_block_independent_demo_and_both_still_enforce_limit(connection):
    formal = await actor(connection, "ADMIN001")
    original = await quota(connection)
    await maintenance(connection, "UPDATE security.deployment_account_quota SET max_active_accounts=active_accounts")
    isolated = await demo(connection, 2)
    assert await quota(connection) == {"scope": "workspace", "limit": 2, "used": 1, "remaining": 1}
    await insert_user(connection, UUID(isolated.workspace_id))
    with pytest.raises(asyncpg.RaiseError, match="演示公司独立上限"):
        async with connection.transaction():
            await insert_user(connection, UUID(isolated.workspace_id))
    await set_request_context(connection, formal)
    assert await quota(connection) == {"scope": "deployment", "limit": original["used"], "used": original["used"], "remaining": 0}
    with pytest.raises(asyncpg.RaiseError, match="部署上限"):
        async with connection.transaction():
            await insert_user(connection, UUID(formal.workspace_id))
    await counters_match(connection)


async def test_demo_upsert_disable_soft_delete_restore_delete_and_bulk_rollback(connection):
    await actor(connection, "ADMIN001")
    formal_before = await quota(connection)
    isolated = await demo(connection, 3)
    workspace = UUID(isolated.workspace_id)
    uid = await insert_user(connection, workspace)
    inactive = await insert_user(connection, workspace, status="inactive")
    await connection.execute("""INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name)
        VALUES($1,$2,$3,'更新而不重复计数') ON CONFLICT(id) DO UPDATE SET display_name=excluded.display_name""", uid, workspace, str(uid))
    assert (await quota(connection))["used"] == 2
    with pytest.raises(asyncpg.RaiseError, match="演示公司独立上限"):
        async with connection.transaction():
            await connection.execute("""INSERT INTO platform.user_ref(workspace_id,external_user_id,display_name)
                SELECT $1,gen_random_uuid()::text,'批量回滚' FROM generate_series(1,2)""", workspace)
    assert (await quota(connection))["used"] == 2
    await connection.execute("UPDATE platform.user_ref SET status='active' WHERE id=$1", inactive)
    await connection.execute("UPDATE platform.user_ref SET deleted_at=clock_timestamp() WHERE id=$1", uid)
    assert (await quota(connection))["used"] == 2
    await connection.execute("UPDATE platform.user_ref SET deleted_at=NULL WHERE id=$1", uid)
    assert (await quota(connection))["used"] == 3
    await connection.execute("UPDATE platform.user_ref SET status='inactive' WHERE id=$1", uid)
    await connection.execute("DELETE FROM platform.user_ref WHERE id=$1", inactive)
    assert (await quota(connection))["used"] == 1
    await actor(connection, "ADMIN001")
    assert await quota(connection) == formal_before
    await counters_match(connection)


async def test_maintenance_moves_charge_both_buckets_and_failed_move_is_atomic(connection):
    formal = await actor(connection, "ADMIN001")
    initial = await quota(connection)
    one = await demo(connection, 2)
    two = await demo(connection, 1)
    uid = await maintenance(connection, """INSERT INTO platform.user_ref(workspace_id,external_user_id,display_name)
        VALUES($1,$2,'无业务依赖的移动测试账号') RETURNING id""", UUID(formal.workspace_id), str(uuid4()))
    await maintenance(connection, "UPDATE platform.user_ref SET workspace_id=$1 WHERE id=$2", UUID(one.workspace_id), uid)
    await set_request_context(connection, formal)
    assert (await quota(connection))["used"] == initial["used"]
    await set_request_context(connection, one)
    assert (await quota(connection))["used"] == 2
    with pytest.raises(asyncpg.RaiseError, match="演示公司独立上限"):
        async with connection.transaction():
            await maintenance(connection, "UPDATE platform.user_ref SET workspace_id=$1 WHERE id=$2", UUID(two.workspace_id), uid)
    assert await maintenance(connection, "SELECT workspace_id FROM platform.user_ref WHERE id=$1", uid) == UUID(one.workspace_id)
    assert (await quota(connection))["used"] == 2
    await maintenance(connection, "UPDATE security.deployment_account_quota SET max_active_accounts=active_accounts")
    with pytest.raises(asyncpg.RaiseError, match="部署上限"):
        async with connection.transaction():
            await maintenance(connection, "UPDATE platform.user_ref SET workspace_id=$1 WHERE id=$2", UUID(formal.workspace_id), uid)
    await maintenance(connection, "UPDATE security.deployment_account_quota SET max_active_accounts=max_active_accounts+1")
    await maintenance(connection, "UPDATE platform.user_ref SET workspace_id=$1 WHERE id=$2", UUID(formal.workspace_id), uid)
    assert (await quota(connection))["used"] == 1
    await counters_match(connection)


async def test_runtime_cannot_enroll_exempt_move_or_read_other_company(connection):
    formal = await actor(connection, "ADMIN001")
    isolated = await demo(connection, 2)
    for sql in ["SELECT * FROM security.isolated_demo_account_quota",
                "UPDATE security.isolated_demo_account_quota SET max_active_accounts=999",
                "DELETE FROM security.isolated_demo_account_quota",
                "SELECT security.register_isolated_demo_quota(gen_random_uuid(),999)"]:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with connection.transaction():
                await connection.execute(sql)
    # Owning a workspace admin role does not authorize a company switch.
    assert await connection.fetchval("SELECT security.company_management_actor($1::uuid)", formal.workspace_id) is None
    companies = await connection.fetchval("SELECT security.company_directory()")
    assert [row["id"] for row in companies] == [isolated.workspace_id]
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with connection.transaction():
            await insert_user(connection, UUID(isolated.workspace_id), managed=True)
    await counters_match(connection)


async def test_enrollment_rejects_formal_existing_and_duplicate_companies(connection):
    formal = await actor(connection, "ADMIN001")
    with pytest.raises(asyncpg.RaiseError, match="有效隔离演示"):
        async with connection.transaction():
            await maintenance(connection, "SELECT security.register_isolated_demo_quota($1::uuid,7)", formal.workspace_id)
    isolated = await demo(connection)
    with pytest.raises(asyncpg.RaiseError, match="创建账号前"):
        async with connection.transaction():
            await maintenance(connection, "SELECT security.register_isolated_demo_quota($1::uuid,7)", isolated.workspace_id)
    with pytest.raises(asyncpg.RaiseError, match="大于零"):
        async with connection.transaction():
            await maintenance(connection, "SELECT security.register_isolated_demo_quota($1::uuid,0)", isolated.workspace_id)


async def test_daily_ai_scheduler_excludes_registered_demo_but_keeps_formal_sales(connection):
    await actor(connection, "ADMIN001")
    isolated = await demo(connection)
    await maintenance(connection, """INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code)
        VALUES($1::uuid,$2::uuid,'sales','self')""", isolated.workspace_id, isolated.user_id)
    await connection.fetchval("SELECT ops.enqueue_daily_sales_competency_reviews()")
    assert await maintenance(connection, "SELECT count(*) FROM insight.sales_competency_review WHERE workspace_id=$1::uuid", isolated.workspace_id) == 0
    assert await maintenance(connection, "SELECT count(*) FROM ops.job WHERE workspace_id=$1::uuid AND job_type='sales_competency.review'", isolated.workspace_id) == 0
    assert await maintenance(connection, "SELECT count(*) FROM insight.sales_competency_review WHERE workspace_id<>$1::uuid", isolated.workspace_id) > 0


async def test_demo_independent_connection_concurrency():
    if not os.environ.get("SALES_TEST_DATABASE_URL"):
        pytest.skip("isolated PostgreSQL is not configured")
    from tests.system.verify_isolated_demo_quota_postgres import verify
    await verify()


async def test_enrollment_cannot_be_removed_or_moved_after_accounts_exist(connection):
    formal = await actor(connection, "ADMIN001")
    isolated = await demo(connection)
    with pytest.raises(asyncpg.RaiseError, match="不能移除"):
        async with connection.transaction():
            await maintenance(connection, "DELETE FROM security.isolated_demo_account_quota WHERE workspace_id=$1::uuid", isolated.workspace_id)
    with pytest.raises(asyncpg.RaiseError, match="所属公司不可修改"):
        async with connection.transaction():
            await maintenance(connection, "UPDATE security.isolated_demo_account_quota SET workspace_id=$1::uuid WHERE workspace_id=$2::uuid", formal.workspace_id, isolated.workspace_id)
    await counters_match(connection)


async def test_upgrade_preserves_formal_65_rows_and_existing_acl_atomically():
    if not os.environ.get("SALES_TEST_DATABASE_URL"):
        pytest.skip("isolated PostgreSQL is not configured")
    from tests.system.verify_isolated_demo_quota_upgrade import verify
    await verify()
