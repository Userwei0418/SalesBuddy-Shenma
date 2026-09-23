"""Run once as postgres OS user on customer server. Creates no sample CRM data."""

import asyncio, json, os, secrets
from pathlib import Path
import asyncpg
from sales_backend.auth.passwords import encode_password
from uuid import uuid4


async def bootstrap(c, target: Path):
    assert await c.fetchval("SELECT rolsuper FROM pg_roles WHERE rolname=current_user")
    password = secrets.token_urlsafe(24)
    uid = uuid4()
    if target.exists():
        raise RuntimeError("Credential receipt exists; inspect before provisioning")
    async with c.transaction():
        if await c.fetchval("SELECT count(*) FROM platform.workspace"):
            raise RuntimeError(
                "Company already exists; refusing to recreate or change accounts"
            )
        wid = await c.fetchval(
            "INSERT INTO platform.workspace(external_workspace_id,name,attributes) VALUES('shenzhoukuntai','神州鲲泰','{\"kind\":\"production\"}'::jsonb) RETURNING id"
        )
        tid = await c.fetchval(
            "INSERT INTO platform.team(workspace_id,code,name) VALUES($1,'management','管理部门') RETURNING id",
            wid,
        )
        await c.execute(
            "SELECT set_config('app.workspace_id',$1,true),set_config('app.user_ref_id',$2,true),set_config('app.role_code','administrator',true)",
            str(wid),
            str(uid),
        )
        await c.execute(
            "INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name) VALUES($1,$2,$3,'CUSTOMERADMIN','神码管理员')",
            uid,
            wid,
            "local:" + str(uid),
        )
        await c.execute(
            "INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) VALUES($1,$2,'administrator','workspace',$3)",
            wid,
            uid,
            tid,
        )
        await c.execute(
            "INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) VALUES($1,$2,$3,'administrator')",
            wid,
            uid,
            tid,
        )
        await c.execute(
            "INSERT INTO platform.password_credential(user_ref_id,workspace_id,password_hash,must_change_password) VALUES($1,$2,$3,true)",
            uid,
            wid,
            encode_password(password),
        )
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(
                {
                    "account": "CUSTOMERADMIN",
                    "password": password,
                    "must_change_password": True,
                },
                f,
            )
    print(
        "Customer company and first administrator created; credential kept in protected server file"
    )


async def main():
    c = await asyncpg.connect(
        host="/var/run/postgresql", database="shenma_sales", user="postgres"
    )
    try:
        await bootstrap(c, Path("/var/lib/shenma-provision/initial-admin.json"))
    finally:
        await c.close()


if __name__ == "__main__":
    asyncio.run(main())
