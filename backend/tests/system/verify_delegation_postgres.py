"""Competing policy saves and preloaded authorization snapshots on independent connections."""

import asyncio
import json
import os
from uuid import uuid4

import asyncpg
from sales_backend.db import set_request_context
from sales_backend.domain.agent import ActorContext, DataScope, RoleCode


async def verify(dsn):
    owner = await asyncpg.connect(dsn)
    if not (await owner.fetchval("SELECT current_database()")).startswith("salegent_verify_integration_"):
        await owner.close()
        raise RuntimeError("Disposable integration database required")
    connections = []
    try:
        ws, admin, editor, target = [uuid4() for _ in range(4)]
        await owner.execute(
            "INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'并发委派隔离验收')",
            ws,
            "delegation-" + uuid4().hex,
        )
        for uid, role in ((admin, "administrator"), (editor, "operations"), (target, "operations")):
            await owner.execute(
                "INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name,account_code) VALUES($1,$2,$3,$3,$3)",
                uid,
                ws,
                str(uid),
            )
            await owner.execute(
                "INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code) VALUES($1,$2,$3,'workspace')",
                ws,
                uid,
                role,
            )
        await owner.execute(
            "INSERT INTO platform.password_credential(workspace_id,user_ref_id,password_hash,must_change_password) VALUES($1,$2,'isolated-unusable',false)",
            ws,
            admin,
        )
        actor = ActorContext(
            workspace_id=str(ws), user_id=str(admin), role=RoleCode.ADMINISTRATOR, data_scope=DataScope.WORKSPACE
        )
        policy = dict(
            version_no=0,
            enabled=True,
            recipient_ids=[str(target)],
            permissions=[dict(permission="opportunity.create", scope="self", team_ids=[])],
            reason="并发上限验收",
        )
        runtime = os.environ["SALES_TEST_ROLE"]
        assert runtime.startswith("salegent_verify_role_") and all(
            x in "0123456789abcdef" for x in runtime.removeprefix("salegent_verify_role_")
        )
        for _ in range(2):
            c = await asyncpg.connect(dsn)
            await c.execute(f'SET ROLE "{runtime}"')
            connections.append(c)

        async def save(c):
            async with c.transaction():
                await set_request_context(c, actor)
                return await c.fetchval(
                    "SELECT security.save_authorization_delegation($1,$2::jsonb)", editor, json.dumps(policy)
                )

        results = await asyncio.gather(*(save(c) for c in connections), return_exceptions=True)
        assert sum(isinstance(r, str) for r in results) == 1
        assert sum(isinstance(r, asyncpg.SerializationError) for r in results) == 1
        print("PASS two concurrent delegation saves accept one version and reject one stale writer")
        async with owner.transaction():
            await set_request_context(owner, actor)
            await owner.execute(
                "SELECT security.save_account_authorization($1,$2::jsonb)",
                editor,
                json.dumps(
                    dict(
                        version_no=0,
                        reason="管理功能与委派分别配置",
                        roles=[],
                        overrides=[
                            dict(
                                permission="authorization.accounts_manage",
                                scope="workspace",
                                team_ids=[],
                                effect="allow",
                            )
                        ],
                    )
                ),
            )
        one, two = connections
        async with one.transaction():
            await set_request_context(
                one,
                ActorContext(
                    workspace_id=str(ws), user_id=str(editor), role=RoleCode.OPERATIONS, data_scope=DataScope.WORKSPACE
                ),
            )
            await one.execute("SELECT security.authorization_refresh()")
            assert await one.fetchval("SELECT security.authorization_has('authorization.accounts_manage')")
            async with two.transaction():
                await set_request_context(two, actor)
                await two.execute(
                    "SELECT security.save_authorization_delegation($1,$2::jsonb)",
                    editor,
                    json.dumps({**policy, "version_no": 1, "enabled": False}),
                )
            try:
                async with one.transaction():
                    await one.execute(
                        "SELECT security.save_account_authorization($1,$2::jsonb)",
                        target,
                        json.dumps(
                            dict(
                                version_no=0,
                                reason="撤权前的旧请求",
                                roles=[],
                                overrides=[
                                    dict(permission="opportunity.create", scope="self", team_ids=[], effect="allow")
                                ],
                            )
                        ),
                    )
            except asyncpg.InsufficientPrivilegeError:
                pass
            else:
                raise AssertionError("A preloaded request must recheck revoked authority under the management lock")
        assert not await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM config.account_authorization WHERE user_ref_id=$1)", target
        )
        print("PASS a preloaded authorization snapshot cannot save after delegation revocation commits")
    finally:
        for c in connections:
            await c.close()
        await owner.close()


if __name__ == "__main__":
    asyncio.run(verify(os.environ["SALES_TEST_DATABASE_URL"]))
