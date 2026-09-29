"""Committed demo quota races; refuses every non-disposable database/role."""

import asyncio
import os
import re
from uuid import uuid4

import asyncpg

from sales_backend.db import set_request_context
from sales_backend.domain.agent import ActorContext, RoleCode
from sales_backend.repositories.operations_accounts import OperationsAccountRepository


async def verify():
    dsn, runtime = os.environ["SALES_TEST_DATABASE_URL"], os.environ["SALES_TEST_ROLE"]
    if not re.fullmatch(r"salegent_verify_role_[a-f0-9]+", runtime):
        raise RuntimeError("Disposable non-bypass role required")
    owner = await asyncpg.connect(dsn)
    workspace, person = uuid4(), uuid4()
    created = False
    try:
        name = await owner.fetchval("SELECT current_database()")
        if not re.fullmatch(r"salegent_verify_integration_[a-f0-9]+", name):
            raise RuntimeError("Disposable integration database required")
        formal_before = dict(await owner.fetchrow("SELECT * FROM security.deployment_account_quota"))
        await owner.execute("""INSERT INTO platform.workspace(id,external_workspace_id,name,attributes)
            VALUES($1,$2,'并发隔离演示公司','{"kind":"demo"}')""", workspace, str(workspace))
        created = True
        await owner.execute("SELECT security.register_isolated_demo_quota($1,2)", workspace)
        await owner.execute("""INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name)
            VALUES($1,$2,$3,'演示管理员')""", person, workspace, str(person))
        await owner.execute("""INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code)
            VALUES($1,$2,'administrator','workspace')""", workspace, person)
        identity = ActorContext(workspace_id=str(workspace), user_id=str(person),
                                role=RoleCode.ADMINISTRATOR, data_scope="workspace", team_ids=())

        async def assert_counts(expected):
            row = await owner.fetchrow("""SELECT q.active_accounts,(SELECT count(*) FROM platform.user_ref u
                WHERE u.workspace_id=q.workspace_id AND u.status='active' AND u.deleted_at IS NULL
                AND COALESCE(u.attributes->>'platform_managed','false')<>'true') AS actual
                FROM security.isolated_demo_account_quota q WHERE workspace_id=$1""", workspace)
            assert row["active_accounts"] == row["actual"] == expected, dict(row)
            assert dict(await owner.fetchrow("SELECT * FROM security.deployment_account_quota")) == formal_before

        async def race(*, restore=False, isolation="read_committed", rollback_first=False):
            ids = [uuid4(), uuid4()]
            if restore:
                await owner.execute("""INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name,status)
                    VALUES($1,$2,$3,'等待启用','inactive')""", ids[1], workspace, str(ids[1]))
            ready, gate, first_written = 0, asyncio.Event(), asyncio.Event()

            async def attempt(index):
                nonlocal ready
                connection = await asyncpg.connect(dsn)
                tx = connection.transaction(isolation=isolation)
                await tx.start()
                try:
                    await connection.execute(f'SET LOCAL ROLE "{runtime}"')
                    assert not await connection.fetchval("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
                    await set_request_context(connection, identity)
                    await connection.fetchval("SELECT count(*) FROM platform.user_ref")
                    ready += 1
                    if ready == 2:
                        gate.set()
                    await gate.wait()
                    if rollback_first and index == 1:
                        await first_written.wait()
                    if index == 0:
                        await OperationsAccountRepository().lock_workspace(connection, str(workspace))
                    if restore and index == 1:
                        await connection.execute("UPDATE platform.user_ref SET status='active' WHERE id=$1", ids[index])
                    else:
                        await connection.execute("""INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name)
                            VALUES($1,$2,$3,'并发演示账号')""", ids[index], workspace, str(ids[index]))
                    if rollback_first and index == 0:
                        first_written.set()
                        await tx.rollback()
                        return "rolled_back"
                    await tx.commit()
                    return "created"
                except (asyncpg.RaiseError, asyncpg.SerializationError) as exc:
                    await tx.rollback()
                    return type(exc).__name__
                finally:
                    await connection.close()

            outcomes = await asyncio.wait_for(asyncio.gather(attempt(0), attempt(1)), timeout=20)
            assert outcomes.count("created") == 1, outcomes
            assert set(outcomes) <= {"created", "RaiseError", "SerializationError", "rolled_back"}, outcomes
            await assert_counts(2)
            await owner.execute("DELETE FROM platform.user_ref WHERE id=ANY($1::uuid[])", ids)
            await assert_counts(1)
            return outcomes

        results = {
            "create": await race(),
            "create_vs_restore": await race(restore=True),
            "repeatable_read": await race(isolation="repeatable_read"),
            "rollback": await race(rollback_first=True),
        }
        print("ISOLATED_DEMO_QUOTA_CONCURRENCY_OK", results, flush=True)
    finally:
        if created:
            # Keep referenced audit identities; the surrounding runner drops this DB.
            await owner.execute("UPDATE platform.user_ref SET status='inactive',deleted_at=clock_timestamp() WHERE workspace_id=$1", workspace)
            await owner.execute("UPDATE platform.workspace SET deleted_at=clock_timestamp() WHERE id=$1", workspace)
        await owner.close()


if __name__ == "__main__":
    asyncio.run(verify())
