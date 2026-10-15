"""One-time authorized organization adjustment; read-only plan unless --apply.

The old combined team becomes East because all its actual members/business belong
there. No business ownership IDs or immutable visit snapshots are rewritten.
North is a new empty team with an acting supervisor. Accounts are provisioned
atomically; initial secret arrives on stdin and is never part of the plan/log.
Requires a maintenance PostgreSQL connection and an existing OPSADMIN identity.
"""

import argparse
import asyncio
import base64
import hashlib
import json
import os
import secrets
import sys
from uuid import uuid4

import asyncpg
from sales_backend.auth.passwords import N, R, P, encode_password
from sales_backend.db import _initialize_connection, set_request_context
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.repositories.operations_accounts import OperationsAccountRepository
from sales_backend.request_metadata import RequestMetadata, request_metadata
from sales_backend.services.operations_accounts import OperationsAccountService


class Conflict(ValueError):
    pass


def initial_hash(password, allow_letters):
    if not allow_letters:
        return encode_password(password)
    # This explicit exception applies to the two user-approved initial passwords
    # only. First login still requires a password meeting the normal policy.
    if not 12 <= len(password) <= 128 or not password.isalpha():
        raise Conflict("Expected the explicitly approved letters-only initial password")
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P, maxmem=64 * 1024 * 1024)
    return f"scrypt${N}${R}${P}${base64.b64encode(salt).decode()}${base64.b64encode(key).decode()}"


def plan_for(org):
    teams = {t["code"]: t for t in org["departments"]}
    accounts = {a["account_code"]: a for a in org["accounts"]}
    required = {
        "ALL": "全部团队",
        "NORTH-EAST": "北区东区",
        "SOUTH": "南区",
        "FDE": "FDE",
        "accept0913": "【演示验收0913】验收组",
    }
    if any(k not in teams or teams[k]["name"] != n or teams[k]["status"] != "active" for k, n in required.items()):
        raise Conflict("Original departments no longer match; review current organization")
    if any(k in teams for k in ["EAST", "NORTH", "PRODUCT-A", "PRODUCT-B"]):
        raise Conflict("A target department already exists; do not duplicate or overwrite")
    for code, name in [("ZJ002", "蒋磊"), ("XS003", "周莹")]:
        a = accounts.get(code)
        if not a or a["display_name"] != name or a["status"] != "active" or a["team_id"] != teams["NORTH-EAST"]["id"]:
            raise Conflict("Original members differ from confirmed assignments")
    if any(k in accounts for k in ["CPXS001", "CPXS002"]) or any(
        a["display_name"] in ["薛佳欣", "金豫玮"] for a in accounts.values()
    ):
        raise Conflict("A requested product sales account already exists; verify before provisioning")
    group_id = teams["accept0913"]["id"]
    if any(
        a["status"] == "active" and any(m["team_id"] == group_id for m in a["memberships"]) for a in accounts.values()
    ):
        raise Conflict("Acceptance department still has an active member")
    snapshot = {
        "departments": [
            {k: t.get(k) for k in ["id", "code", "name", "status", "version_no", "parent_team_id", "kind"]}
            for t in org["departments"]
        ],
        "accounts": [
            {
                k: a.get(k)
                for k in [
                    "id",
                    "account_code",
                    "display_name",
                    "status",
                    "version_no",
                    "team_id",
                    "roles",
                    "memberships",
                ]
            }
            for a in org["accounts"]
        ],
    }
    digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    return (
        teams,
        accounts,
        {
            "plan_sha256": digest,
            "rename": "北区东区 → 东区",
            "new_departments": ["北区", "产品销售A", "产品销售B"],
            "assignments": [
                "蒋磊：东区主管、北区代管",
                "周莹：东区销售",
                "薛佳欣：产品销售A主管 / CPXS001",
                "金豫玮：产品销售B主管 / CPXS002",
            ],
            "retire": "【演示验收0913】验收组",
            "business_ids_unchanged": True,
            "historical_targets_reassigned": False,
        },
    )


async def adjust(connection, password="", *, apply=False, expected=None, allow_letters=False):
    if await connection.fetchval("SELECT count(*) FROM platform.workspace") != 1:
        raise Conflict("This one-time plan is only for the existing single-workspace system")
    record = await IdentityRepository().find_maintenance_administrator(
        connection, workspace_external_id="demo-sales-workspace", account_code="OPSADMIN"
    )
    if not record:
        raise Conflict("An existing active OPSADMIN is required")
    actor = record.context
    await set_request_context(connection, actor)
    repo = OperationsAccountRepository()
    await repo.lock_workspace(connection, actor.workspace_id)
    teams, accounts, plan = plan_for(await repo.organization(connection))
    # There are no existing regional team targets to reclassify. Fail closed if
    # someone created one since the read-only review.
    if await connection.fetchval(
        "SELECT count(*) FROM crm.sales_target WHERE team_id=$1::uuid", teams["NORTH-EAST"]["id"]
    ):
        raise Conflict("Combined department has historical targets; review their retention first")
    if await connection.fetchval(
        "SELECT count(*) FROM crm.sales_target_batch_request WHERE team_id=$1::uuid AND status='pending'",
        teams["NORTH-EAST"]["id"],
    ):
        raise Conflict("Combined department has pending target approvals")
    if not apply:
        return {"applied": False, **plan}
    if expected != plan["plan_sha256"]:
        raise Conflict("Organization changed after preview; regenerate and review the plan")
    hashes = [initial_hash(password, allow_letters) for _ in range(2)]
    await repo.save_department(
        connection,
        actor,
        teams["NORTH-EAST"]["id"],
        {**teams["NORTH-EAST"], "code": "EAST", "name": "东区", "kind": "sales"},
    )
    for code, kind in [("SOUTH", "sales"), ("FDE", "fde")]:
        await repo.save_department(connection, actor, teams[code]["id"], {**teams[code], "kind": kind})
    new = {}
    for code, name, kind in [
        ("NORTH", "北区", "sales"),
        ("PRODUCT-A", "产品销售A", "product_sales"),
        ("PRODUCT-B", "产品销售B", "product_sales"),
    ]:
        new[code] = await repo.save_department(
            connection,
            actor,
            None,
            dict(code=code, name=name, kind=kind, parent_team_id=teams["ALL"]["id"], status="active"),
        )
    east = teams["NORTH-EAST"]["id"]
    jiang = accounts["ZJ002"]
    await OperationsAccountService().update(
        connection,
        actor,
        jiang["id"],
        dict(
            display_name="蒋磊",
            status="active",
            version_no=jiang["version_no"],
            team_id=east,
            roles=["supervisor"],
            memberships=[
                dict(team_id=east, roles=["supervisor"]),
                dict(team_id=new["NORTH"]["id"], roles=["supervisor"], acting=True),
            ],
        ),
    )
    created = []
    for code, name, team, encoded in [
        ("CPXS001", "薛佳欣", new["PRODUCT-A"]["id"], hashes[0]),
        ("CPXS002", "金豫玮", new["PRODUCT-B"]["id"], hashes[1]),
    ]:
        data = dict(account_code=code, display_name=name, roles=["supervisor"], team_id=team)
        await OperationsAccountService().validate_organization(connection, data)
        user = await repo.create(connection, actor, data)
        await repo.set_password(connection, user["id"], encoded)
        created.append({"account_code": code, "id": user["id"], "must_change_password": True})
    group = teams["accept0913"]
    await repo.save_department(connection, actor, group["id"], {**group, "status": "inactive"})
    await connection.execute(
        "UPDATE platform.team SET deleted_at=clock_timestamp(),attributes=attributes||$2::jsonb WHERE id=$1::uuid",
        group["id"],
        {"retired_reason": "用户要求移除验收组；关联验收记录保留", "retired_change": "admin-org-20260915"},
    )
    result = {"applied": True, **plan, "created_accounts": created, "request_id": request_metadata.get().request_id}
    await connection.execute(
        """INSERT INTO ops.audit_log(workspace_id,actor_user_ref_id,actor_role_code,action_code,module_code,object_type,object_label,request_id,after_snapshot,result_code)
      VALUES($1::uuid,$2::uuid,'administrator','organization.adjust','organization','team','20260915组织调整',$3::uuid,$4::jsonb,'success')""",
        actor.workspace_id,
        actor.user_id,
        result["request_id"],
        result,
    )
    return result


async def run(args):
    connection = await asyncpg.connect(os.environ["DATABASE_URL"])
    await _initialize_connection(connection)
    token = request_metadata.set(
        RequestMetadata(request_id=str(uuid4()), client_ip="", user_agent="adjust_organization_20260915")
    )
    try:
        async with connection.transaction():
            await connection.execute("SET LOCAL statement_timeout='20s'; SET LOCAL lock_timeout='5s'")
            return await adjust(
                connection,
                sys.stdin.readline().rstrip("\r\n") if args.apply else "",
                apply=args.apply,
                expected=args.expected_plan,
                allow_letters=args.allow_letters_only_initial,
            )
    finally:
        request_metadata.reset(token)
        await connection.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--expected-plan")
    p.add_argument("--allow-letters-only-initial", action="store_true")
    args = p.parse_args()
    try:
        print(json.dumps(asyncio.run(run(args)), ensure_ascii=False, indent=2))
    except Exception as exc:
        print(
            str(exc)
            if isinstance(exc, Conflict)
            else f"Organization adjustment failed ({type(exc).__name__}); transaction rolled back",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
