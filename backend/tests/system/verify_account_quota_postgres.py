"""Independent-connection quota races, restricted to a disposable integration DB."""

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
    workspaces, people = [uuid4(), uuid4()], [uuid4(), uuid4()]
    original_limit = None
    try:
        name = await owner.fetchval("SELECT current_database()")
        if not re.fullmatch(r"salegent_verify_integration_[a-f0-9]+", name):
            raise RuntimeError("Disposable integration database required")
        original_limit = await owner.fetchval("SELECT max_active_accounts FROM security.deployment_account_quota")
        for workspace, person in zip(workspaces, people):
            await owner.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'额度并发隔离公司')", workspace, str(workspace))
            await owner.execute("INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name) VALUES($1,$2,$3,'隔离管理员')", person, workspace, str(person))
            await owner.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code) VALUES($1,$2,'administrator','workspace')", workspace, person)

        async def assert_counter(expected):
            row = await owner.fetchrow("""SELECT active_accounts,(SELECT count(*) FROM platform.user_ref
                WHERE status='active' AND deleted_at IS NULL AND COALESCE(attributes->>'platform_managed','false')<>'true') AS actual
                FROM security.deployment_account_quota""")
            assert row["active_accounts"] == row["actual"] == expected, dict(row)

        async def race(*, restore=False, isolation="read_committed", rollback_first=False):
            ids = [uuid4(), uuid4()]
            if restore:
                await owner.execute("INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name,status) VALUES($1,$2,$3,'等待启用','inactive')", ids[1], workspaces[1], str(ids[1]))
            initial = await owner.fetchval("SELECT active_accounts FROM security.deployment_account_quota")
            await owner.execute("UPDATE security.deployment_account_quota SET max_active_accounts=$1", initial + 1)
            ready, gate, first_written = 0, asyncio.Event(), asyncio.Event()

            async def attempt(index):
                nonlocal ready
                connection = await asyncpg.connect(dsn)
                tx = connection.transaction(isolation=isolation)
                await tx.start()
                try:
                    await connection.execute(f'SET LOCAL ROLE "{runtime}"')
                    assert not await connection.fetchval("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
                    actor = ActorContext(workspace_id=str(workspaces[index]), user_id=str(people[index]),
                                         role=RoleCode.ADMINISTRATOR, data_scope="workspace", team_ids=())
                    await set_request_context(connection, actor)
                    # Materialize each REPEATABLE READ snapshot before the race.
                    await connection.fetchval("SELECT count(*) FROM platform.user_ref")
                    ready += 1
                    if ready == 2:
                        gate.set()
                    await gate.wait()
                    if rollback_first and index == 1:
                        await first_written.wait()
                    if index == 0:
                        # Exercise the normal repository lock order against direct SQL.
                        await OperationsAccountRepository().lock_workspace(connection, str(workspaces[index]))
                    if restore and index == 1:
                        await connection.execute("UPDATE platform.user_ref SET status='active' WHERE id=$1", ids[index])
                    else:
                        await connection.execute("INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name) VALUES($1,$2,$3,'并发体验账号')", ids[index], workspaces[index], str(ids[index]))
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
            await assert_counter(initial + 1)
            await owner.execute("DELETE FROM platform.user_ref WHERE id=ANY($1::uuid[])", ids)
            await assert_counter(initial)
            return outcomes

        results = {
            "cross_company_create": await race(),
            "create_vs_restore": await race(restore=True),
            "repeatable_read": await race(isolation="repeatable_read"),
            "rollback": await race(rollback_first=True),
        }
        print("ACCOUNT_QUOTA_CONCURRENCY_OK", results, flush=True)
    finally:
        if original_limit is not None:
            try:
                # Immutable audit rows reference these actors. Retire their
                # synthetic companies; the isolated runner drops the whole DB.
                await owner.execute("UPDATE platform.user_ref SET status='inactive',deleted_at=clock_timestamp() WHERE workspace_id=ANY($1::uuid[])", workspaces)
                await owner.execute("UPDATE platform.workspace SET deleted_at=clock_timestamp() WHERE id=ANY($1::uuid[])", workspaces)
            finally:
                await owner.execute("UPDATE security.deployment_account_quota SET max_active_accounts=$1", original_limit)
        await owner.close()


if __name__ == "__main__":
    asyncio.run(verify())
