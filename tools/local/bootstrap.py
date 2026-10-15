"""Initialize only the marked loopback cluster; never accepts a database URL."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid

import asyncpg
from local_paths import state_from_config

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "backend/src"), str(ROOT / "database/scripts"), str(ROOT / "tools/installation")]
from migrate import discover, item, migrate
from runtime_roles import identifier, load_policy, verify_service_roles
from upgrade_permissions import reconcile_service_roles
from sales_backend.auth.passwords import encode_password

LOCAL_SERVICES = frozenset({"api", "worker", "platform"})
LOCAL_ROLES = {service: "salesbuddy_local_" + service for service in ("api", "worker", "platform")}


async def reconcile_local_runtime(connection, cfg, *, roles=None):
    """Atomically replace legacy local grants with the product service policy.

    The CLI validates the owned fixed loopback cluster before calling this.
    Explicit role names also allow disposable-database verification of the same
    code without connecting to the user's daily environment. Existing passwords,
    business rows, memberships and maintenance ownership are never rewritten.
    """
    roles = LOCAL_ROLES if roles is None else roles
    if set(roles) != LOCAL_SERVICES or len(set(roles.values())) != len(LOCAL_SERVICES):
        raise RuntimeError("Local runtime requires distinct API, worker and platform identities")
    policy = load_policy()
    async with connection.transaction():
        database = await connection.fetchval("SELECT current_database()")
        for service, role in roles.items():
            quoted = identifier(role)
            if not await connection.fetchval("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=$1)", role):
                password = await connection.fetchval("SELECT quote_literal($1::text)", cfg[service + "_password"])
                await connection.execute(f'CREATE ROLE {quoted} LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD {password}')
            await connection.execute(f'GRANT CONNECT ON DATABASE {identifier(database)} TO {quoted}')
            await connection.execute(f'ALTER ROLE {quoted} IN DATABASE {identifier(database)} SET search_path=pg_catalog,public')
        await connection.execute(f'REVOKE ALL ON DATABASE {identifier(database)} FROM PUBLIC')
        await connection.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
        return await reconcile_service_roles(connection, roles, policy)


def identity(name):
    return uuid.uuid5(uuid.NAMESPACE_URL, "salesbuddy-local-only/" + name)


def render_account_guide(accounts, test_password):
    """Only browser fixture credentials belong in this private user guide."""
    rows = '\n'.join('| ' + x['workspace'] + ' | ' + x['account'] + ' | ' + x['role'] + ' |'
                     for x in accounts)
    return ("# 本机合成验收账号\n\n仅适用于 http://127.0.0.1:18800，不能登录生产。\n\n"
        + "本机初始浏览器登录密码：`" + test_password + "`\n\n"
        + "以上初始密码适用于下列公司合成账号和 PLATFORMADMIN。已人工改密的账号请使用新密码；"
          "启动或升级不会重置账号密码或业务数据。\n\n"
        + "## 公司后台\n\n访问 http://127.0.0.1:18800/admin，填写公司码、账号和密码。\n\n"
          "| 公司码 | 账号 | 角色 |\n|---|---|---|\n" + rows + "\n\n"
        + "## 平台管理\n\n访问 http://127.0.0.1:18800/platform。\n\n"
          "- 平台账号：`PLATFORMADMIN`。\n"
          "- 初始密码：与上方“本机初始浏览器登录密码”相同；已改密则使用新密码。\n"
          "- 动态验证码：在源码目录运行 `python3 tools/local/dev.py code`，填写当前六位码。\n\n"
          "同一个动态码只能使用一次；已登录或完成敏感操作验证后，请等待下一轮新码。"
          "平台身份与公司账号独立，平台登录不需要公司码。\n")


def write_account_guide(path, accounts, test_password):
    if path.is_symlink():
        raise RuntimeError('Local account guide must not be a symlink')
    fd, temporary = tempfile.mkstemp(prefix='.TEST_ACCOUNTS.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(render_account_guide(accounts, test_password))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


async def connect(cfg, database="salesbuddy_local", service="owner"):
    return await asyncpg.connect(host="127.0.0.1", port=15432, database=database,
                                 user="salesbuddy_local_" + service, password=cfg[service + "_password"],
                                 command_timeout=300)


async def owner(cfg):
    if cfg.get("kind") != "salesbuddy-local-v1" or cfg.get("db_port") != 15432:
        raise RuntimeError("Only the fixed local environment may be initialized")
    admin = await connect(cfg, "postgres")
    actual = Path(await admin.fetchval("SHOW data_directory")).resolve()
    expected = (state_from_config(cfg) / "postgres").resolve()
    if actual != expected:
        await admin.close()
        raise RuntimeError("Database belongs to another environment; refused")
    return admin


async def seed(connection, cfg):
    await connection.execute("""CREATE TABLE IF NOT EXISTS public.salesbuddy_local_fixture
        (id integer PRIMARY KEY CHECK(id=1), version integer NOT NULL, data jsonb NOT NULL);
        REVOKE ALL ON public.salesbuddy_local_fixture FROM PUBLIC;""")
    previous = await connection.fetchval("SELECT data FROM public.salesbuddy_local_fixture WHERE id=1")
    if previous:
        return json.loads(previous)
    if await connection.fetchval("SELECT count(*) FROM platform.workspace"):
        raise RuntimeError("Unrecognized data: refusing to insert fixtures into a nonempty database")
    result = {"companies": [], "accounts": []}
    async with connection.transaction():
        for prefix, title in (("A", "本地测试甲公司"), ("B", "本地测试乙公司")):
            ws = identity(prefix)
            team = identity(prefix + "-sales")
            fde_team = identity(prefix + "-fde")
            code = "local-" + prefix.lower()
            await connection.execute("""INSERT INTO platform.workspace(id,external_workspace_id,name,attributes)
                VALUES($1,$2,$3,'{"local_synthetic":true}'::jsonb)""", ws, code, title)
            for tid, tcode, name in ((team, "sales", "测试销售团队"), (fde_team, "fde", "测试产品团队")):
                await connection.execute("INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,$3,$4)", tid, ws, tcode, name)
            for suffix, role, scope in (("ADMIN", "administrator", "workspace"), ("SALES", "sales", "self"),
                                        ("MANAGER", "manager", "workspace"), ("FDE", "fde", "self"),
                                        ("OPS", "operations", "workspace")):
                uid = identity(prefix + suffix)
                account = prefix + suffix
                tid = fde_team if role == "fde" else team
                await connection.execute("""INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name,account_code)
                    VALUES($1,$2,$3,$4,$3)""", uid, ws, account, title + "-" + suffix)
                await connection.execute("""INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id)
                    VALUES($1,$2,$3,$4,$5)""", ws, uid, role, scope, None if role in {"administrator", "operations"} else tid)
                if role not in {"administrator", "operations"}:
                    await connection.execute("""INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role)
                        VALUES($1,$2,$3,$4)""", ws, uid, tid, role)
                await connection.execute("""INSERT INTO platform.password_credential(user_ref_id,workspace_id,password_hash,must_change_password)
                    VALUES($1,$2,$3,false)""", uid, ws, encode_password(cfg["test_password"]))
                result["accounts"].append({"workspace": code, "account": account, "role": role})
            customer = identity(prefix + "-customer")
            await connection.execute("""INSERT INTO crm.customer(id,workspace_id,name,normalized_name,owner_user_ref_id,
                owner_team_id,created_by_user_ref_id,level_code) VALUES($1,$2,$3,$3,$4,$5,$4,'Tier-2')""",
                customer, ws, title + "-合成客户", identity(prefix + "SALES"), team)
            result["companies"].append({"workspace": code, "id": str(ws), "customer_id": str(customer), "name": title})
        await connection.execute("INSERT INTO public.salesbuddy_local_fixture VALUES(1,1,$1::jsonb)", json.dumps(result))
    return result


async def verify(conn, cfg):
    db = ROOT / 'database'
    expected = [item('BASELINE_V1', db / 'baseline/V001__current_schema.sql'),
                *(item(p.stem.split('__', 1)[0], p) for p in sorted((db / 'seeds').glob('S*.sql'))), *discover(db)]
    actual = {r['migration_key']: r['checksum_sha256'] for r in await conn.fetch(
        'SELECT migration_key,checksum_sha256 FROM ops.migration_checksum')}
    if actual != {m.key: m.checksum for m in expected}:
        raise RuntimeError('Database migration checksums do not match this build; check did not modify the database')
    runtime_policy = await verify_service_roles(conn, LOCAL_ROLES)
    for service in ("api", "worker"):
        role = "salesbuddy_local_" + service
        row = await conn.fetchrow("SELECT rolsuper,rolbypassrls,rolcreaterole,rolcreatedb FROM pg_roles WHERE rolname=$1", role)
        if not row or any(row.values()):
            raise RuntimeError("Runtime role has elevated database privileges")
        for table in ("platform.password_credential", "security.login_throttle", "ops.migration_checksum"):
            if await conn.fetchval("SELECT has_table_privilege($1,$2,'SELECT')", role, table):
                raise RuntimeError("Runtime role can read private credentials or deployment ledger")
        if await conn.fetchval("SELECT has_function_privilege($1,'security.reconcile_runtime_grants()','EXECUTE')", role):
            raise RuntimeError("Runtime role can reconcile its own grants")
        runtime = await connect(cfg, service=service)
        try:
            if await runtime.fetchval("SELECT count(*) FROM crm.customer"):
                raise RuntimeError("Customer data visible without tenant identity")
        finally:
            await runtime.close()
    print(json.dumps({"database": "salesbuddy_local", "migrations": len(expected),
                      "schema": expected[-1].key, "runtime_roles": "NOSUPERUSER NOBYPASSRLS",
                      "credentials_private": True, "no_tenant_context": "no_customers",
                      "runtime_policy": runtime_policy}))


async def main():
    action, filename = sys.argv[1:]
    cfg = json.loads(Path(filename).read_text())
    admin = await owner(cfg)
    try:
        exists = await admin.fetchval("SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname='salesbuddy_local')")
        if not exists:
            if action != "init":
                raise RuntimeError("Local database has not been initialized")
            await admin.execute("CREATE DATABASE salesbuddy_local TEMPLATE template0")
    finally:
        await admin.close()
    conn = await connect(cfg)
    try:
        if action == "init":
            await migrate(conn)
            await reconcile_local_runtime(conn, cfg)
            await conn.execute("""INSERT INTO control.operator(account_code,display_name,password_hash)
                VALUES('PLATFORMADMIN','本机平台管理员',$1) ON CONFLICT(account_code) DO NOTHING""", encode_password(cfg['test_password']))
            import base64
            from sales_backend.security.platform_mfa import PlatformMFACipher
            operator = await conn.fetchrow("SELECT id,mfa_secret FROM control.operator WHERE account_code='PLATFORMADMIN'")
            if operator['mfa_secret'] is None:
                cipher = PlatformMFACipher.from_file(str(state_from_config(cfg) / 'platform-mfa-keys.json'), 'local-v1')
                await conn.execute('UPDATE control.operator SET mfa_secret=$2::jsonb,mfa_revision=mfa_revision+1 WHERE id=$1', operator['id'], json.dumps(cipher.seal(str(operator['id']),base64.b32decode(cfg['platform_totp_seed']))))
            fixtures = await seed(conn, cfg)
            local = state_from_config(cfg)
            (local / "fixtures.json").write_text(json.dumps(fixtures, ensure_ascii=False, indent=2))
            write_account_guide(local / "TEST_ACCOUNTS.md", fixtures['accounts'], cfg['test_password'])
        await verify(conn, cfg)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
