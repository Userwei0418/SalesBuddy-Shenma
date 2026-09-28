"""Deployment quota under real HTTP, SQL and the non-bypass application role."""

from uuid import UUID, uuid4

import asyncpg
import pytest

from sales_backend.db import set_request_context
from tests.integration.test_operations_api import client_for, sign_in
from tests.integration.test_operations_claims_sql import actor

pytestmark = pytest.mark.asyncio


async def quota(connection):
    return await connection.fetchval("SELECT security.deployment_account_quota_status()")


async def maintenance(connection, sql, *args):
    role = await connection.fetchval("SELECT current_user")
    await connection.execute("RESET ROLE")
    try:
        async with connection.transaction():
            return await connection.fetchval(sql, *args)
    finally:
        await connection.execute(f'SET LOCAL ROLE "{role}"')


async def insert_user(connection, workspace, *, status="active", deleted=False, managed=False):
    uid = uuid4()
    await connection.execute(
        """INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name,status,deleted_at,attributes)
        VALUES($1,$2,$3,$3,'额度隔离测试',$4,CASE WHEN $5 THEN clock_timestamp() END,$6::jsonb)""",
        uid, workspace, "QUOTA" + uid.hex, status, deleted, {"platform_managed": True} if managed else {},
    )
    return uid


async def test_http_last_seat_replay_reactivation_and_admins_count(connection):
    admin = await actor(connection, "ADMIN001")
    initial = await quota(connection)
    # All six fixture accounts count, including operations and administrator.
    assert initial == {"scope": "deployment", "limit": 50, "used": 6, "remaining": 44}
    async with await client_for(connection) as client:
        await sign_in(client, "ADMIN001")
        org = (await client.get("/api/v1/console/organization")).json()
        for _ in range(49 - initial["used"]):
            await insert_user(connection, UUID(admin.workspace_id))
        body = dict(account_code="QUOTALAST", display_name="最后一个体验账号", roles=["sales"],
                    team_id=org["departments"][0]["id"], temporary_password="Quota-Only-Test-2026")
        key = str(uuid4())
        created = await client.post("/api/v1/console/accounts", json=body, headers={"Idempotency-Key": key})
        assert created.status_code == 201, created.text
        replay = await client.post("/api/v1/console/accounts", json=body, headers={"Idempotency-Key": key})
        assert replay.json() == created.json()
        org = (await client.get("/api/v1/console/organization")).json()
        assert org["account_quota"] == {"scope": "deployment", "limit": 50, "used": 50, "remaining": 0}
        rejected = await client.post("/api/v1/console/accounts", json={**body, "account_code": "QUOTAOVER"},
                                     headers={"Idempotency-Key": str(uuid4())})
        assert rejected.status_code == 409 and "部署上限" in rejected.text
        assert not await connection.fetchval("SELECT EXISTS(SELECT 1 FROM platform.user_ref WHERE account_code='QUOTAOVER')")
        uid = created.json()["id"]
        update = {k: v for k, v in body.items() if k != "temporary_password"}
        update.update(version_no=1, status="active", display_name="满额仍可编辑")
        edited = await client.put(f"/api/v1/console/accounts/{uid}", json=update, headers={"Idempotency-Key": str(uuid4())})
        assert edited.status_code == 200, edited.text
        update.update(version_no=2, status="inactive")
        disabled = await client.put(f"/api/v1/console/accounts/{uid}", json=update, headers={"Idempotency-Key": str(uuid4())})
        assert disabled.status_code == 200, disabled.text
        assert (await quota(connection))["remaining"] == 1
        await insert_user(connection, UUID(admin.workspace_id))
        update.update(version_no=3, status="active")
        restored = await client.put(f"/api/v1/console/accounts/{uid}", json=update, headers={"Idempotency-Key": str(uuid4())})
        assert restored.status_code == 409 and "部署上限" in restored.text
        assert await connection.fetchval("SELECT status FROM platform.user_ref WHERE id=$1::uuid", uid) == "inactive"
        assert (await quota(connection))["used"] == 50


async def test_sql_bulk_rollback_restore_and_delete_share_the_quota(connection):
    admin = await actor(connection, "ADMIN001")
    workspace = UUID(admin.workspace_id)
    initial = (await quota(connection))["used"]
    await maintenance(connection, "UPDATE security.deployment_account_quota SET max_active_accounts=$1", initial + 1)
    inactive = await insert_user(connection, workspace, status="inactive")
    deleted = await insert_user(connection, workspace, deleted=True)
    with pytest.raises(asyncpg.RaiseError, match="部署上限"):
        async with connection.transaction():
            await connection.execute("""INSERT INTO platform.user_ref(workspace_id,external_user_id,display_name)
                SELECT $1,gen_random_uuid()::text,'批量额度测试' FROM generate_series(1,2)""", workspace)
    assert (await quota(connection))["used"] == initial
    occupying = await insert_user(connection, workspace)
    await connection.execute("""INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name)
        VALUES($1,$2,$3,'重放更新') ON CONFLICT(id) DO UPDATE SET display_name=excluded.display_name""",
        occupying, workspace, str(occupying))
    assert (await quota(connection))["used"] == initial + 1
    for uid, sql in [(inactive, "status='active'"), (deleted, "deleted_at=NULL")]:
        with pytest.raises(asyncpg.RaiseError, match="部署上限"):
            async with connection.transaction():
                await connection.execute(f"UPDATE platform.user_ref SET {sql} WHERE id=$1", uid)
    await connection.execute("DELETE FROM platform.user_ref WHERE id=$1", occupying)
    await connection.execute("UPDATE platform.user_ref SET status='active' WHERE id=$1", inactive)
    assert (await quota(connection))["used"] == initial + 1
    await connection.execute("UPDATE platform.user_ref SET deleted_at=clock_timestamp() WHERE id=$1", inactive)
    await connection.execute("UPDATE platform.user_ref SET deleted_at=NULL WHERE id=$1", deleted)
    assert (await quota(connection))["used"] == initial + 1


async def test_runtime_cannot_change_quota_or_manufacture_managed_exemption(connection):
    admin = await actor(connection, "ADMIN001")
    for sql in ["SELECT * FROM security.deployment_account_quota",
                "UPDATE security.deployment_account_quota SET max_active_accounts=500",
                "DELETE FROM security.deployment_account_quota",
                "SELECT security.guard_deployment_account_quota()"]:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with connection.transaction():
                await connection.execute(sql)
    with pytest.raises(asyncpg.InsufficientPrivilegeError, match="部署维护"):
        async with connection.transaction():
            await insert_user(connection, UUID(admin.workspace_id), managed=True)
    with pytest.raises(asyncpg.InsufficientPrivilegeError, match="部署维护"):
        async with connection.transaction():
            await connection.execute("UPDATE platform.user_ref SET attributes=attributes||'{\"platform_managed\":true}'::jsonb WHERE id=$1::uuid", admin.user_id)
    await actor(connection, "XS001")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with connection.transaction():
            await quota(connection)


async def test_managed_mirror_and_lowering_limit_do_not_disable_accounts(connection):
    admin = await actor(connection, "ADMIN001")
    initial = (await quota(connection))["used"]
    uid, workspace = uuid4(), UUID(admin.workspace_id)
    await maintenance(connection, """INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name,attributes)
        VALUES($1,$2,$3,'平台管理镜像','{"platform_managed":true}')""", uid, workspace, str(uid))
    assert (await quota(connection))["used"] == initial
    await maintenance(connection, "UPDATE security.deployment_account_quota SET max_active_accounts=$1", initial - 1)
    assert (await quota(connection))["remaining"] == 0
    assert await connection.fetchval("SELECT status FROM platform.user_ref WHERE id=$1::uuid", admin.user_id) == "active"
    # Existing users can still be edited while the deployment is over its new limit.
    await connection.execute("UPDATE platform.user_ref SET display_name=display_name WHERE id=$1::uuid", admin.user_id)
    with pytest.raises(asyncpg.RaiseError, match="部署上限"):
        async with connection.transaction():
            await insert_user(connection, workspace)
    with pytest.raises(asyncpg.RaiseError, match="部署上限"):
        async with connection.transaction():
            await maintenance(connection, "UPDATE platform.user_ref SET attributes='{}' WHERE id=$1", uid)
    assert (await quota(connection))["used"] == initial


async def test_channels_and_extra_appointments_do_not_consume_seats(connection):
    admin = await actor(connection, "ADMIN001")
    initial = (await quota(connection))["used"]
    async with await client_for(connection) as client:
        await sign_in(client, "ADMIN001")
        for _ in range(2):
            assert (await client.post("/api/v1/auth/password/login", json={"account_code": "ADMIN001", "password": "Isolated-Testing-2026"})).status_code == 200
    await set_request_context(connection, admin)
    await connection.execute("""INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code)
        VALUES($1::uuid,$2::uuid,'operations','workspace')""", admin.workspace_id, admin.user_id)
    assert (await quota(connection))["used"] == initial


async def test_independent_connections_compete_across_companies():
    # No rollback fixture: the system verifier owns its committed synthetic
    # companies and connections, and refuses any non-disposable database.
    import os
    if not os.environ.get("SALES_TEST_DATABASE_URL"):
        pytest.skip("isolated PostgreSQL is not configured")
    from tests.system.verify_account_quota_postgres import verify
    await verify()
