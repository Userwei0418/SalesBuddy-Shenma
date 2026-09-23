"""Split an explicitly approved demo/company administrator delegation atomically.

DB maintenance owner required for grant revocation. Preview is the default. Apply
requires its exact state digest; password is read once from stdin, never the plan.
No company names, account names, production IDs or credentials are hard-coded.
The existing business person's appointments and identity remain untouched.
"""

import argparse
import asyncio
import hashlib
import json
import sys
from uuid import uuid4

import asyncpg
from pydantic import TypeAdapter

from sales_backend.auth.passwords import encode_password
from sales_backend.contracts.operations import AccountCode
from sales_backend.db import _initialize_connection, set_request_context
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.repositories.operations_accounts import OperationsAccountRepository
from sales_backend.services.operations_accounts import OperationsAccountService


async def run(connection, plan, apply=False, expected=None, password=""):
    repo = OperationsAccountRepository()
    identities = IdentityRepository()
    source = await identities.find_maintenance_administrator(
        connection, workspace_external_id=plan["source_company"], account_code=plan["source_account"]
    )
    if not source or source.context.user_id != plan["source_user_id"]:
        raise ValueError("Source administrator identity differs from approved plan")
    await set_request_context(connection, source.context)
    wid = await connection.fetchval(
        "SELECT id::text FROM platform.workspace WHERE external_workspace_id=$1 AND status='active' AND deleted_at IS NULL",
        plan["target_company"],
    )
    if not wid or wid == source.context.workspace_id:
        raise ValueError("Two distinct active companies required")
    for workspace in sorted([source.context.workspace_id, wid]):
        await repo.lock_workspace(connection, workspace)
    delegated = await connection.fetchval("SELECT security.company_management_actor($1::uuid)", wid)
    if not delegated:
        raise PermissionError("Expected existing delegation is absent")
    proxy = identities._actor(delegated).context
    if proxy.user_id != plan["retired_proxy_id"]:
        raise ValueError("Delegated identity differs from approved plan")
    grants = [
        dict(r)
        for r in await connection.fetch(
            """SELECT source_workspace_id::text,source_user_id::text,
        target_workspace_id::text,target_user_id::text FROM security.company_management_grant
        WHERE status='active' AND (source_user_id=$1::uuid OR target_user_id=$2::uuid)""",
            source.context.user_id,
            proxy.user_id,
        )
    ]
    if (
        len(grants) != 1
        or grants[0]["target_user_id"] != proxy.user_id
        or grants[0]["source_user_id"] != source.context.user_id
    ):
        raise ValueError("Unexpected grants; review the entire delegation before changing it")
    await set_request_context(connection, source.context)
    source_row = next(a for a in (await repo.organization(connection))["accounts"] if a["id"] == source.context.user_id)
    if source_row["platform_managed"]:
        raise ValueError("Source must be an independent password account")
    await set_request_context(connection, proxy)
    org = await repo.organization(connection)
    business = next((a for a in org["accounts"] if a["id"] == plan["business_user_id"]), None)
    old_proxy = next((a for a in org["accounts"] if a["id"] == proxy.user_id), None)
    if (
        not business
        or business["account_code"] != plan["business_account"].upper()
        or business["status"] != "active"
        or business["platform_managed"]
    ):
        raise ValueError("Existing business identity differs from plan")
    if not old_proxy or not old_proxy["platform_managed"]:
        raise ValueError("Only the managed proxy may be retired")
    new_code = TypeAdapter(AccountCode).validate_python(plan["new_account"]).upper()
    # All-company collision check avoids ambiguous company-free login later.
    if await connection.fetchval(
        "SELECT EXISTS(SELECT 1 FROM platform.user_ref WHERE deleted_at IS NULL AND (account_code=$1 OR phone_number=$1))",
        new_code,
    ):
        raise ValueError("New account or phone identifier already exists")
    if not plan["new_name"].strip() or len(plan["new_name"]) > 100:
        raise ValueError("New administrator name required (1-100 characters)")
    if await connection.fetchval(
        "SELECT EXISTS(SELECT 1 FROM security.company_management_grant WHERE source_user_id=$1::uuid AND status='active')",
        business["id"],
    ):
        raise ValueError("Business identity already has cross-company grants")
    snapshot = {
        "plan": plan,
        "source": source_row,
        "business": business,
        "proxy": old_proxy,
        "grants": grants,
        "departments": org["departments"],
    }
    digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, default=str).encode()).hexdigest()
    result = {
        "applied": False,
        "plan_sha256": digest,
        "independent_administrators": 3,
        "retired_proxies": 1,
        "revoked_grants": 1,
    }
    if not apply:
        return result
    if digest != expected:
        raise ValueError("Plan/state changed; preview again")
    # Validate the secret before the first write; independent salts per account.
    hashes = [await asyncio.to_thread(encode_password, password) for _ in range(2)]
    created = await OperationsAccountService().create(
        connection,
        proxy,
        dict(
            account_code=new_code,
            display_name=plan["new_name"],
            roles=["administrator", "operations"],
            company_roles=["administrator", "operations"],
            team_id=None,
            memberships=[],
        ),
        password,
    )
    await connection.execute(
        """INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code)
        SELECT $1::uuid,$2::uuid,'administrator','workspace' WHERE NOT EXISTS(
        SELECT 1 FROM platform.role_binding WHERE user_ref_id=$2::uuid AND role_code='administrator'
        AND team_id IS NULL AND clock_timestamp()>=valid_from AND clock_timestamp()<valid_to)""",
        wid,
        business["id"],
    )
    await repo.set_password(connection, business["id"], hashes[0])
    await repo.bump_version(connection, business["id"])
    after = next(a for a in (await repo.organization(connection))["accounts"] if a["id"] == business["id"])
    for field in ("id", "account_code", "email", "phone_number", "display_name", "memberships", "team_id"):
        if business[field] != after[field]:
            raise RuntimeError("Business identity or appointments changed: " + field)
    await connection.execute(
        "UPDATE security.company_management_grant SET status='revoked' WHERE source_user_id=$1::uuid AND target_user_id=$2::uuid AND status='active'",
        source.context.user_id,
        proxy.user_id,
    )
    await connection.execute(
        "UPDATE platform.auth_session SET status='revoked',revoked_at=clock_timestamp() WHERE user_ref_id=$1::uuid AND status='active'",
        proxy.user_id,
    )
    await connection.execute(
        "UPDATE platform.user_ref SET status='inactive',version_no=version_no+1,updated_at=clock_timestamp() WHERE id=$1::uuid",
        proxy.user_id,
    )
    # Audit the target using its new independent administrator, not a dead proxy.
    new_admin = await identities.find_maintenance_administrator(
        connection, workspace_external_id=plan["target_company"], account_code=new_code
    )
    if not new_admin:
        raise RuntimeError("New company administrator could not be resolved")
    result = {**result, "applied": True, "business_identity_preserved": True, "new_administrator_id": created["id"]}
    for admin in (new_admin, source):
        await set_request_context(connection, admin.context)
        if admin is source:
            await repo.set_password(connection, source.context.user_id, hashes[1])
            await repo.bump_version(connection, source.context.user_id)
        companies = await connection.fetchval("SELECT security.company_directory()")
        if isinstance(companies, str):
            companies = json.loads(companies)
        if [c["id"] for c in companies] != [admin.context.workspace_id]:
            raise RuntimeError("Administrator still sees another company")
        await connection.execute(
            """INSERT INTO ops.audit_log(workspace_id,actor_user_ref_id,actor_role_code,action_code,
            module_code,object_type,object_id,object_label,request_id,after_snapshot,changed_fields)
            VALUES($1::uuid,$2::uuid,'administrator','organization.admin_isolation','platform','workspace',$1::uuid,
            'Approved administrator isolation',$3::uuid,$4::jsonb,ARRAY['roles','company_management_grant','credentials'])""",
            admin.context.workspace_id,
            admin.context.user_id,
            str(uuid4()),
            result,
        )
    return result


async def main(args):
    plan = json.loads(open(args.plan, encoding="utf8").read())
    password = sys.stdin.readline().rstrip("\r\n") if args.apply else ""
    c = await asyncpg.connect(host=args.socket, database=args.database, user=args.db_user)
    await _initialize_connection(c)
    try:
        async with c.transaction(readonly=not args.apply):
            result = await run(c, plan, args.apply, args.expected_plan, password)
        print(json.dumps(result))
    finally:
        await c.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--plan", required=True)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--expected-plan")
    p.add_argument("--socket", default="/var/run/postgresql")
    p.add_argument("--database", default="shenma_sales")
    p.add_argument("--db-user", default="postgres")
    asyncio.run(main(p.parse_args()))
