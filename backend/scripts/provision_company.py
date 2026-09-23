"""Initialize an empty company from a reviewed organization plan; maintenance CLI only.

Preview by default. Apply needs the exact plan digest and an initial password on
stdin. Copies identity/organization and enum definitions only, never CRM data,
sessions, credentials, task history or Agent execution history/bindings.
"""

import argparse
import asyncio
import hashlib
import json
import re
import sys
from uuid import uuid4

import asyncpg

from sales_backend.db import _initialize_connection, set_request_context
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.repositories.operations_accounts import OperationsAccountRepository
from sales_backend.request_metadata import RequestMetadata, request_metadata
from sales_backend.auth.passwords import encode_password


def initial_hash(password, allow_letters=False):
    # Customer fork uses the current common policy, with no historical exception.
    return encode_password(password)


async def provision(connection, manifest, *, apply=False, expected=None, password="", allow_letters=False):
    source = manifest["source_company"]
    target = manifest["target_company"]
    if not re.fullmatch(r"[a-z][a-z0-9-]{2,63}", target["code"]):
        raise ValueError("Invalid company code")
    if not target["name"].strip() or len(target["name"]) > 100:
        raise ValueError("Invalid company name")
    admin = await IdentityRepository().find_maintenance_administrator(
        connection, workspace_external_id=source, account_code=manifest["administrator_account"]
    )
    if not admin:
        raise ValueError("Existing administrator required")
    await set_request_context(connection, admin.context)
    await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended('company-provisioning',0))")
    if await connection.fetchval(
        "SELECT EXISTS(SELECT 1 FROM platform.workspace WHERE external_workspace_id=$1)", target["code"]
    ):
        raise ValueError("Target company already exists; no overwrite or duplicate provisioning")
    repository = OperationsAccountRepository()
    await repository.lock_workspace(connection, admin.context.workspace_id)
    org = await repository.organization(connection)
    teams = [t for t in org["departments"] if t["status"] == "active"]
    people = [
        u
        for u in org["accounts"]
        if u["status"] == "active" and u["id"] != admin.context.user_id and not u.get("platform_managed")
    ]
    if any(set(p["roles"]) & {"administrator", "operations"} for p in people):
        raise ValueError("Additional management accounts require a separate reviewed plan")
    mapping = manifest["accounts"]
    if set(mapping) != {p["account_code"] for p in people}:
        raise ValueError("Account mapping must exactly match active business personnel")
    codes = []
    for person in people:
        entry = mapping[person["account_code"]]
        if entry["name"] != person["display_name"] or not re.fullmatch(r"[a-z][a-z0-9]{2,63}", entry["account"]):
            raise ValueError("Reviewed name/pinyin account no longer matches")
        codes.append(entry["account"].upper())
        if not person["memberships"] or person["team_id"] not in {t["id"] for t in teams}:
            raise ValueError("Every person requires an active primary department")
    if len(set(codes)) != len(codes):
        raise ValueError("Pinyin accounts collide")
    if await connection.fetchval(
        """SELECT EXISTS(SELECT 1 FROM platform.user_ref u JOIN platform.workspace w ON w.id=u.workspace_id
      WHERE u.account_code=ANY($1::text[]) AND u.deleted_at IS NULL AND u.status='active' AND w.status='active')""",
        codes,
    ):
        raise ValueError("Pinyin account exists in another company; review unique login routing")
    snapshot = {"manifest": manifest, "teams": teams, "people": people, "source_admin": admin.context.user_id}
    digest = hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
    result = {
        "applied": False,
        "plan_sha256": digest,
        "name": target["name"],
        "business_accounts": len(people),
        "departments": len(teams),
        "copies_business_data": False,
    }
    if not apply:
        return result
    if expected != digest:
        raise ValueError("Organization changed since preview; review a fresh plan")
    hashes = {p["id"]: initial_hash(password, allow_letters) for p in people}
    wid = str(uuid4())
    managed_id = str(uuid4())
    await connection.execute(
        """INSERT INTO platform.workspace(id,external_workspace_id,name,attributes)
      VALUES($1::uuid,$2,$3,$4::jsonb)""",
        wid,
        target["code"],
        target["name"],
        {"kind": "production", "initialized_from_organization": source},
    )
    await connection.execute(
        """UPDATE platform.workspace SET name='演示公司',attributes=attributes||'{"kind":"demo"}'::jsonb,
      version_no=version_no+1 WHERE id=$1::uuid""",
        admin.context.workspace_id,
    )
    team_ids = {t["id"]: str(uuid4()) for t in teams}
    for t in teams:
        await connection.execute(
            """INSERT INTO platform.team(id,workspace_id,code,name,attributes)
          VALUES($1::uuid,$2::uuid,$3,$4,$5::jsonb)""",
            team_ids[t["id"]],
            wid,
            t["code"],
            t["name"],
            {"kind": t.get("kind", "general")},
        )
    for t in teams:
        if t["parent_team_id"]:
            if t["parent_team_id"] not in team_ids:
                raise ValueError("Inactive parent department")
            await connection.execute(
                "UPDATE platform.team SET parent_team_id=$2::uuid WHERE id=$1::uuid",
                team_ids[t["id"]],
                team_ids[t["parent_team_id"]],
            )
    root = next((t for t in teams if t["code"] == "ALL"), None)
    if root is None:
        root = next(t for t in teams if not t["parent_team_id"])
    await connection.execute(
        """INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name,attributes)
      VALUES($1::uuid,$2::uuid,$3,'OPSADMIN','运营管理员',$4::jsonb)""",
        managed_id,
        wid,
        "managed-" + managed_id,
        {"platform_managed": True},
    )
    await connection.execute(
        "INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code) VALUES($1::uuid,$2::uuid,'administrator','workspace')",
        wid,
        managed_id,
    )
    await connection.execute(
        "INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role,is_primary) VALUES($1::uuid,$2::uuid,$3::uuid,'administrator',true)",
        wid,
        managed_id,
        team_ids[root["id"]],
    )
    await connection.execute(
        "INSERT INTO security.company_management_grant(source_workspace_id,source_user_id,target_workspace_id,target_user_id) VALUES($1::uuid,$2::uuid,$3::uuid,$4::uuid)",
        admin.context.workspace_id,
        admin.context.user_id,
        wid,
        managed_id,
    )
    raw = await connection.fetchrow(
        "SELECT * FROM security.resolve_account_actor($1,'OPSADMIN','administrator')", target["code"]
    )
    target_actor = IdentityRepository()._actor(raw).context
    await set_request_context(connection, target_actor)
    for p in people:
        entry = mapping[p["account_code"]]
        memberships = [
            {"team_id": team_ids[m["team_id"]], "roles": m["roles"], "acting": m.get("acting", False)}
            for m in p["memberships"]
        ]
        created = await repository.create(
            connection,
            target_actor,
            {
                "account_code": entry["account"],
                "display_name": p["display_name"],
                "team_id": team_ids[p["team_id"]],
                "roles": p["roles"],
                "memberships": memberships,
            },
        )
        await repository.set_password(connection, created["id"], hashes[p["id"]])
    # Workspace-local dictionaries are prerequisite enums, not business records.
    # No source dictionary IDs or inactive/expired items survive the copy.
    dictionaries = await connection.fetch(
        "SELECT id,code,name FROM config.dictionary WHERE workspace_id=$1::uuid AND status='active'",
        admin.context.workspace_id,
    )
    for d in dictionaries:
        new_id = str(uuid4())
        await connection.execute(
            "INSERT INTO config.dictionary(id,workspace_id,code,name) VALUES($1::uuid,$2::uuid,$3,$4)",
            new_id,
            wid,
            d["code"],
            d["name"],
        )
        await connection.execute(
            """INSERT INTO config.dictionary_item(dictionary_id,item_code,item_label,sort_order,attributes)
          SELECT $1::uuid,item_code,item_label,sort_order,attributes FROM config.dictionary_item
          WHERE dictionary_id=$2 AND is_active AND clock_timestamp()>=valid_from AND clock_timestamp()<valid_to""",
            new_id,
            d["id"],
        )
    await connection.execute(
        """INSERT INTO ops.audit_log(workspace_id,actor_user_ref_id,actor_role_code,action_code,module_code,object_type,object_id,object_label,after_snapshot,result_code,request_id)
      VALUES($1::uuid,$2::uuid,'administrator','company.initialize','organization','workspace',$1::uuid,$3,$4::jsonb,'success',NULLIF(current_setting('app.request_id',true),'')::uuid)""",
        wid,
        managed_id,
        target["name"],
        {
            "source_administrator_id": admin.context.user_id,
            "business_accounts": len(people),
            "departments": len(teams),
            "business_records_copied": 0,
        },
    )
    return {**result, "applied": True, "company_id": wid, "request_id": request_metadata.get().request_id}


async def main(args):
    manifest = json.loads(open(args.manifest, encoding="utf8").read())
    password = sys.stdin.readline().rstrip("\r\n") if args.apply else ""
    conn = await asyncpg.connect(host=args.host, port=args.port, database=args.database, user=args.user)
    await _initialize_connection(conn)
    token = request_metadata.set(RequestMetadata(request_id=str(uuid4()), user_agent="company-initialization"))
    try:
        async with conn.transaction():
            result = await provision(
                conn,
                manifest,
                apply=args.apply,
                expected=args.expected_plan,
                password=password,
                allow_letters=args.allow_letters_initial,
            )
        print(json.dumps(result, ensure_ascii=False))
    finally:
        request_metadata.reset(token)
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--host", default="/tmp")
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--database", required=True)
    parser.add_argument("--user", default="postgres")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-plan")
    parser.add_argument("--allow-letters-initial", action="store_true")
    asyncio.run(main(parser.parse_args()))
