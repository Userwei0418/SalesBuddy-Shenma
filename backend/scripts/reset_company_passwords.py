"""Authorized maintenance: preview exact accounts, then reset with a stdin secret.

Uses existing administrator grants and account mutations in one transaction.
Never provisions a credential for delegated platform identities or changes data.
"""

import argparse
import asyncio
import hashlib
import json
import sys
from uuid import uuid4

import asyncpg

from sales_backend.auth.passwords import encode_password, validate_password, verify_password
from sales_backend.db import _initialize_connection, set_request_context
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.repositories.operations_accounts import OperationsAccountRepository
from sales_backend.request_metadata import RequestMetadata, request_metadata


async def reset_companies(connection, workspaces, *, source_workspace, administrator,
                          apply=False, expected=None, password=""):
    identity = IdentityRepository()
    source = await identity.find_maintenance_administrator(
        connection, workspace_external_id=source_workspace, account_code=administrator,
    )
    if not source:
        raise ValueError("An existing active administrator is required")
    repo = OperationsAccountRepository()
    plans, actors, targets = [], {}, {}
    for code in sorted(set(workspaces)):
        await set_request_context(connection, source.context)
        wid = await connection.fetchval(
            "SELECT id::text FROM platform.workspace WHERE external_workspace_id=$1 AND status='active' AND deleted_at IS NULL",
            code,
        )
        if not wid:
            raise ValueError("Requested company is missing or inactive")
        actor = source.context
        if wid != actor.workspace_id:
            delegated = await connection.fetchval("SELECT security.company_management_actor($1::uuid)", wid)
            if not delegated:
                raise ValueError("Administrator has no explicit grant to requested company")
            actor = identity._actor(delegated).context
        actors[code] = actor
        await set_request_context(connection, actor)
        await repo.lock_workspace(connection, wid)
        rows = await connection.fetch("""SELECT u.id::text,u.account_code,u.version_no,u.status,
            p.password_changed_at::text FROM platform.user_ref u
            JOIN platform.password_credential p ON p.user_ref_id=u.id AND p.workspace_id=u.workspace_id
            WHERE u.workspace_id=$1::uuid AND u.deleted_at IS NULL
            AND COALESCE(u.attributes->>'platform_managed','false')<>'true'
            ORDER BY u.id FOR UPDATE OF u,p""", wid)
        if not rows:
            raise ValueError("Requested company has no password accounts")
        targets[code] = rows
        plans.append({"company": code, "workspace_id": wid, "policy": await repo.password_policy(connection),
                      "accounts": [dict(r) for r in rows]})
    digest = hashlib.sha256(json.dumps(plans, sort_keys=True).encode()).hexdigest()
    result = {"applied": False, "plan_sha256": digest,
              "companies": [{"company": p["company"], "accounts": len(p["accounts"])} for p in plans],
              "total_accounts": sum(len(p["accounts"]) for p in plans), "require_initial_change": False}
    if not apply:
        return result
    if not expected or digest != expected:
        raise ValueError("Account or policy state changed; review a fresh plan")
    validate_password(password)
    # Independent salts, generated only after validating the reviewed scope.
    for plan in plans:
        actor = actors[plan["company"]]
        await set_request_context(connection, actor)
        if plan["policy"]["require_initial_change"]:
            await repo.set_password_policy(connection, False, plan["policy"]["version_no"])
        for row in targets[plan["company"]]:
            encoded = await asyncio.to_thread(encode_password, password)
            await repo.set_password(connection, row["id"], encoded)
            await repo.bump_version(connection, row["id"])
            stored = await connection.fetchval(
                "SELECT password_hash FROM platform.password_credential WHERE user_ref_id=$1::uuid", row["id"]
            )
            if not await asyncio.to_thread(verify_password, password, stored):
                raise ValueError("Credential verification failed; transaction rolled back")
        await connection.execute("""INSERT INTO ops.audit_log(workspace_id,actor_user_ref_id,actor_role_code,
            action_code,module_code,object_type,object_id,object_label,request_id,after_snapshot,changed_fields)
            VALUES($1::uuid,$2::uuid,'administrator','account.password_batch','platform','workspace',$1::uuid,
            '授权批量重置账号密码',$3::uuid,$4::jsonb,ARRAY['password','require_initial_change'])""",
            actor.workspace_id, actor.user_id, str(uuid4()),
            {"account_count": len(targets[plan["company"]]), "require_initial_change": False,
             "sessions_revoked": True, "plan_sha256": digest})
    return {**result, "applied": True, "credential_verification": "passed", "sessions_revoked": True}


async def main(args):
    request_metadata.set(RequestMetadata(request_id=str(uuid4()), user_agent="authorized-password-maintenance"))
    connection = await asyncpg.connect(host="/var/run/postgresql", database="shenma_sales", user="postgres")
    await _initialize_connection(connection)
    try:
        async with connection.transaction():
            result = await reset_companies(connection, args.workspace, source_workspace=args.source_workspace,
                administrator=args.administrator, apply=args.apply, expected=args.expected_plan,
                password=sys.stdin.readline().rstrip("\r\n") if args.apply else "")
        print(json.dumps(result, ensure_ascii=False))
    finally:
        await connection.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", action="append", required=True)
    parser.add_argument("--source-workspace", required=True)
    parser.add_argument("--administrator", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-plan")
    try:
        asyncio.run(main(parser.parse_args()))
    except Exception as exc:
        # Database diagnostics can contain password hashes; never echo them.
        raise SystemExit(f"Password maintenance failed ({type(exc).__name__}); no credentials logged") from None
